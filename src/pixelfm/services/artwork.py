"""Thumbnail download and terminal pixel-art rendering.

Downloaded bytes are only ever decoded by Pillow (with a restricted set of
image formats and size limits); they are never written to disk or executed.
"""

from __future__ import annotations

import asyncio
import io
import ssl
import urllib.error
import urllib.request
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError
from rich.color import Color, ColorSystem
from rich.console import Console
from rich.style import Style
from rich.text import Text

from pixelfm import __version__
from pixelfm.models import Song
from pixelfm.services.cache import DiskCache, LRUCache
from pixelfm.services.threads import run_in_thread

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
ALLOWED_FORMATS = ("JPEG", "PNG", "WEBP", "GIF")

#: Sparse-to-dense ramps, indexed by brightness.
MONO_RAMP = " .:-=+*#%@"
GLYPH_RAMP = " .:/1>0#$"
#: Default tint for glyph art and the placeholder label (the theme's cyan).
PHOSPHOR = (0, 229, 255)


class ArtworkMode(StrEnum):
    """How artwork is drawn."""

    COLOR = "color"
    MONO = "mono"
    GLYPH = "glyph"

    def next(self) -> ArtworkMode:
        """The following mode, wrapping around."""
        modes = list(ArtworkMode)
        return modes[(modes.index(self) + 1) % len(modes)]


class ColorDepth(StrEnum):
    """Colour precision used for COLOR and GLYPH output."""

    TRUECOLOR = "truecolor"
    REDUCED = "256"


class ArtworkError(Exception):
    """A thumbnail could not be fetched or decoded."""


@dataclass(frozen=True, eq=False, slots=True)
class TerminalArt:
    """Rendered artwork: ``height`` lines of exactly ``width`` cells.

    Instances are immutable and safe to cache and share between widgets.
    """

    width: int
    height: int
    mode: ArtworkMode
    lines: tuple[Text, ...]
    placeholder: bool = False

    def to_text(self) -> Text:
        """A fresh multi-line :class:`Text` (callers may mutate it)."""
        return Text("\n").join(self.lines)

    def to_ansi(self, color_system: str = "truecolor") -> str:
        """Render to an ANSI escape string, e.g. for printing outside Textual."""
        buffer = io.StringIO()
        console = Console(
            file=buffer,
            color_system=color_system,  # type: ignore[arg-type]
            force_terminal=True,
            width=self.width,
            legacy_windows=False,
        )
        console.print(self.to_text(), end="", soft_wrap=True)
        return buffer.getvalue()


# --------------------------------------------------------------------- images


