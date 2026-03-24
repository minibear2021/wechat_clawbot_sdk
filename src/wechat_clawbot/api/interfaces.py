from __future__ import annotations

from typing import Any, Protocol

from ..models import AccountSession, LoginSession, PollCursor
from .protocol import GetConfigResponse, GetUpdatesResponse, GetUploadUrlResponse, SendTypingResponse


class AsyncHttpTransport(Protocol):
    async def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
        timeout_is_expected: bool = False,
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class AsyncBotApiClient(Protocol):
    async def fetch_qrcode(self, login_session: LoginSession) -> dict[str, Any]: ...

    async def poll_qrcode_status(
        self,
        qrcode: str,
        *,
        base_url: str,
        route_tag: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]: ...

    async def get_updates(
        self,
        session: AccountSession,
        cursor: PollCursor,
    ) -> GetUpdatesResponse: ...

    async def send_message(
        self,
        session: AccountSession,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def get_config(
        self,
        session: AccountSession,
        *,
        user_id: str,
        context_token: str | None = None,
    ) -> GetConfigResponse: ...

    async def send_typing(
        self,
        session: AccountSession,
        *,
        user_id: str,
        typing_ticket: str,
        status: int,
    ) -> SendTypingResponse: ...

    async def get_upload_url(
        self,
        session: AccountSession,
        payload: dict[str, Any],
    ) -> GetUploadUrlResponse: ...

    async def close(self) -> None: ...
