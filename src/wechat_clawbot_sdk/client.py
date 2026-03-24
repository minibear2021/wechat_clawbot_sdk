from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from urllib.parse import unquote, urlparse

from ._logging import LoggerLike, create_sdk_logger
from .api import TypingStatus
from .api.client import WeChatBotApiClient
from .api.config_cache import AsyncConfigProvider, CachedConfig, WeChatBotConfigCache
from .api.httpx_transport import HttpxAsyncTransport
from .api.interfaces import AsyncBotApiClient, AsyncHttpTransport
from .auth.interfaces import AsyncLoginService, QRCodeRefreshCallback
from .auth.service import AsyncQrLoginService
from .errors import ProtocolError, SessionExpiredError, TransportError, ValidationError
from .messaging.interfaces import AsyncMessageService
from .messaging.service import AsyncMessageServiceImpl
from .messaging.typing import AsyncTypingService
from .messaging.typing import AsyncTypingServiceImpl
from .media.mime import get_mime_from_filename
from .models import (
    AccountStatus,
    AccountSession,
    InboundMessage,
    MediaPayload,
    OutboundMessage,
    PollCursor,
    PollEvent,
    PollEventType,
    PollingStatus,
    QRCodeSession,
)
from .polling.interfaces import AsyncPollingService
from .polling.service import AsyncPollingServiceImpl
from .state import FileStateStore
from .state import resolve_default_state_dir
from .state.interfaces import AsyncStateStore


EventHandler = Callable[[PollEvent], Awaitable[None] | None]

DEFAULT_LOGIN_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"


