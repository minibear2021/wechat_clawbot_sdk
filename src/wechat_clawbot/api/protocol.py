from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class UploadMediaType(IntEnum):
    IMAGE = 1
    VIDEO = 2
    FILE = 3
    VOICE = 4


class MessageType(IntEnum):
    NONE = 0
    USER = 1
    BOT = 2


class MessageItemType(IntEnum):
    NONE = 0
    TEXT = 1
    IMAGE = 2
    VOICE = 3
    FILE = 4
    VIDEO = 5


class MessageState(IntEnum):
    NEW = 0
    GENERATING = 1
    FINISH = 2


class TypingStatus(IntEnum):
    TYPING = 1
    CANCEL = 2


@dataclass(slots=True)
class BaseInfo:
    channel_version: str | None = None


@dataclass(slots=True)
class GetUploadUrlRequest:
    filekey: str | None = None
    media_type: int | None = None
    to_user_id: str | None = None
    rawsize: int | None = None
    rawfilemd5: str | None = None
    filesize: int | None = None
    thumb_rawsize: int | None = None
    thumb_rawfilemd5: str | None = None
    thumb_filesize: int | None = None
    no_need_thumb: bool | None = None
    aeskey: str | None = None


@dataclass(slots=True)
class GetUploadUrlResponse:
    upload_param: str | None = None
    thumb_upload_param: str | None = None


@dataclass(slots=True)
class TextItem:
    text: str | None = None


@dataclass(slots=True)
class CdnMedia:
    encrypt_query_param: str | None = None
    aes_key: str | None = None
    encrypt_type: int | None = None


@dataclass(slots=True)
class ImageItem:
    media: CdnMedia | None = None
    thumb_media: CdnMedia | None = None
    aeskey: str | None = None
    url: str | None = None
    mid_size: int | None = None
    thumb_size: int | None = None
    thumb_height: int | None = None
    thumb_width: int | None = None
    hd_size: int | None = None


@dataclass(slots=True)
class VoiceItem:
    media: CdnMedia | None = None
    encode_type: int | None = None
    bits_per_sample: int | None = None
    sample_rate: int | None = None
    playtime: int | None = None
    text: str | None = None


@dataclass(slots=True)
class FileItem:
    media: CdnMedia | None = None
    file_name: str | None = None
    md5: str | None = None
    len: str | None = None


@dataclass(slots=True)
class VideoItem:
    media: CdnMedia | None = None
    video_size: int | None = None
    play_length: int | None = None
    video_md5: str | None = None
    thumb_media: CdnMedia | None = None
    thumb_size: int | None = None
    thumb_height: int | None = None
    thumb_width: int | None = None


@dataclass(slots=True)
class RefMessage:
    message_item: MessageItem | None = None
    title: str | None = None


@dataclass(slots=True)
class MessageItem:
    type: int | None = None
    create_time_ms: int | None = None
    update_time_ms: int | None = None
    is_completed: bool | None = None
    msg_id: str | None = None
    ref_msg: RefMessage | None = None
    text_item: TextItem | None = None
    image_item: ImageItem | None = None
    voice_item: VoiceItem | None = None
    file_item: FileItem | None = None
    video_item: VideoItem | None = None


@dataclass(slots=True)
class WeixinMessage:
    seq: int | None = None
    message_id: int | None = None
    from_user_id: str | None = None
    to_user_id: str | None = None
    client_id: str | None = None
    create_time_ms: int | None = None
    update_time_ms: int | None = None
    delete_time_ms: int | None = None
    session_id: str | None = None
    group_id: str | None = None
    message_type: int | None = None
    message_state: int | None = None
    item_list: list[MessageItem] = field(default_factory=list)
    context_token: str | None = None


@dataclass(slots=True)
class GetUpdatesRequest:
    sync_buf: str | None = None
    get_updates_buf: str = ""


@dataclass(slots=True)
class GetUpdatesResponse:
    ret: int | None = None
    errcode: int | None = None
    errmsg: str | None = None
    msgs: list[WeixinMessage] = field(default_factory=list)
    sync_buf: str | None = None
    get_updates_buf: str | None = None
    longpolling_timeout_ms: int | None = None


@dataclass(slots=True)
class SendMessageRequest:
    msg: WeixinMessage | None = None


@dataclass(slots=True)
class SendTypingRequest:
    ilink_user_id: str | None = None
    typing_ticket: str | None = None
    status: int | None = None


@dataclass(slots=True)
class SendTypingResponse:
    ret: int | None = None
    errmsg: str | None = None


@dataclass(slots=True)
class GetConfigResponse:
    ret: int | None = None
    errmsg: str | None = None
    typing_ticket: str | None = None
