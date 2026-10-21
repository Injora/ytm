"""Artwork-derived palettes, the dynamic theme, and Party Mode."""

from __future__ import annotations

import asyncio
import io
import itertools
import random
import threading
import time
from collections.abc import Callable

import pytest
from PIL import Image, ImageDraw

from pixelfm.app import PixelFMApp
from pixelfm.config import Config
from pixelfm.models import Song
from pixelfm.services.artwork import ArtworkService
from pixelfm.services.library import Library
from pixelfm.services.metering import StereoLevel
from pixelfm.services.palette import (
    DEFAULT_PALETTE,
    FOREGROUND_CONTRAST,
    TEXT_CONTRAST,
    PaletteService,
    contrast,
    extract_palette,
    hls,
    hue_distance,
    luminance,
)
from pixelfm.services.party import (
    MAX_FLASHES_PER_SECOND,
    MAX_FPS,
    NEON,
    THEME_STEP_SECONDS,
    PartyLights,
    PartyMode,
)
from pixelfm.services.playback import SimulatedPlayer
from pixelfm.state import PlaybackStatus
from pixelfm.ui.theming import DYNAMIC_THEME, PARTY_PANELS
from pixelfm.widgets import BoothHeader, LightBar, SettingsList, StatusBar
from tests.conftest import make_song

S = PlaybackStatus

# --------------------------------------------------------------- artwork

ARTS: dict[str, tuple[tuple[int, int, int], list[tuple[tuple, tuple]]]] = {
    "blue": (
        (110, 115, 125),
        [((10, 10, 70, 70), (30, 80, 220)), ((80, 20, 110, 80), (70, 80, 95))],
    ),
    "red": ((150, 20, 25), [((30, 10, 100, 60), (240, 140, 20))]),
    "purple": (
        (70, 20, 110),
        [((20, 20, 80, 70), (150, 60, 200)), ((90, 10, 115, 40), (180, 60, 140))],
    ),
    "cyan": (
        (10, 150, 160),
        [((20, 20, 60, 60), (150, 220, 235)), ((70, 40, 110, 85), (10, 70, 80))],
    ),
    "mono": ((30, 30, 30), [((20, 20, 90, 70), (200, 200, 200))]),
}


def art(name: str) -> bytes:
    background, blobs = ARTS[name]
    image = Image.new("RGB", (120, 90), background)
    draw = ImageDraw.Draw(image)
    for box, colour in blobs:
        draw.rectangle(box, fill=colour)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def solid(rgb: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), rgb).save(buffer, "PNG")
    return buffer.getvalue()


def song_with_art(n: int, name: str) -> Song:
    base = make_song(n)
    return Song(
        id=base.id,
        title=base.title,
        artist=base.artist,
        duration=base.duration,
        source_url=base.source_url,
        thumbnail_url=f"https://example.com/art/{name}.png",
    )


def fetch_art(url: str) -> bytes:
    return art(url.rsplit("/", 1)[1].removesuffix(".png"))


def hue_deg(rgb: tuple[int, int, int]) -> float:
    return hls(rgb)[0] * 360


# ------------------------------------------------------------- extraction


def test_blue_grey_artwork_gives_blue_and_steel() -> None:
    palette = extract_palette(art("blue"))
    assert palette.source == "artwork"
    assert 200 <= hue_deg(palette.primary) <= 250  # electric blue
    assert hls(palette.secondary)[2] < 0.3  # steel/slate: low saturation
    assert 200 <= hue_deg(palette.background) <= 250  # tinted, not neutral


def test_red_orange_artwork_gives_crimson_and_amber() -> None:
    palette = extract_palette(art("red"))
    hues = sorted([hue_deg(palette.primary), hue_deg(palette.secondary)])
    assert hues[0] < 15 or hues[1] > 345  # crimson
    assert any(20 <= h <= 50 for h in hues)  # amber


def test_purple_and_cyan_artwork() -> None:
    assert 260 <= hue_deg(extract_palette(art("purple")).primary) <= 300
    assert 170 <= hue_deg(extract_palette(art("cyan")).primary) <= 200


