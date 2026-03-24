from __future__ import annotations

from typing import Protocol

from ..models import AccountSession, OutboundMessage


class AsyncMessageService(Protocol):
    async def send_text(self, session: AccountSession, message: OutboundMessage) -> None: ...

    async def send_media(self, session: AccountSession, message: OutboundMessage) -> None: ...

    async def close(self) -> None: ...
