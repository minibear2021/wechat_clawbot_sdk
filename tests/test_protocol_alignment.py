from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from wechat_clawbot_sdk.api.headers import build_client_version, build_common_headers, build_json_headers
from wechat_clawbot_sdk.auth.service import AsyncQrLoginService
from wechat_clawbot_sdk.media.transfer import download_plain_cdn_buffer, upload_buffer_to_cdn
from wechat_clawbot_sdk.version import CHANNEL_VERSION, PROTOCOL_CLIENT_VERSION


class FakeResponse:
    def __init__(self, *, status_code: int = 200, content: bytes = b"", headers: dict[str, str] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")


class FakeAsyncClient:
    last_get_url: str | None = None
    last_post_url: str | None = None
    last_post_content: bytes | None = None
    get_response = FakeResponse(content=b"downloaded")
    post_response = FakeResponse(status_code=200, headers={"x-encrypted-param": "encrypted-param"})

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, headers: dict[str, str] | None = None) -> FakeResponse:
        FakeAsyncClient.last_get_url = url
        return FakeAsyncClient.get_response

    async def post(self, url: str, content: bytes | None = None, headers: dict[str, str] | None = None) -> FakeResponse:
        FakeAsyncClient.last_post_url = url
        FakeAsyncClient.last_post_content = content
        return FakeAsyncClient.post_response


class FakeQrApiClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = list(responses)
        self.poll_base_urls: list[str] = []

    async def fetch_qrcode(self, login_session) -> dict[str, object]:
        return {"qrcode": "qr-code", "qrcode_img_content": "qr-image"}

    async def poll_qrcode_status(self, qrcode: str, *, base_url: str, route_tag: str | None = None, timeout_seconds: float | None = None) -> dict[str, object]:
        self.poll_base_urls.append(base_url)
        return self._responses.pop(0)


class HeadersAlignmentTests(unittest.TestCase):
    def test_common_headers_include_protocol_alignment_fields(self) -> None:
        headers = build_common_headers(route_tag="route-a")
        json_headers = build_json_headers(body="{}", route_tag="route-a")

        self.assertEqual(PROTOCOL_CLIENT_VERSION, "2.1.1")
        self.assertEqual(CHANNEL_VERSION, PROTOCOL_CLIENT_VERSION)
        self.assertEqual(build_client_version("2.1.1"), "131329")
        self.assertEqual(headers["iLink-App-Id"], "bot")
        self.assertEqual(headers["iLink-App-ClientVersion"], "131329")
        self.assertEqual(headers["SKRouteTag"], "route-a")
        self.assertEqual(json_headers["iLink-App-Id"], "bot")
        self.assertEqual(json_headers["iLink-App-ClientVersion"], "131329")


class QrRedirectTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_for_login_follows_redirect_host(self) -> None:
        api_client = FakeQrApiClient(
            responses=[
                {"status": "scaned_but_redirect", "redirect_host": "redirect.example.com"},
                {
                    "status": "confirmed",
                    "ilink_bot_id": "bot-1",
                    "bot_token": "token-1",
                    "baseurl": "https://api.example.com",
                    "ilink_user_id": "user-1",
                },
            ]
        )
        service = AsyncQrLoginService(api_client=api_client, base_url="https://ilinkai.weixin.qq.com")
        started = await service.start_login()

        async def no_sleep(_: float) -> None:
            return None

        with patch("wechat_clawbot_sdk.auth.service.sleep", new=no_sleep):
            session = await service.wait_for_login(started.qrcode, timeout_seconds=2.0)

        self.assertEqual(api_client.poll_base_urls, ["https://ilinkai.weixin.qq.com", "https://redirect.example.com"])
        self.assertEqual(session.account_id, "bot-1")
        self.assertEqual(session.base_url, "https://api.example.com")
        self.assertEqual(session.user_id, "user-1")


class CdnFullUrlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        FakeAsyncClient.last_get_url = None
        FakeAsyncClient.last_post_url = None
        FakeAsyncClient.last_post_content = None

    async def test_download_uses_full_url_when_available(self) -> None:
        with patch("wechat_clawbot_sdk.media.transfer.httpx.AsyncClient", new=FakeAsyncClient):
            data = await download_plain_cdn_buffer(
                None,
                "https://fallback.example.com/c2c",
                full_url="https://cdn.example.com/download?id=123",
            )

        self.assertEqual(data, b"downloaded")
        self.assertEqual(FakeAsyncClient.last_get_url, "https://cdn.example.com/download?id=123")

    async def test_upload_prefers_upload_full_url(self) -> None:
        with patch("wechat_clawbot_sdk.media.transfer.httpx.AsyncClient", new=FakeAsyncClient):
            result = await upload_buffer_to_cdn(
                plaintext=b"hello world",
                upload_full_url="https://cdn.example.com/upload?id=456",
                upload_param="legacy-upload-param",
                filekey="file-key",
                cdn_base_url="https://fallback.example.com/c2c",
                aes_key=b"0123456789abcdef",
            )

        self.assertEqual(result, "encrypted-param")
        self.assertEqual(FakeAsyncClient.last_post_url, "https://cdn.example.com/upload?id=456")
        self.assertIsNotNone(FakeAsyncClient.last_post_content)


if __name__ == "__main__":
    unittest.main()