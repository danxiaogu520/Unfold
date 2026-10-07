"""Wikimedia Commons metadata plus bounded, host-restricted raster download."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import httpx

from unfold.app.files import MAX_IMAGE_BYTES
from unfold.provider import GenerationError

API = "https://commons.wikimedia.org/w/api.php"
LICENSES = {
    "CC0",
    "Public domain",
    "CC BY 2.0",
    "CC BY 2.5",
    "CC BY 3.0",
    "CC BY 4.0",
    "CC BY-SA 2.0",
    "CC BY-SA 2.5",
    "CC BY-SA 3.0",
    "CC BY-SA 4.0",
}


def plain(text: str) -> str:
    class Parser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []

        def handle_data(self, data: str) -> None:
            self.parts.append(data)

    parser = Parser()
    parser.feed(text)
    return "".join(parser.parts).strip()[:6000]


def image_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return bool(
            parsed.scheme == "https"
            and parsed.hostname in {"upload.wikimedia.org", "thumb.wikimedia.org"}
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
            and parsed.path.startswith("/wikipedia/commons/")
        )
    except ValueError:
        return False


class CommonsImages:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(
            timeout=20,
            follow_redirects=False,
            headers={"User-Agent": "Unfold/0.1 (local educational prototype)"},
        )
        self.owns_client = client is None

    def close(self) -> None:
        if self.owns_client:
            self.client.close()

    def search(self, query: str) -> list[dict[str, Any]]:
        if not 1 <= len(query.strip()) <= 200:
            raise GenerationError("invalid_input", "image search query must have 1..200 characters")
        params: dict[str, str | int] = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrnamespace": 6,
            "gsrlimit": 5,
            "gsrsearch": query + " filetype:bitmap",
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": 960,
        }
        try:
            with self.client.stream("GET", API, params=params) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 2_000_000:
                        raise GenerationError(
                            "budget_exceeded", "image metadata exceeds response limit"
                        )
            import json

            payload = json.loads(data)
        except (httpx.HTTPError, ValueError) as exc:
            raise GenerationError("provider_error", "image search unavailable") from exc
        candidates = []
        for page in sorted(
            payload.get("query", {}).get("pages", {}).values(),
            key=lambda item: (item.get("index", 999), item.get("title", "")),
        ):
            infos = page.get("imageinfo", [])
            if not infos:
                continue
            info = infos[0]
            metadata = info.get("extmetadata", {})

            def field(key: str, source: dict[str, Any] = metadata) -> str:
                return plain(str(source.get(key, {}).get("value", "")))

            license_name, artist = field("LicenseShortName"), field("Artist")
            download = info.get("thumburl", info.get("url", ""))
            description = info.get("descriptionurl", "")
            source = urlsplit(description)
            if (
                license_name not in LICENSES
                or not image_url(download)
                or source.scheme != "https"
                or source.hostname != "commons.wikimedia.org"
                or (field("AttributionRequired") == "true" and not artist)
                or field("Restrictions")
            ):
                continue
            license_url = field("LicenseUrl")
            if license_url.startswith("http://creativecommons.org/"):
                license_url = "https://" + license_url.removeprefix("http://")
            candidates.append(
                {
                    "provider": "wikimedia_commons",
                    "title": plain(page["title"]),
                    "source_url": description,
                    "download_url": download,
                    "artist": artist or None,
                    "license": license_name,
                    "license_url": license_url or None,
                    "usage_terms": field("UsageTerms"),
                    "credit": field("Credit"),
                    "description": field("ImageDescription"),
                    "search_query": query,
                    "modification": "provider thumbnail; bytes stored unchanged",
                    "teaching_suitability": "unverified",
                }
            )
        return candidates

    def download(self, candidate: dict[str, Any]) -> bytes:
        url = candidate["download_url"]
        if not isinstance(url, str) or not image_url(url):
            raise GenerationError("capability_limit", "image download host is not supported")
        try:
            with self.client.stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0]
                if content_type not in {"image/png", "image/jpeg", "image/webp"}:
                    raise GenerationError(
                        "capability_limit", "source did not return a supported raster"
                    )
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_IMAGE_BYTES:
                        raise GenerationError(
                            "budget_exceeded", "image download exceeds file limit"
                        )
                return bytes(data)
        except httpx.HTTPError as exc:
            raise GenerationError("provider_error", "image download unavailable") from exc