def decode_image(data: bytes) -> Image.Image:
    """Decode untrusted bytes into an RGB image, or raise :class:`ArtworkError`."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ArtworkError("image too large")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(data), formats=ALLOWED_FORMATS)
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ArtworkError("image dimensions too large")
            image.draft("RGB", (256, 256))  # fast JPEG downscale on decode
            image.load()
    except ArtworkError:
        raise
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        ValueError,
        SyntaxError,  # raised by some Pillow plugins for malformed headers
    ) as exc:
        raise ArtworkError("unreadable image") from exc

    image = ImageOps.exif_transpose(image) or image
    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        image = Image.alpha_composite(background, rgba)
    return image.convert("RGB")


def trim_letterbox(image: Image.Image, threshold: int = 24) -> Image.Image:
    """Crop near-black bars (common on 4:3 YouTube thumbnails)."""
    mask = image.convert("L").point(lambda v: 255 if v > threshold else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return image
    left, top, right, bottom = bbox
    if (right - left) * (bottom - top) < 0.25 * image.width * image.height:
        return image  # mostly dark artwork, not letterboxing
    return image.crop(bbox)


def fit_cells(image: Image.Image, width: int, height: int) -> Image.Image:
    """Centre-crop to the aspect of, and scale to, ``width`` x ``height`` pixels."""
    return ImageOps.fit(image, (width, height), Image.Resampling.LANCZOS)


def placeholder_image(
    size: int = 64, tint: tuple[int, int, int] = PHOSPHOR
) -> Image.Image:
    """A vinyl record, used whenever real artwork is unavailable."""
    image = Image.new("RGB", (size, size), (8, 12, 10))
    draw = ImageDraw.Draw(image)
    margin = size // 16
    draw.ellipse((margin, margin, size - margin, size - margin), fill=(22, 32, 26))
    for ring in range(3):
        inset = margin + size // 10 + ring * size // 12
        draw.ellipse(
            (inset, inset, size - inset, size - inset), outline=(40, 60, 48), width=1
        )
    label = size * 5 // 16
    draw.ellipse((label, label, size - label, size - label), fill=tint)
    hole = size * 15 // 32
    draw.ellipse((hole, hole, size - hole, size - hole), fill=(8, 12, 10))
    return image


# ------------------------------------------------------------------ rendering


RGB = tuple[int, int, int]

#: Quadrant block for a 2x2 pixel pattern (bit 0 top-left, 1 top-right,
#: 2 bottom-left, 3 bottom-right) drawn in the foreground colour over the
#: background colour. A pattern and its inverse look the same with the
#: colours swapped, so only patterns with the bottom-right bit set are needed.
QUADRANTS = {8: "▗", 9: "▚", 10: "▐", 11: "▜", 12: "▄", 13: "▙", 14: "▟", 15: "█"}
_PATTERNS = tuple(
    (
        char,
        tuple(i for i in range(4) if mask >> i & 1),
        tuple(i for i in range(4) if not mask >> i & 1),
    )
    for mask, char in QUADRANTS.items()
)


def _mean(pixels: list[RGB]) -> RGB:
    n = len(pixels)
    return (
        sum(p[0] for p in pixels) // n,
        sum(p[1] for p in pixels) // n,
        sum(p[2] for p in pixels) // n,
    )


def _error(pixels: list[RGB], colour: RGB) -> int:
    return sum(
        (p[0] - colour[0]) ** 2 + (p[1] - colour[1]) ** 2 + (p[2] - colour[2]) ** 2
        for p in pixels
    )


def quadrant_cell(pixels: list[RGB]) -> tuple[str, RGB, RGB]:
    """Best two-colour quadrant block for one cell's 2x2 pixels (in reading
    order). Returns (character, foreground, background).

    A single-colour cell is a space on that background: background colour
    fills the whole cell, so flat areas never show font seams or line gaps.
    """
    if pixels.count(pixels[0]) == 4:
        return " ", pixels[0], pixels[0]
    best: tuple[int, str, RGB, RGB] | None = None
    for char, on_index, off_index in _PATTERNS:
        on = [pixels[i] for i in on_index]
        off = [pixels[i] for i in off_index]
        fg = _mean(on)
        bg = _mean(off) if off else fg
        error = _error(on, fg) + (_error(off, bg) if off else 0)
        if best is None or error < best[0]:
            best = (error, char, fg, bg)
            if error == 0:
                break
    assert best is not None
    return best[1], best[2], best[3]


@lru_cache(maxsize=4096)
def _color(rgb: tuple[int, int, int], depth: ColorDepth) -> Color:
    color = Color.from_rgb(*rgb)
    if depth is ColorDepth.REDUCED:
        return color.downgrade(ColorSystem.EIGHT_BIT)
    return color


def _line(cells: list[tuple[str, Style | None]]) -> Text:
    """Build a line, merging runs of identical style into one span."""
    text = Text(no_wrap=True, overflow="crop")
    run_chars: list[str] = []
    run_style: Style | None = None
    for char, style in cells:
        if run_chars and style != run_style:
            text.append("".join(run_chars), run_style)
            run_chars = []
        run_chars.append(char)
        run_style = style
    if run_chars:
        text.append("".join(run_chars), run_style)
    return text


def render_image(
    image: Image.Image,
    width: int,
    height: int,
    mode: ArtworkMode = ArtworkMode.COLOR,
    *,
    depth: ColorDepth = ColorDepth.TRUECOLOR,
    unicode: bool = True,
    tint: tuple[int, int, int] = PHOSPHOR,
    placeholder: bool = False,
) -> TerminalArt:
    """Render ``image`` into a ``width`` x ``height`` cell grid.

    * COLOR + unicode: quadrant blocks, 2x2 pixels per cell, each cell drawn
      with the two colours that best fit its four pixels. That is twice the
      detail of half-blocks, with smooth (Lanczos) downscaling.
    * COLOR without unicode: background-coloured spaces, one pixel per cell.
    * MONO: uncoloured ASCII brightness ramp.
    * GLYPH: command-line glyphs (``$ > # 0 1 : /``) tinted by brightness.
    """
    if width < 1 or height < 1:
        raise ValueError("width and height must be positive")
    image = trim_letterbox(image.convert("RGB"))
    lines: list[Text] = []

    if mode is ArtworkMode.COLOR and unicode:
        # Crop to the physical aspect (cells are ~1:2); 2x2 pixels per cell.
        pixels = fit_cells(image, width * 2, height * 2).load()
        for row in range(height):
            cells = []
            for col in range(width):
                x, y = col * 2, row * 2
                char, fg, bg = quadrant_cell(
                    [
                        pixels[x, y],
                        pixels[x + 1, y],
                        pixels[x, y + 1],
                        pixels[x + 1, y + 1],
                    ]
                )
                cells.append(
                    (char, Style(color=_color(fg, depth), bgcolor=_color(bg, depth)))
                )
            lines.append(_line(cells))
    elif mode is ArtworkMode.COLOR:
        scaled = fit_cells(image, width, height * 2).resize((width, height))
        pixels = scaled.load()
        for row in range(height):
            lines.append(
                _line(
                    [
                        (" ", Style(bgcolor=_color(pixels[col, row], depth)))
                        for col in range(width)
                    ]
                )
            )
    else:
        ramp = MONO_RAMP if mode is ArtworkMode.MONO else GLYPH_RAMP
        scaled = fit_cells(image, width, height * 2).resize((width, height))
        gray = ImageOps.autocontrast(scaled.convert("L"))
        pixels = gray.load()
        for row in range(height):
            cells: list[tuple[str, Style | None]] = []
            for col in range(width):
                level = pixels[col, row]
                char = ramp[level * (len(ramp) - 1) // 255]
                style = None
                if mode is ArtworkMode.GLYPH:
                    k = 0.35 + 0.65 * level / 255
                    shade = (round(tint[0] * k), round(tint[1] * k), round(tint[2] * k))
                    style = Style(color=_color(shade, depth))
                cells.append((char, style))
            lines.append(_line(cells))

    return TerminalArt(width, height, mode, tuple(lines), placeholder=placeholder)


def render_bytes(
    data: bytes,
    width: int,
    height: int,
    mode: ArtworkMode = ArtworkMode.COLOR,
    *,
    depth: ColorDepth = ColorDepth.TRUECOLOR,
    unicode: bool = True,
    tint: tuple[int, int, int] = PHOSPHOR,
) -> TerminalArt:
    """Decode and render image bytes. Raises :class:`ArtworkError` if corrupt."""
    return render_image(
        decode_image(data),
        width,
        height,
        mode,
        depth=depth,
        unicode=unicode,
        tint=tint,
    )


# ------------------------------------------------------------------- fetching


class _NoRedirectToOtherSchemes(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if urlparse(newurl).scheme not in {"http", "https"}:
            raise ArtworkError("refused non-HTTP redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:  # pragma: no cover - certifi is a dependency
        return ssl.create_default_context()


def fetch_image(url: str, timeout: float = 8.0) -> bytes:
    """Blocking HTTP(S) download of an image, size-capped. Data only."""
    if urlparse(url).scheme not in {"http", "https"}:
        raise ArtworkError("unsupported thumbnail URL")
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(context=_ssl_context()),
        _NoRedirectToOtherSchemes(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    request = urllib.request.Request(
        url, headers={"User-Agent": f"PixelFM/{__version__}", "Accept": "image/*"}
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            if not response.headers.get_content_type().startswith("image/"):
                raise ArtworkError("thumbnail is not an image")
            data = response.read(MAX_IMAGE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ArtworkError(f"thumbnail request failed (HTTP {exc.code})") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ArtworkError("network error while fetching thumbnail") from exc
    if len(data) > MAX_IMAGE_BYTES:
        raise ArtworkError("image too large")
    return data


# -------------------------------------------------------------------- service


@dataclass(frozen=True, slots=True)
class ArtworkResult:
    """Artwork to show, plus a reason if it is the placeholder due to a failure."""

    art: TerminalArt
    error: str | None = None


class ArtworkService:
    """Fetch, cache, and render song thumbnails off the event loop.

    Downloaded bytes and rendered art are cached separately, so switching
    artwork mode re-renders without re-downloading. Network failures are not
    cached; corrupt images are. With ``disk`` set, downloaded thumbnails also
    persist across sessions in the cache directory (as opaque data files).
    """

    def __init__(
        self,
        fetcher: Callable[[str], bytes] | None = None,
        *,
        timeout: float = 8.0,
        image_cache_size: int = 64,
        render_cache_size: int = 256,
        disk: DiskCache | None = None,
    ) -> None:
        self._timeout = timeout
        self._download = fetcher or (lambda url: fetch_image(url, timeout=timeout))
        self.disk = disk
        self.images: LRUCache[str, bytes] = LRUCache(image_cache_size)
        self.rendered: LRUCache[tuple[object, ...], TerminalArt] = LRUCache(
            render_cache_size
        )
        self.covers: LRUCache[tuple[str, int, int], Image.Image] = LRUCache(16)

    def _fetch(self, url: str) -> bytes:
        """Disk cache first, then the network. Runs in a worker thread."""
        if self.disk is not None and (data := self.disk.get(url)) is not None:
            return data
        data = self._download(url)
        if self.disk is not None:
            self.disk.set(url, data)
        return data

    def _placeholder_key(self, *spec: object) -> tuple[object, ...]:
        return ("<placeholder>", *spec)

    async def placeholder(
        self,
        width: int,
        height: int,
        mode: ArtworkMode,
        depth: ColorDepth = ColorDepth.TRUECOLOR,
        unicode: bool = True,
        tint: tuple[int, int, int] = PHOSPHOR,
    ) -> TerminalArt:
        """The shared placeholder, rendered once per size/mode/tint."""
        key = self._placeholder_key(width, height, mode, depth, unicode, tint)
        art = self.rendered.get(key)
        if art is None:
            art = await asyncio.to_thread(
                render_image,
                placeholder_image(tint=tint),
                width,
                height,
                mode,
                depth=depth,
                unicode=unicode,
                placeholder=True,
                tint=tint,
            )
            self.rendered.set(key, art)
        return art

    async def image_data(self, url: str) -> bytes:
        """Thumbnail bytes: memory cache, disk cache, then the network (in a
        thread). Raises ``TimeoutError``, :class:`ArtworkError`, or the
        network error; failures are not cached."""
        data = self.images.get(url)
        if data is None:
            data = await asyncio.wait_for(
                run_in_thread(self._fetch, url), self._timeout + 1
            )
            self.images.set(url, data)
        return data

    async def cover_image(
        self, song: Song | None, width: int, height: int
    ) -> Image.Image | None:
        """The actual cover, cropped and scaled to ``width`` x ``height``
        pixels, for terminals that can draw images. ``None`` on any failure
        (the character rendering stays on screen)."""
        if song is None or not song.thumbnail_url or width < 1 or height < 1:
            return None
        key = (song.thumbnail_url, width, height)
        if (cover := self.covers.get(key)) is not None:
            return cover

        def prepare(data: bytes) -> Image.Image:
            image = trim_letterbox(decode_image(data))
            return fit_cells(image, width, height)

        try:
            data = await self.image_data(song.thumbnail_url)
            cover = await run_in_thread(prepare, data)
        except Exception:
            return None
        self.covers.set(key, cover)
        return cover

    async def artwork_for(
        self,
        song: Song | None,
        width: int,
        height: int,
        mode: ArtworkMode = ArtworkMode.COLOR,
        depth: ColorDepth = ColorDepth.TRUECOLOR,
        unicode: bool = True,
        tint: tuple[int, int, int] = PHOSPHOR,
    ) -> ArtworkResult:
        """Return rendered artwork for ``song``. Never raises for bad input."""
        spec = (width, height, mode, depth, unicode, tint)
        if song is None:
            return ArtworkResult(await self.placeholder(*spec))
        url = song.thumbnail_url
        if not url:
            return ArtworkResult(await self.placeholder(*spec), "no thumbnail")

        key = (url, *spec)
        if (art := self.rendered.get(key)) is not None:
            return ArtworkResult(art, "unreadable image" if art.placeholder else None)

        try:
            data = await self.image_data(url)
        except TimeoutError:
            return ArtworkResult(
                await self.placeholder(*spec), "thumbnail download timed out"
            )
        except ArtworkError as exc:
            return ArtworkResult(await self.placeholder(*spec), str(exc))
        except Exception:
            return ArtworkResult(
                await self.placeholder(*spec),
                "network error while fetching thumbnail",
            )

        try:
            art = await asyncio.to_thread(
                render_bytes,
                data,
                width,
                height,
                mode,
                depth=depth,
                unicode=unicode,
                tint=tint,
            )
        except ArtworkError as exc:
            if self.disk is not None:
                self.disk.discard(url)  # don't keep serving a corrupt file
            art = await self.placeholder(*spec)
            self.rendered.set(key, art)
            return ArtworkResult(art, str(exc))
        self.rendered.set(key, art)
        return ArtworkResult(art)
