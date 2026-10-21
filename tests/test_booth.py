"""DJ booth: transport buttons, timeline, mixer, meters, settings."""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Input

from pixelfm.app import PixelFMApp
from pixelfm.config import Config
from pixelfm.models import Song
from pixelfm.services.artwork import ArtworkService
from pixelfm.services.library import Library
from pixelfm.services.metering import StereoLevel
from pixelfm.services.playback import SimulatedPlayer
from pixelfm.state import AudioFx, PlaybackStatus
from pixelfm.styles import glyphs_for
from pixelfm.widgets import (
    BoothHeader,
    Fader,
    SettingsList,
    Spectrum,
    StereoMeter,
    Timeline,
    Toggle,
)
from tests.conftest import image_bytes, make_song

S = PlaybackStatus
SONGS = [make_song(n) for n in range(1, 4)]


class Provider:
    name = "youtube"
    browsable = False

    async def search(self, query: str, limit: int = 20) -> list[Song]:
        return SONGS


def make_app(**kwargs) -> PixelFMApp:  # type: ignore[no-untyped-def]
    options = dict(
        config=Config(),
        provider=Provider(),
        artwork=ArtworkService(lambda url: image_bytes()),
        player=SimulatedPlayer(load_delay=0),
        library=Library.memory(),
        unicode=True,
        truecolor=True,
        welcome=False,
        effects=True,
    )
    options.update(kwargs)
    return PixelFMApp(**options)  # type: ignore[arg-type]


async def until(predicate, pilot, limit: float = 3.0) -> None:  # type: ignore[no-untyped-def]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while not predicate():
        assert loop.time() < deadline, "condition not reached"
        await pilot.pause(0.02)


async def load_and_play(app: PixelFMApp, pilot) -> None:  # type: ignore[no-untyped-def]
    await pilot.press("slash")
    app.query_one("#search", Input).value = "x"
    await pilot.press("enter")
    await app.workers.wait_for_complete()
    await pilot.press("enter", "j", "a", "1")
    await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
    await pilot.pause()


async def test_transport_buttons_perform_real_actions() -> None:
    app = make_app()
    async with app.run_test(size=(140, 42)) as pilot:
        await load_and_play(app, pilot)
        await pilot.click("#tx-play")
        await until(lambda: app.player.get_state().status is S.PAUSED, pilot)
        await pilot.pause(0.6)
        await pilot.click("#tx-play")
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        await pilot.pause(0.6)
        await pilot.click("#tx-shuffle")
        assert app.queue.shuffle
        await pilot.pause(0.6)
        await pilot.click("#tx-repeat")
        assert str(app.queue.repeat) == "all"
        await pilot.pause(0.6)
        await pilot.click("#tx-next")
        await until(lambda: app.player.get_state().song == SONGS[1], pilot)
        await pilot.pause(0.6)
        await pilot.click("#tx-prev")
        await until(lambda: app.player.get_state().song == SONGS[0], pilot)


async def test_keyboard_only_operation_of_transport() -> None:
    app = make_app()
    async with app.run_test(size=(140, 42)) as pilot:
        await load_and_play(app, pilot)
        app.query_one("#tx-like").focus()
        await pilot.press("enter")
        assert app.library.favorite_ids() == {SONGS[0].id}


