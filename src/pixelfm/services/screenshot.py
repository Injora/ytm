"""Reading a playlist from screenshots, with the computer's own text recognition.

1. **Read text**: Apple's Vision framework on macOS (the ``pixelfm-ocr``
   helper next to the media helper), Tesseract on Linux. Nothing is sent
   anywhere: the image never leaves the computer. Windows has its own
   engine, but PixelFM doesn't run on Windows yet.
2. **Rebuild rows** (pure, :func:`parse_lines`): song lists put the titles in
   one column, with the artist either underneath (Spotify) or in the next
   column (Apple Music, YouTube Music). Headers, track numbers, dates, play
   counts and the playlist's own heading are dropped; durations are kept
   when they can be read.
3. The rows become an ordinary import (:class:`SpotifyPlaylist` with source
   "screenshot"), so matching and review work exactly as for Spotify links.

It is a reading, not a certainty: the user checks every row before anything
is imported.
"""

from __future__ import annotations

import csv
import io
import itertools
import logging
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from pixelfm.services.matching import core_title, normalize
from pixelfm.services.spotify import SpotifyPlaylist, SpotifyTrack

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".heic", ".webp", ".tif", ".tiff", ".bmp")
HELPER = Path(__file__).resolve().parent.parent / "native" / "PixelFMMedia.app"
OCR_EXECUTABLE = "Contents/MacOS/pixelfm-ocr"
TIMEOUT = 60  # seconds for one image
MAX_PIXELS = 40_000_000  # refuse absurd images instead of hanging

LINUX_HELP = (
    "Screenshot import on Linux needs Tesseract: sudo apt install tesseract-ocr "
    "(Fedora: sudo dnf install tesseract; Arch: sudo pacman -S tesseract "
    "tesseract-data-eng), or run `pixelfm --install-deps`."
)


class ScreenshotError(Exception):
    """A screenshot couldn't be read (with what to do about it)."""


@dataclass(frozen=True, slots=True)
class TextLine:
    """One run of text and its box, in pixels from the top left."""

    text: str
    x: float
    y: float
    w: float
    h: float
    confidence: float = 1.0
    #: The first word's box (x, y, w, h) when the engine reports words.
    first_word: tuple[float, float, float, float] | None = None
    last_word: tuple[float, float, float, float] | None = None

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def middle(self) -> float:
        return self.y + self.h / 2


@dataclass(frozen=True, slots=True)
class Recognized:
    width: float
    height: float
    lines: tuple[TextLine, ...]


class Reader(Protocol):
    name: str

    def read(self, path: Path) -> Recognized: ...


# ------------------------------------------------------------------ engines


def _run(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            timeout=TIMEOUT,
            check=False,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # never touches PixelFM's terminal
        )
    except subprocess.TimeoutExpired:
        raise ScreenshotError(
            "Reading the screenshot took too long; try a smaller one."
        ) from None
    except OSError as exc:
        raise ScreenshotError(f"Couldn't start the text reader: {exc}") from None


def _stderr(result: subprocess.CompletedProcess[bytes]) -> str:
    lines = result.stderr.decode(errors="replace").strip().splitlines()
    return lines[-1].removeprefix("pixelfm-ocr: ") if lines else ""


class VisionReader:
    """macOS: Apple's Vision text recognition through ``pixelfm-ocr``."""

    name = "macOS text recognition"

    def __init__(self, executable: Path) -> None:
        self.executable = executable

    def read(self, path: Path) -> Recognized:
        result = _run([str(self.executable), str(path)])
        if result.returncode != 0:
            raise ScreenshotError(
                f"Couldn't read {path.name}: {_stderr(result) or 'unknown error'}"
            )
        return parse_vision(result.stdout)

    def clipboard(self, target: Path) -> bool:
        """Save the clipboard's image to ``target``; ``False`` if there's none."""
        result = _run([str(self.executable), "--clipboard", str(target)])
        if result.returncode == 3:
            return False
        if result.returncode != 0:
            raise ScreenshotError(
                f"Couldn't read the clipboard: {_stderr(result) or 'unknown error'}"
            )
        return True


def parse_vision(raw: bytes | str) -> Recognized:
    import json

    try:
        data = json.loads(raw)
        lines = tuple(
            TextLine(
                str(line["text"]),
                float(line["x"]),
                float(line["y"]),
                float(line["w"]),
                float(line["h"]),
                float(line.get("confidence", 1.0)),
            )
            for line in data["lines"]
        )
        return Recognized(float(data["width"]), float(data["height"]), lines)
    except (ValueError, KeyError, TypeError) as exc:
        raise ScreenshotError(
            f"The text reader gave an unexpected answer ({exc})."
        ) from None


