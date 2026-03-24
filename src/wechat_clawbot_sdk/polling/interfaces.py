from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from ..models import AccountSession, PollCursor, PollEvent


class AsyncPollingService(Protocol):
    def poll_events(
        self,
        session: AccountSession,
        cursor: PollCursor,
    ) -> AsyncIterator[PollEvent]: ...

    async def close(self) -> None: ...