async def test_timeline_click_and_keys_seek() -> None:
    app = make_app()
    async with app.run_test(size=(140, 42)) as pilot:
        await load_and_play(app, pilot)
        timeline = app.query_one(Timeline)
        assert timeline.seekable and timeline.duration == 180
        start, width = timeline._bar_span()
        await pilot.click(Timeline, offset=(start + width // 2, 0))
        await until(lambda: app.player.get_state().position >= 80, pilot)
        timeline.focus()
        before = app.player.get_state().position
        await pilot.press("left")
        await until(lambda: app.player.get_state().position < before, pilot)


async def test_mixer_faders_and_keys_drive_the_engine() -> None:
    app = make_app()
    async with app.run_test(size=(140, 42)) as pilot:
        await load_and_play(app, pilot)
        bass = app.query_one("#fader-bass", Fader)
        bass.focus()
        await pilot.press("right", "right")
        await pilot.pause()
        assert app.player.get_state().fx.bass == 4
        await pilot.press("right_parenthesis", "N")
        await pilot.pause()
        assert app.player.get_state().fx == AudioFx(bass=4, treble=2, normalize=True)
        assert app.query_one("#toggle-norm", Toggle).on
        vol = app.query_one("#fader-vol", Fader)
        vol.focus()
        await pilot.press("left")
        await pilot.pause()
        assert app.player.get_state().volume == 75
        assert app.library.get_pref("fx") == {"bass": 4, "treble": 2, "normalize": True}


async def test_meters_show_explicit_states_never_fake_motion() -> None:
    meter = StereoMeter(glyphs_for(True))
    spectrum = Spectrum(glyphs_for(True))
    app = make_app()
    async with app.run_test(size=(140, 42)) as pilot:
        await load_and_play(app, pilot)
        await pilot.pause(0.3)
        meter = app.query_one(StereoMeter)
        spectrum = app.query_one(Spectrum)
        assert meter.state == "sim"  # the simulated engine has nothing to measure
        assert "SIMULATED ENGINE" in str(meter.render())
        assert "SIMULATED ENGINE" in str(spectrum.render())
        meter.feed(StereoLevel(1.0, -12, -15, -3, -4), "live")
        rendered = str(meter.render())
        assert (
            "-12.0 dB" in rendered
            and "-15.0 dB" in rendered
            and "PEAK HOLD" in rendered
        )
        spectrum.feed((-20.0,) * 7, "live")
        assert "▔" in str(spectrum.render()) or "█" in str(spectrum.render())


def test_meter_falloff_is_display_smoothing_of_real_values() -> None:
    meter = StereoMeter(glyphs_for(True))
    meter.refresh = lambda *a, **k: None  # type: ignore[method-assign]
    meter.feed(StereoLevel(0, -10, -10, -2, -2), "live")
    loud = meter._left.value
    meter.feed(None, "idle")  # no new data: falls, never rises
    assert meter._left.value <= loud
    assert meter._left.peak == -2  # peak held


async def test_header_reports_status_and_engine() -> None:
    app = make_app()
    async with app.run_test(size=(140, 42)) as pilot:
        await load_and_play(app, pilot)
        header = app.query_one(BoothHeader)
        assert "PLAYING" in str(header.query_one("#header-status").render())
        assert "simulated" in str(header.query_one("#header-engine").render())


@pytest.mark.parametrize(
    "setting,pref,expected",
    [
        ("layout", "layout", "full"),
        ("effects", "effects", "full"),
        ("liked_order", "liked_order", "title"),
        ("theme", "theme", "pixelfm"),  # after the default, "music"
    ],
)
async def test_settings_rows_change_persisted_preferences(
    setting: str, pref: str, expected: str
) -> None:
    app = make_app()
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("8")
        settings = app.query_one(SettingsList)
        settings.highlighted = settings.get_option_index(setting)
        await pilot.press("enter")
        await pilot.pause()
        assert app.library.get_pref(pref) == expected


async def test_settings_downloads_folder_prompt(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app = make_app()
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("8")
        settings = app.query_one(SettingsList)
        settings.highlighted = settings.get_option_index("downloads_dir")
        await pilot.press("enter")
        app.screen.query_one(Input).value = str(tmp_path / "My Music")
        await pilot.press("enter")
        await pilot.pause()
        assert app._downloads_dir() == tmp_path / "My Music"


async def test_cli_override_beats_saved_preference() -> None:
    library = Library.memory()
    library.set_pref("layout", "compact")
    app = make_app(
        library=library, config=Config(layout="full"), cli_overrides={"layout"}
    )
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        assert not app.screen.has_class("-compact")


async def test_reduced_effects_turn_meters_off() -> None:
    app = make_app(effects=False)
    async with app.run_test(size=(140, 42)) as pilot:
        await load_and_play(app, pilot)
        await pilot.pause(0.3)
        assert app.query_one(StereoMeter).state == "off"
