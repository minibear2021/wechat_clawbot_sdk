from __future__ import annotations

from asyncio import CancelledError, Task, create_task, gather, sleep
from typing import Protocol

from .._logging import SdkLogger, create_sdk_logger
from ..api import AsyncBotApiClient, TypingStatus
from ..api.config_cache import AsyncConfigProvider
from ..models import AccountSession


TYPING_KEEPALIVE_INTERVAL_SECONDS = 5.0


TypingKey = tuple[str, str]


class AsyncTypingService(Protocol):
    async def send_typing(
        self,
        session: AccountSession,
        *,
        user_id: str,
        context_token: str | None = None,
        status: int = int(TypingStatus.TYPING),
    ) -> bool: ...

    async def close(self) -> None: ...


class AsyncTypingServiceImpl:
    def __init__(
        self,
        *,
        api_client: AsyncBotApiClient,
        config_provider: AsyncConfigProvider,
        logger: SdkLogger | None = None,
    ) -> None:
        self._api_client = api_client
        self._config_provider = config_provider
        self._keepalive_tasks: dict[TypingKey, Task[None]] = {}
        self._logger = logger or create_sdk_logger().child("typing")

    async def send_typing(
        self,
        session: AccountSession,
        *,
        user_id: str,
        context_token: str | None = None,
        status: int = int(TypingStatus.TYPING),
    ) -> bool:
        config = await self._config_provider.get_for_user(
            session,
            user_id=user_id,
            context_token=context_token,
        )
        if not config.typing_ticket:
            if status == int(TypingStatus.CANCEL):
                await self._stop_keepalive(session.account_id, user_id)
            self._logger.debug("typing ticket missing account_id=%s user_id=%s", session.account_id, user_id)
            return False
        key = (session.account_id, user_id)
        if status == int(TypingStatus.CANCEL):
            await self._stop_keepalive(*key)
            self._logger.debug("typing cancel requested account_id=%s user_id=%s", session.account_id, user_id)
        elif status == int(TypingStatus.TYPING):
            await self._stop_keepalive(*key)
            self._logger.debug("typing start requested account_id=%s user_id=%s", session.account_id, user_id)
        await self._api_client.send_typing(
            session,
            user_id=user_id,
            typing_ticket=config.typing_ticket,
            status=status,
        )
        if status == int(TypingStatus.TYPING):
            self._keepalive_tasks[key] = create_task(
                self._run_keepalive(
                    session=session,
                    user_id=user_id,
                    typing_ticket=config.typing_ticket,
                )
            )
            self._keepalive_tasks[key].add_done_callback(
                lambda task, typing_key=key: self._discard_keepalive_task(typing_key, task)
            )
        return True

    async def close(self) -> None:
        tasks = list(self._keepalive_tasks.values())
        self._keepalive_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await gather(*tasks, return_exceptions=True)
        self._logger.debug("typing service closed")

    async def _run_keepalive(
        self,
        *,
        session: AccountSession,
        user_id: str,
        typing_ticket: str,
    ) -> None:
        try:
            while True:
                await sleep(TYPING_KEEPALIVE_INTERVAL_SECONDS)
                self._logger.debug("typing keepalive account_id=%s user_id=%s", session.account_id, user_id)
                await self._api_client.send_typing(
                    session,
                    user_id=user_id,
                    typing_ticket=typing_ticket,
                    status=int(TypingStatus.TYPING),
                )
        except CancelledError:
            raise
        except Exception as exc:
            self._logger.warning(
                "typing keepalive failed account_id=%s user_id=%s err=%s",
                session.account_id,
                user_id,
                exc,
            )
            return

    async def _stop_keepalive(self, account_id: str, user_id: str) -> None:
        task = self._keepalive_tasks.pop((account_id, user_id), None)
        if task is None:
            return
        task.cancel()
        await gather(task, return_exceptions=True)
        self._logger.debug("typing keepalive stopped account_id=%s user_id=%s", account_id, user_id)

    def _discard_keepalive_task(self, key: TypingKey, task: Task[None]) -> None:
        if self._keepalive_tasks.get(key) is task:
            self._keepalive_tasks.pop(key, None)