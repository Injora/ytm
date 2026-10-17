"""Music-derived colour palettes: the current track's artwork sets the theme.

A palette is a set of colour *roles* (accents, backgrounds, borders, text),
not "the dominant colour everywhere". Extraction quantizes a small copy of
the thumbnail, picks a vivid, well-represented accent hue and a distinct
second one, then builds dark tinted backgrounds around them. Every text role
is checked against the background with the WCAG contrast formula and
lightened until it is readable.

Extraction runs in a worker thread and results are cached per artwork URL.
"""

from __future__ import annotations

import colorsys
import logging
from dataclasses import dataclass, fields, replace

from PIL import Image
from textual.theme import Theme

from pixelfm.models import Song
from pixelfm.services.artwork import (
    ArtworkError,
    ArtworkService,
    decode_image,
    trim_letterbox,
)
from pixelfm.services.cache import LRUCache
from pixelfm.services.threads import run_in_thread
from pixelfm.styles import PIXELFM_THEME, hex_to_rgb, make_theme

log = logging.getLogger(__name__)

RGB = tuple[int, int, int]

#: Minimum contrast against the background: WCAG AA for normal text.
TEXT_CONTRAST = 4.5
FOREGROUND_CONTRAST = 12.0
SAMPLE_SIZE = 64
QUANT_COLORS = 12
#: Below this share of colourful pixels the artwork counts as monochrome.
MONOCHROME_SHARE = 0.05
#: Minimum hue distance (fraction of the colour wheel) for a second accent.
SECOND_HUE_DISTANCE = 0.09


@dataclass(frozen=True, slots=True)
class Palette:
    """Colour roles for one theme, as RGB triples."""

    primary: RGB  # main accent: active controls, progress, focus
    secondary: RGB  # second accent: labels, hot meter levels
    background: RGB
    surface: RGB  # deck and mini player panels
    panel: RGB  # buttons, raised areas
    foreground: RGB  # body text
    muted: RGB  # secondary text
    blurred: RGB  # unfocused borders and separators
    cursor_blurred: RGB  # selected row in an unfocused list
    hover: RGB
    source: str = "default"  # "artwork", "monochrome", or "default"

    def roles(self) -> dict[str, RGB]:
        return {
            f.name: getattr(self, f.name) for f in fields(self) if f.name != "source"
        }

    def hex(self, role: str) -> str:
        return to_hex(getattr(self, role))


# ------------------------------------------------------------- colour maths


