"""Pixel-art renderer and artwork service (fetcher is always faked)."""

from __future__ import annotations

import threading

import pytest
from PIL import Image
from rich.color import ColorType

from pixelfm.services.artwork import (
    GLYPH_RAMP,
    MONO_RAMP,
    QUADRANTS,
    ArtworkError,
    ArtworkMode,
    ArtworkService,
    ColorDepth,
    decode_image,
    fetch_image,
    placeholder_image,
    render_bytes,
    render_image,
    trim_letterbox,
)
from tests.conftest import image_bytes, make_song


def spans_colors(art, attr: str = "color"):  # type: ignore[no-untyped-def]
    for line in art.lines:
        for span in line.spans:
            color = getattr(span.style, attr)
            if color is not None:
                yield color


# ------------------------------------------------------------------ renderer


@pytest.mark.parametrize("mode", list(ArtworkMode))
@pytest.mark.parametrize("unicode", [True, False])
@pytest.mark.parametrize("size", [(16, 8), (5, 3), (1, 1), (40, 12)])
def test_render_dimensions(
    mode: ArtworkMode, unicode: bool, size: tuple[int, int]
) -> None:
    width, height = size
    art = render_bytes(image_bytes(), width, height, mode, unicode=unicode)
    assert (art.width, art.height, art.mode) == (width, height, mode)
    assert len(art.lines) == height
    assert all(line.cell_len == width for line in art.lines)
    assert art.to_text().plain.count("\n") == height - 1


def test_render_rejects_zero_size() -> None:
    with pytest.raises(ValueError):
        render_bytes(image_bytes(), 0, 4)


def test_color_mode_uses_quadrant_blocks_and_truecolor() -> None:
    art = render_bytes(image_bytes(), 8, 4, ArtworkMode.COLOR)
    assert set(art.to_text().plain.replace("\n", "")) <= {" ", *QUADRANTS.values()}
    colors = list(spans_colors(art)) + list(spans_colors(art, "bgcolor"))
    assert colors and all(c.type is ColorType.TRUECOLOR for c in colors)
    # The left half is the red-ish test colour, the right half white.
    first = art.lines[0].spans[0].style.color.triplet
    assert (first.red, first.green, first.blue) == (200, 40, 90)


def test_reduced_color_fallback_uses_256_palette() -> None:
    art = render_bytes(image_bytes(), 8, 4, ArtworkMode.COLOR, depth=ColorDepth.REDUCED)
    colors = list(spans_colors(art)) + list(spans_colors(art, "bgcolor"))
    assert colors and all(c.type is ColorType.EIGHT_BIT for c in colors)
    assert "38;5;" in art.to_ansi("256")


def test_color_without_unicode_uses_background_spaces() -> None:
    art = render_bytes(image_bytes(), 8, 4, ArtworkMode.COLOR, unicode=False)
    assert set(art.to_text().plain.replace("\n", "")) == {" "}
    assert list(spans_colors(art, "bgcolor"))
    assert art.to_text().plain.isascii()


def test_mono_mode_is_uncoloured_ascii() -> None:
    art = render_bytes(image_bytes(), 12, 6, ArtworkMode.MONO)
    plain = art.to_text().plain.replace("\n", "")
    assert plain.isascii()
    assert set(plain) <= set(MONO_RAMP)
    assert not any(span.style for line in art.lines for span in line.spans)


def test_glyph_mode_uses_command_glyphs_by_brightness() -> None:
    # Dark left half, white right half: darkest and brightest glyphs.
    # (Not pure black, which letterbox trimming would crop away.)
    image = Image.new("RGB", (64, 32), (40, 40, 40))
    image.paste((255, 255, 255), (32, 0, 64, 32))
    art = render_image(image, 10, 4, ArtworkMode.GLYPH, unicode=False)
    for line in art.to_text().plain.split("\n"):
        assert line[0] == GLYPH_RAMP[0]
        assert line[-1] == GLYPH_RAMP[-1] == "$"
    assert set(art.to_text().plain.replace("\n", "")) <= set(GLYPH_RAMP)
    # Tinted: bright glyphs are brighter green than dark ones.
    colors = list(spans_colors(art))
    greens = [c.triplet.green for c in colors]
    assert max(greens) > min(greens)


def test_glyph_ramp_contains_requested_characters() -> None:
    assert set("$>#01:/") <= set(GLYPH_RAMP)


def test_trim_letterbox_removes_black_bars() -> None:
    image = Image.new("RGB", (100, 100), (0, 0, 0))
    image.paste((255, 0, 0), (0, 20, 100, 80))
    assert trim_letterbox(image).size == (100, 60)


