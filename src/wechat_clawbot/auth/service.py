from __future__ import annotations

import inspect
from asyncio import sleep
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .._logging import SdkLogger, create_sdk_logger
from ..api import AsyncBotApiClient
from ..errors import ProtocolError, ValidationError
from ..models import AccountSession, LoginSession, QRCodeSession
from .interfaces import QRCodeRefreshCallback


ACTIVE_LOGIN_TTL_SECONDS = 5 * 60
DEFAULT_LOGIN_TIMEOUT_SECONDS = 480.0
DEFAULT_QR_REFRESH_LIMIT = 3
DEFAULT_BOT_TYPE = 3


@dataclass(slots=True)
class ActiveLogin:
    qrcode: str
    qrcode_image_content: str
    route_tag: str | None
    bot_type: int
    started_at_monotonic: float
    refresh_count: int = 0


class AsyncQrLoginService:
    def __init__(
        self,
        *,
        api_client: AsyncBotApiClient,
        base_url: str,
        refresh_limit: int = DEFAULT_QR_REFRESH_LIMIT,
        logger: SdkLogger | None = None,
    ) -> None:
        self._api_client = api_client
        self._base_url = base_url
        self._refresh_limit = refresh_limit
        self._active_logins: dict[str, ActiveLogin] = {}
        self._logger = logger or create_sdk_logger().child("auth")

    async def start_login(self, *, route_tag: str | None = None, bot_type: int = DEFAULT_BOT_TYPE) -> QRCodeSession:
        self._logger.info("starting QR login route_tag=%s bot_type=%s", route_tag, bot_type)
        login_session = LoginSession(base_url=self._base_url, route_tag=route_tag, bot_type=bot_type)
        payload = await self._api_client.fetch_qrcode(login_session)
        qrcode = self._require_string(payload, "qrcode")
        qrcode_image_content = self._require_string(payload, "qrcode_img_content")

        session = QRCodeSession(
            qrcode=qrcode,
            qrcode_image_content=qrcode_image_content,
            refresh_count=0,
        )
        self._active_logins[qrcode] = ActiveLogin(
            qrcode=qrcode,
            qrcode_image_content=qrcode_image_content,
            route_tag=route_tag,
            bot_type=bot_type,
            started_at_monotonic=self._monotonic(),
            refresh_count=0,
        )
        self._purge_expired_logins()
        return session

    async def wait_for_login(
        self,
        qrcode: str,
        *,
        route_tag: str | None = None,
        timeout_seconds: float | None = None,
        on_qrcode_refresh: QRCodeRefreshCallback | None = None,
    ) -> AccountSession:
        active_login = self._active_logins.get(qrcode)
        if active_login is None:
            self._logger.warning("wait_for_login called with unknown qrcode session")
            raise ValidationError("unknown qrcode session; call start_login first")

        effective_timeout = max(timeout_seconds or DEFAULT_LOGIN_TIMEOUT_SECONDS, 1.0)
        deadline = self._monotonic() + effective_timeout
        current_qrcode = qrcode

        while self._monotonic() < deadline:
            payload = await self._api_client.poll_qrcode_status(
                current_qrcode,
                base_url=self._base_url,
                route_tag=route_tag or active_login.route_tag,
            )
            status = self._require_string(payload, "status")
            self._logger.debug(
                "qrcode status=%s qrcode=%s refresh_count=%s route_tag=%s",
                status,
                current_qrcode,
                active_login.refresh_count,
                route_tag or active_login.route_tag,
            )

            if status == "wait":
                self._logger.debug(
                    "qrcode waiting for scan qrcode=%s refresh_count=%s",
                    current_qrcode,
                    active_login.refresh_count,
                )
                await sleep(1.0)
                continue

            if status == "scaned":
                self._logger.debug(
                    "qrcode scanned, waiting for confirmation qrcode=%s refresh_count=%s",
                    current_qrcode,
                    active_login.refresh_count,
                )
                await sleep(1.0)
                continue

            if status == "expired":
                self._logger.info(
                    "qrcode expired qrcode=%s refresh_count=%s",
                    current_qrcode,
                    active_login.refresh_count,
                )
                if active_login.refresh_count >= self._refresh_limit:
                    self._active_logins.pop(current_qrcode, None)
                    self._logger.warning("login timed out after repeated qrcode expiry")
                    raise ProtocolError("login timed out after repeated qrcode expiry")

                refreshed = await self.start_login(
                    route_tag=route_tag or active_login.route_tag,
                    bot_type=active_login.bot_type,
                )
                refreshed_state = self._active_logins.pop(refreshed.qrcode)
                refreshed_state.refresh_count = active_login.refresh_count + 1
                self._active_logins.pop(current_qrcode, None)
                self._active_logins[refreshed.qrcode] = refreshed_state
                active_login = refreshed_state
                current_qrcode = refreshed.qrcode
                self._logger.info(
                    "qrcode refreshed new_qrcode=%s refresh_count=%s",
                    current_qrcode,
                    active_login.refresh_count,
                )
                refreshed.refresh_count = refreshed_state.refresh_count
                await self._emit_refresh_callback(on_qrcode_refresh, refreshed)
                await sleep(1.0)
                continue

            if status != "confirmed":
                self._active_logins.pop(current_qrcode, None)
                self._logger.warning("unexpected qrcode status=%s", status)
                raise ProtocolError(f"unexpected qrcode status: {status}")

            account_id = self._require_string(payload, "ilink_bot_id")
            bot_token = self._require_string(payload, "bot_token")
            base_url = self._require_string(payload, "baseurl")
            user_id = self._optional_string(payload, "ilink_user_id")
            self._active_logins.pop(current_qrcode, None)
            self._logger.info(
                "login confirmed qrcode=%s account_id=%s has_user_id=%s",
                current_qrcode,
                account_id,
                bool(user_id),
            )
            return AccountSession(
                account_id=account_id,
                bot_id=account_id,
                base_url=base_url,
                bot_token=bot_token,
                route_tag=route_tag or active_login.route_tag,
                user_id=user_id,
            )

        self._active_logins.pop(current_qrcode, None)
        self._logger.warning("login timed out waiting for qrcode confirmation")
        raise ProtocolError("login timed out waiting for qrcode confirmation")

    async def close(self) -> None:
        self._active_logins.clear()

    def _purge_expired_logins(self) -> None:
        now = self._monotonic()
        expired = [
            qrcode
            for qrcode, active in self._active_logins.items()
            if (now - active.started_at_monotonic) >= ACTIVE_LOGIN_TTL_SECONDS
        ]
        for qrcode in expired:
            self._active_logins.pop(qrcode, None)

    @staticmethod
    def _require_string(payload: dict[str, object], field_name: str) -> str:
        value = payload.get(field_name)
        if isinstance(value, str) and value:
            return value
        raise ProtocolError(f"missing required field: {field_name}")

    @staticmethod
    def _optional_string(payload: dict[str, object], field_name: str) -> str | None:
        value = payload.get(field_name)
        return value if isinstance(value, str) and value else None

    @staticmethod
    async def _emit_refresh_callback(
        callback: QRCodeRefreshCallback | None,
        qrcode_session: QRCodeSession,
    ) -> None:
        if callback is None:
            return
        result = callback(qrcode_session)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _monotonic() -> float:
        from time import monotonic

        return monotonic()