class TesseractReader:
    """Linux (or anywhere it is installed): the ``tesseract`` command."""

    name = "Tesseract"

    def __init__(self, executable: str) -> None:
        self.executable = executable

    def read(self, path: Path) -> Recognized:
        from PIL import Image, ImageOps, ImageStat, UnidentifiedImageError

        try:
            with Image.open(path) as opened:
                if opened.width * opened.height > MAX_PIXELS:
                    raise ScreenshotError(f"{path.name} is too large to read.")
                image = opened.convert("L")
        except (OSError, UnidentifiedImageError) as exc:
            raise ScreenshotError(f"Couldn't open {path.name} as an image.") from exc
        width, height = image.size
        # Tesseract reads dark text on a light background best, at a decent
        # size: dark themes are inverted, small screenshots enlarged.
        if ImageStat.Stat(image).mean[0] < 128:
            image = ImageOps.invert(image)
        scale = 2 if width < 1600 else 1
        if scale != 1:
            image = image.resize((width * scale, height * scale), Image.LANCZOS)
        with tempfile.TemporaryDirectory(prefix="pixelfm-ocr-") as tmp:
            prepared = Path(tmp) / "screenshot.png"
            image.save(prepared)
            result = _run([self.executable, str(prepared), "stdout", "tsv"])
        if result.returncode != 0:
            raise ScreenshotError(
                f"Tesseract couldn't read {path.name}: "
                f"{_stderr(result) or 'unknown error'}"
            )
        return parse_tesseract(
            result.stdout.decode(errors="replace"), width, height, scale
        )


