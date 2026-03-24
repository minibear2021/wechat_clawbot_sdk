from __future__ import annotations

import json
import os
import sys
from asyncio import to_thread
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from ..errors import ValidationError
from ..models import AccountSession, PollCursor


DEFAULT_STATE_DIR_ENV_VAR = "WECHAT_CLAWBOT_SDK_STATE_DIR"


def resolve_default_state_dir() -> Path:
    override = os.environ.get(DEFAULT_STATE_DIR_ENV_VAR)
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "wechat_clawbot_sdk"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "wechat_clawbot_sdk"
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home).expanduser() / "wechat_clawbot_sdk"
    return Path.home() / ".local" / "state" / "wechat_clawbot_sdk"


class FileStateStore:
    def __init__(self, root_dir: str | Path) -> None:
        self._root_dir = Path(root_dir).expanduser()

    async def save_account_session(self, session: AccountSession) -> None:
        path = self._account_session_path(session.account_id)
        payload = asdict(session)
        await self._write_json(path, payload)

    async def load_account_session(self, account_id: str) -> AccountSession:
        path = self._account_session_path(account_id)
        payload = await self._read_json(path)
        if payload is None:
            raise ValidationError(f"unknown account_id: {account_id}")
        return AccountSession(**cast(dict[str, Any], payload))

    async def save_poll_cursor(self, account_id: str, cursor: PollCursor) -> None:
        path = self._poll_cursor_path(account_id)
        await self._write_json(path, asdict(cursor))

    async def load_poll_cursor(self, account_id: str) -> PollCursor:
        payload = await self._read_json(self._poll_cursor_path(account_id))
        if payload is None:
            return PollCursor()
        return PollCursor(**cast(dict[str, Any], payload))

    async def save_context_token(
        self,
        *,
        account_id: str,
        user_id: str,
        context_token: str | None,
    ) -> None:
        path = self._context_tokens_path(account_id)
        payload = await self._read_json(path) or {}
        if context_token:
            payload[user_id] = context_token
        else:
            payload.pop(user_id, None)
        await self._write_or_delete_json(path, payload)

    async def load_context_token(self, *, account_id: str, user_id: str) -> str | None:
        payload = await self._read_json(self._context_tokens_path(account_id))
        if payload is None:
            return None
        value = payload.get(user_id)
        return value if isinstance(value, str) and value else None

    async def close(self) -> None:
        return None

    def _account_session_path(self, account_id: str) -> Path:
        return self._root_dir / "accounts" / f"{account_id}.json"

    def _poll_cursor_path(self, account_id: str) -> Path:
        return self._root_dir / "accounts" / f"{account_id}.sync.json"

    def _context_tokens_path(self, account_id: str) -> Path:
        return self._root_dir / "accounts" / f"{account_id}.context-tokens.json"

    async def _read_json(self, path: Path) -> dict[str, object] | None:
        return await to_thread(self._read_json_sync, path)

    async def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        await to_thread(self._write_json_sync, path, payload)

    async def _write_or_delete_json(self, path: Path, payload: dict[str, object]) -> None:
        await to_thread(self._write_or_delete_json_sync, path, payload)

    @staticmethod
    def _read_json_sync(path: Path) -> dict[str, object] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    @staticmethod
    def _write_json_sync(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    @staticmethod
    def _write_or_delete_json_sync(path: Path, payload: dict[str, object]) -> None:
        if payload:
            FileStateStore._write_json_sync(path, payload)
            return
        try:
            path.unlink()
        except FileNotFoundError:
            return
