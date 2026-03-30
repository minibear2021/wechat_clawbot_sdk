from __future__ import annotations

import base64
import secrets
from json import dumps

from ..version import CHANNEL_VERSION, PROTOCOL_CLIENT_VERSION
from .protocol import BaseInfo


AUTHORIZATION_TYPE = "ilink_bot_token"
BOT_API_USER_AGENT = "node"
ILINK_APP_ID = "bot"


def build_base_info() -> BaseInfo:
    return BaseInfo(channel_version=CHANNEL_VERSION)


def build_client_version(version: str) -> str:
    parts = version.split(".")

    def parse(index: int) -> int:
        try:
            return int(parts[index]) if index < len(parts) else 0
        except ValueError:
            return 0

    major = parse(0)
    minor = parse(1)
    patch = parse(2)
    encoded = ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)
    return str(encoded)


def build_common_headers(*, route_tag: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": BOT_API_USER_AGENT,
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": build_client_version(PROTOCOL_CLIENT_VERSION),
    }
    if route_tag:
        headers["SKRouteTag"] = route_tag
    return headers


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
        "X-WECHAT-UIN": random_wechat_uin(),
        **build_common_headers(route_tag=route_tag),
    }
    if token:
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


def build_login_headers(*, route_tag: str | None = None) -> dict[str, str]:
    return build_common_headers(route_tag=route_tag)


def build_qrcode_status_headers(*, route_tag: str | None = None) -> dict[str, str]:
    return build_common_headers(route_tag=route_tag)


def encode_json_body(payload: dict[str, object]) -> str:
    return dumps(payload, ensure_ascii=False, separators=(",", ":"))
