from __future__ import annotations

import re
import secrets
from pathlib import Path
from tempfile import gettempdir
from time import time_ns
from typing import Any

from .._logging import SdkLogger, create_sdk_logger
from ..api import AsyncBotApiClient, MessageItemType, MessageState, MessageType, UploadMediaType
from ..errors import MediaError, ValidationError
from ..media.mime import get_mime_from_filename
from ..media.transfer import download_remote_media_to_temp, encode_hex_aes_key_for_message, prepare_upload
from ..models import AccountSession, MediaPayload, OutboundMessage


CLIENT_ID_PREFIX = "openclaw-weixin"


def generate_client_id(prefix: str = CLIENT_ID_PREFIX) -> str:
    return f"{prefix}:{time_ns() // 1_000_000}-{secrets.token_hex(4)}"


def markdown_to_plain_text(text: str) -> str:
    result = text
    result = re.sub(r"```[^\n]*\n?([\s\S]*?)```", lambda match: match.group(1).strip(), result)
    result = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", result)
    result = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", result)
    result = re.sub(r"^\|[\s:|-]+\|$", "", result, flags=re.MULTILINE)
    result = re.sub(
        r"^\|(.+)\|$",
        lambda match: "  ".join(cell.strip() for cell in match.group(1).split("|")),
        result,
        flags=re.MULTILINE,
    )
    result = re.sub(r"^#{1,6}\s*", "", result, flags=re.MULTILINE)
    result = re.sub(r"(\*\*|__)(.*?)\1", r"\2", result)
    result = re.sub(r"(\*|_)(.*?)\1", r"\2", result)
    result = re.sub(r"~~(.*?)~~", r"\1", result)
    result = re.sub(r"`([^`]*)`", r"\1", result)
    result = re.sub(r"^>\s?", "", result, flags=re.MULTILINE)
    return result.strip()


def build_text_message_request(
    *,
    to_user_id: str,
    text: str,
    context_token: str,
    client_id: str,
) -> dict[str, Any]:
    item_list = []
    if text:
        item_list.append(
            {
                "type": int(MessageItemType.TEXT),
                "text_item": {"text": text},
            }
        )
    return {
        "msg": {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": client_id,
            "message_type": int(MessageType.BOT),
            "message_state": int(MessageState.FINISH),
            "item_list": item_list or None,
            "context_token": context_token,
        }
    }