class AsyncWeChatBotClient:
    """Async SDK entrypoint for third-party systems."""

    def __init__(
        self,
        *,
        login_service: AsyncLoginService,
        polling_service: AsyncPollingService,
        message_service: AsyncMessageService,
        state_store: AsyncStateStore,
        config_provider: AsyncConfigProvider | None = None,
        typing_service: AsyncTypingService | None = None,
        api_client: AsyncBotApiClient | None = None,
        close_api_client: bool = False,
        logger: logging.Logger | LoggerLike | None = None,
        debug: bool = False,
    ) -> None:
        self._login_service = login_service
        self._polling_service = polling_service
        self._message_service = message_service
        self._state_store = state_store
        self._config_provider = config_provider
        self._typing_service = typing_service
        self._api_client = api_client
        self._close_api_client = close_api_client
        self._logger = create_sdk_logger(logger, debug=debug).child("client")

    @classmethod
    def create(
        cls,
        *,
        login_base_url: str = DEFAULT_LOGIN_BASE_URL,
        cdn_base_url: str = DEFAULT_CDN_BASE_URL,
        state_store: AsyncStateStore | None = None,
        state_dir: str | Path | None = None,
        transport: AsyncHttpTransport | None = None,
        config_provider: AsyncConfigProvider | None = None,
        typing_service: AsyncTypingService | None = None,
        logger: logging.Logger | LoggerLike | None = None,
        debug: bool = False,
    ) -> AsyncWeChatBotClient:
        if state_store is not None and state_dir is not None:
            raise ValidationError("state_store and state_dir cannot be used together")
        sdk_logger = create_sdk_logger(logger, debug=debug)
        resolved_transport = transport or HttpxAsyncTransport(logger=sdk_logger.child("transport"))
        api_client = WeChatBotApiClient(transport=resolved_transport, logger=sdk_logger.child("api"))
        resolved_state_store = state_store or FileStateStore(state_dir or resolve_default_state_dir())
        resolved_config_provider = config_provider or WeChatBotConfigCache(
            api_client=api_client,
            logger=sdk_logger.child("config"),
        )
        resolved_typing_service = typing_service or AsyncTypingServiceImpl(
            api_client=api_client,
            config_provider=resolved_config_provider,
            logger=sdk_logger.child("typing"),
        )
        return cls(
            login_service=AsyncQrLoginService(
                api_client=api_client,
                base_url=login_base_url,
                logger=sdk_logger.child("auth"),
            ),
            polling_service=AsyncPollingServiceImpl(
                api_client=api_client,
                logger=sdk_logger.child("polling"),
            ),
            message_service=AsyncMessageServiceImpl(
                api_client=api_client,
                cdn_base_url=cdn_base_url,
                logger=sdk_logger.child("message"),
            ),
            state_store=resolved_state_store,
            config_provider=resolved_config_provider,
            typing_service=resolved_typing_service,
            api_client=api_client,
            close_api_client=True,
            logger=logger,
            debug=debug,
        )

    async def start_login(self, *, route_tag: str | None = None, bot_type: int = 3) -> QRCodeSession:
        self._logger.info("start_login route_tag=%s bot_type=%s", route_tag, bot_type)
        return await self._login_service.start_login(route_tag=route_tag, bot_type=bot_type)

    async def wait_for_login(
        self,
        qrcode: str,
        *,
        route_tag: str | None = None,
        timeout_seconds: float | None = None,
        on_qrcode_refresh: QRCodeRefreshCallback | None = None,
    ) -> AccountSession:
        self._logger.info("wait_for_login qrcode=%s", qrcode)
        session = await self._login_service.wait_for_login(
            qrcode,
            route_tag=route_tag,
            timeout_seconds=timeout_seconds,
            on_qrcode_refresh=on_qrcode_refresh,
        )
        await self._state_store.save_account_session(session)
        self._logger.info("account session saved account_id=%s", session.account_id)
        return session

    async def poll_events(
        self,
        account_id: str,
    ) -> AsyncIterator[PollEvent]:
        self._logger.info("poll_events start account_id=%s", account_id)
        session = await self._state_store.load_account_session(account_id)
        cursor = await self._state_store.load_poll_cursor(account_id)
        async for event in self._polling_service.poll_events(session, cursor):
            await self._state_store.save_poll_cursor(account_id, event.cursor)
            if event.message is not None and event.message.user_id and event.message.context_token:
                await self._state_store.save_context_token(
                    account_id=event.message.account_id,
                    user_id=event.message.user_id,
                    context_token=event.message.context_token,
                )
                self._logger.debug(
                    "context token persisted account_id=%s user_id=%s",
                    event.message.account_id,
                    event.message.user_id,
                )
            yield event

    async def consume_events(
        self,
        account_id: str,
        on_event: EventHandler,
        *,
        message_only: bool = False,
        stop_on_error: bool = False,
    ) -> None:
        async for event in self.poll_events(account_id):
            if message_only and event.event_type is not PollEventType.MESSAGE:
                continue
            result = on_event(event)
            if inspect.isawaitable(result):
                await result
            if stop_on_error and event.event_type in {PollEventType.ERROR, PollEventType.SESSION_EXPIRED}:
                return

    async def send_text(
        self,
        *,
        account_id: str,
        user_id: str,
        text: str,
        context_token: str | None = None,
    ) -> None:
        resolved_context_token = context_token or await self._state_store.load_context_token(
            account_id=account_id,
            user_id=user_id,
        )
        if not resolved_context_token:
            raise ValidationError("context_token is required for replies")
        message = OutboundMessage(
            account_id=account_id,
            user_id=user_id,
            context_token=resolved_context_token,
            text=text,
        )
        session = await self._state_store.load_account_session(account_id)
        await self._message_service.send_text(session, message)

    async def send_media(
        self,
        *,
        account_id: str,
        user_id: str,
        context_token: str | None,
        media: MediaPayload,
        text: str | None = None,
    ) -> None:
        resolved_context_token = context_token or await self._state_store.load_context_token(
            account_id=account_id,
            user_id=user_id,
        )
        if not resolved_context_token:
            raise ValidationError("context_token is required for replies")
        message = OutboundMessage(
            account_id=account_id,
            user_id=user_id,
            context_token=resolved_context_token,
            text=text,
            media=[media],
        )
        session = await self._state_store.load_account_session(account_id)
        await self._message_service.send_media(session, message)

    async def send_image(
        self,
        *,
        account_id: str,
        user_id: str,
        context_token: str | None = None,
        local_path: str | Path | None = None,
        remote_url: str | None = None,
        filename: str | None = None,
        mime_type: str | None = None,
        text: str | None = None,
    ) -> None:
        await self.send_media(
            account_id=account_id,
            user_id=user_id,
            context_token=context_token,
            media=self._build_media_payload(
                local_path=local_path,
                remote_url=remote_url,
                filename=filename,
                mime_type=mime_type,
                fallback_mime_type="image/png",
            ),
            text=text,
        )

    async def send_video(
        self,
        *,
        account_id: str,
        user_id: str,
        context_token: str | None = None,
        local_path: str | Path | None = None,
        remote_url: str | None = None,
        filename: str | None = None,
        mime_type: str | None = None,
        text: str | None = None,
    ) -> None:
        await self.send_media(
            account_id=account_id,
            user_id=user_id,
            context_token=context_token,
            media=self._build_media_payload(
                local_path=local_path,
                remote_url=remote_url,
                filename=filename,
                mime_type=mime_type,
                fallback_mime_type="video/mp4",
            ),
            text=text,
        )

    async def send_file(
        self,
        *,
        account_id: str,
        user_id: str,
        context_token: str | None = None,
        local_path: str | Path | None = None,
        remote_url: str | None = None,
        filename: str | None = None,
        mime_type: str | None = None,
        text: str | None = None,
    ) -> None:
        await self.send_media(
            account_id=account_id,
            user_id=user_id,
            context_token=context_token,
            media=self._build_media_payload(
                local_path=local_path,
                remote_url=remote_url,
                filename=filename,
                mime_type=mime_type,
                fallback_mime_type="application/octet-stream",
            ),
            text=text,
        )

    async def get_account_session(self, account_id: str) -> AccountSession:
        return await self._state_store.load_account_session(account_id)

    async def get_poll_cursor(self, account_id: str) -> PollCursor:
        return await self._state_store.load_poll_cursor(account_id)

    async def get_account_status(self, account_id: str) -> AccountStatus:
        try:
            session = await self._state_store.load_account_session(account_id)
        except ValidationError:
            return AccountStatus(account_id=account_id, logged_in=False)
        return AccountStatus(account_id=account_id, logged_in=True, session=session)

    async def is_account_session_alive(self, account_id: str, *, timeout_ms: int = 1_000) -> bool:
        session = await self._state_store.load_account_session(account_id)
        cursor = await self._state_store.load_poll_cursor(account_id)
        probe_cursor = PollCursor(
            get_updates_buf=cursor.get_updates_buf,
            timeout_ms=timeout_ms,
        )
        try:
            await self._api_client.get_updates(session, probe_cursor)
            self._logger.debug("account session alive account_id=%s", account_id)
            return True
        except SessionExpiredError:
            pause_session = getattr(self._polling_service, "pause_session", None)
            if callable(pause_session):
                pause_session(account_id)
            self._logger.warning("account session expired account_id=%s", account_id)
            return False
        except (ProtocolError, TransportError):
            self._logger.debug("account session probe inconclusive account_id=%s", account_id)
            return True

    async def get_polling_status(self, account_id: str) -> PollingStatus:
        cursor = await self._state_store.load_poll_cursor(account_id)
        try:
            await self._state_store.load_account_session(account_id)
            has_session = True
        except ValidationError:
            has_session = False
        remaining_pause_ms = 0
        session_paused = False
        remaining_pause_getter = getattr(self._polling_service, "get_remaining_pause_seconds", None)
        is_session_paused_getter = getattr(self._polling_service, "is_session_paused", None)
        if callable(remaining_pause_getter):
            remaining_pause_seconds = remaining_pause_getter(account_id)
            if isinstance(remaining_pause_seconds, int | float):
                remaining_pause_ms = int(max(float(remaining_pause_seconds), 0.0) * 1000)
                session_paused = remaining_pause_ms > 0
        elif callable(is_session_paused_getter):
            session_paused = bool(is_session_paused_getter(account_id))
        return PollingStatus(
            account_id=account_id,
            cursor=cursor,
            has_session=has_session,
            session_paused=session_paused,
            remaining_pause_ms=remaining_pause_ms,
        )

    async def remember_inbound_message(self, message: InboundMessage) -> None:
        if not message.user_id or not message.context_token:
            return
        await self._state_store.save_context_token(
            account_id=message.account_id,
            user_id=message.user_id,
            context_token=message.context_token,
        )

    async def get_context_token(self, *, account_id: str, user_id: str) -> str | None:
        return await self._state_store.load_context_token(account_id=account_id, user_id=user_id)

    async def get_cached_config(
        self,
        *,
        account_id: str,
        user_id: str,
        context_token: str | None = None,
    ) -> CachedConfig:
        if self._config_provider is None:
            raise ValidationError("config_provider is not configured")
        session = await self._state_store.load_account_session(account_id)
        resolved_context_token = context_token or await self._state_store.load_context_token(
            account_id=account_id,
            user_id=user_id,
        )
        return await self._config_provider.get_for_user(
            session,
            user_id=user_id,
            context_token=resolved_context_token,
        )

    async def send_typing(
        self,
        *,
        account_id: str,
        user_id: str,
        status: int = int(TypingStatus.TYPING),
        context_token: str | None = None,
    ) -> bool:
        if self._typing_service is None:
            raise ValidationError("typing_service is not configured")
        session = await self._state_store.load_account_session(account_id)
        resolved_context_token = context_token or await self._state_store.load_context_token(
            account_id=account_id,
            user_id=user_id,
        )
        return await self._typing_service.send_typing(
            session,
            user_id=user_id,
            context_token=resolved_context_token,
            status=status,
        )

    async def close(self) -> None:
        self._logger.info("closing client")
        await self._login_service.close()
        await self._polling_service.close()
        await self._message_service.close()
        if self._typing_service is not None:
            await self._typing_service.close()
        if self._config_provider is not None:
            await self._config_provider.close()
        await self._state_store.close()
        if self._close_api_client and self._api_client is not None:
            await self._api_client.close()

    @staticmethod
    def _build_media_payload(
        *,
        local_path: str | Path | None,
        remote_url: str | None,
        filename: str | None,
        mime_type: str | None,
        fallback_mime_type: str,
    ) -> MediaPayload:
        resolved_path = Path(local_path) if local_path is not None else None
        resolved_filename = filename
        if resolved_path is not None:
            resolved_filename = resolved_filename or resolved_path.name
        elif remote_url is not None:
            resolved_filename = resolved_filename or _filename_from_remote_url(remote_url)
        if not resolved_filename:
            raise ValidationError("filename is required when media source has no basename")
        resolved_mime_type = mime_type or get_mime_from_filename(resolved_filename)
        if not resolved_mime_type or resolved_mime_type == "application/octet-stream":
            resolved_mime_type = mime_type or fallback_mime_type
        return MediaPayload(
            filename=resolved_filename,
            mime_type=resolved_mime_type,
            local_path=resolved_path,
            remote_url=remote_url,
        )


def _filename_from_remote_url(remote_url: str) -> str:
    path = unquote(urlparse(remote_url).path)
    name = Path(path).name
    return name or "media.bin"
