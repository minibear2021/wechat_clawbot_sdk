from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any, TypeVar
from urllib.parse import quote, urljoin

from .._logging import SdkLogger, create_sdk_logger
from ..errors import ProtocolError, SessionExpiredError, TransportError
from ..models import AccountSession, LoginSession, PollCursor
from .encoding import to_wire_dict
from .headers import build_base_info, build_json_headers, build_login_headers, build_qrcode_status_headers
from .interfaces import AsyncBotApiClient, AsyncHttpTransport
from .protocol import (
    GetConfigResponse,
    GetUpdatesRequest,
    GetUpdatesResponse,
    GetUploadUrlRequest,
    GetUploadUrlResponse,
    SendTypingResponse,
)


T = TypeVar("T")
SESSION_EXPIRED_ERRCODE = -14
DEFAULT_LONG_POLL_TIMEOUT_SECONDS = 35.0
DEFAULT_API_TIMEOUT_SECONDS = 15.0
DEFAULT_CONFIG_TIMEOUT_SECONDS = 10.0


def _ensure_trailing_slash(base_url: str) -> str:
    return base_url if base_url.endswith("/") else f"{base_url}/"


def _build_url(base_url: str, endpoint: str) -> str:
    return urljoin(_ensure_trailing_slash(base_url), endpoint)


def _decode_dataclass(model_type: type[T], payload: dict[str, Any]) -> T:
    if not is_dataclass(model_type):
        raise TypeError(f"model_type must be a dataclass type, got {model_type!r}")
    allowed = {field.name for field in fields(model_type)}
    kwargs = {key: value for key, value in payload.items() if key in allowed}
    return model_type(**kwargs)


