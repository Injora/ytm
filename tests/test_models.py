from __future__ import annotations

import dataclasses

import pytest

from pixelfm.models import Song, SongValidationError, format_duration

VALID = dict(
    id="dQw4w9WgXcQ",
    title="Title",
    artist="Artist",
    duration=213,
    source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
)


def test_valid_song() -> None:
    song = Song(**VALID, thumbnail_url="https://i.ytimg.com/vi/x/hq.jpg")
    assert song.duration_label == "3:33"
    assert song.thumbnail_url is not None


def test_song_is_immutable_and_hashable() -> None:
    song = Song(**VALID)
    with pytest.raises(dataclasses.FrozenInstanceError):
        song.title = "other"  # type: ignore[misc]
    assert song in {song}
    assert Song(**VALID) == song


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", ""),
        ("id", "has space"),
        ("id", "x" * 65),
        ("title", "   "),
        ("artist", ""),
        ("duration", -1),
        ("duration", 1.5),
        ("duration", True),
        ("source_url", "ftp://example.com/a"),
        ("source_url", "not a url"),
        ("thumbnail_url", "file:///etc/passwd"),
        ("thumbnail_url", "javascript:alert(1)"),
    ],
)
def test_invalid_song(field: str, value: object) -> None:
    with pytest.raises(SongValidationError):
        Song(**{**VALID, field: value})  # type: ignore[arg-type]


def test_unknown_duration_label() -> None:
    assert Song(**{**VALID, "duration": 0}).duration_label == "--:--"


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0:00"),
        (59, "0:59"),
        (61, "1:01"),
        (3600, "1:00:00"),
        (3725, "1:02:05"),
        (-5, "0:00"),
    ],
)
def test_format_duration(seconds: int, expected: str) -> None:
    assert format_duration(seconds) == expected
