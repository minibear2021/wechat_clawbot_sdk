from __future__ import annotations

from typing import Any

import httpx

from .._logging import SdkLogger, create_sdk_logger
from ..errors import TransportError
from .interfaces import AsyncHttpTransport


class HttpxAsyncTransport(AsyncHttpTransport):
    def __init__(self, *, client: httpx.AsyncClient | None = None, logger: SdkLogger | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()
        self._logger = logger or create_sdk_logger().child("transport")

    async def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
        timeout_is_expected: bool = False,
    ) -> dict[str, Any]:
        content = None
        if json_body is not None:
            import json

            content = json.dumps(json_body, ensure_ascii=False, separators=(",", ":"))
        try:
            self._logger.debug("request %s %s timeout=%s", method, url, timeout_seconds)
            response = await self._client.request(
                method=method,
                url=url,
                headers=headers,
                content=content,
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            self._logger.debug("response %s %s status=%s", method, url, response.status_code)
            if not response.text:
                return {}
            return response.json()
        except httpx.TimeoutException as exc:
            if timeout_is_expected:
                self._logger.debug("long-poll wait elapsed %s %s", method, url)
            else:
                self._logger.warning("request timed out %s %s", method, url)
            raise TransportError(f"request timed out: {method} {url}") from exc
        except httpx.HTTPStatusError as exc:
            body = exc.response.text
            self._logger.warning(
                "http status error %s %s status=%s",
                method,
                url,
                exc.response.status_code,
            )
            raise TransportError(
                f"http error: {method} {url} -> {exc.response.status_code} {body}"
            ) from exc
        except httpx.HTTPError as exc:
            self._logger.warning("transport error %s %s err=%s", method, url, exc)
            raise TransportError(f"transport error: {method} {url}: {exc}") from exc

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
