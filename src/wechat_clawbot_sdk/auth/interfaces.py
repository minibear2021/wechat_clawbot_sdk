from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from ..models import AccountSession, QRCodeSession


QRCodeRefreshCallback = Callable[[QRCodeSession], Awaitable[None] | None]


class AsyncLoginService(Protocol):
    async def start_login(self, *, route_tag: str | None = None, bot_type: int = 3) -> QRCodeSession: ...

    async def wait_for_login(
        self,
        qrcode: str,
        *,
        route_tag: str | None = None,
        timeout_seconds: float | None = None,
        on_qrcode_refresh: QRCodeRefreshCallback | None = None,
    ) -> AccountSession: ...

    async def close(self) -> None: ...