def parse_tesseract(
    tsv: str, width: float, height: float, scale: float = 1
) -> Recognized:
    """Tesseract's word table as lines. A line is split where words are far
    apart: that is the gap between two columns, not between two words."""
    rows = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    lines: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("level") != "5" or not (row.get("text") or "").strip():
            continue
        try:
            word = {
                "text": row["text"].strip(),
                "x": int(row["left"]) / scale,
                "y": int(row["top"]) / scale,
                "w": int(row["width"]) / scale,
                "h": int(row["height"]) / scale,
                "conf": float(row["conf"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
        key = (row["page_num"], row["block_num"], row["par_num"], row["line_num"])
        lines.setdefault(key, []).append(word)
    out: list[TextLine] = []
    for words in lines.values():
        segment: list[dict[str, Any]] = []
        for word in sorted(words, key=lambda w: w["x"]):
            if segment:
                previous = segment[-1]
                gap = word["x"] - (previous["x"] + previous["w"])
                if gap > 2.5 * max(previous["h"], word["h"]):
                    out.append(_segment(segment))
                    segment = []
            segment.append(word)
        if segment:
            out.append(_segment(segment))
    return Recognized(width, height, tuple(out))


def _segment(words: list[dict[str, Any]]) -> TextLine:
    x = min(w["x"] for w in words)
    y = min(w["y"] for w in words)
    right = max(w["x"] + w["w"] for w in words)
    bottom = max(w["y"] + w["h"] for w in words)
    first, last = words[0], words[-1]
    return TextLine(
        " ".join(w["text"] for w in words),
        x,
        y,
        right - x,
        bottom - y,
        max(0.0, min(1.0, min(w["conf"] for w in words) / 100)),
        (first["x"], first["y"], first["w"], first["h"]),
        (last["x"], last["y"], last["w"], last["h"]),
    )


def ocr_helper(env: dict[str, str] | None = None) -> Path | None:
    env = dict(os.environ) if env is None else env
    override = env.get("PIXELFM_OCR_HELPER")
    executable = Path(override) if override else HELPER / OCR_EXECUTABLE
    return (
        executable if executable.is_file() and os.access(executable, os.X_OK) else None
    )


def default_reader(
    platform: str = sys.platform, env: dict[str, str] | None = None
) -> Reader:
    """The built-in reader for this system.

    Raises:
        ScreenshotError: none is available (with how to get one).
    """
    if platform == "darwin":
        helper = ocr_helper(env)
        if helper is None:
            raise ScreenshotError(
                "The text-recognition helper is missing (a source checkout needs "
                "`npm run build:helper`)."
            )
        return VisionReader(helper)
    if platform.startswith("win"):
        raise ScreenshotError("Screenshot import isn't available on Windows yet.")
    tool = shutil.which("tesseract")
    if tool is None:
        raise ScreenshotError(LINUX_HELP)
    return TesseractReader(tool)


def clipboard_image(reader: Reader, target: Path) -> bool:
    """Save the image on the clipboard to ``target`` (PNG). ``False`` if the
    clipboard holds no image.

    Raises:
        ScreenshotError: the clipboard can't be read on this system.
    """
    if isinstance(reader, VisionReader):
        return reader.clipboard(target)
    for command in (
        ["wl-paste", "--no-newline", "--type", "image/png"],
        ["xclip", "-selection", "clipboard", "-target", "image/png", "-out"],
    ):
        tool = shutil.which(command[0])
        if tool is None:
            continue
        result = _run([tool, *command[1:]])
        if result.returncode != 0 or not result.stdout:
            return False
        target.write_bytes(result.stdout)
        return True
    raise ScreenshotError(
        "Reading images from the clipboard needs wl-clipboard (Wayland) or xclip "
        "(X11). Or save the screenshot and drop the file here instead."
    )


def split_paths(text: str) -> list[Path]:
    """Image paths from what a terminal pastes when files are dropped on it:
    one or more paths, quoted or with backslash-escaped spaces."""
    import shlex

    text = text.strip()
    if not text:
        return []
    whole = Path(text).expanduser()
    if whole.suffix.lower() in IMAGE_SUFFIXES and whole.is_file():
        return [whole]  # a single path with unescaped spaces
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = [text]
    paths = []
    for part in parts:
        if part.startswith("file://"):
            from urllib.parse import unquote, urlsplit

            part = unquote(urlsplit(part).path)
        paths.append(Path(part).expanduser())
    return paths


def looks_like_images(text: str) -> bool:
    paths = split_paths(text)
    return bool(paths) and all(p.suffix.lower() in IMAGE_SUFFIXES for p in paths)


# ------------------------------------------------------------------ parsing

_DURATION = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
_NUMBERISH = re.compile(r"^[#\d.,\s]+$")  # track numbers, play counts
_MONTHS = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATES = (
    re.compile(rf"^{_MONTHS}\s+\d{{1,2}},?\s+\d{{4}}$", re.IGNORECASE),
    re.compile(rf"^\d{{1,2}}\s+{_MONTHS},?\s+\d{{4}}$", re.IGNORECASE),
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    re.compile(r"^\d{1,2}[/.]\d{1,2}[/.]\d{2,4}$"),
    re.compile(
        r"^\d+\s+(second|minute|hour|day|week|month|year)s?\s+ago$", re.IGNORECASE
    ),
    re.compile(r"^(today|yesterday)$", re.IGNORECASE),
)
_META = (
    re.compile(r"\b\d+\s+(songs?|tracks?|videos?|likes?|saves?|views?)\b", re.I),
    re.compile(r"^(public|private|collaborative|shared)?\s*playlist\b", re.I),
    re.compile(r"\b\d+\s*(hr|hrs|min|mins|sec|secs)\b", re.I),
)
HEADERS = {
    "#",
    "# title",
    "title",
    "song",
    "songs",
    "name",
    "track",
    "artist",
    "artists",
    "album",
    "time",
    "duration",
    "date added",
    "added",
    "plays",
    "popularity",
    "genre",
    "rating",
}
_ROW_HEADERS = {"#", "# title", "title", "song", "name", "track"}
_SEPARATORS = re.compile(r"\s+[•·]\s+")
_LABELS = {"song", "video", "single", "ep", "album", "episode", "podcast"}
_BADGE = re.compile(r"^(e|el|ei|e1|e\||\[e\]|b|8|ⓔ|🅴)$", re.IGNORECASE)


def _clean(text: str) -> str:
    text = " ".join(text.replace("\u00a0", " ").split())
    text = re.sub(r"^\|(?= \w)", "I", text)  # Tesseract reads "I" as "|"
    text = text.strip(" |")
    return re.sub(r"\s*(…|\.\.\.)$", "", text)  # cut-off titles


def _noise(text: str) -> bool:
    if len(text) < 2 or not re.search(r"\w", text):
        return True
    if _NUMBERISH.match(text) or any(p.match(text) for p in _DATES):
        return True
    return any(p.search(text) for p in _META)


def _column_of(lines: Sequence[TextLine], tolerance: float) -> list[list[TextLine]]:
    """Group lines whose left edges line up."""
    columns: list[list[TextLine]] = []
    for line in sorted(lines, key=lambda line: line.x):
        if columns and line.x - columns[-1][0].x <= tolerance:
            columns[-1].append(line)
        else:
            columns.append([line])
    return [sorted(c, key=lambda line: line.y) for c in columns]


def _stack(column: list[TextLine]) -> list[list[TextLine]]:
    """Title-column lines grouped into entries: a title with the artist
    right under it, or one line per entry when nothing is stacked."""
    if len(column) < 2:
        return [[line] for line in column]
    height = statistics.median(line.h for line in column) or 1.0
    gaps = [(b.y - a.bottom) / height for a, b in itertools.pairwise(column)]
    ordered = sorted(gaps)
    threshold = None
    for low, high in itertools.pairwise(ordered):
        # Two kinds of gap (inside an entry, between entries): split between.
        if high - low > 0.5 and high > 2 * max(low, 0.15):
            threshold = (low + high) / 2
            break
    if threshold is None:
        threshold = 0.5 if statistics.median(gaps) < 0.5 else -1.0
    groups: list[list[TextLine]] = [[column[0]]]
    for gap, line in zip(gaps, column[1:], strict=False):
        if gap < threshold and len(groups[-1]) < 3:
            groups[-1].append(line)
        else:
            groups.append([line])
    return groups


def _beside(
    column: list[TextLine] | None, top: float, bottom: float, height: float
) -> TextLine | None:
    """The line of ``column`` level with ``top``..``bottom``, if any."""
    if not column:
        return None
    middle = (top + bottom) / 2
    near = [
        line
        for line in column
        if top - height / 2 <= line.middle <= bottom + height / 2
    ]
    return min(near, key=lambda line: abs(line.middle - middle)) if near else None


def _badge(image: Any, box: tuple[float, float, float, float] | None) -> bool:
    """Is ``box`` a filled "explicit" badge rather than letters? A badge is a
    solid block; letters cover a small part of their box."""
    if image is None or box is None:
        return False
    x, y, w, h = box
    try:
        left, top = int(max(0, x)), int(max(0, y + h * 0.15))
        right = int(min(image.width, x + max(w, h * 0.5)))
        bottom = int(min(image.height, y + h * 0.85))
        if right - left < 3 or bottom - top < 3:
            return False
        region = image.crop((left, top, right, bottom)).convert("L")
        edge = image.crop(
            (int(max(0, x - h * 0.4)), top, int(max(1, x - 1)), bottom)
        ).convert("L")
        pixels = list(region.tobytes())
        background = statistics.median(edge.tobytes()) if edge.width else pixels[0]
    except (OSError, ValueError, statistics.StatisticsError):
        return False
    filled = sum(abs(p - background) > 40 for p in pixels) / len(pixels)
    return filled > 0.5


def _strip_badges(line: TextLine, image: Any) -> str:
    """Remove an "explicit" badge read as a letter ("El Billie Eilish")."""
    words = _clean(line.text).split(" ")
    if len(words) > 1 and _BADGE.match(words[0]):
        box = line.first_word or (line.x, line.y, line.h * 0.75, line.h)
        if _badge(image, box):
            words = words[1:]
    if len(words) > 1 and _BADGE.match(words[-1]):
        end = line.x + line.w - line.h * 0.75
        box = line.last_word or (end, line.y, line.h, line.h)
        if _badge(image, box):
            words = words[:-1]
    return " ".join(words)


def _seconds(text: str) -> float:
    parts = [int(p) for p in text.split(":")]
    total = 0
    for part in parts:
        total = total * 60 + part
    return float(total)


def _artist_and_album(text: str) -> tuple[tuple[str, ...], str, float]:
    """ "Artist • Album" / "Song • Artist • 3:45" lines into their parts."""
    duration = 0.0
    parts = []
    for part in _SEPARATORS.split(text):
        part = part.strip()
        if _DURATION.match(part):
            duration = _seconds(part)
        elif part and part.casefold() not in _LABELS:
            parts.append(part)
    if not parts:
        return (), "", duration
    artists = tuple(a for a in re.split(r"\s*,\s*", parts[0]) if a)
    return artists, parts[1] if len(parts) > 1 else "", duration


def guess_name(recognized: Recognized) -> str:
    """The playlist's heading (the biggest text near the top), or ""."""
    lines = [
        line
        for line in recognized.lines
        if not _noise(_clean(line.text))
        and line.y < recognized.height * 0.45
        and _clean(line.text).casefold() not in HEADERS
    ]
    if len(lines) < 2:
        return ""
    height = statistics.median(line.h for line in recognized.lines)
    tallest = max(lines, key=lambda line: line.h)
    return _clean(tallest.text) if tallest.h > 1.6 * height else ""


def parse_lines(
    recognized: Recognized, image: Any = None, start: int = 0
) -> list[SpotifyTrack]:
    """Songs (title, artists, album, duration) from recognised text lines."""
    lines = [
        replace(line, text=_clean(line.text))
        for line in recognized.lines
        if line.confidence >= 0.3 and _clean(line.text)
    ]
    if not lines:
        return []
    height = statistics.median(line.h for line in lines) or 1.0

    # A header row ("# Title Album", "Song Artist Time"): the list starts below.
    headers = [line for line in lines if line.text.casefold() in HEADERS]
    for header in sorted(headers, key=lambda line: line.y):
        row = [h for h in headers if abs(h.middle - header.middle) < height]
        if len(row) >= 2 or header.text.casefold() in _ROW_HEADERS:
            below = max(h.bottom for h in row)
            lines = [line for line in lines if line.middle > below]
            break

    durations = [line for line in lines if _DURATION.match(line.text)]
    text = [
        line
        for line in lines
        if not _noise(line.text) and line.text.casefold() not in HEADERS
    ]
    if not text:
        return []
    tolerance = max(recognized.width * 0.015, height * 0.6)
    columns = _column_of(text, tolerance)
    biggest = max(len(c) for c in columns)
    titles_at = next(i for i, c in enumerate(columns) if len(c) >= 0.5 * biggest)
    title_column = columns[titles_at]
    groups = _stack(title_column)

    # The next column with about as many lines as there are entries.
    beside = next(
        (c for c in columns[titles_at + 1 :] if len(c) >= max(1, 0.4 * len(groups))),
        None,
    )
    after = None
    if beside is not None:
        later = columns[columns.index(beside) + 1 :]
        after = next((c for c in later if len(c) >= max(1, 0.4 * len(groups))), None)

    title_height = statistics.median(g[0].h for g in groups)
    rows: list[tuple[SpotifyTrack, bool]] = []
    for group in groups:
        first = group[0]
        if first.h > 1.8 * title_height:
            continue  # a heading, not a song
        top, bottom = first.y, group[-1].bottom
        title = _strip_badges(first, image)
        album = ""
        if len(group) > 1:
            artists, album, duration = _artist_and_album(_strip_badges(group[1], image))
            if len(group) > 2 and not album:
                album = group[2].text
            side = _beside(beside, top, bottom, height)
            album = album or (side.text if side is not None else "")
        else:
            side = _beside(beside, top, bottom, height)
            artists, album, duration = _artist_and_album(
                _strip_badges(side, image) if side is not None else ""
            )
            third = _beside(after, top, bottom, height)
            album = album or (third.text if third is not None else "")
        clock = _beside([d for d in durations if d.x > first.x], top, bottom, height)
        if clock is not None:
            duration = _seconds(clock.text)
        if not title or _noise(title):
            continue
        rows.append(
            (SpotifyTrack(0, title, artists, album, duration), clock is not None)
        )
    timed = sum(has for _, has in rows)
    if timed >= 2 and timed >= 0.6 * len(rows):
        rows = [row for row in rows if row[1]]  # headings and cut-off rows
    return [replace(track, position=start + i) for i, (track, _) in enumerate(rows)]


def song_key(title: str, artists: Sequence[str]) -> tuple[str, str]:
    """Two readings of the same song have the same key."""
    return core_title(title), normalize(artists[0]) if artists else ""


def read_screenshots(
    paths: Sequence[Path], reader: Reader
) -> tuple[SpotifyPlaylist, list[str]]:
    """Every song in ``paths``, in order, without the repeats that overlapping
    screenshots of one long list have. Returns the playlist and a note per
    screenshot that had no songs.

    Raises:
        ScreenshotError: a file is missing or can't be read at all.
    """
    from PIL import Image

    tracks: list[SpotifyTrack] = []
    seen: set[tuple[str, str]] = set()
    empty: list[str] = []
    name = ""
    for path in paths:
        if not path.is_file():
            raise ScreenshotError(f"There is no file at {path}.")
        recognized = reader.read(path)
        image = None
        try:
            image = Image.open(path)
            image.load()
        except OSError:
            image = None  # e.g. HEIC without a plug-in: badges stay as read
        try:
            found = parse_lines(recognized, image)
        finally:
            if image is not None:
                image.close()
        log.info(
            "screenshot %s: %d lines read, %d songs",
            path.name,
            len(recognized.lines),
            len(found),
        )
        name = name or guess_name(recognized)
        if not found:
            empty.append(path.name)
        for track in found:
            key = song_key(track.title, track.artists)
            if key in seen:
                continue
            seen.add(key)
            tracks.append(replace(track, position=len(tracks)))
    playlist = SpotifyPlaylist(
        name=name or "Screenshot import",
        total=len(tracks),
        source="screenshot",
        tracks=tuple(tracks),
    )
    return playlist, empty
