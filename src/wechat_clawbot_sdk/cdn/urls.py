from __future__ import annotations

from urllib.parse import quote


def build_cdn_download_url(encrypted_query_param: str, cdn_base_url: str) -> str:
    return f"{cdn_base_url.rstrip('/')}/download?encrypted_query_param={quote(encrypted_query_param)}"


def build_cdn_upload_url(*, cdn_base_url: str, upload_param: str, filekey: str) -> str:
    return (
        f"{cdn_base_url.rstrip('/')}/upload?encrypted_query_param={quote(upload_param)}"
        f"&filekey={quote(filekey)}"
    )