class WeChatBotApiClient(AsyncBotApiClient):
    def __init__(self, *, transport: AsyncHttpTransport, logger: SdkLogger | None = None) -> None:
        self._transport = transport
        self._logger = logger or create_sdk_logger().child("api")

    async def fetch_qrcode(self, login_session: LoginSession) -> dict[str, Any]:
        endpoint = f"ilink/bot/get_bot_qrcode?bot_type={quote(str(login_session.bot_type))}"
        self._logger.info(
            "fetch_qrcode base_url=%s route_tag=%s bot_type=%s",
            login_session.base_url,
            login_session.route_tag,
            login_session.bot_type,
        )
        return await self._transport.request(
            method="GET",
            url=_build_url(login_session.base_url, endpoint),
            headers=build_login_headers(route_tag=login_session.route_tag),
            timeout_seconds=DEFAULT_API_TIMEOUT_SECONDS,
        )

    async def poll_qrcode_status(
        self,
        qrcode: str,
        *,
        route_tag: str | None = None,
        timeout_seconds: float | None = None,
        base_url: str,
    ) -> dict[str, Any]:
        endpoint = f"ilink/bot/get_qrcode_status?qrcode={quote(qrcode)}"
        self._logger.debug(
            "poll_qrcode_status qrcode=%s route_tag=%s timeout=%s",
            qrcode,
            route_tag,
            timeout_seconds or DEFAULT_LONG_POLL_TIMEOUT_SECONDS,
        )
        try:
            return await self._transport.request(
                method="GET",
                url=_build_url(base_url, endpoint),
                headers=build_qrcode_status_headers(route_tag=route_tag),
                timeout_seconds=timeout_seconds or DEFAULT_LONG_POLL_TIMEOUT_SECONDS,
                timeout_is_expected=True,
            )
        except TransportError as exc:
            if "timed out" in str(exc):
                self._logger.debug(
                    "poll_qrcode_status long-poll wait elapsed, treating as wait qrcode=%s",
                    qrcode,
                )
                return {"status": "wait"}
            raise

    async def get_updates(
        self,
        session: AccountSession,
        cursor: PollCursor,
    ) -> GetUpdatesResponse:
        request = GetUpdatesRequest(get_updates_buf=cursor.get_updates_buf or "")
        body = to_wire_dict({**to_wire_dict(request), "base_info": build_base_info()})
        self._logger.debug(
            "get_updates account_id=%s timeout_ms=%s has_buf=%s",
            session.account_id,
            cursor.timeout_ms,
            bool(cursor.get_updates_buf),
        )
        try:
            payload = await self._transport.request(
                method="POST",
                url=_build_url(session.base_url, "ilink/bot/getupdates"),
                headers=build_json_headers(
                    body=self._encode_body(body),
                    token=session.bot_token,
                    route_tag=session.route_tag,
                ),
                json_body=body,
                timeout_seconds=(cursor.timeout_ms / 1000.0) if cursor.timeout_ms else DEFAULT_LONG_POLL_TIMEOUT_SECONDS,
                timeout_is_expected=True,
            )
        except TransportError as exc:
            if "timed out" in str(exc):
                self._logger.debug(
                    "get_updates long-poll wait elapsed, treating as empty response account_id=%s",
                    session.account_id,
                )
                return GetUpdatesResponse(ret=0, msgs=[], get_updates_buf=cursor.get_updates_buf)
            raise
        response = _decode_dataclass(GetUpdatesResponse, payload)
        code = response.errcode if response.errcode not in (None, 0) else response.ret
        self._raise_protocol_error_if_needed(code if code not in (None, 0) else None, response.errmsg)
        self._logger.debug(
            "get_updates ok account_id=%s message_count=%s longpolling_timeout_ms=%s",
            session.account_id,
            len(response.msgs or []),
            response.longpolling_timeout_ms,
        )
        return response

    async def send_message(
        self,
        session: AccountSession,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        body = to_wire_dict({**payload, "base_info": build_base_info()})
        msg = payload.get("msg") if isinstance(payload, dict) else None
        item_list = msg.get("item_list") if isinstance(msg, dict) else None
        self._logger.info(
            "send_message account_id=%s to_user_id=%s item_count=%s has_context_token=%s",
            session.account_id,
            msg.get("to_user_id") if isinstance(msg, dict) else None,
            len(item_list) if isinstance(item_list, list) else 0,
            bool(msg.get("context_token")) if isinstance(msg, dict) else False,
        )
        response = await self._transport.request(
            method="POST",
            url=_build_url(session.base_url, "ilink/bot/sendmessage"),
            headers=build_json_headers(
                body=self._encode_body(body),
                token=session.bot_token,
                route_tag=session.route_tag,
            ),
            json_body=body,
            timeout_seconds=DEFAULT_API_TIMEOUT_SECONDS,
        )
        errcode = response.get("errcode")
        errmsg = response.get("errmsg")
        self._raise_protocol_error_if_needed(errcode, errmsg)
        self._logger.debug("send_message ok account_id=%s", session.account_id)
        return response

    async def get_config(
        self,
        session: AccountSession,
        *,
        user_id: str,
        context_token: str | None = None,
    ) -> GetConfigResponse:
        body = to_wire_dict(
            {
                "ilink_user_id": user_id,
                "context_token": context_token,
                "base_info": build_base_info(),
            }
        )
        self._logger.debug(
            "get_config account_id=%s user_id=%s has_context_token=%s",
            session.account_id,
            user_id,
            bool(context_token),
        )
        payload = await self._transport.request(
            method="POST",
            url=_build_url(session.base_url, "ilink/bot/getconfig"),
            headers=build_json_headers(
                body=self._encode_body(body),
                token=session.bot_token,
                route_tag=session.route_tag,
            ),
            json_body=body,
            timeout_seconds=DEFAULT_CONFIG_TIMEOUT_SECONDS,
        )
        response = _decode_dataclass(GetConfigResponse, payload)
        self._raise_protocol_error_if_needed(response.ret if response.ret and response.ret < 0 else None, response.errmsg)
        self._logger.debug(
            "get_config ok account_id=%s user_id=%s has_typing_ticket=%s",
            session.account_id,
            user_id,
            bool(response.typing_ticket),
        )
        return response

    async def send_typing(
        self,
        session: AccountSession,
        *,
        user_id: str,
        typing_ticket: str,
        status: int,
    ) -> SendTypingResponse:
        body = to_wire_dict(
            {
                "ilink_user_id": user_id,
                "typing_ticket": typing_ticket,
                "status": status,
                "base_info": build_base_info(),
            }
        )
        self._logger.debug(
            "send_typing account_id=%s user_id=%s status=%s",
            session.account_id,
            user_id,
            status,
        )
        payload = await self._transport.request(
            method="POST",
            url=_build_url(session.base_url, "ilink/bot/sendtyping"),
            headers=build_json_headers(
                body=self._encode_body(body),
                token=session.bot_token,
                route_tag=session.route_tag,
            ),
            json_body=body,
            timeout_seconds=DEFAULT_CONFIG_TIMEOUT_SECONDS,
        )
        response = _decode_dataclass(SendTypingResponse, payload)
        self._raise_protocol_error_if_needed(response.ret if response.ret and response.ret < 0 else None, response.errmsg)
        self._logger.debug("send_typing ok account_id=%s user_id=%s", session.account_id, user_id)
        return response

    async def get_upload_url(
        self,
        session: AccountSession,
        payload: dict[str, Any] | GetUploadUrlRequest,
    ) -> GetUploadUrlResponse:
        body = to_wire_dict({**to_wire_dict(payload), "base_info": build_base_info()})
        wire_payload = to_wire_dict(payload)
        self._logger.info(
            "get_upload_url account_id=%s to_user_id=%s media_type=%s rawsize=%s",
            session.account_id,
            wire_payload.get("to_user_id"),
            wire_payload.get("media_type"),
            wire_payload.get("rawsize"),
        )
        raw = await self._transport.request(
            method="POST",
            url=_build_url(session.base_url, "ilink/bot/getuploadurl"),
            headers=build_json_headers(
                body=self._encode_body(body),
                token=session.bot_token,
                route_tag=session.route_tag,
            ),
            json_body=body,
            timeout_seconds=DEFAULT_API_TIMEOUT_SECONDS,
        )
        response = _decode_dataclass(GetUploadUrlResponse, raw)
        errcode = raw.get("errcode")
        errmsg = raw.get("errmsg")
        self._raise_protocol_error_if_needed(errcode, errmsg)
        self._logger.debug(
            "get_upload_url ok account_id=%s has_upload_param=%s",
            session.account_id,
            bool(response.upload_param),
        )
        return response

    async def close(self) -> None:
        self._logger.debug("api client closed")
        await self._transport.close()

    @staticmethod
    def _encode_body(payload: dict[str, Any]) -> str:
        from .headers import encode_json_body

        return encode_json_body(payload)

    @staticmethod
    def _raise_protocol_error_if_needed(errcode: int | None, errmsg: str | None) -> None:
        if errcode is None:
            return
        if errcode == SESSION_EXPIRED_ERRCODE:
            raise SessionExpiredError(errmsg or "session expired", errcode=errcode)
        raise ProtocolError(errmsg or f"protocol error: {errcode}", errcode=errcode)
