"""YouTube provider: parsing, missing metadata, failures, and timeouts.

The real yt-dlp extractor is never called; every test injects a fake.
"""

from __future__ import annotations

import threading
import time
import urllib.error
from typing import Any

import pytest

from pixelfm.services.search import SearchError
from pixelfm.services.youtube import (
    YouTubeSearchProvider,
    friendly_error,
    parse_entry,
    parse_search_results,
    pick_thumbnail,
)


def test_parse_fixture(ytsearch_info: dict[str, Any]) -> None:
    songs = parse_search_results(ytsearch_info)
    assert [s.id for s in songs] == [
        "K0HSD_i2DvA",
        "a5uQMwRMHcs",
        "LIVEstream1",
        "NoMetadata1",
        "BadNumbers1",
    ]
    first = songs[0]
    assert first.title.startswith("Daft Punk - Around The World")
    assert first.artist == "Daft Punk"
    assert first.duration == 242
    assert first.source_url == "https://www.youtube.com/watch?v=K0HSD_i2DvA"
    # Smallest thumbnail at least 160px wide.
    assert (
        first.thumbnail_url == "https://i.ytimg.com/vi/K0HSD_i2DvA/hq720.jpg?sqp=small"
    )


def test_missing_metadata_fallbacks(ytsearch_info: dict[str, Any]) -> None:
    songs = {s.id: s for s in parse_search_results(ytsearch_info)}

    da_funk = songs["a5uQMwRMHcs"]
    assert da_funk.title == "Da Funk (Official Video)"  # whitespace collapsed
    assert da_funk.artist == "DaftPunkVEVO"  # channel null -> uploader
    assert da_funk.duration == 332  # float rounded
    assert da_funk.thumbnail_url == "https://i.ytimg.com/vi/a5uQMwRMHcs/hqdefault.jpg"

    live = songs["LIVEstream1"]
    assert live.duration == 0 and live.duration_label == "--:--"
    assert live.thumbnail_url is not None  # only a small one; still used

    bare = songs["NoMetadata1"]
    assert (bare.title, bare.artist, bare.duration, bare.thumbnail_url) == (
        "Untitled",
        "Unknown artist",
        0,
        None,
    )

    weird = songs["BadNumbers1"]
    assert weird.duration == 0  # "4:20" string ignored
    assert weird.artist == "Unknown artist"  # whitespace-only channel ignored
    assert weird.thumbnail_url is None  # javascript: URL rejected


@pytest.mark.parametrize(
    "info", [None, [], "x", {}, {"entries": None}, {"entries": "nope"}]
)
def test_parse_garbage_returns_empty(info: object) -> None:
    assert parse_search_results(info) == []


def test_parse_entry_skips_non_videos() -> None:
    assert parse_entry({"_type": "playlist", "id": "abcdefghijk"}) is None
    assert parse_entry({"ie_key": "YoutubeTab", "id": "abcdefghijk"}) is None
    assert parse_entry({"id": "short"}) is None


def test_pick_thumbnail_prefers_largest_when_all_small() -> None:
    entry = {
        "thumbnails": [
            {"url": "https://x/1.jpg", "width": 90},
            {"url": "https://x/2.jpg", "width": 120},
            {"url": "https://x/3.jpg"},
        ]
    }
    assert pick_thumbnail(entry) == "https://x/2.jpg"


def fake_extractor(info: Any) -> Any:
    calls: list[tuple[str, int]] = []

    def extract(query: str, limit: int) -> Any:
        calls.append((query, limit))
        return info

    extract.calls = calls  # type: ignore[attr-defined]
    return extract


async def test_provider_maps_results(ytsearch_info: dict[str, Any]) -> None:
    extract = fake_extractor(ytsearch_info)
    provider = YouTubeSearchProvider(extractor=extract)
    songs = await provider.search("  daft   punk ", limit=3)
    assert len(songs) == 3
    assert extract.calls == [("daft punk", 3)]


async def test_provider_empty_results() -> None:
    provider = YouTubeSearchProvider(extractor=fake_extractor({"entries": []}))
    assert await provider.search("zzzz") == []


async def test_provider_rejects_blank_query() -> None:
    provider = YouTubeSearchProvider(extractor=fake_extractor({}))
    with pytest.raises(SearchError, match="Type something"):
        await provider.search("   ")


async def test_provider_wraps_network_failure() -> None:
    def extract(query: str, limit: int) -> Any:
        raise urllib.error.URLError("[Errno 8] nodename nor servname provided")

    provider = YouTubeSearchProvider(extractor=extract)
    with pytest.raises(SearchError, match="Network error"):
        await provider.search("x")


async def test_provider_wraps_ytdlp_error() -> None:
    class DownloadError(Exception):
        pass

    def extract(query: str, limit: int) -> Any:
        raise DownloadError(
            "ERROR: \x1b[0;31m[youtube:search] something odd\x1b[0m\nmore"
        )

    provider = YouTubeSearchProvider(extractor=extract)
    with pytest.raises(SearchError) as info:
        await provider.search("x")
    message = str(info.value)
    assert message == "YouTube search failed: [youtube:search] something odd"
    assert "\x1b" not in message and "\n" not in message


async def test_provider_timeout_returns_promptly() -> None:
    release = threading.Event()

    def extract(query: str, limit: int) -> Any:
        release.wait(5)  # simulates a hung network call
        return {"entries": []}

    provider = YouTubeSearchProvider(timeout=0.1, extractor=extract)
    started = time.monotonic()
    try:
        with pytest.raises(SearchError, match="timed out"):
            await provider.search("x")
    finally:
        release.set()
    assert time.monotonic() - started < 2


@pytest.mark.parametrize(
    "message,expected",
    [
        ("HTTP Error 429: Too Many Requests", "rate-limiting"),
        ("Sign in to confirm you're not a bot", "sign-in"),
        ("Unable to download API page: <urlopen error timed out>", "Network error"),
        ("", "YouTube search failed: RuntimeError"),
    ],
)
def test_friendly_error(message: str, expected: str) -> None:
    assert expected in friendly_error(RuntimeError(message))