def test_trim_letterbox_keeps_dark_artwork() -> None:
    image = Image.new("RGB", (100, 100), (0, 0, 0))
    image.paste((255, 255, 255), (45, 45, 55, 55))
    assert trim_letterbox(image).size == (100, 100)


def test_decode_handles_transparency() -> None:
    image = Image.new("RGBA", (10, 10), (255, 0, 0, 0))
    import io

    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    assert decode_image(buffer.getvalue()).getpixel((0, 0)) == (0, 0, 0)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not an image",
        image_bytes()[:40],
        b"%PDF-1.4 fake",
        b"#!/bin/sh\nrm -rf /\n",
    ],
)
def test_corrupt_or_non_image_bytes(data: bytes) -> None:
    with pytest.raises(ArtworkError):
        decode_image(data)


def test_disallowed_format_rejected() -> None:
    with pytest.raises(ArtworkError):
        decode_image(image_bytes(fmt="BMP"))


def test_placeholder_renders_in_all_modes() -> None:
    for mode in ArtworkMode:
        art = render_image(placeholder_image(), 16, 8, mode, placeholder=True)
        assert art.placeholder and len(art.lines) == 8


def test_mode_cycles() -> None:
    assert ArtworkMode.COLOR.next() is ArtworkMode.MONO
    assert ArtworkMode.MONO.next() is ArtworkMode.GLYPH
    assert ArtworkMode.GLYPH.next() is ArtworkMode.COLOR


def test_fetch_rejects_non_http_urls() -> None:
    with pytest.raises(ArtworkError):
        fetch_image("file:///etc/passwd")


# ------------------------------------------------------------------- service


class FakeFetcher:
    def __init__(self, data: bytes | Exception) -> None:
        self.data = data
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


async def test_service_renders_and_caches() -> None:
    fetcher = FakeFetcher(image_bytes())
    service = ArtworkService(fetcher)
    song = make_song(1)

    result = await service.artwork_for(song, 16, 8, ArtworkMode.COLOR)
    assert result.error is None and not result.art.placeholder
    again = await service.artwork_for(song, 16, 8, ArtworkMode.COLOR)
    assert again.art is result.art  # rendered cache hit
    assert fetcher.calls == [song.thumbnail_url]

    # A different mode re-renders from cached bytes without re-downloading.
    glyph = await service.artwork_for(song, 16, 8, ArtworkMode.GLYPH)
    assert glyph.art.mode is ArtworkMode.GLYPH
    assert len(fetcher.calls) == 1
    assert len(service.images) == 1 and len(service.rendered) == 2


async def test_service_placeholder_for_no_song_or_thumbnail() -> None:
    service = ArtworkService(FakeFetcher(b""))
    idle = await service.artwork_for(None, 16, 8)
    missing = await service.artwork_for(make_song(1, thumbnail=False), 16, 8)
    assert idle.art.placeholder and idle.error is None
    assert missing.art is idle.art  # one consistent, cached placeholder
    assert missing.error == "no thumbnail"


async def test_service_network_failure_not_cached() -> None:
    fetcher = FakeFetcher(ArtworkError("network error while fetching thumbnail"))
    service = ArtworkService(fetcher)
    song = make_song(1)
    result = await service.artwork_for(song, 16, 8)
    assert result.art.placeholder and "network" in (result.error or "")
    await service.artwork_for(song, 16, 8)
    assert len(fetcher.calls) == 2  # retried, not cached


async def test_service_unexpected_exception_becomes_placeholder() -> None:
    service = ArtworkService(FakeFetcher(ConnectionResetError("reset")))
    result = await service.artwork_for(make_song(1), 16, 8)
    assert result.art.placeholder and result.error


async def test_service_corrupt_image_cached_as_placeholder() -> None:
    fetcher = FakeFetcher(b"garbage bytes")
    service = ArtworkService(fetcher)
    song = make_song(1)
    first = await service.artwork_for(song, 16, 8)
    second = await service.artwork_for(song, 16, 8)
    assert first.art.placeholder and first.error == "unreadable image"
    assert second.art is first.art and second.error == "unreadable image"
    assert len(fetcher.calls) == 1


async def test_service_timeout() -> None:
    release = threading.Event()

    def slow(url: str) -> bytes:
        release.wait(5)
        return image_bytes()

    service = ArtworkService(slow, timeout=0.05)
    try:
        result = await service.artwork_for(make_song(1), 16, 8)
    finally:
        release.set()
    assert result.art.placeholder and result.error == "thumbnail download timed out"
