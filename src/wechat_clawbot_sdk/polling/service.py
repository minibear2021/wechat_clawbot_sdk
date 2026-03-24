from __future__ import annotations

from asyncio import Event, sleep
from collections.abc import AsyncIterator
from dataclasses import asdict, is_dataclass, replace
from time import monotonic
from typing import Any

from .._logging import SdkLogger, create_sdk_logger
from ..api import AsyncBotApiClient, GetUpdatesResponse
from ..errors import ProtocolError, SessionExpiredError, TransportError
from ..messaging import normalize_inbound_message
from ..models import AccountSession, PollCursor, PollEvent, PollEventType


DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000
MAX_CONSECUTIVE_FAILURES = 3
BACKOFF_DELAY_SECONDS = 30.0
RETRY_DELAY_SECONDS = 2.0
SESSION_PAUSE_DURATION_SECONDS = 60.0 * 60.0


class AsyncPollingServiceImpl:
    def __init__(
        self,
        *,
        api_client: AsyncBotApiClient,
        logger: SdkLogger | None = None,
    ) -> None:
        self._api_client = api_client
        self._closed = Event()
        self._pause_until_by_account: dict[str, float] = {}
        self._logger = logger or create_sdk_logger().child("polling")

    async def poll_events(
        self,
        session: AccountSession,
        cursor: PollCursor,
    ) -> AsyncIterator[PollEvent]:
        next_cursor = replace(cursor)
        if next_cursor.timeout_ms is None:
            next_cursor.timeout_ms = DEFAULT_LONG_POLL_TIMEOUT_MS
        consecutive_failures = 0

        while not self._closed.is_set():
            remaining_pause_seconds = self.get_remaining_pause_seconds(session.account_id)
            if remaining_pause_seconds > 0:
                self._logger.info(
                    "session paused account_id=%s remaining=%sms",
                    session.account_id,
                    int(remaining_pause_seconds * 1000),
                )
                yield PollEvent(
                    event_type=PollEventType.SESSION_PAUSED,
                    account_id=session.account_id,
                    cursor=replace(next_cursor),
                    metadata={"remaining_pause_ms": int(remaining_pause_seconds * 1000)},
                )
                await sleep(min(remaining_pause_seconds, RETRY_DELAY_SECONDS))
                continue

            try:
                response = await self._api_client.get_updates(session, next_cursor)
            except SessionExpiredError as exc:
                consecutive_failures = 0
                self.pause_session(session.account_id)
                self._logger.warning("session expired account_id=%s", session.account_id)
                remaining_pause_seconds = self.get_remaining_pause_seconds(session.account_id)
                yield PollEvent(
                    event_type=PollEventType.SESSION_EXPIRED,
                    account_id=session.account_id,
                    cursor=replace(next_cursor),
                    error=exc,
                    metadata={"remaining_pause_ms": int(remaining_pause_seconds * 1000)},
                )
                continue
            except (ProtocolError, TransportError) as exc:
                consecutive_failures += 1
                self._logger.warning(
                    "polling error account_id=%s consecutive_failures=%s err=%s",
                    session.account_id,
                    consecutive_failures,
                    exc,
                )
                yield PollEvent(
                    event_type=PollEventType.ERROR,
                    account_id=session.account_id,
                    cursor=replace(next_cursor),
                    error=exc,
                    metadata={"consecutive_failures": consecutive_failures},
                )
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    consecutive_failures = 0
                    await sleep(BACKOFF_DELAY_SECONDS)
                else:
                    await sleep(RETRY_DELAY_SECONDS)
                continue

            consecutive_failures = 0
            next_cursor = self._advance_cursor(next_cursor, response)
            messages = response.msgs or []
            self._logger.debug("poll response account_id=%s message_count=%s", session.account_id, len(messages))
            if not messages:
                yield PollEvent(
                    event_type=PollEventType.TIMEOUT,
                    account_id=session.account_id,
                    cursor=replace(next_cursor),
                    metadata={"longpolling_timeout_ms": response.longpolling_timeout_ms},
                )
                continue

            for raw_message in messages:
                normalized_raw = self._raw_message_to_dict(raw_message)
                yield PollEvent(
                    event_type=PollEventType.MESSAGE,
                    account_id=session.account_id,
                    cursor=replace(next_cursor),
                    message=normalize_inbound_message(session.account_id, normalized_raw),
                    metadata={"raw_message": normalized_raw},
                )

    async def close(self) -> None:
        self._closed.set()
        self._logger.debug("polling service closed")

    def pause_session(self, account_id: str, *, duration_seconds: float = SESSION_PAUSE_DURATION_SECONDS) -> None:
        self._pause_until_by_account[account_id] = monotonic() + duration_seconds

    def is_session_paused(self, account_id: str) -> bool:
        return self.get_remaining_pause_seconds(account_id) > 0

    def get_remaining_pause_seconds(self, account_id: str) -> float:
        pause_until = self._pause_until_by_account.get(account_id)
        if pause_until is None:
            return 0.0
        remaining = pause_until - monotonic()
        if remaining <= 0:
            self._pause_until_by_account.pop(account_id, None)
            return 0.0
        return remaining

    @staticmethod
    def _advance_cursor(cursor: PollCursor, response: GetUpdatesResponse) -> PollCursor:
        next_cursor = replace(cursor)
        if response.get_updates_buf is not None:
            next_cursor.get_updates_buf = response.get_updates_buf
        if response.longpolling_timeout_ms is not None and response.longpolling_timeout_ms > 0:
            next_cursor.timeout_ms = response.longpolling_timeout_ms
        return next_cursor

    @staticmethod
    def _raw_message_to_dict(raw_message: object) -> dict[str, Any]:
        if isinstance(raw_message, dict):
            return raw_message
        if is_dataclass(raw_message) and not isinstance(raw_message, type):
            return asdict(raw_message)
        raise ProtocolError(f"unsupported message payload type: {type(raw_message)!r}")