class AsyncMessageServiceImpl:
    def __init__(
        self,
        *,
        api_client: AsyncBotApiClient,
        cdn_base_url: str | None = None,
        logger: SdkLogger | None = None,
    ) -> None:
        self._api_client = api_client
        self._cdn_base_url = cdn_base_url
        self._logger = logger or create_sdk_logger().child("message")

    async def send_text(self, session: AccountSession, message: OutboundMessage) -> None:
        if not message.context_token:
            raise ValidationError("context_token is required for replies")
        plain_text = markdown_to_plain_text(message.text or "")
        self._logger.info(
            "send_text account_id=%s user_id=%s text_len=%s",
            message.account_id,
            message.user_id,
            len(plain_text),
        )
        payload = build_text_message_request(
            to_user_id=message.user_id,
            text=plain_text,
            context_token=message.context_token,
            client_id=generate_client_id(),
        )
        await self._api_client.send_message(session, payload)

    async def send_media(self, session: AccountSession, message: OutboundMessage) -> None:
        if not message.context_token:
            raise ValidationError("context_token is required for replies")
        if not self._cdn_base_url:
            raise MediaError("cdn_base_url is required for media sends")
        if not message.media:
            raise MediaError("at least one media payload is required")

        caption = markdown_to_plain_text(message.text or "")
        self._logger.info(
            "send_media account_id=%s user_id=%s media_count=%s caption_len=%s",
            message.account_id,
            message.user_id,
            len(message.media),
            len(caption),
        )
        for index, media in enumerate(message.media):
            file_path = await self._resolve_media_path(media)
            self._logger.debug(
                "send_media resolved file account_id=%s user_id=%s index=%s path=%s mime=%s",
                message.account_id,
                message.user_id,
                index,
                file_path,
                media.mime_type,
            )
            payload = await self._build_media_request(
                session=session,
                media=media,
                file_path=file_path,
                to_user_id=message.user_id,
                context_token=message.context_token,
            )
            if index == 0 and caption:
                self._logger.debug(
                    "send_media sending caption first account_id=%s user_id=%s",
                    message.account_id,
                    message.user_id,
                )
                await self.send_text(
                    session,
                    OutboundMessage(
                        account_id=message.account_id,
                        user_id=message.user_id,
                        context_token=message.context_token,
                        text=caption,
                    ),
                )
            await self._api_client.send_message(session, payload)
            self._logger.debug(
                "send_media payload sent account_id=%s user_id=%s index=%s",
                message.account_id,
                message.user_id,
                index,
            )

    async def close(self) -> None:
        return None

    async def _resolve_media_path(self, media: MediaPayload) -> Path:
        if media.local_path is not None:
            self._logger.debug("using local media path path=%s", media.local_path)
            return media.local_path
        if media.remote_url:
            self._logger.info("downloading remote media url=%s", media.remote_url)
            return await download_remote_media_to_temp(
                media.remote_url,
                Path(gettempdir()) / "wechat_clawbot_sdk" / "outbound",
                logger=self._logger.child("transfer"),
            )
        raise MediaError("media payload requires local_path or remote_url")

    async def _build_media_request(
        self,
        *,
        session: AccountSession,
        media: MediaPayload,
        file_path: Path,
        to_user_id: str,
        context_token: str,
    ) -> dict[str, Any]:
        mime_type = media.mime_type or get_mime_from_filename(file_path.name)
        self._logger.debug(
            "build_media_request account_id=%s user_id=%s file=%s mime=%s",
            session.account_id,
            to_user_id,
            file_path,
            mime_type,
        )
        if mime_type.startswith("video/"):
            uploaded = await prepare_upload(
                file_path=file_path,
                to_user_id=to_user_id,
                media_type=int(UploadMediaType.VIDEO),
                api_client=self._api_client,
                session=session,
                cdn_base_url=self._cdn_base_url,
                logger=self._logger.child("transfer"),
            )
            self._logger.debug("video upload prepared user_id=%s ciphertext_size=%s", to_user_id, uploaded.file_size_ciphertext)
            item = {
                "type": int(MessageItemType.VIDEO),
                "video_item": {
                    "media": {
                        "encrypt_query_param": uploaded.download_encrypted_query_param,
                        "aes_key": encode_hex_aes_key_for_message(uploaded.aeskey_hex),
                        "encrypt_type": 1,
                    },
                    "video_size": uploaded.file_size_ciphertext,
                },
            }
        elif mime_type.startswith("image/"):
            uploaded = await prepare_upload(
                file_path=file_path,
                to_user_id=to_user_id,
                media_type=int(UploadMediaType.IMAGE),
                api_client=self._api_client,
                session=session,
                cdn_base_url=self._cdn_base_url,
                logger=self._logger.child("transfer"),
            )
            self._logger.debug("image upload prepared user_id=%s ciphertext_size=%s", to_user_id, uploaded.file_size_ciphertext)
            item = {
                "type": int(MessageItemType.IMAGE),
                "image_item": {
                    "media": {
                        "encrypt_query_param": uploaded.download_encrypted_query_param,
                        "aes_key": encode_hex_aes_key_for_message(uploaded.aeskey_hex),
                        "encrypt_type": 1,
                    },
                    "mid_size": uploaded.file_size_ciphertext,
                },
            }
        else:
            uploaded = await prepare_upload(
                file_path=file_path,
                to_user_id=to_user_id,
                media_type=int(UploadMediaType.FILE),
                api_client=self._api_client,
                session=session,
                cdn_base_url=self._cdn_base_url,
                logger=self._logger.child("transfer"),
            )
            self._logger.debug("file upload prepared user_id=%s file_name=%s size=%s", to_user_id, file_path.name, uploaded.file_size)
            item = {
                "type": int(MessageItemType.FILE),
                "file_item": {
                    "media": {
                        "encrypt_query_param": uploaded.download_encrypted_query_param,
                        "aes_key": encode_hex_aes_key_for_message(uploaded.aeskey_hex),
                        "encrypt_type": 1,
                    },
                    "file_name": file_path.name,
                    "len": str(uploaded.file_size),
                },
            }

        return {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": generate_client_id(),
                "message_type": int(MessageType.BOT),
                "message_state": int(MessageState.FINISH),
                "item_list": [item],
                "context_token": context_token,
            }
        }