def to_hex(rgb: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def hls(rgb: RGB) -> tuple[float, float, float]:
    return colorsys.rgb_to_hls(*(c / 255 for c in rgb))


def from_hls(h: float, lightness: float, saturation: float) -> RGB:
    r, g, b = colorsys.hls_to_rgb(h % 1.0, _clamp(lightness), _clamp(saturation))
    return (round(r * 255), round(g * 255), round(b * 255))


def luminance(rgb: RGB) -> float:
    """WCAG relative luminance."""

    def linear(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: RGB, b: RGB) -> float:
    """WCAG contrast ratio, 1.0 to 21.0."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def ensure_contrast(rgb: RGB, background: RGB, minimum: float) -> RGB:
    """Lighten ``rgb`` (keeping its hue) until it reads on ``background``."""
    h, lightness, s = hls(rgb)
    while contrast(rgb, background) < minimum and lightness < 0.98:
        lightness = min(0.98, lightness + 0.02)
        rgb = from_hls(h, lightness, s)
    return rgb


def hue_distance(a: float, b: float) -> float:
    d = abs(a - b) % 1.0
    return min(d, 1.0 - d)


def mix(a: RGB, b: RGB, t: float) -> RGB:
    t = _clamp(t)
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def blend(a: Palette, b: Palette, t: float) -> Palette:
    """Interpolate every role from ``a`` (t=0) to ``b`` (t=1)."""
    roles = {name: mix(value, getattr(b, name), t) for name, value in a.roles().items()}
    return Palette(**roles, source=b.source if t >= 0.5 else a.source)


# ------------------------------------------------------------- palettes


def palette_from_theme(theme: Theme) -> Palette:
    """Roles of a PixelFM theme (raises ``KeyError`` for other themes)."""
    variables = theme.variables
    black = (0, 0, 0)
    return Palette(
        primary=hex_to_rgb(theme.primary, black),
        secondary=hex_to_rgb(theme.secondary, black),
        background=hex_to_rgb(theme.background, black),
        surface=hex_to_rgb(theme.surface, black),
        panel=hex_to_rgb(theme.panel, black),
        foreground=hex_to_rgb(theme.foreground, black),
        muted=hex_to_rgb(variables["footer-description-foreground"], black),
        blurred=hex_to_rgb(variables["border-blurred"], black),
        cursor_blurred=hex_to_rgb(variables["block-cursor-blurred-background"], black),
        hover=hex_to_rgb(variables["block-hover-background"], black),
        source="default",
    )


#: Fallback when there is no artwork: the stock neon cyan/magenta look.
DEFAULT_PALETTE = palette_from_theme(PIXELFM_THEME)


def build_palette(
    hue: float,
    saturation: float,
    second: tuple[float, float],
    *,
    source: str = "artwork",
) -> Palette:
    """Roles around an accent hue/saturation and a second accent."""
    tint = _clamp(saturation * 0.5, 0.0, 0.4)
    background = from_hls(hue, 0.05, tint)

    def accent(h: float, s: float, floor: float) -> RGB:
        rgb = from_hls(h, 0.62, _clamp(max(floor, s * 1.15)))
        return ensure_contrast(rgb, background, TEXT_CONTRAST)

    second_hue, second_saturation = second
    # A low-saturation second accent stays low (steel, slate) rather than
    # being forced into a neon colour the artwork does not have.
    second_floor = 0.6 if second_saturation >= 0.3 else 0.0
    secondary = accent(second_hue, second_saturation, second_floor)
    if second_floor == 0.0:
        h, _, s = hls(secondary)
        secondary = ensure_contrast(from_hls(h, 0.7, s), background, TEXT_CONTRAST)
    return Palette(
        primary=accent(hue, saturation, 0.6),
        secondary=secondary,
        background=background,
        surface=from_hls(hue, 0.075, tint),
        panel=from_hls(hue, 0.11, tint * 0.9),
        foreground=ensure_contrast(
            from_hls(hue, 0.92, 0.25), background, FOREGROUND_CONTRAST
        ),
        muted=ensure_contrast(from_hls(hue, 0.62, 0.15), background, TEXT_CONTRAST),
        blurred=from_hls(hue, 0.22, 0.25),
        cursor_blurred=from_hls(hue, 0.19, 0.5),
        hover=from_hls(hue, 0.09, tint),
        source=source,
    )


def monochrome_palette() -> Palette:
    """Black-and-white artwork: silver, white, and graphite."""
    background = from_hls(0, 0.06, 0)
    return Palette(
        primary=ensure_contrast(from_hls(0.58, 0.78, 0.06), background, 7.0),
        secondary=from_hls(0, 0.95, 0),
        background=background,
        surface=from_hls(0, 0.085, 0),
        panel=from_hls(0, 0.12, 0),
        foreground=from_hls(0, 0.92, 0),
        muted=ensure_contrast(from_hls(0, 0.6, 0), background, TEXT_CONTRAST),
        blurred=from_hls(0, 0.24, 0),
        cursor_blurred=from_hls(0.58, 0.22, 0.08),
        hover=from_hls(0, 0.1, 0),
        source="monochrome",
    )


@dataclass(frozen=True, slots=True)
class Swatch:
    rgb: RGB
    share: float  # fraction of sampled pixels

    @property
    def chroma(self) -> float:
        return (max(self.rgb) - min(self.rgb)) / 255

    @property
    def hue(self) -> float:
        return hls(self.rgb)[0]

    @property
    def saturation(self) -> float:
        return hls(self.rgb)[2]

    @property
    def colourful(self) -> bool:
        return self.chroma >= 0.16 and max(self.rgb) >= 50

    @property
    def score(self) -> float:
        """Prefer vivid colours, weighted by (dampened) coverage."""
        return (self.share**0.75) * self.chroma


def swatches(image: Image.Image) -> list[Swatch]:
    """Dominant colours of ``image`` with their coverage."""
    small = trim_letterbox(image.convert("RGB"))
    small.thumbnail((SAMPLE_SIZE, SAMPLE_SIZE))
    quantized = small.quantize(colors=QUANT_COLORS, method=Image.Quantize.MEDIANCUT)
    flat = quantized.getpalette() or []
    counts = quantized.getcolors() or []
    total = sum(count for count, _ in counts) or 1
    result = [
        Swatch(tuple(flat[i * 3 : i * 3 + 3]), count / total)  # type: ignore[arg-type]
        for count, i in counts
    ]
    return sorted(result, key=lambda s: s.share, reverse=True)


def palette_from_image(image: Image.Image) -> Palette:
    colours = swatches(image)
    vivid = [s for s in colours if s.colourful]
    if sum(s.share for s in vivid) < MONOCHROME_SHARE:
        return monochrome_palette()
    main = max(vivid, key=lambda s: s.score)
    others = [
        s
        for s in vivid
        if hue_distance(s.hue, main.hue) >= SECOND_HUE_DISTANCE and s.share >= 0.03
    ]
    neutral = sum(
        s.share for s in colours if not s.colourful and 0.2 <= hls(s.rgb)[1] <= 0.85
    )
    if others:
        second = max(others, key=lambda s: s.score)
        second_accent = (second.hue, second.saturation)
    elif neutral >= 0.25:
        second_accent = (main.hue, 0.15)  # grey in the artwork: steel / slate
    else:
        second_accent = ((main.hue + 0.1) % 1.0, main.saturation)  # analogous
    return build_palette(main.hue, main.saturation, second_accent)


def extract_palette(data: bytes) -> Palette:
    """Palette for encoded image bytes; raises :class:`ArtworkError`."""
    return palette_from_image(decode_image(data))


def with_accents(palette: Palette, primary: RGB, secondary: RGB) -> Palette:
    """``palette`` with replaced accents, kept readable on its background."""
    background = palette.background
    return replace(
        palette,
        primary=ensure_contrast(primary, background, TEXT_CONTRAST),
        secondary=ensure_contrast(secondary, background, TEXT_CONTRAST),
    )


def theme_from_palette(palette: Palette, name: str) -> Theme:
    return make_theme(
        name,
        primary=palette.hex("primary"),
        secondary=palette.hex("secondary"),
        background=palette.hex("background"),
        surface=palette.hex("surface"),
        panel=palette.hex("panel"),
        foreground=palette.hex("foreground"),
        muted=palette.hex("muted"),
        blurred=palette.hex("blurred"),
        cursor_blurred=palette.hex("cursor_blurred"),
        hover=palette.hex("hover"),
    )


class PaletteService:
    """Palettes per track artwork, extracted in a thread and cached by URL."""

    def __init__(self, artwork: ArtworkService, cache_size: int = 256) -> None:
        self.artwork = artwork
        self.cache: LRUCache[str, Palette] = LRUCache(cache_size)
        self.extractions = 0

    async def palette_for(self, song: Song | None) -> Palette:
        """Never raises: missing or unreadable artwork gives the default."""
        if song is None or not song.thumbnail_url:
            return DEFAULT_PALETTE
        url = song.thumbnail_url
        if (cached := self.cache.get(url)) is not None:
            return cached
        try:
            data = await self.artwork.image_data(url)
        except Exception as exc:  # network trouble is not cached: retry later
            log.info("no artwork for palette (%s): %s", song.id, exc)
            return DEFAULT_PALETTE
        self.extractions += 1
        try:
            palette = await run_in_thread(extract_palette, data)
        except ArtworkError as exc:
            log.info("unreadable artwork for palette (%s): %s", song.id, exc)
            palette = DEFAULT_PALETTE
        self.cache.set(url, palette)
        return palette