def test_monochrome_artwork_gives_silver_white_graphite() -> None:
    palette = extract_palette(art("mono"))
    assert palette.source == "monochrome"
    for rgb in (palette.primary, palette.secondary, palette.background):
        assert hls(rgb)[2] < 0.1  # no invented colour
    assert hls(palette.background)[1] < 0.1


def test_different_artwork_gives_different_palettes() -> None:
    palettes = {name: extract_palette(art(name)) for name in ARTS}
    primaries = [p.primary for p in palettes.values()]
    assert len(set(primaries)) == len(primaries)
    blue, red = palettes["blue"], palettes["red"]
    assert hue_distance(hls(blue.primary)[0], hls(red.primary)[0]) > 0.3


def test_not_the_dominant_colour_everywhere() -> None:
    palette = extract_palette(art("red"))
    assert palette.background != palette.primary
    assert hls(palette.background)[1] < 0.1  # dark background
    assert hls(palette.foreground)[1] > 0.85  # light text
    assert palette.primary != palette.secondary


@pytest.mark.parametrize("seed", range(25))
def test_every_text_role_is_readable(seed: int) -> None:
    rng = random.Random(seed)
    image = Image.new("RGB", (60, 40), tuple(rng.randrange(256) for _ in range(3)))
    draw = ImageDraw.Draw(image)
    for _ in range(4):
        x, y = rng.randrange(50), rng.randrange(30)
        colour = tuple(rng.randrange(256) for _ in range(3))
        draw.rectangle((x, y, x + 15, y + 12), fill=colour)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    palette = extract_palette(buffer.getvalue())
    bg = palette.background
    for role in ("primary", "secondary", "muted"):
        assert contrast(getattr(palette, role), bg) >= TEXT_CONTRAST, role
    assert contrast(palette.foreground, bg) >= FOREGROUND_CONTRAST
    # Selected rows: background-coloured text on the accent.
    assert contrast(bg, palette.primary) >= TEXT_CONTRAST


@pytest.mark.parametrize(
    "rgb", [(255, 255, 255), (0, 0, 0), (255, 255, 0), (0, 0, 255), (20, 0, 40)]
)
def test_extreme_artwork_stays_readable(rgb: tuple[int, int, int]) -> None:
    palette = extract_palette(solid(rgb))
    assert contrast(palette.primary, palette.background) >= TEXT_CONTRAST
    assert contrast(palette.foreground, palette.background) >= FOREGROUND_CONTRAST


def test_default_palette_is_readable() -> None:
    p = DEFAULT_PALETTE
    for role in ("primary", "secondary", "muted", "foreground"):
        assert contrast(getattr(p, role), p.background) >= TEXT_CONTRAST


# ---------------------------------------------------------- the service


async def test_missing_and_broken_artwork_fall_back_safely() -> None:
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        if "offline" in url:
            raise OSError("network down")
        return b"not an image"

    service = PaletteService(ArtworkService(fetch))
    assert await service.palette_for(None) == DEFAULT_PALETTE
    assert await service.palette_for(make_song(1, thumbnail=False)) == DEFAULT_PALETTE
    broken = song_with_art(2, "broken")
    assert await service.palette_for(broken) == DEFAULT_PALETTE
    offline = song_with_art(3, "offline")
    assert await service.palette_for(offline) == DEFAULT_PALETTE
    await service.palette_for(broken)
    await service.palette_for(offline)
    # Unreadable images are cached; network failures are retried later.
    assert calls.count("https://example.com/art/broken.png") == 1
    assert calls.count("https://example.com/art/offline.png") == 2


async def test_palettes_are_cached_per_artwork() -> None:
    fetches: list[str] = []

    def fetch(url: str) -> bytes:
        fetches.append(url)
        return fetch_art(url)

    service = PaletteService(ArtworkService(fetch))
    a, b = song_with_art(1, "blue"), song_with_art(2, "blue")  # same artwork URL
    first = await service.palette_for(a)
    assert await service.palette_for(b) is first
    assert service.extractions == 1 and len(fetches) == 1


