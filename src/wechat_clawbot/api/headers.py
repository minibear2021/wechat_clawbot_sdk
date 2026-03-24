from __future__ import annotations

import base64
import secrets
from json import dumps

from ..version import CHANNEL_VERSION
from .protocol import BaseInfo


AUTHORIZATION_TYPE = "ilink_bot_token"
QRCODE_CLIENT_VERSION = "1"
BOT_API_USER_AGENT = "node"


def build_base_info() -> BaseInfo:
    return BaseInfo(channel_version=CHANNEL_VERSION)


def random_wechat_uin() -> str:
    uint32_value = secrets.randbits(32)
    decimal_string = str(uint32_value).encode("utf-8")
    return base64.b64encode(decimal_string).decode("ascii")


def build_json_headers(
    *,
    body: str,
    token: str | None = None,
    route_tag: str | None = None,
) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": AUTHORIZATION_TYPE,
        "Content-Length": str(len(body.encode("utf-8"))),
        "User-Agent": BOT_API_USER_AGENT,
        "X-WECHAT-UIN": random_wechat_uin(),
    }
    if token:
        headers["Authorization"] = f"Bearer {token.strip()}"
    if route_tag:
        headers["SKRouteTag"] = route_tag
    return headers


def build_login_headers(*, route_tag: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {"User-Agent": BOT_API_USER_AGENT}
    if route_tag:
        headers["SKRouteTag"] = route_tag
    return headers


def build_qrcode_status_headers(*, route_tag: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": BOT_API_USER_AGENT,
        "iLink-App-ClientVersion": QRCODE_CLIENT_VERSION,
    }
    if route_tag:
        headers["SKRouteTag"] = route_tag
    return headers


def encode_json_body(payload: dict[str, object]) -> str:
    return dumps(payload, ensure_ascii=False, separators=(",", ":"))
