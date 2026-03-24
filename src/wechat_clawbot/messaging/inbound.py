from __future__ import annotations

from typing import Any

from ..api.protocol import MessageItemType
from ..models import InboundMessage, MediaPayload


def normalize_inbound_message(account_id: str, raw_message: dict[str, Any]) -> InboundMessage:
    message_id = raw_message.get("message_id")
    user_id = _string_or_empty(raw_message.get("from_user_id"))
    context_token = _optional_string(raw_message.get("context_token"))
    item_list = raw_message.get("item_list")
    items = item_list if isinstance(item_list, list) else []
    text = body_from_item_list(items)
    return InboundMessage(
        account_id=account_id,
        user_id=user_id,
        message_id=str(message_id) if message_id is not None else None,
        context_token=context_token,
        to_user_id=_optional_string(raw_message.get("to_user_id")),
        session_id=_optional_string(raw_message.get("session_id")),
        client_id=_optional_string(raw_message.get("client_id")),
        timestamp_ms=_optional_int(raw_message.get("create_time_ms")),
        text=text or None,
        command_body=text or None,
        raw_message=raw_message,
        media=extract_media_payloads(items),
    )


def is_media_item(item: dict[str, Any]) -> bool:
    item_type = item.get("type")
    return item_type in {
        int(MessageItemType.IMAGE),
        int(MessageItemType.VIDEO),
        int(MessageItemType.FILE),
        int(MessageItemType.VOICE),
    }


def body_from_item_list(item_list: list[dict[str, Any]]) -> str:
    if not item_list:
        return ""
    for item in item_list:
        item_type = item.get("type")
        if item_type == int(MessageItemType.TEXT):
            text = _extract_text_item(item)
            if text is None:
                continue
            ref = item.get("ref_msg")
            if not isinstance(ref, dict):
                return text
            ref_message_item = ref.get("message_item")
            if isinstance(ref_message_item, dict) and is_media_item(ref_message_item):
                return text
            parts: list[str] = []
            ref_title = ref.get("title")
            if isinstance(ref_title, str) and ref_title:
                parts.append(ref_title)
            if isinstance(ref_message_item, dict):
                ref_body = body_from_item_list([ref_message_item])
                if ref_body:
                    parts.append(ref_body)
            if not parts:
                return text
            return f"[引用: {' | '.join(parts)}]\n{text}"
        if item_type == int(MessageItemType.VOICE):
            voice_item = item.get("voice_item")
            if isinstance(voice_item, dict):
                voice_text = voice_item.get("text")
                if isinstance(voice_text, str) and voice_text:
                    return voice_text
    return ""


def extract_media_payloads(item_list: list[dict[str, Any]]) -> list[MediaPayload]:
    payloads: list[MediaPayload] = []
    for item in item_list:
        item_type = item.get("type")
        if item_type == int(MessageItemType.IMAGE):
            image_item = item.get("image_item")
            if isinstance(image_item, dict):
                payloads.append(
                    MediaPayload(
                        filename="image",
                        mime_type="image/*",
                        metadata={"kind": "image", **image_item},
                    )
                )
        elif item_type == int(MessageItemType.VIDEO):
            video_item = item.get("video_item")
            if isinstance(video_item, dict):
                payloads.append(
                    MediaPayload(
                        filename="video.mp4",
                        mime_type="video/mp4",
                        metadata={"kind": "video", **video_item},
                    )
                )
        elif item_type == int(MessageItemType.FILE):
            file_item = item.get("file_item")
            if isinstance(file_item, dict):
                filename = file_item.get("file_name")
                payloads.append(
                    MediaPayload(
                        filename=filename if isinstance(filename, str) and filename else "file",
                        mime_type="application/octet-stream",
                        metadata={"kind": "file", **file_item},
                    )
                )
        elif item_type == int(MessageItemType.VOICE):
            voice_item = item.get("voice_item")
            if isinstance(voice_item, dict):
                payloads.append(
                    MediaPayload(
                        filename="voice",
                        mime_type=_voice_mime_type(voice_item),
                        metadata={"kind": "voice", **voice_item},
                    )
                )
    return payloads


def _extract_text_item(item: dict[str, Any]) -> str | None:
    text_item = item.get("text_item")
    if not isinstance(text_item, dict):
        return None
    text = text_item.get("text")
    if isinstance(text, str):
        return text
    return None


def _voice_mime_type(voice_item: dict[str, Any]) -> str:
    encode_type = voice_item.get("encode_type")
    if encode_type == 6:
        return "audio/silk"
    if encode_type == 7:
        return "audio/mpeg"
    if encode_type == 8:
        return "audio/ogg"
    return "audio/wav"


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None