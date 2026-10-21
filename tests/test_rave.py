"""RAVE mode: the fast light rig, its safety limits, and its app controls."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable
from dataclasses import fields

import pytest

from pixelfm.services.library import Library
from pixelfm.services.metering import StereoLevel
from pixelfm.services.palette import contrast, extract_palette, luminance
from pixelfm.services.party import (
    DANCE_PALETTES,
    NEON,
    RAVE_FPS,
    RAVE_MAX_FPS,
    RAVE_MAX_SWAPS_PER_SECOND,
    PartyLights,
    PartyMode,
    RaveFrame,
    RaveLights,
    RavePattern,
)
from pixelfm.services.playback import SimulatedPlayer
from pixelfm.state import PlaybackStatus
from pixelfm.widgets import BoothHeader, LightBar, StatusBar, Timeline, ViewTabs
from tests.test_theming import SONGS, art, make_app

S = PlaybackStatus
POSITIONS = [i / 10 for i in range(10)]


def run(lights: RaveLights, seconds: float, loudness=None) -> list[RaveFrame]:  # type: ignore[no-untyped-def]
    step = 1 / lights.fps
    return [
        lights.frame(i * step, loudness(i) if loudness else None, POSITIONS, 0.5)
        for i in range(int(seconds * lights.fps))
    ]


def colour_speed(colours: list[tuple[int, int, int]], fps: float) -> float:
    """How far a region's colour travels per second (RGB distance)."""
    moved = sum(
        sum(abs(x - y) for x, y in zip(a, b, strict=True))
        for a, b in itertools.pairwise(colours)
    )
    return moved / (len(colours) / fps)


# ------------------------------------------------------------- engine


def test_rave_runs_at_a_high_but_bounded_frame_rate() -> None:
    assert 15 <= RaveLights().fps == RAVE_FPS <= RAVE_MAX_FPS


@pytest.mark.parametrize(
    "pattern", [RavePattern.CHASE, RavePattern.RAINBOW, RavePattern.LASER]
)
def test_rave_colours_change_much_faster_than_ambient(pattern: RavePattern) -> None:
    rave = run(RaveLights(pattern), 4)
    ambient = PartyLights(PartyMode.AMBIENT)
    old = [ambient.frame(i / ambient.fps).panels[0] for i in range(4 * ambient.fps)]
    for region in (2, 5, 8):
        fast = colour_speed([f.zones[region] for f in rave], RAVE_FPS)
        assert fast > 4 * colour_speed(old, ambient.fps)
    strip = [f.strip[10] for f in rave]
    assert sum(a != b for a, b in itertools.pairwise(strip)) / 4 >= 5  # 5+/s


def test_regions_are_offset_but_share_one_clock() -> None:
    lights = RaveLights(RavePattern.CHASE)
    frame = lights.frame(1.0, None, POSITIONS)
    assert len(set(frame.zones)) > 3  # not all regions in unison
    # Coordinated: the chase travels. A region later on shows, a moment
    # later, the colour an earlier region shows now.
    a = RaveLights(RavePattern.RAINBOW).frame(2.0, None, [0.2]).zones[0]
    b = RaveLights(RavePattern.RAINBOW).frame(2.0 + 0.1 / 0.45, None, [0.3]).zones[0]
    assert a == b


def test_dance_floor_swaps_are_rate_limited_and_never_red() -> None:
    lights = RaveLights(RavePattern.DANCE)
    frames = run(lights, 20)
    assert frames[-1].swaps <= RAVE_MAX_SWAPS_PER_SECOND * 20
    swap_times: list[float] = []
    previous = frames[0].zones
    for i, frame in enumerate(frames[1:], start=1):
        if frame.zones != previous:
            swap_times.append(i / RAVE_FPS)
        previous = frame.zones
    for t in swap_times:
        assert (
            len([x for x in swap_times if t <= x < t + 1]) <= RAVE_MAX_SWAPS_PER_SECOND
        )
    allowed = {c for pair in DANCE_PALETTES for c in pair}
    assert NEON[4] not in allowed  # red never alternates


