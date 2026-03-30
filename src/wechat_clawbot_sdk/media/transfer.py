from __future__ import annotations

import base64
import io
import secrets
from dataclasses import dataclass
from hashlib import md5
from pathlib import Path
from tempfile import gettempdir
from time import time_ns
from typing import Any

import httpx

from .._logging import SdkLogger, create_sdk_logger
from ..cdn import aes_ecb_padded_size, build_cdn_download_url, build_cdn_upload_url, decrypt_aes_ecb, encrypt_aes_ecb
from ..errors import MediaError
from .mime import get_extension_from_content_type_or_url, get_mime_from_filename
from .silk_transcode import silk_to_wav


WEIXIN_MEDIA_MAX_BYTES = 100 * 1024 * 1024
UPLOAD_MAX_RETRIES = 3
MEDIA_HTTP_USER_AGENT = "node"
ENABLE_CDN_URL_FALLBACK = True


@dataclass(slots=True)
class UploadedFileInfo:
    filekey: str
    download_encrypted_query_param: str
    aeskey_hex: str
    file_size: int
    file_size_ciphertext: int


@dataclass(slots=True)
class DownloadedMedia:
    local_path: Path
    mime_type: str


def parse_aes_key(aes_key_base64: str) -> bytes:
    decoded = base64.b64decode(aes_key_base64)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        as_ascii = decoded.decode("ascii", errors="ignore")
        if len(as_ascii) == 32 and all(char in "0123456789abcdefABCDEF" for char in as_ascii):
            return bytes.fromhex(as_ascii)
    raise MediaError("invalid aes_key encoding")


async def download_remote_media_to_temp(
    url: str,
    dest_dir: str | Path | None = None,
    *,
    logger: SdkLogger | None = None,
) -> Path:
    resolved_logger = logger or create_sdk_logger().child("transfer")
    resolved_logger.info("download remote media url=%s", url)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": MEDIA_HTTP_USER_AGENT})
        response.raise_for_status()
        content = response.content
        if len(content) > WEIXIN_MEDIA_MAX_BYTES:
            raise MediaError("remote media exceeds supported size limit")
        ext = get_extension_from_content_type_or_url(response.headers.get("content-type"), url)
        target_dir = Path(dest_dir) if dest_dir is not None else Path(gettempdir()) / "wechat_clawbot_sdk"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"weixin-remote-{md5(url.encode('utf-8')).hexdigest()[:12]}{ext}"
        target.write_bytes(content)
        resolved_logger.debug("downloaded remote media target=%s size=%s", target, len(content))
        return target


async def download_plain_cdn_buffer(
    encrypted_query_param: str | None,
    cdn_base_url: str,
    *,
    full_url: str | None = None,
    logger: SdkLogger | None = None,
) -> bytes:
    resolved_logger = logger or create_sdk_logger().child("transfer")
    if full_url:
        url = full_url
    elif encrypted_query_param and ENABLE_CDN_URL_FALLBACK:
        url = build_cdn_download_url(encrypted_query_param, cdn_base_url)
    else:
        raise MediaError("CDN download URL missing (need full_url or encrypt_query_param)")
    resolved_logger.debug("download cdn buffer url=%s", url)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": MEDIA_HTTP_USER_AGENT})
        response.raise_for_status()
        resolved_logger.debug("downloaded cdn buffer size=%s", len(response.content))
        return response.content


async def download_and_decrypt_buffer(
    encrypted_query_param: str | None,
    aes_key_base64: str,
    cdn_base_url: str,
    *,
    full_url: str | None = None,
    logger: SdkLogger | None = None,
) -> bytes:
    resolved_logger = logger or create_sdk_logger().child("transfer")
    encrypted = await download_plain_cdn_buffer(
        encrypted_query_param,
        cdn_base_url,
        full_url=full_url,
        logger=resolved_logger,
    )
    resolved_logger.debug("decrypt cdn buffer size=%s", len(encrypted))
    return decrypt_aes_ecb(encrypted, parse_aes_key(aes_key_base64))


