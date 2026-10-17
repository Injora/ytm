"""Shared fixtures. No test in this suite touches the network."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from pixelfm.models import Song

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch the real library, caches, or logs from tests."""
    monkeypatch.setenv("PIXELFM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PIXELFM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("PIXELFM_LOG_FILE", str(tmp_path / "pixelfm.log"))
    monkeypatch.setenv("PIXELFM_REDUCED_MOTION", "0")  # don't read macOS settings
    monkeypatch.delenv("NO_COLOR", raising=False)


@pytest.fixture
def ytsearch_info() -> dict[str, Any]:
    """A yt-dlp flat search result, including malformed entries."""
    return json.loads((FIXTURES / "ytsearch_flat.json").read_text())


def make_song(n: int = 1, *, duration: int = 180, thumbnail: bool = True) -> Song:
    song_id = f"song{n:07d}"
    return Song(
        id=song_id,
        title=f"Track {n}",
        artist=f"Artist {n}",
        duration=duration,
        source_url=f"https://example.com/watch?v={song_id}",
        thumbnail_url=f"https://example.com/thumb/{song_id}.png" if thumbnail else None,
    )


def image_bytes(
    size: tuple[int, int] = (64, 48),
    color: tuple[int, int, int] = (200, 40, 90),
    fmt: str = "PNG",
) -> bytes:
    """Encode a two-tone test image (left half ``color``, right half white)."""
    image = Image.new("RGB", size, color)
    image.paste((255, 255, 255), (size[0] // 2, 0, size[0], size[1]))
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()
