from __future__ import annotations

from typing import Protocol

from ..models import AccountSession, PollCursor


class AsyncStateStore(Protocol):
    async def save_account_session(self, session: AccountSession) -> None: ...

    async def load_account_session(self, account_id: str) -> AccountSession: ...

    async def save_poll_cursor(self, account_id: str, cursor: PollCursor) -> None: ...

    async def load_poll_cursor(self, account_id: str) -> PollCursor: ...

    async def save_context_token(
        self,
        *,
        account_id: str,
        user_id: str,
        context_token: str | None,
    ) -> None: ...

    async def load_context_token(self, *, account_id: str, user_id: str) -> str | None: ...

    async def close(self) -> None: ...
