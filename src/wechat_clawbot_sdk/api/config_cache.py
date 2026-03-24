from __future__ import annotations

from dataclasses import dataclass
from random import random
from typing import Protocol

from .._logging import SdkLogger, create_sdk_logger
from ..models import AccountSession
from .interfaces import AsyncBotApiClient


CONFIG_CACHE_TTL_MS = 24 * 60 * 60 * 1000
CONFIG_CACHE_INITIAL_RETRY_MS = 2_000
CONFIG_CACHE_MAX_RETRY_MS = 60 * 60 * 1000


@dataclass(slots=True)
class CachedConfig:
    typing_ticket: str = ""


@dataclass(slots=True)
class ConfigCacheEntry:
    config: CachedConfig
    ever_succeeded: bool
    next_fetch_at_ms: int
    retry_delay_ms: int


class AsyncConfigProvider(Protocol):
    async def get_for_user(
        self,
        session: AccountSession,
        user_id: str,
        context_token: str | None = None,
    ) -> CachedConfig: ...

    async def close(self) -> None: ...


class WeChatBotConfigCache:
    def __init__(self, *, api_client: AsyncBotApiClient, logger: SdkLogger | None = None) -> None:
        self._api_client = api_client
        self._cache: dict[tuple[str, str], ConfigCacheEntry] = {}
        self._logger = logger or create_sdk_logger().child("config")

    async def get_for_user(
        self,
        session: AccountSession,
        user_id: str,
        context_token: str | None = None,
    ) -> CachedConfig:
        now = self._now_ms()
        cache_key = (session.account_id, user_id)
        entry = self._cache.get(cache_key)
        should_fetch = entry is None or now >= entry.next_fetch_at_ms
        if not should_fetch:
            self._logger.debug("config cache hit account_id=%s user_id=%s", session.account_id, user_id)

        if should_fetch:
            fetch_ok = False
            try:
                self._logger.debug("fetching config account_id=%s user_id=%s", session.account_id, user_id)
                response = await self._api_client.get_config(
                    session,
                    user_id=user_id,
                    context_token=context_token,
                )
                if response.ret == 0:
                    self._cache[cache_key] = ConfigCacheEntry(
                        config=CachedConfig(typing_ticket=response.typing_ticket or ""),
                        ever_succeeded=True,
                        next_fetch_at_ms=now + int(random() * CONFIG_CACHE_TTL_MS),
                        retry_delay_ms=CONFIG_CACHE_INITIAL_RETRY_MS,
                    )
                    fetch_ok = True
                    self._logger.debug("config fetch ok account_id=%s user_id=%s", session.account_id, user_id)
            except Exception as exc:
                self._logger.warning(
                    "config fetch failed account_id=%s user_id=%s err=%s",
                    session.account_id,
                    user_id,
                    exc,
                )
                fetch_ok = False

            if not fetch_ok:
                prev_delay = entry.retry_delay_ms if entry else CONFIG_CACHE_INITIAL_RETRY_MS
                next_delay = min(prev_delay * 2, CONFIG_CACHE_MAX_RETRY_MS)
                if entry is not None:
                    entry.next_fetch_at_ms = now + next_delay
                    entry.retry_delay_ms = next_delay
                else:
                    self._cache[cache_key] = ConfigCacheEntry(
                        config=CachedConfig(),
                        ever_succeeded=False,
                        next_fetch_at_ms=now + CONFIG_CACHE_INITIAL_RETRY_MS,
                        retry_delay_ms=CONFIG_CACHE_INITIAL_RETRY_MS,
                    )

        cached = self._cache.get(cache_key)
        return cached.config if cached is not None else CachedConfig()

    async def close(self) -> None:
        self._cache.clear()

    @staticmethod
    def _now_ms() -> int:
        from time import time

        return int(time() * 1000)