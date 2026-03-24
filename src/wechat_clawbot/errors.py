class WeChatBotSdkError(Exception):
    """Base class for SDK errors."""


class TransportError(WeChatBotSdkError):
    """Raised when the HTTP transport fails before a protocol response is received."""


class ProtocolError(WeChatBotSdkError):
    """Raised when the server returns a protocol-level error."""

    def __init__(self, message: str, *, errcode: int | None = None) -> None:
        super().__init__(message)
        self.errcode = errcode


class SessionExpiredError(ProtocolError):
    """Raised when the server indicates the bot session has expired."""


class ValidationError(WeChatBotSdkError):
    """Raised when SDK inputs are incomplete or invalid."""


class MediaError(WeChatBotSdkError):
    """Raised when media preparation, upload, or download fails."""