async def download_inbound_media_item(
    item: dict[str, Any],
    *,
    cdn_base_url: str,
    dest_dir: str | Path | None = None,
    logger: SdkLogger | None = None,
) -> DownloadedMedia | None:
    resolved_logger = logger or create_sdk_logger().child("transfer")
    item_type = item.get("type")
    target_dir = Path(dest_dir) if dest_dir is not None else Path(gettempdir()) / "wechat_clawbot_sdk" / "inbound"
    target_dir.mkdir(parents=True, exist_ok=True)
    resolved_logger.debug("download inbound media item_type=%s target_dir=%s", item_type, target_dir)

    if item_type == 2:
        image_item = item.get("image_item") or {}
        media = image_item.get("media") or {}
        encrypted_query_param = media.get("encrypt_query_param")
        full_url = media.get("full_url")
        if not isinstance(encrypted_query_param, str):
            encrypted_query_param = None
        if not isinstance(full_url, str):
            full_url = None
        if encrypted_query_param is None and full_url is None:
            return None
        aes_key_base64 = media.get("aes_key")
        if isinstance(image_item.get("aeskey"), str):
            aes_key_base64 = base64.b64encode(bytes.fromhex(image_item["aeskey"])).decode("ascii")
        buffer = (
            await download_and_decrypt_buffer(
                encrypted_query_param,
                aes_key_base64,
                cdn_base_url,
                full_url=full_url,
                logger=resolved_logger,
            )
            if isinstance(aes_key_base64, str)
            else await download_plain_cdn_buffer(
                encrypted_query_param,
                cdn_base_url,
                full_url=full_url,
                logger=resolved_logger,
            )
        )
        target = target_dir / "image.bin"
        target.write_bytes(buffer)
        return DownloadedMedia(local_path=target, mime_type="image/*")

    if item_type == 3:
        voice_item = item.get("voice_item") or {}
        media = voice_item.get("media") or {}
        encrypted_query_param = media.get("encrypt_query_param")
        full_url = media.get("full_url")
        aes_key_base64 = media.get("aes_key")
        if not isinstance(encrypted_query_param, str):
            encrypted_query_param = None
        if not isinstance(full_url, str):
            full_url = None
        if (encrypted_query_param is None and full_url is None) or not isinstance(aes_key_base64, str):
            return None
        buffer = await download_and_decrypt_buffer(
            encrypted_query_param,
            aes_key_base64,
            cdn_base_url,
            full_url=full_url,
            logger=resolved_logger,
        )
        resolved_logger.debug("downloaded voice buffer size=%s, attempting silk transcode", len(buffer))
        wav_buffer = silk_to_wav(buffer, logger=resolved_logger)
        if wav_buffer is not None:
            target = target_dir / "voice.wav"
            target.write_bytes(wav_buffer)
            return DownloadedMedia(local_path=target, mime_type="audio/wav")
        target = target_dir / "voice.silk"
        target.write_bytes(buffer)
        return DownloadedMedia(local_path=target, mime_type="audio/silk")

    if item_type == 4:
        file_item = item.get("file_item") or {}
        media = file_item.get("media") or {}
        encrypted_query_param = media.get("encrypt_query_param")
        full_url = media.get("full_url")
        aes_key_base64 = media.get("aes_key")
        if not isinstance(encrypted_query_param, str):
            encrypted_query_param = None
        if not isinstance(full_url, str):
            full_url = None
        if (encrypted_query_param is None and full_url is None) or not isinstance(aes_key_base64, str):
            return None
        buffer = await download_and_decrypt_buffer(
            encrypted_query_param,
            aes_key_base64,
            cdn_base_url,
            full_url=full_url,
            logger=resolved_logger,
        )
        filename = file_item.get("file_name") if isinstance(file_item.get("file_name"), str) else "file.bin"
        target = target_dir / filename
        target.write_bytes(buffer)
        return DownloadedMedia(local_path=target, mime_type=get_mime_from_filename(filename))

    if item_type == 5:
        video_item = item.get("video_item") or {}
        media = video_item.get("media") or {}
        encrypted_query_param = media.get("encrypt_query_param")
        full_url = media.get("full_url")
        aes_key_base64 = media.get("aes_key")
        if not isinstance(encrypted_query_param, str):
            encrypted_query_param = None
        if not isinstance(full_url, str):
            full_url = None
        if (encrypted_query_param is None and full_url is None) or not isinstance(aes_key_base64, str):
            return None
        buffer = await download_and_decrypt_buffer(
            encrypted_query_param,
            aes_key_base64,
            cdn_base_url,
            full_url=full_url,
            logger=resolved_logger,
        )
        target = target_dir / "video.mp4"
        target.write_bytes(buffer)
        return DownloadedMedia(local_path=target, mime_type="video/mp4")

    return None