async def test_extraction_runs_off_the_event_loop() -> None:
    gate = threading.Event()

    def slow_fetch(url: str) -> bytes:
        gate.wait(2)  # a slow network (or decode) in the worker thread
        return art("red")

    service = PaletteService(ArtworkService(slow_fetch))
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while not gate.is_set():
            ticks += 1
            await asyncio.sleep(0.01)
            if ticks == 10:
                gate.set()

    palette, _ = await asyncio.gather(
        service.palette_for(song_with_art(1, "red")), ticker()
    )
    assert ticks >= 10  # the loop kept running while extraction waited
    assert palette.source == "artwork"


# ------------------------------------------------------------- the app

SONGS = [
    song_with_art(1, "blue"),
    song_with_art(2, "red"),
    make_song(3, thumbnail=False),
]


class Provider:
    name = "youtube"
    browsable = False

    async def search(self, query: str, limit: int = 20) -> list[Song]:
        return SONGS


def make_app(**kwargs) -> PixelFMApp:  # type: ignore[no-untyped-def]
    options = dict(
        config=Config(),
        provider=Provider(),
        artwork=ArtworkService(fetch_art),
        player=SimulatedPlayer(load_delay=0),
        library=Library.memory(),
        unicode=True,
        truecolor=True,
        welcome=False,
        effects=True,
    )
    options.update(kwargs)
    return PixelFMApp(**options)  # type: ignore[arg-type]


async def until(predicate: Callable[[], bool], pilot, limit: float = 3.0) -> None:  # type: ignore[no-untyped-def]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while not predicate():
        assert loop.time() < deadline, "condition not reached"
        await pilot.pause(0.02)


def primary(app: PixelFMApp) -> str:
    return str(app.current_theme.primary)


async def test_theme_follows_the_current_track_with_a_crossfade() -> None:
    app = make_app()
    painted: list[str] = []
    async with app.run_test(size=(140, 42)) as pilot:
        original = app._paint
        app._paint = lambda p: (painted.append(p.hex("primary")), original(p))[1]  # type: ignore[method-assign]
        assert app.theme == DYNAMIC_THEME
        assert primary(app) == DEFAULT_PALETTE.hex("primary")  # nothing playing
        blue = extract_palette(art("blue"))
        app._play_entry(app.queue.play_now(SONGS[0]))
        await until(lambda: primary(app) == blue.hex("primary"), pilot)
        assert len(painted) >= 3  # several intermediate steps, not one jump
        assert len(set(painted)) >= 3
        red = extract_palette(art("red"))
        app._play_entry(app.queue.play_now(SONGS[1]))
        await until(lambda: primary(app) == red.hex("primary"), pilot)
        # Every view uses the theme: the tab strip, tables, borders, buttons.
        css = app.get_css_variables()["primary"]  # Textual rounds via HSL
        rgb = tuple(int(css[i : i + 2], 16) for i in (1, 3, 5))
        assert all(abs(a - b) <= 2 for a, b in zip(rgb, red.primary, strict=True))
        app._play_entry(app.queue.play_now(SONGS[2]))  # no artwork
        await until(lambda: primary(app) == DEFAULT_PALETTE.hex("primary"), pilot)


async def test_reduced_effects_switch_palettes_without_animation() -> None:
    app = make_app(effects=False)
    async with app.run_test(size=(140, 42)) as pilot:
        steps: list[str] = []
        original = app._paint
        app._paint = lambda p: (steps.append(p.hex("primary")), original(p))[1]  # type: ignore[method-assign]
        app._play_entry(app.queue.play_now(SONGS[0]))
        blue = extract_palette(art("blue")).hex("primary")
        await until(lambda: primary(app) == blue, pilot)
        assert steps == [blue]


async def test_static_theme_choice_is_respected() -> None:
    library = Library.memory()
    library.set_pref("theme", "pixelfm-phosphor")
    app = make_app(library=library)
    async with app.run_test(size=(140, 42)) as pilot:
        app._play_entry(app.queue.play_now(SONGS[1]))
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        await pilot.pause(0.5)
        assert app.theme == "pixelfm-phosphor"


