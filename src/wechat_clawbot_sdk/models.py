from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class PollEventType(StrEnum):
    MESSAGE = "message"
    TIMEOUT = "timeout"
    SESSION_PAUSED = "session_paused"
    SESSION_EXPIRED = "session_expired"
    ERROR = "error"


@dataclass(slots=True)
class AccountSession:
    account_id: str
    bot_id: str
    base_url: str
    bot_token: str
    route_tag: str | None = None
    user_id: str | None = None


@dataclass(slots=True)
class AccountStatus:
    account_id: str
    logged_in: bool
    session: AccountSession | None = None


@dataclass(slots=True)
class LoginSession:
    base_url: str
    route_tag: str | None = None
    bot_type: int = 3


@dataclass(slots=True)
class QRCodeSession:
    qrcode: str
    qrcode_image_content: str
    refresh_count: int = 0


@dataclass(slots=True)
class PollCursor:
    get_updates_buf: str = ""
    timeout_ms: int | None = None


@dataclass(slots=True)
class PollingStatus:
    account_id: str
    cursor: PollCursor
    has_session: bool
    session_paused: bool
    remaining_pause_ms: int = 0


@dataclass(slots=True)
class MediaPayload:
    filename: str
    mime_type: str
    local_path: Path | None = None
    remote_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class InboundMessage:
    account_id: str
    user_id: str
    message_id: str | None
    context_token: str | None
    to_user_id: str | None = None
    session_id: str | None = None
    client_id: str | None = None
    timestamp_ms: int | None = None
    text: str | None = None
    command_body: str | None = None
    raw_message: dict[str, Any] = field(default_factory=dict)
    media: list[MediaPayload] = field(default_factory=list)


@dataclass(slots=True)
class OutboundMessage:
    account_id: str
    user_id: str
    context_token: str
    text: str | None = None
    media: list[MediaPayload] = field(default_factory=list)


@dataclass(slots=True)
class PollEvent:
    event_type: PollEventType
    account_id: str
    cursor: PollCursor
    message: InboundMessage | None = None
    error: Exception | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