@pytest.mark.parametrize("pattern", list(RavePattern))
def test_never_brighter_than_the_neon_set(pattern: RavePattern) -> None:
    brightest = max(luminance(c) for c in NEON)
    for frame in run(RaveLights(pattern), 17, lambda i: -4.0 if i % 2 else -50.0):
        for colour in (*frame.zones, *frame.strip, *frame.progress, *frame.meter):
            assert luminance(colour) <= brightest + 1e-6  # no white flashes


def test_bass_pulse_uses_measurements_only() -> None:
    quiet = run(RaveLights(RavePattern.BASS), 4)
    assert not any(f.reactive for f in quiet)
    tail = [f.intensity for f in quiet[-16:]]
    assert max(tail) - min(tail) < 0.01  # steady: no invented beat
    loud = run(RaveLights(RavePattern.BASS), 4, lambda i: -6.0 if (i // 3) % 2 else -42)
    assert all(f.reactive for f in loud)
    values = [f.intensity for f in loud[6:]]
    assert max(values) - min(values) > 0.4
    names = {f.name for f in fields(RaveFrame)}
    assert not names & {"bpm", "beat", "tempo", "key"}  # nothing fabricated


def test_auto_rotates_patterns_with_a_sweep() -> None:
    lights = RaveLights(RavePattern.AUTO)
    seen = {f.pattern for f in run(lights, 41)}
    assert seen == set(RavePattern) - {RavePattern.AUTO}


def test_progress_colours_follow_playback() -> None:
    lights = RaveLights(RavePattern.CHASE)
    start = lights.frame(1, None, [], 0.0).progress
    later = lights.frame(1, None, [], 0.5).progress
    assert start != later


# ---------------------------------------------------------------- app


async def until(predicate: Callable[[], bool], pilot, limit: float = 3.0) -> None:  # type: ignore[no-untyped-def]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while not predicate():
        assert loop.time() < deadline, "condition not reached"
        await pilot.pause(0.02)


def running_party_timers(app) -> int:  # type: ignore[no-untyped-def]
    return sum(
        1
        for timer in app._timers
        if getattr(timer._callback, "__name__", "") == "_party_tick"
        and timer._task is not None
        and not timer._task.done()
    )


async def test_r_starts_rave_cycles_patterns_and_p_stops_it() -> None:
    library = Library.memory()
    app = make_app(library=library)
    async with app.run_test(size=(140, 42)) as pilot:
        app._play_entry(app.queue.play_now(SONGS[0]))
        app.queue.add(SONGS[1])
        await pilot.press("r", "z")  # repeat all, shuffle on
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        await pilot.pause(1.2)  # the artwork palette has crossfaded in
        before = (app.queue.entries, app.queue.repeat, app.queue.shuffle)
        artwork_primary = app._palette_target.hex("primary")

        await pilot.press("R")  # immediately on
        assert isinstance(app._party, RaveLights)
        assert app.screen.has_class("-rave")
        label = str(app.query_one("#party-toggle").label)
        assert label.startswith("RAVE MODE: ON")
        await pilot.pause(0.5)
        assert app.query_one("#top-lights", LightBar).colours
        assert app.query_one(Timeline).gradient
        assert app.query_one(ViewTabs).accent is not None
        signal = app.query_one("#signal").styles.border_top[1]
        mixer = app.query_one("#mixer").styles.border_top[1]
        assert signal != mixer  # offset regions, one rig
        assert running_party_timers(app) == 1

        await pilot.press("R")  # next pattern, same single timer
        assert app._rave_pattern is RavePattern.CHASE
        assert library.get_pref("rave_pattern") == "chase"
        await pilot.press("R", "R", "R")
        await pilot.pause(0.2)
        assert running_party_timers(app) == 1
        state = app.player.get_state()
        assert state.status is S.PLAYING and state.song == SONGS[0]
        assert (app.queue.entries, app.queue.repeat, app.queue.shuffle) == before

        await pilot.press("P")  # immediately off
        assert app._party is None and not app.screen.has_class("-rave")
        assert running_party_timers(app) == 0
        assert not app.query_one("#signal").styles.inline.has_rule("border_top")
        assert not app.query_one("Footer").styles.inline.has_rule("background")
        assert app.query_one("#top-lights", LightBar).colours == ()
        assert app.query_one(Timeline).gradient == ()
        # The artwork's palette comes back (not a fixed green or cyan theme).
        await until(lambda: app._palette_now.hex("primary") == artwork_primary, pilot)
        assert (app.queue.entries, app.queue.repeat, app.queue.shuffle) == before
        assert app.player.get_state().song == SONGS[0]


async def test_rave_lights_every_view_and_keeps_focus_visible() -> None:
    app = make_app()
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("R")
        for key, panel in (
            ("2", "#results"),
            ("4", "#playlist-nav"),
            ("8", "#settings"),
        ):
            await pilot.press(key)
            await pilot.pause(0.4)
            assert app.query_one(panel).styles.inline.has_rule("border_top"), panel
            assert app.query_one("#now-playing").styles.inline.has_rule("border_top")
        await pilot.press("2")
        app.query_one("#results").focus()
        await pilot.pause(0.4)
        assert app.query_one("#results").styles.border_top[0] == "heavy"
        assert app.query_one("#queue").styles.border_top[0] == "solid"
        assert app.query_one(StatusBar).styles.inline.has_rule("background")
        header = app.query_one(BoothHeader)
        assert header._logo_colours  # the logo carries the lights


async def test_text_stays_readable_during_rave() -> None:
    app = make_app()
    async with app.run_test(size=(140, 42)) as pilot:
        app._play_entry(app.queue.play_now(SONGS[1]))
        await pilot.press("R")
        await pilot.pause(2.5)  # at least two whole-theme accent steps
        palette = app._palette_now
        assert contrast(palette.primary, palette.background) >= 4.5
        assert contrast(palette.secondary, palette.background) >= 4.5
        assert palette.background == extract_palette(art("red")).background


async def test_bass_pulse_says_no_signal_without_measurements() -> None:
    library = Library.memory()
    library.set_pref("rave_pattern", "bass")
    app = make_app(library=library)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("R")
        await pilot.pause(0.3)
        assert "NO SIGNAL" in str(app.query_one("#party-toggle").label)

    class Measuring(SimulatedPlayer):
        name = "measuring"

        def stereo_level(self) -> StereoLevel | None:
            return StereoLevel(0.0, -10.0, -10.0, -2.0, -2.0)

    app = make_app(library=library, player=Measuring(load_delay=0))
    async with app.run_test(size=(140, 42)) as pilot:
        app._play_entry(app.queue.play_now(SONGS[0]))
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        await pilot.press("R")
        await pilot.pause(0.3)
        assert "NO SIGNAL" not in str(app.query_one("#party-toggle").label)


async def test_reduced_effects_and_no_color_refuse_rave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = make_app(effects=False)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("R")
        assert app._party is None
        assert "needs effects" in app.query_one(StatusBar).message
    monkeypatch.setenv("NO_COLOR", "1")
    app = make_app()
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("R")
        assert app._party is None


async def test_shutdown_and_resize_leave_no_extra_timers() -> None:
    app = make_app()
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("R")
        for size in ((100, 30), (90, 26), (160, 50)):
            await pilot.resize_terminal(*size)
            await pilot.pause(0.2)
        assert running_party_timers(app) == 1
        await pilot.press("q")
    assert app._party is None and app._party_timer is None
    assert running_party_timers(app) == 0


async def test_rave_is_documented() -> None:
    from pixelfm.widgets.dialogs import HELP

    assert "RAVE" in HELP
    app = make_app()
    async with app.run_test(size=(140, 42)):
        keys = {b.key: b for b in app.BINDINGS}  # type: ignore[union-attr]
        assert keys["R"].show is True  # in the footer