async def test_no_color_keeps_the_default_palette(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    app = make_app()
    async with app.run_test(size=(140, 42)) as pilot:
        app._play_entry(app.queue.play_now(SONGS[1]))
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        await pilot.pause(0.5)
        assert app._palette_now == DEFAULT_PALETTE
        await pilot.press("P")
        assert "NO_COLOR" in app.query_one(StatusBar).message
        assert app._party is None


# ------------------------------------------------------- party engine


def frames(lights: PartyLights, seconds: float, loudness=None):  # type: ignore[no-untyped-def]
    step = 1 / lights.fps
    return [
        lights.frame(i * step, loudness(i) if loudness else None)
        for i in range(int(seconds * lights.fps))
    ]


@pytest.mark.parametrize("mode", [PartyMode.AMBIENT, PartyMode.PULSE, PartyMode.STROBE])
def test_party_frame_rate_and_brightness_limits(mode: PartyMode) -> None:
    lights = PartyLights(mode)
    assert lights.fps <= MAX_FPS
    brightest = max(luminance(c) for c in NEON)
    for frame in frames(lights, 20, lambda i: -10.0 if i % 3 else -40.0):
        colours = (*frame.panels, *frame.bar, frame.accent, frame.accent2)
        for colour in (*colours, *frame.theme_colours):
            assert luminance(colour) <= brightest + 1e-6  # never a white flash


def test_strobe_never_exceeds_the_flash_limit() -> None:
    lights = PartyLights(PartyMode.STROBE)
    # Worst case: a loud onset on every other frame for 30 seconds.
    starts = []
    previous = False
    for i, frame in enumerate(frames(lights, 30, lambda i: -5.0 if i % 2 else -45.0)):
        if frame.flash and not previous:
            starts.append(i / lights.fps)
        previous = frame.flash
    assert starts
    for t in starts:
        window = [s for s in starts if t <= s < t + 1.0]
        assert len(window) <= MAX_FLASHES_PER_SECOND


def test_strobe_without_measurements_is_a_calm_one_hertz() -> None:
    lights = PartyLights(PartyMode.STROBE)
    result = frames(lights, 10)
    starts = [
        i for i in range(1, len(result)) if result[i].flash and not result[i - 1].flash
    ]
    assert 8 <= len(starts) + int(result[0].flash) <= 11
    assert not any(f.reactive for f in result)


def test_pulse_reacts_only_to_measured_loudness() -> None:
    quiet = PartyLights(PartyMode.PULSE)
    unmeasured = frames(quiet, 6)
    assert not any(f.reactive for f in unmeasured)
    late = [f.intensity for f in unmeasured[-10:]]
    assert max(late) - min(late) < 0.01  # steady: no invented beat
    loud = PartyLights(PartyMode.PULSE)
    measured = frames(loud, 6, lambda i: -6.0 if (i // 4) % 2 else -40.0)
    assert all(f.reactive for f in measured)
    values = [f.intensity for f in measured[8:]]
    assert max(values) - min(values) > 0.3  # follows the real level


def test_theme_colours_change_slowly_and_patterns_vary() -> None:
    lights = PartyLights(PartyMode.AMBIENT)
    result = frames(lights, 60)
    changes = [
        i / lights.fps
        for i in range(1, len(result))
        if result[i].theme_colours != result[i - 1].theme_colours
    ]
    for a, b in itertools.pairwise(changes):
        assert b - a >= THEME_STEP_SECONDS - 1 / lights.fps
    assert len({f.scene for f in result}) >= 4  # several scenes/patterns
    assert len({f.panels for f in result}) > 20  # lights actually move


# ---------------------------------------------------------- party in the app


async def test_party_toggle_keeps_queue_and_track() -> None:
    app = make_app()
    async with app.run_test(size=(140, 42)) as pilot:
        app._play_entry(app.queue.play_now(SONGS[0]))
        app.queue.add(SONGS[1])
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        entries = app.queue.entries
        header = app.query_one(BoothHeader)
        assert "PARTY MODE: OFF" in str(header.query_one("#party-toggle").label)

        await pilot.press("P")
        assert app._party is not None and app.screen.has_class("-party")
        assert "PARTY MODE: ON · AMBIENT" in str(
            header.query_one("#party-toggle").label
        )
        await pilot.pause(0.6)
        deck = app.query_one("#deck")
        assert deck.styles.inline.has_rule("border_top")  # lights on the panels
        assert app.query_one(LightBar).colours
        assert app.queue.entries == entries
        assert app.player.get_state().song == SONGS[0]
        assert app.player.get_state().status is S.PLAYING

        await pilot.click("#party-toggle")  # the header button turns it off
        assert app._party is None and not app.screen.has_class("-party")
        for selector in PARTY_PANELS:
            assert not app.query_one(selector).styles.inline.has_rule("border_top")
        assert app.query_one(LightBar).colours == ()
        assert app.queue.entries == entries
        assert app.player.get_state().song == SONGS[0]


async def test_party_refresh_frequency_and_background() -> None:
    app = make_app()
    async with app.run_test(size=(140, 42)) as pilot:
        app._play_entry(app.queue.play_now(SONGS[0]))
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        await pilot.pause(1.2)  # let the palette crossfade finish
        background = app.get_css_variables()["background"]
        ticks: list[float] = []
        paints: list[float] = []
        tick, paint = app._party_tick, app._paint
        app._party_tick = lambda: (ticks.append(time.monotonic()), tick())[1]  # type: ignore[method-assign]
        app._paint = lambda p: (paints.append(time.monotonic()), paint(p))[1]  # type: ignore[method-assign]
        await pilot.press("P")
        start = time.monotonic()
        await pilot.pause(3.0)
        elapsed = time.monotonic() - start
        assert app._party_timer is not None
        assert len(ticks) <= MAX_FPS * elapsed + 2  # bounded frame rate
        assert len(paints) <= elapsed / THEME_STEP_SECONDS + 2  # rare restyles
        assert app.get_css_variables()["background"] == background  # never flashes


async def test_party_styles_from_settings_are_saved() -> None:
    library = Library.memory()
    app = make_app(library=library)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("8")
        settings = app.query_one(SettingsList)
        settings.highlighted = settings.get_option_index("party")
        seen = []
        for _ in range(5):
            await pilot.press("enter")
            await pilot.pause()
            seen.append(None if app._party is None else str(app._party.mode))
        assert seen == ["ambient", "pulse", "strobe", "rave", None]
        assert library.get_pref("party") is False
        assert library.get_pref("party_mode") == "rave"


async def test_party_preference_restores_and_exit_stops_it() -> None:
    library = Library.memory()
    library.set_pref("party", True)
    library.set_pref("party_mode", "pulse")
    app = make_app(library=library)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.3)
        assert app._party is not None and app._party.mode is PartyMode.PULSE
        label = str(app.query_one("#party-toggle").label)
        assert "PULSE" in label and "NO SIGNAL" in label  # nothing measured
        await pilot.press("q")
    assert not app.is_running
    assert app._party is None and app._party_timer is None
    assert library.get_pref("party") is True  # the preference itself is kept


async def test_pulse_uses_real_measurements_when_available() -> None:
    class MeasuringPlayer(SimulatedPlayer):
        name = "measuring"

        def stereo_level(self) -> StereoLevel | None:
            return StereoLevel(0.0, -12.0, -12.0, -3.0, -3.0)

    library = Library.memory()
    library.set_pref("party_mode", "pulse")
    app = make_app(library=library, player=MeasuringPlayer(load_delay=0))
    async with app.run_test(size=(140, 42)) as pilot:
        app._play_entry(app.queue.play_now(SONGS[0]))
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        await pilot.press("P")
        await pilot.pause(0.5)
        assert "NO SIGNAL" not in str(app.query_one("#party-toggle").label)


async def test_reduced_effects_refuse_party_mode() -> None:
    app = make_app(effects=False)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("P")
        assert app._party is None
        assert "Party Mode needs effects" in app.query_one(StatusBar).message
