from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable


@runtime_checkable
class LoggerLike(Protocol):
    def debug(self, msg: str, *args: object, **kwargs: object) -> None: ...

    def info(self, msg: str, *args: object, **kwargs: object) -> None: ...

    def warning(self, msg: str, *args: object, **kwargs: object) -> None: ...

    def error(self, msg: str, *args: object, **kwargs: object) -> None: ...

    def exception(self, msg: str, *args: object, **kwargs: object) -> None: ...


class SdkLogger:
    def __init__(
        self,
        logger: logging.Logger | LoggerLike | None = None,
        *,
        debug_enabled: bool = False,
        component: str = "sdk",
    ) -> None:
        self._logger = logger
        self._debug_enabled = debug_enabled
        self._component = component

    def child(self, component: str) -> SdkLogger:
        child_component = f"{self._component}.{component}" if self._component else component
        return SdkLogger(self._logger, debug_enabled=self._debug_enabled, component=child_component)

    def debug(self, message: str, *args: object, **kwargs: object) -> None:
        if self._logger is None or not self._debug_enabled:
            return
        self._logger.debug(self._format(message), *args, **kwargs)

    def info(self, message: str, *args: object, **kwargs: object) -> None:
        if self._logger is None:
            return
        self._logger.info(self._format(message), *args, **kwargs)

    def warning(self, message: str, *args: object, **kwargs: object) -> None:
        if self._logger is None:
            return
        self._logger.warning(self._format(message), *args, **kwargs)

    def error(self, message: str, *args: object, **kwargs: object) -> None:
        if self._logger is None:
            return
        self._logger.error(self._format(message), *args, **kwargs)

    def exception(self, message: str, *args: object, **kwargs: object) -> None:
        if self._logger is None:
            return
        self._logger.exception(self._format(message), *args, **kwargs)

    def _format(self, message: str) -> str:
        return f"[wechat_clawbot_sdk:{self._component}] {message}"


def create_sdk_logger(
    logger: logging.Logger | LoggerLike | None = None,
    *,
    debug: bool = False,
) -> SdkLogger:
    return SdkLogger(logger, debug_enabled=debug)