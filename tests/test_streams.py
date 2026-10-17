from __future__ import annotations

import threading
import urllib.error
from typing import Any

import pytest

from pixelfm.services.streams import (
    StreamError,
    StreamInfo,
    StreamResolver,
    classify_stream_error,
    stream_from_info,
)
from tests.conftest import make_song

INFO: dict[str, Any] = {
    "url": "https://rr1.googlevideo.com/videoplayback?expire=1792251182&itag=140",
    "ext": "m4a",
    "acodec": "mp4a.40.2",
    "abr": 129.6,
    "duration": 149,
    "is_live": False,
    "http_headers": {
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
        "X-Evil": "a\r\nInjected: 1",
        "bad name": "x",
        "Count": 3,
    },
}


def test_stream_from_info() -> None:
    stream = stream_from_info(INFO)
    assert stream.url == INFO["url"]
    assert stream.expires_at == 1792251182
    assert stream.duration == 149 and not stream.is_live
    assert dict(stream.headers) == {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
    assert stream.description == "m4a mp4a.40.2 130k"


def test_live_stream_has_no_duration() -> None:
    stream = stream_from_info({**INFO, "is_live": True})
    assert stream.is_live and stream.duration is None


@pytest.mark.parametrize(
    "info",
    [None, {}, {"url": None}, {"url": "file:///etc/passwd"}, {"url": "concat:a|b"}],
)
def test_stream_from_info_rejects_unplayable(info: object) -> None:
    with pytest.raises(StreamError):
        stream_from_info(info)


def test_expiry() -> None:
    stream = StreamInfo(url="https://x", expires_at=1000.0)
    assert not stream.expired(now=800)
    assert stream.expired(now=900)  # within the 120 s margin
    assert not StreamInfo(url="https://x").expired()


@pytest.mark.parametrize(
    "message,expected,retryable",
    [
        ("ERROR: [youtube] abc: Video unavailable", "unavailable", False),
        # Exact wording from a live yt-dlp run (2026.08.19):
        (
            "ERROR: [youtube] aaaaaaaaaaa: This video is unavailable",
            "This video is unavailable.",
            False,
        ),
        ("ERROR: [youtube] x: This video is private", "unavailable", False),
        ("Sign in to confirm your age", "age-restricted", False),
        ("Join this channel to get access to members-only content", "sign-in", False),
        (
            "The uploader has not made this video available in your country",
            "region",
            False,
        ),
        ("Requested format is not available", "updating yt-dlp", False),
        ("No supported JavaScript runtime could be found", "brew install deno", False),
        ("HTTP Error 429: Too Many Requests", "rate-limiting", True),
        ("Unable to download webpage: <urlopen error timed out>", "Network", True),
        ("something new and strange", "Could not load", True),
    ],
)
def test_classify_stream_error(message: str, expected: str, retryable: bool) -> None:
    error = classify_stream_error(RuntimeError(message))
    assert expected in error.message
    assert error.retryable is retryable


async def test_resolver_caches_until_expiry_or_refresh() -> None:
    calls: list[str] = []

    def extract(url: str) -> Any:
        calls.append(url)
        return {**INFO, "url": "https://x/a?expire=99999999999"}

    resolver = StreamResolver(extract)
    song = make_song(1)
    first = await resolver.resolve(song)
    assert await resolver.resolve(song) is first
    assert calls == [song.source_url]
    await resolver.resolve(song, refresh=True)
    assert len(calls) == 2


async def test_resolver_skips_expired_cache() -> None:
    calls: list[str] = []

    def extract(url: str) -> Any:
        calls.append(url)
        return {**INFO, "url": "https://x/a?expire=1"}  # long expired

    resolver = StreamResolver(extract)
    await resolver.resolve(make_song(1))
    await resolver.resolve(make_song(1))
    assert len(calls) == 2


async def test_resolver_maps_errors() -> None:
    def extract(url: str) -> Any:
        raise urllib.error.URLError("nodename nor servname provided")

    with pytest.raises(StreamError, match="Network error"):
        await StreamResolver(extract).resolve(make_song(1))


async def test_resolver_timeout() -> None:
    release = threading.Event()

    def extract(url: str) -> Any:
        release.wait(5)
        return INFO

    try:
        with pytest.raises(StreamError, match="Timed out"):
            await StreamResolver(extract, timeout=0.05).resolve(make_song(1))
    finally:
        release.set()
