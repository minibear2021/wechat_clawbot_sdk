from __future__ import annotations

from dataclasses import replace

from ..errors import ValidationError
from ..models import AccountSession, PollCursor


class InMemoryStateStore:
    def __init__(self) -> None:
        self._sessions: dict[str, AccountSession] = {}
        self._poll_cursors: dict[str, PollCursor] = {}
        self._context_tokens: dict[tuple[str, str], str | None] = {}

    async def save_account_session(self, session: AccountSession) -> None:
        self._sessions[session.account_id] = replace(session)

    async def load_account_session(self, account_id: str) -> AccountSession:
        session = self._sessions.get(account_id)
        if session is None:
            raise ValidationError(f"unknown account_id: {account_id}")
        return replace(session)

    async def save_poll_cursor(self, account_id: str, cursor: PollCursor) -> None:
        self._poll_cursors[account_id] = replace(cursor)

    async def load_poll_cursor(self, account_id: str) -> PollCursor:
        cursor = self._poll_cursors.get(account_id)
        if cursor is None:
            return PollCursor()
        return replace(cursor)

    async def save_context_token(
        self,
        *,
        account_id: str,
        user_id: str,
        context_token: str | None,
    ) -> None:
        self._context_tokens[(account_id, user_id)] = context_token

    async def load_context_token(self, *, account_id: str, user_id: str) -> str | None:
        return self._context_tokens.get((account_id, user_id))

    async def close(self) -> None:
        return None
