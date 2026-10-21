"""Screenshot import: reading text, rebuilding rows, adding to playlists.

The parser is tested on what the real engines (Apple Vision, Tesseract 5)
returned for screenshots in four common layouts (made by
``fixtures/screenshots/make_shots.py``), so these tests need neither engine.
Tests that run a real engine are skipped where it isn't installed.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from textual.widgets import Input, OptionList

from pixelfm.models import Song
from pixelfm.services.library import Library
from pixelfm.services.matching import Matcher, same_song
from pixelfm.services.screenshot import (
    LINUX_HELP,
    Recognized,
    ScreenshotError,
    TesseractReader,
    TextLine,
    VisionReader,
    _strip_badges,
    default_reader,
    looks_like_images,
    ocr_helper,
    parse_lines,
    parse_tesseract,
    parse_vision,
    read_screenshots,
    split_paths,
)
from pixelfm.services.spotify import SpotifyPlaylist, SpotifyTrack
from pixelfm.widgets import TextPrompt
from pixelfm.widgets.importer import ChoicePicker, ScreenshotReview, parse_row
from tests.conftest import image_bytes
from tests.test_import import make_app, status, until, video
from tests.test_spotify import FakeSpotify

SHOTS = Path(__file__).parent / "fixtures" / "screenshots"
LAYOUTS = ("spotify-desktop", "apple-music", "spotify-mobile", "youtube-music")
SONGS = [
    ("Blinding Lights", ("The Weeknd",), 200),
    ("Levitating (feat. DaBaby)", ("Dua Lipa", "DaBaby"), 203),
    ("bad guy", ("Billie Eilish",), 194),
    ("Mr. Brightside", ("The Killers",), 222),
    ("Heat Waves", ("Glass Animals",), 238),
    ("Tum Hi Ho", ("Arijit Singh",), 262),
    ("Don't Start Now", ("Dua Lipa",), 183),
    ("Sweater Weather", ("The Neighbourhood",), 240),
]


def vision(layout: str) -> Recognized:
    return parse_vision((SHOTS / f"{layout}.vision.json").read_text())


def tesseract(layout: str) -> Recognized:
    header, tsv = (SHOTS / f"{layout}.tesseract.tsv").read_text().split("\n", 1)
    scale = int(header.removeprefix("# scale="))
    return parse_tesseract(tsv, 0, 0, scale)


def badge_image(layout: str) -> Image.Image | None:
    path = SHOTS / f"{layout}.png"
    return Image.open(path) if path.exists() else None


# ------------------------------------------------------------- the parser


@pytest.mark.parametrize("engine", ["vision", "tesseract"])
@pytest.mark.parametrize("layout", LAYOUTS)
def test_reads_every_layout(engine: str, layout: str) -> None:
    recognized = vision(layout) if engine == "vision" else tesseract(layout)
    tracks = parse_lines(recognized, badge_image(layout))
    expected = SONGS[:7] if layout == "spotify-mobile" else SONGS
    assert [(t.title, t.artists) for t in tracks] == [
        (title, artists) for title, artists, _ in expected
    ]
    assert [t.position for t in tracks] == list(range(len(expected)))
    if engine == "vision" and layout != "spotify-mobile":  # the phone hides them
        assert [t.duration for t in tracks] == [d for _, _, d in expected]
    if layout != "spotify-mobile":
        assert tracks[0].album == "After Hours"


def test_the_playlist_heading_becomes_the_name() -> None:
    from pixelfm.services.screenshot import guess_name

    assert guess_name(vision("spotify-desktop")) == "Road Trip"
    assert guess_name(vision("youtube-music")) == "Chill Mix"


def _drawn(text: str, *, badge: bool) -> tuple[Image.Image, TextLine]:
    image = Image.new("RGB", (400, 60), (18, 18, 18))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=28)
    x = 10
    if badge:  # Spotify's explicit badge: a filled box with a dark "E"
        draw.rectangle([10, 14, 34, 42], fill=(170, 170, 170))
        draw.text((14, 12), "E", font=font, fill=(18, 18, 18))
        x = 44
    draw.text((x, 12), text, font=font, fill=(200, 200, 200))
    width = int(draw.textlength(text, font=font)) + x - 10
    read = f"El {text}" if badge else text
    return image, TextLine(read, 10, 10, width, 34)


def test_an_explicit_badge_is_not_part_of_the_artist() -> None:
    image, line = _drawn("Billie Eilish", badge=True)
    assert _strip_badges(line, image) == "Billie Eilish"
    image, line = _drawn("El Alfa", badge=False)  # a real word: kept
    assert _strip_badges(line, image) == "El Alfa"
    assert _strip_badges(line, None) == "El Alfa"  # no pixels: never guessed


def test_headers_numbers_dates_and_counts_are_not_songs() -> None:
    def line(text: str, x: float, y: float, h: float = 20) -> TextLine:
        return TextLine(text, x, y, len(text) * 9, h)

    recognized = Recognized(
        1000,
        400,
        (
            line("Liked Songs", 20, 10, 60),
            line("Injora • 2 songs, 7 min", 20, 80),
            line("#", 20, 140),
            line("Title", 60, 140),
            line("Plays", 600, 140),
            line("1", 20, 180),
            line("Song One", 60, 175),
            line("Artist One", 60, 198, 16),
            line("1,234,567", 600, 180),
            line("2 days ago", 750, 180),
            line("2", 20, 240),
            line("Song Two", 60, 235),
            line("Artist Two • Some Album", 60, 258, 16),
            line("•••", 900, 240),
        ),
    )
    tracks = parse_lines(recognized)
    assert [(t.title, t.artists, t.album) for t in tracks] == [
        ("Song One", ("Artist One",), ""),
        ("Song Two", ("Artist Two",), "Some Album"),
    ]


def test_cut_off_titles_and_tesseract_bars_are_cleaned() -> None:
    lines = (
        TextLine("| Want It That Way", 50, 50, 200, 20),
        TextLine("Backstreet Boys", 400, 50, 150, 20),
        TextLine("A Very Long Song Title That Spotify Cu…", 50, 100, 300, 20),
        TextLine("Someone", 400, 100, 80, 20),
    )
    tracks = parse_lines(Recognized(800, 200, lines))
    assert [t.title for t in tracks] == [
        "I Want It That Way",
        "A Very Long Song Title That Spotify Cu",
    ]


class FakeReader:
    name = "fake reader"

    def __init__(self, *results: Recognized) -> None:
        self.results = list(results)
        self.read_paths: list[Path] = []

    def read(self, path: Path) -> Recognized:
        self.read_paths.append(path)
        return self.results[min(len(self.read_paths), len(self.results)) - 1]


def shot(tmp_path: Path, name: str = "shot.png") -> Path:
    path = tmp_path / name
    path.write_bytes(image_bytes())
    return path


def test_overlapping_screenshots_are_merged_without_repeats(tmp_path: Path) -> None:
    whole = vision("apple-music")
    top = Recognized(whole.width, whole.height, whole.lines)
    # A second screenshot, scrolled: its first rows are the last ones above.
    later = [line for line in whole.lines if line.y > 400 or line.text in ("Song",)]
    bottom = Recognized(whole.width, whole.height, tuple(later))
    empty = Recognized(100, 100, ())
    reader = FakeReader(top, bottom, empty)
    paths = [shot(tmp_path, "a.png"), shot(tmp_path, "b.png"), shot(tmp_path, "c.png")]
    playlist, no_songs = read_screenshots(paths, reader)
    assert [t.title for t in playlist.tracks] == [s[0] for s in SONGS]
    assert [t.position for t in playlist.tracks] == list(range(8))
    assert playlist.source == "screenshot" and playlist.name == "Favourites"
    assert no_songs == ["c.png"]
    with pytest.raises(ScreenshotError, match="no file"):
        read_screenshots([tmp_path / "missing.png"], reader)


# ------------------------------------------------------------ paths & engines


def test_dropped_files_become_paths(tmp_path: Path) -> None:
    spaced = shot(tmp_path, "Screenshot 2026-10-21 at 09.27.04.png")
    other = shot(tmp_path, "two.png")
    escaped = str(spaced).replace(" ", "\\ ")
    assert split_paths(escaped) == [spaced]  # Terminal.app
    assert split_paths(f"'{spaced}' '{other}'") == [spaced, other]  # iTerm, quoted
    assert split_paths(str(spaced)) == [spaced]  # pasted without escaping
    assert split_paths(f"file://{other!s}") == [other]
    assert split_paths("") == []
    assert looks_like_images(escaped)
    assert not looks_like_images("https://open.spotify.com/playlist/x")
    assert not looks_like_images(str(tmp_path / "export.csv"))


def test_each_system_gets_its_own_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "pixelfm-ocr"
    with pytest.raises(ScreenshotError, match="build:helper"):
        default_reader("darwin", {"PIXELFM_OCR_HELPER": str(helper)})
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o755)
    reader = default_reader("darwin", {"PIXELFM_OCR_HELPER": str(helper)})
    assert isinstance(reader, VisionReader)
    with pytest.raises(ScreenshotError, match="Windows"):
        default_reader("win32", {})
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(ScreenshotError) as error:
        default_reader("linux", {})
    assert str(error.value) == LINUX_HELP and "tesseract-ocr" in LINUX_HELP
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    assert isinstance(default_reader("linux", {}), TesseractReader)


def script(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\nimport sys\n{body}\n")
    path.chmod(0o755)
    return path


def test_vision_reader_runs_the_helper(tmp_path: Path) -> None:
    answer = (SHOTS / "spotify-mobile.vision.json").read_text()
    (tmp_path / "answer.json").write_text(answer)
    helper = script(
        tmp_path / "pixelfm-ocr",
        "args = sys.argv[1:]\n"
        "if args[0] == '--clipboard':\n"
        "    if 'empty' in args[1]: sys.exit(3)\n"
        "    open(args[1], 'wb').write(b'png'); sys.exit(0)\n"
        "if 'bad' in args[0]:\n"
        "    print('pixelfm-ocr: can\\'t open it as an image', file=sys.stderr)\n"
        "    sys.exit(2)\n"
        f"print(open({str(tmp_path / 'answer.json')!r}).read())\n",
    )
    reader = VisionReader(helper)
    recognized = reader.read(shot(tmp_path))
    assert len(parse_lines(recognized)) == 7
    with pytest.raises(ScreenshotError, match="can't open it as an image"):
        reader.read(tmp_path / "bad.png")
    assert reader.clipboard(tmp_path / "clip.png") is True
    assert (tmp_path / "clip.png").read_bytes() == b"png"
    assert reader.clipboard(tmp_path / "empty.png") is False


def test_tesseract_reader_prepares_dark_screenshots(tmp_path: Path) -> None:
    tsv = (SHOTS / "spotify-mobile.tesseract.tsv").read_text().split("\n", 1)[1]
    (tmp_path / "answer.tsv").write_text(tsv)
    tool = script(
        tmp_path / "tesseract",
        "from PIL import Image, ImageStat\n"
        "image = Image.open(sys.argv[1])\n"
        f"open({str(tmp_path / 'seen')!r}, 'w').write("
        "f'{image.width} {ImageStat.Stat(image).mean[0]:.0f}')\n"
        f"print(open({str(tmp_path / 'answer.tsv')!r}).read())\n",
    )
    dark = tmp_path / "dark.png"
    Image.new("RGB", (390, 844), (18, 18, 18)).save(dark)
    recognized = TesseractReader(str(tool)).read(dark)
    width, mean = (tmp_path / "seen").read_text().split()
    assert width == "780" and int(mean) > 200  # enlarged 2x, made light
    assert recognized.width == 390
    assert [t.title for t in parse_lines(recognized)] == [s[0] for s in SONGS[:7]]
    (tmp_path / "broken.png").write_bytes(b"not an image")
    with pytest.raises(ScreenshotError, match="Couldn't open"):
        TesseractReader(str(tool)).read(tmp_path / "broken.png")


@pytest.mark.skipif(ocr_helper() is None, reason="macOS text-recognition helper")
def test_real_vision_reads_a_screenshot() -> None:
    reader = default_reader()
    playlist, _ = read_screenshots([SHOTS / "spotify-desktop.png"], reader)
    assert [(t.title, t.artists) for t in playlist.tracks] == [
        (title, artists) for title, artists, _ in SONGS
    ]


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract")
def test_real_tesseract_reads_a_screenshot() -> None:
    reader = TesseractReader(str(shutil.which("tesseract")))
    playlist, _ = read_screenshots([SHOTS / "spotify-desktop.png"], reader)
    assert [(t.title, t.artists) for t in playlist.tracks] == [
        (title, artists) for title, artists, _ in SONGS
    ]


# ----------------------------------------------------- same song, library


@pytest.mark.parametrize(
    ("title", "artist", "same"),
    [
        ("Blinding Lights", "The Weeknd", True),
        ("Blinding Lights", "The Weeknd - Topic", True),
        ("The Weeknd - Blinding Lights (Official Video)", "TheWeekndVEVO", True),
        ("Blinding Lights (feat. Nobody)", "The Weeknd", True),
        ("Blinding Lights", "Some Cover Band", False),
        ("The Weeknd - Blinding Lights (Live)", "The Weeknd", False),
        ("Save Your Tears", "The Weeknd", False),
    ],
)
def test_same_song(title: str, artist: str, same: bool) -> None:
    assert same_song("Blinding Lights", ("The Weeknd",), title, artist) is same


def screenshot_playlist(*songs: tuple[str, tuple[str, ...]]) -> SpotifyPlaylist:
    tracks = tuple(
        SpotifyTrack(i, title, artists) for i, (title, artists) in enumerate(songs)
    )
    return SpotifyPlaylist(
        "Shots", total=len(tracks), source="screenshot", tracks=tracks
    )


def test_adding_skips_songs_already_in_the_playlist() -> None:
    library = Library.memory()
    mine = library.create_playlist("Mine")
    library.add_to_playlist(
        mine.id,
        video(1, "The Weeknd - Blinding Lights (Official Video)", "TheWeekndVEVO", 263),
    )
    result = library.add_import(
        mine.id,
        screenshot_playlist(
            ("Blinding Lights", ("The Weeknd",)),
            ("bad guy", ("Billie Eilish",)),
            ("Heat Waves", ("Glass Animals",)),
        ),
    )
    assert (result.added, result.already, result.unusable) == (2, 1, 0)
    titles = [t.song.title for t in library.playlist_tracks(mine.id)]
    assert titles == [
        "The Weeknd - Blinding Lights (Official Video)",
        "bad guy",
        "Heat Waves",
    ]
    info = library.import_info(mine.id)
    assert info is not None and info.source == "screenshot" and info.total == 3
    assert info.counts == {"pending": 2, "duplicate": 1}

    # A second addition continues after the first, and knows its songs too.
    again = library.add_import(
        mine.id,
        screenshot_playlist(
            ("Bad Guy", ("Billie Eilish",)), ("Tum Hi Ho", ("Arijit Singh",))
        ),
    )
    assert (again.added, again.already) == (1, 1)
    items = library.import_items(mine.id)
    assert [i.position for i in items] == list(range(5))
    assert library.import_info(mine.id).total == 5  # type: ignore[union-attr]


def test_a_match_already_in_the_playlist_is_skipped() -> None:
    library = Library.memory()
    mine = library.create_playlist("Mine")
    existing = video(7, "Mr Brightside [HD]", "random uploader", 222)
    library.add_to_playlist(mine.id, existing)
    library.add_import(
        mine.id,
        screenshot_playlist(("Mr. Brightside", ("The Killers",)), ("A", ("B",))),
    )
    # Not the same by name (another uploader), but it is the same video.
    stored = library.set_match(mine.id, 0, existing, status="review", confidence=0.7)
    assert stored == "duplicate"
    ids = [t.song.id for t in library.playlist_tracks(mine.id)]
    assert ids.count(existing.id) == 1 and len(ids) == 2  # placeholder removed
    [first, _] = library.import_items(mine.id)
    assert first.status == "duplicate" and first.note == "already in the playlist"


def test_two_entries_of_one_import_with_one_match_still_go_to_review() -> None:
    library = Library.memory()
    created, _ = library.create_import(
        screenshot_playlist(("Song A", ("X",)), ("Song A (Remastered)", ("X",)))
    )
    match = video(3, "Song A", "X - Topic", 200)
    assert library.set_match(created.id, 0, match, status="matched") == "matched"
    assert library.set_match(created.id, 1, match, status="matched") == "unmatched"


def test_rows_typed_in_review() -> None:
    assert parse_row("Song — A, B") == ("Song", ("A", "B"))
    assert parse_row("Song - With - Dashes - Artist") == (
        "Song - With - Dashes",
        ("Artist",),
    )
    assert parse_row("Just a title") == ("Just a title", ())
    assert parse_row("  ") is None


# ----------------------------------------------------------------- the app


class Tube:
    """Fake YouTube: the official recording of whatever is asked for, except
    Mr. Brightside, which is only an upload the playlist already has."""

    name = "youtube"
    browsable = False

    def __init__(self, brightside: Song) -> None:
        self.brightside = brightside
        self.queries: list[str] = []

    async def search(self, query: str, limit: int = 20) -> list[Song]:
        self.queries.append(query)
        n = 500 + len(self.queries)  # before awaiting: searches run in parallel
        await asyncio.sleep(0)
        artist, _, title = query.partition(" - ")
        if "Brightside" in title:
            return [self.brightside]
        duration = next((d for t, _, d in SONGS if t.startswith(title[:8])), 200)
        return [video(n, title, f"{artist} - Topic", duration)]


async def pick(app, pilot, option_id: str) -> None:  # type: ignore[no-untyped-def]
    options = app.screen.query_one(OptionList)
    options.highlighted = options.get_option_index(option_id)
    await pilot.press("enter")


async def test_screenshot_adds_new_songs_to_an_existing_playlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = Library.open(tmp_path / "library.sqlite3")
    trip = library.create_playlist("Road Trip")
    brightside = video(7, "Mr Brightside [HD]", "random uploader", 222)
    for song in (
        video(1, "The Weeknd - Blinding Lights (Official Video)", "TheWeekndVEVO", 263),
        video(2, "Heat Waves", "Glass Animals - Topic", 238),
        brightside,
    ):
        library.add_to_playlist(trip.id, song)
    reader = FakeReader(vision("spotify-desktop"))
    monkeypatch.setattr("pixelfm.ui.importing.default_reader", lambda: reader)
    youtube = Tube(brightside)
    app = make_app(library, youtube, FakeSpotify())  # type: ignore[arg-type]
    app._matcher_instance = Matcher(youtube.search, pause=0)
    path = shot(tmp_path, "Screenshot 2026-10-21 at 09.27.04.png")
    messages: list[str] = []
    show = app._status

    def record(message: str, *args: object, **kwargs: object) -> None:
        messages.append(message)
        show(message, *args, **kwargs)  # type: ignore[arg-type]

    app._status = record  # type: ignore[method-assign]
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("4", "I")
        await pilot.pause()
        app.screen.query_one(Input).value = str(path).replace(" ", "\\ ")
        await pilot.press("enter")
        await until(lambda: isinstance(app.screen, ScreenshotReview), pilot)
        assert reader.read_paths == [path]
        rows = app.screen.query_one(OptionList)
        assert rows.option_count == 8
        rows.highlighted = 7
        await pilot.press("space")  # leave out Sweater Weather
        await pilot.press("enter")
        await until(lambda: isinstance(app.screen, ChoicePicker), pilot)
        await pick(app, pilot, str(trip.id))
        await until(
            lambda: app._matching is None and "Matching done" in status(app), pilot
        )
        heading = str(app.query_one("#playlist-heading").render())
        assert "screenshot:" in heading
        await pilot.press("q")

    assert any(
        m.startswith("Added 5 songs to “Road Trip”; 2 already in it (skipped).")
        for m in messages
    )
    titles = [t.song.title for t in library.playlist_tracks(trip.id)]
    assert titles == [
        "The Weeknd - Blinding Lights (Official Video)",
        "Heat Waves",
        "Mr Brightside [HD]",
        "Levitating (feat. DaBaby)",
        "bad guy",
        "Tum Hi Ho",
        "Don't Start Now",
    ]
    info = library.import_info(trip.id)
    assert info is not None and info.total == 7
    assert info.counts == {"matched": 4, "duplicate": 3}
    queries = " | ".join(youtube.queries)
    assert "Blinding Lights" not in queries and "Heat Waves" not in queries


async def test_a_clipboard_screenshot_becomes_a_new_playlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = Library.memory()
    library.create_playlist("Road Trip")  # the name is taken: no overwrite
    reader = FakeReader(vision("spotify-mobile"))
    pasted: list[bool] = [False]

    def clipboard(_reader: object, target: Path) -> bool:
        if not pasted[0]:
            return False
        target.write_bytes(image_bytes())
        return True

    monkeypatch.setattr("pixelfm.ui.importing.default_reader", lambda: reader)
    monkeypatch.setattr("pixelfm.ui.importing.clipboard_image", clipboard)
    youtube = Tube(video(7, "x", "y", 1))
    app = make_app(library, youtube, FakeSpotify())  # type: ignore[arg-type]
    app._matcher_instance = Matcher(youtube.search, pause=0)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("4", "I")
        await pilot.pause()
        await pilot.press("enter")  # empty: the clipboard, which has no image
        await until(lambda: "no image on the clipboard" in status(app), pilot)

        pasted[0] = True
        await pilot.press("I")
        await pilot.pause()
        await pilot.press("enter")
        await until(lambda: isinstance(app.screen, ScreenshotReview), pilot)
        await pilot.press("enter")
        await until(lambda: isinstance(app.screen, ChoicePicker), pilot)
        await pick(app, pilot, "new")
        await until(lambda: isinstance(app.screen, TextPrompt), pilot)
        assert app.screen.query_one(Input).value == "Road Trip"  # its heading
        await pilot.press("enter")
        await until(
            lambda: app._matching is None and "Matching done" in status(app), pilot
        )
        await pilot.press("q")
    created = next(p for p in library.playlists() if p.name == "Road Trip (2)")
    assert created.count == 7
    info = library.import_info(created.id)
    assert info is not None and info.source == "screenshot"
    assert (info.matched, info.unmatched) == (6, 1)  # no Mr. Brightside on "YouTube"


async def test_a_missing_reader_says_how_to_get_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def missing() -> None:
        raise ScreenshotError(LINUX_HELP)

    monkeypatch.setattr("pixelfm.ui.importing.default_reader", missing)
    app = make_app(Library.memory(), Tube(video(1, "x", "y", 1)), FakeSpotify())  # type: ignore[arg-type]
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("4", "I")
        await pilot.pause()
        app.screen.query_one(Input).value = str(shot(tmp_path))
        await pilot.press("enter")
        await until(lambda: "needs Tesseract" in status(app), pilot)
        await pilot.press("q")