async def upload_buffer_to_cdn(
    *,
    plaintext: bytes,
    upload_full_url: str | None = None,
    upload_param: str | None = None,
    filekey: str,
    cdn_base_url: str,
    aes_key: bytes,
    logger: SdkLogger | None = None,
) -> str:
    resolved_logger = logger or create_sdk_logger().child("transfer")
    ciphertext = encrypt_aes_ecb(plaintext, aes_key)
    if upload_full_url:
        url = upload_full_url
    elif upload_param:
        url = build_cdn_upload_url(cdn_base_url=cdn_base_url, upload_param=upload_param, filekey=filekey)
    else:
        raise MediaError("CDN upload URL missing (need upload_full_url or upload_param)")
    resolved_logger.info(
        "upload buffer to cdn filekey=%s plaintext_size=%s ciphertext_size=%s",
        filekey,
        len(plaintext),
        len(ciphertext),
    )
    async with httpx.AsyncClient(follow_redirects=True) as client:
        last_error: Exception | None = None
        for attempt in range(1, UPLOAD_MAX_RETRIES + 1):
            try:
                resolved_logger.debug("cdn upload attempt=%s url=%s", attempt, url)
                response = await client.post(
                    url,
                    content=ciphertext,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "User-Agent": MEDIA_HTTP_USER_AGENT,
                    },
                )
                if 400 <= response.status_code < 500:
                    raise MediaError(f"CDN upload client error {response.status_code}: {response.text}")
                if response.status_code != 200:
                    raise MediaError(f"CDN upload server error {response.status_code}: {response.text}")
                encrypted_param = response.headers.get("x-encrypted-param")
                if not encrypted_param:
                    raise MediaError("CDN upload response missing x-encrypted-param header")
                resolved_logger.debug("cdn upload succeeded filekey=%s attempt=%s", filekey, attempt)
                return encrypted_param
            except Exception as exc:
                last_error = exc if isinstance(exc, Exception) else MediaError(str(exc))
                resolved_logger.warning("cdn upload failed filekey=%s attempt=%s err=%s", filekey, attempt, last_error)
                if isinstance(last_error, MediaError) and "client error" in str(last_error):
                    raise last_error
                if attempt == UPLOAD_MAX_RETRIES:
                    break
        raise last_error or MediaError("CDN upload failed")


async def prepare_upload(
    *,
    file_path: str | Path,
    to_user_id: str,
    media_type: int,
    api_client: Any,
    session: Any,
    cdn_base_url: str,
    logger: SdkLogger | None = None,
) -> UploadedFileInfo:
    resolved_logger = logger or create_sdk_logger().child("transfer")
    path = Path(file_path)
    plaintext = path.read_bytes()
    rawsize = len(plaintext)
    rawfilemd5 = md5(plaintext).hexdigest()
    filesize = aes_ecb_padded_size(rawsize)
    filekey = secrets.token_hex(16)
    aes_key = secrets.token_bytes(16)
    resolved_logger.info(
        "prepare upload file=%s to_user_id=%s media_type=%s rawsize=%s",
        path,
        to_user_id,
        media_type,
        rawsize,
    )

    upload_url_response = await api_client.get_upload_url(
        session,
        payload={
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": rawsize,
            "rawfilemd5": rawfilemd5,
            "filesize": filesize,
            "no_need_thumb": True,
            "aeskey": aes_key.hex(),
        },
    )
    upload_full_url = getattr(upload_url_response, "upload_full_url", None)
    upload_param = getattr(upload_url_response, "upload_param", None)
    if not isinstance(upload_full_url, str) or not upload_full_url:
        upload_full_url = None
    if not isinstance(upload_param, str) or not upload_param:
        upload_param = None
    if upload_full_url is None and upload_param is None:
        raise MediaError("getUploadUrl returned no upload URL")
    resolved_logger.debug("upload url prepared filekey=%s", filekey)

    download_encrypted_query_param = await upload_buffer_to_cdn(
        plaintext=plaintext,
        upload_full_url=upload_full_url,
        upload_param=upload_param,
        filekey=filekey,
        cdn_base_url=cdn_base_url,
        aes_key=aes_key,
        logger=resolved_logger,
    )
    resolved_logger.info("prepare upload complete filekey=%s rawsize=%s", filekey, rawsize)
    return UploadedFileInfo(
        filekey=filekey,
        download_encrypted_query_param=download_encrypted_query_param,
        aeskey_hex=aes_key.hex(),
        file_size=rawsize,
        file_size_ciphertext=filesize,
    )


def encode_hex_aes_key_for_message(aeskey_hex: str) -> str:
    return base64.b64encode(aeskey_hex.encode("ascii")).decode("ascii")