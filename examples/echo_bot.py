from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from wechat_clawbot_sdk import (
    AsyncWeChatBotClient,
    DEFAULT_CDN_BASE_URL,
    DEFAULT_LOGIN_BASE_URL,
    DEFAULT_STATE_DIR_ENV_VAR,
    PollEvent,
    PollEventType,
    download_inbound_media_item,
    resolve_default_state_dir,
)
from wechat_clawbot_sdk.api import TypingStatus


LOGGER_NAME = "wechat_clawbot_sdk.echo_bot"
DEFAULT_LOG_LEVEL = "INFO"


def setup_logging() -> logging.Logger:
    level_name = read_env("WECHAT_CLAWBOT_SDK_LOG_LEVEL", default=DEFAULT_LOG_LEVEL) or DEFAULT_LOG_LEVEL
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.debug("logging configured level=%s", logging.getLevelName(level))
    return logger


def read_env(name: str, *, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    return default


def resolve_state_dir() -> Path:
    configured = read_env(DEFAULT_STATE_DIR_ENV_VAR)
    if configured:
        return Path(configured).expanduser()
    return resolve_default_state_dir()


def list_available_account_ids(state_dir: Path) -> list[str]:
    accounts_dir = state_dir / "accounts"
    if not accounts_dir.exists():
        return []
    account_ids: list[str] = []
    for path in sorted(accounts_dir.glob("*.json")):
        if path.name.endswith(".sync.json") or path.name.endswith(".context-tokens.json"):
            continue
        account_ids.append(path.stem)
    return account_ids


def choose_account_id(account_ids: list[str]) -> str:
    if len(account_ids) == 1:
        selected = account_ids[0]
        logging.getLogger(LOGGER_NAME).info("found one persisted account, loading %s", selected)
        return selected

    logger = logging.getLogger(LOGGER_NAME)
    logger.info("available persisted accounts:")
    for index, account_id in enumerate(account_ids, start=1):
        logger.info("%s. %s", index, account_id)

    while True:
        raw = input(f"Select an account to load [1-{len(account_ids)}]: ").strip()
        if raw.isdigit():
            selected_index = int(raw)
            if 1 <= selected_index <= len(account_ids):
                return account_ids[selected_index - 1]
        logger.warning("invalid account selection, please try again")


def describe_media_kind(mime_type: str, metadata: dict[str, object]) -> str:
    kind = metadata.get("kind")
    if isinstance(kind, str) and kind:
        return kind
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "voice"
    return "file"


def build_echo_reply(event: PollEvent) -> str | None:
    message = event.message
    if message is None:
        return None

    parts: list[str] = []
    if message.text:
        parts.append(f"text={message.text}")

    if message.media:
        media_descriptions: list[str] = []
        for media in message.media:
            media_kind = describe_media_kind(media.mime_type, media.metadata)
            media_descriptions.append(f"{media_kind}:{media.filename}")
        parts.append(f"media=[{', '.join(media_descriptions)}]")

    if not parts:
        return None
    return f"echo: {'; '.join(parts)}"


def extract_media_items(event: PollEvent) -> list[dict[str, object]]:
    message = event.message
    if message is None:
        return []
    item_list = message.raw_message.get("item_list")
    if not isinstance(item_list, list):
        return []
    media_items: list[dict[str, object]] = []
    for item in item_list:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type in {2, 3, 4, 5}:
            media_items.append(item)
    return media_items


async def send_echo_media(
    client: AsyncWeChatBotClient,
    event: PollEvent,
    *,
    cdn_base_url: str,
) -> int:
    logger = logging.getLogger(LOGGER_NAME)
    sent_count = 0
    for item in extract_media_items(event):
        downloaded = await download_inbound_media_item(
            item,
            cdn_base_url=cdn_base_url,
            logger=logging.getLogger("wechat_clawbot_sdk"),
        )
        if downloaded is None:
            logger.warning(
                "skip unsupported media item account_id=%s user_id=%s item_type=%s",
                event.account_id,
                event.message.user_id if event.message else None,
                item.get("type"),
            )
            continue

        local_path = downloaded.local_path
        try:
            if downloaded.mime_type.startswith("image/"):
                await client.send_image(
                    account_id=event.account_id,
                    user_id=event.message.user_id,
                    local_path=local_path,
                    filename=local_path.name,
                    mime_type=downloaded.mime_type,
                )
            elif downloaded.mime_type.startswith("video/"):
                await client.send_video(
                    account_id=event.account_id,
                    user_id=event.message.user_id,
                    local_path=local_path,
                    filename=local_path.name,
                    mime_type=downloaded.mime_type,
                )
            else:
                await client.send_file(
                    account_id=event.account_id,
                    user_id=event.message.user_id,
                    local_path=local_path,
                    filename=local_path.name,
                    mime_type=downloaded.mime_type,
                )
            sent_count += 1
            logger.info(
                "echo media sent account_id=%s user_id=%s file=%s mime=%s",
                event.account_id,
                event.message.user_id,
                local_path.name,
                downloaded.mime_type,
            )
        finally:
            local_path.unlink(missing_ok=True)
    return sent_count


async def resolve_account_session(client: AsyncWeChatBotClient):
    logger = logging.getLogger(LOGGER_NAME)
    account_id = read_env("WECHAT_CLAWBOT_SDK_ACCOUNT_ID")
    if account_id:
        status = await client.get_account_status(account_id)
        if status.logged_in and status.session is not None:
            if await client.is_account_session_alive(account_id):
                logger.info("reusing persisted session for %s", account_id)
                return status.session
            logger.warning("persisted session for %s has expired, starting QR login", account_id)
        else:
            logger.info("no persisted session found for %s, starting QR login", account_id)

    available_account_ids = list_available_account_ids(resolve_state_dir())
    if available_account_ids:
        selected_account_id = choose_account_id(available_account_ids)
        status = await client.get_account_status(selected_account_id)
        if status.logged_in and status.session is not None:
            if await client.is_account_session_alive(selected_account_id):
                logger.info("reusing persisted session for %s", selected_account_id)
                return status.session
            logger.warning("persisted session for %s has expired, starting QR login", selected_account_id)
        else:
            logger.warning("persisted account %s is not available, starting QR login", selected_account_id)

    logger.info("starting QR login flow")
    qrcode_session = await client.start_login()
    logger.info("scan this QR code payload in your own renderer:")
    print(qrcode_session.qrcode_image_content)
    account = await client.wait_for_login(qrcode_session.qrcode)
    logger.info("logged in as %s", account.account_id)
    logger.info(
        "set WECHAT_CLAWBOT_SDK_ACCOUNT_ID=%s to reuse this persisted session on the next run",
        account.account_id,
    )
    return account


async def handle_event(client: AsyncWeChatBotClient, event: PollEvent, *, cdn_base_url: str) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    if event.event_type is not PollEventType.MESSAGE or event.message is None:
        return
    reply_text = build_echo_reply(event)
    has_media_items = bool(extract_media_items(event))
    if reply_text is None and not has_media_items:
        logger.info("ignoring unsupported message account_id=%s user_id=%s", event.account_id, event.message.user_id)
        return
    logger.info(
        "received message account_id=%s user_id=%s text=%r media_count=%s",
        event.account_id,
        event.message.user_id,
        event.message.text,
        len(event.message.media),
    )
    await client.send_typing(
        account_id=event.account_id,
        user_id=event.message.user_id,
        status=int(TypingStatus.TYPING),
    )
    try:
        logger.info("typing started, waiting 10 seconds before reply")
        await asyncio.sleep(10)
        if reply_text is not None:
            await client.send_text(
                account_id=event.account_id,
                user_id=event.message.user_id,
                text=reply_text,
            )
            logger.info("echo text sent account_id=%s user_id=%s", event.account_id, event.message.user_id)
        if has_media_items:
            sent_count = await send_echo_media(client, event, cdn_base_url=cdn_base_url)
            logger.info(
                "echo media completed account_id=%s user_id=%s sent_count=%s",
                event.account_id,
                event.message.user_id,
                sent_count,
            )
    finally:
        await client.send_typing(
            account_id=event.account_id,
            user_id=event.message.user_id,
            status=int(TypingStatus.CANCEL),
        )
        logger.info("typing cancelled account_id=%s user_id=%s", event.account_id, event.message.user_id)


async def main() -> None:
    logger = setup_logging()
    debug_enabled = (read_env("WECHAT_CLAWBOT_SDK_DEBUG", default="0") or "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    cdn_base_url = read_env("WECHAT_CLAWBOT_SDK_CDN_BASE_URL", default=DEFAULT_CDN_BASE_URL) or DEFAULT_CDN_BASE_URL
    logger.info("starting echo bot")
    client = AsyncWeChatBotClient.create(
        login_base_url=read_env("WECHAT_CLAWBOT_SDK_LOGIN_BASE_URL", default=DEFAULT_LOGIN_BASE_URL)
        or DEFAULT_LOGIN_BASE_URL,
        cdn_base_url=cdn_base_url,
        state_dir=read_env(DEFAULT_STATE_DIR_ENV_VAR),
        logger=logging.getLogger("wechat_clawbot_sdk"),
        debug=debug_enabled,
    )
    account = await resolve_account_session(client)
    logger.info("begin consuming events for account_id=%s", account.account_id)

    try:
        await client.consume_events(
            account.account_id,
            lambda event: handle_event(client, event, cdn_base_url=cdn_base_url),
            message_only=True,
        )
    finally:
        logger.info("closing echo bot")
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())