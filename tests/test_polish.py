"""Release polish: config recovery, effects, levels, layouts, caches, CLI."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from PIL import Image
from textual.widgets import Input

from pixelfm.__main__ import main
from pixelfm.app import PixelFMApp
from pixelfm.config import (
    Config,
    detect_reduced_motion,
    load_config,
    parse_config_lenient,
    resolve_effects,
)
from pixelfm.services.artwork import (
    ArtworkMode,
    ArtworkService,
    quadrant_cell,
    render_image,
)
from pixelfm.services.cache import DiskCache
from pixelfm.services.ffplay import FFplayPlayer, build_args
from pixelfm.services.library import Library
from pixelfm.services.metering import meter_filter
from pixelfm.services.playback import SimulatedPlayer
from pixelfm.services.streams import StreamInfo
from pixelfm.state import PlaybackStatus
from pixelfm.widgets import AlbumArt, LevelMeter, StatusBar, WelcomeScreen
from pixelfm.widgets.now_playing import meter_markup
from tests.conftest import image_bytes, make_song
from tests.fakes import FakeReaper, FakeResolver, FakeSpawner, settle

S = PlaybackStatus
SONGS = [make_song(n) for n in range(1, 4)]


class Provider:
    name = "youtube"
    browsable = False

    async def search(self, query: str, limit: int = 20):  # type: ignore[no-untyped-def]
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
    )
    options.update(kwargs)
    return PixelFMApp(**options)  # type: ignore[arg-type]


async def until(predicate, pilot, limit: float = 3.0) -> None:  # type: ignore[no-untyped-def]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while not predicate():
        assert loop.time() < deadline, "condition not reached"
        await pilot.pause(0.02)


async def play_first(app: PixelFMApp, pilot) -> None:  # type: ignore[no-untyped-def]
    await pilot.press("slash")
    app.query_one("#search", Input).value = "x"
    await pilot.press("enter")
    await app.workers.wait_for_complete()
    await pilot.press("enter")


def ffplay_engine(levels: bool = True) -> tuple[FFplayPlayer, FakeSpawner]:
    spawner = FakeSpawner()
    player = FFplayPlayer(
        FakeResolver(),
        spawn=spawner,
        reaper=FakeReaper(),
        which=lambda name: "/bin/ffplay",
        retry_delays=(0,),
        notify_interval=0,
        levels=levels,
    )
    return player, spawner


# ------------------------------------------------------------------ config


def test_lenient_config_keeps_valid_settings() -> None:
    config, problems = parse_config_lenient(
        {"volume": 30, "artwork_mode": "sepia", "colour": "x", "layout": "compact"}
    )
    assert config.volume == 30 and config.layout == "compact"
    assert config.artwork_mode == "color"  # invalid value -> default
    assert len(problems) == 2


def test_load_config_reports_each_invalid_key(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('volume = 500\nvisualizer = "yes"\nsearch_limit = 7\n')
    config, warnings = load_config(path)
    assert config.search_limit == 7 and config.volume == 80
    assert len(warnings) == 2 and all("Ignored invalid setting" in w for w in warnings)


def test_reduced_motion_and_effects() -> None:
    assert detect_reduced_motion({"PIXELFM_REDUCED_MOTION": "1"}) is True
    assert detect_reduced_motion({"PIXELFM_REDUCED_MOTION": "0"}) is False
    assert resolve_effects("full", {"PIXELFM_REDUCED_MOTION": "1"}) is True
    assert resolve_effects("reduced", {}) is False
    assert resolve_effects("auto", {"PIXELFM_REDUCED_MOTION": "1"}) is False


# ------------------------------------------------------------ real levels


def test_level_filter_is_added_only_when_enabled() -> None:
    stream = StreamInfo(url="https://x/a.m4a")
    args = build_args("ffplay", stream, filters=meter_filter())
    assert args[args.index("-af") + 1] == meter_filter()
    assert "-af" not in build_args("ffplay", stream)


async def test_levels_follow_the_playback_clock() -> None:
    player, spawner = ffplay_engine()
    await player.play(SONGS[0])
    proc = spawner.last
    assert "-af" in proc.args
    for i, db in enumerate([-30.0, -20.0, -10.0, -5.0]):
        proc.emit(f"frame:{i}    pts:{i * 2205}    pts_time:{i * 0.05:.2f}\n")
        proc.emit(f"lavfi.astats.1.RMS_level={db}\nlavfi.astats.2.RMS_level={db}\n")
    proc.emit("frame:4 pts:8820 pts_time:0.2\nlavfi.astats.1.RMS_level=-inf\n")
    proc.emit("frame:5 pts:9999 pts_time:0.25\n")  # closes the previous block
    proc.progress(0.06)  # audible position: only the first blocks count
    await settle()
    assert player.audio_levels(10) == [-30.0, -20.0, -10.0]
    proc.progress(0.5)
    await settle()
    assert player.audio_levels(2) == [-5.0, -90.0]  # -inf becomes silence
    assert not any("astats" in line for line in player.stderr_tail)


async def test_levels_empty_when_not_playing_or_simulated() -> None:
    player, _ = ffplay_engine()
    assert player.audio_levels(10) == []
    assert SimulatedPlayer().audio_levels(10) == []  # never invented


async def test_missing_level_filter_falls_back_to_plain_playback() -> None:
    player, spawner = ffplay_engine()
    await player.play(SONGS[0])
    spawner.last.emit("[AVFilterGraph @ 0x1] No such filter: 'astats'\n")
    spawner.last.exit(0)
    await settle(30)
    assert len(spawner.processes) == 2
    assert "-af" not in spawner.last.args
    assert player.get_state().status is S.LOADING


def test_meter_markup() -> None:
    assert meter_markup([], 20, " ▁█") == ""
    markup = meter_markup([-36.0, -21.0, -6.0], 20, " ▁█")
    assert "lvl" in markup and " ▁█" in markup


# ---------------------------------------------------------- artwork extras


def test_cover_art_keeps_detail_without_scanlines() -> None:
    """Each cell resolves 2x2 pixels exactly when it holds two colours."""
    red, blue = (220, 30, 40), (20, 40, 230)
    char, fg, bg = quadrant_cell([red, blue, blue, red])
    assert char == "▚" and {fg, bg} == {red, blue}
    assert quadrant_cell([red, red, blue, blue])[0] == "▄"
    assert quadrant_cell([red] * 4) == (" ", red, red)
    # A flat grey image stays one flat colour: no darkened alternate rows.
    grey = Image.new("RGB", (40, 40), (200, 200, 200))
    art = render_image(grey, 4, 2)
    assert art.to_text().plain.replace("\n", "") == " " * 8
    colours = {span.style.bgcolor.triplet for line in art.lines for span in line.spans}
    assert {(c.red, c.green, c.blue) for c in colours} == {(200, 200, 200)}


def test_quadrants_double_horizontal_detail() -> None:
    """A 1-pixel-wide stripe pattern survives (half-blocks would blur it)."""
    image = Image.new("RGB", (8, 4))
    for x in range(8):
        for y in range(4):
            image.putpixel((x, y), (255, 255, 255) if x % 2 else (60, 60, 60))
    art = render_image(image, 4, 2)
    assert set(art.to_text().plain.replace("\n", "")) == {"▐"}


def test_disk_cache(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path / "c", max_files=2)
    assert cache.get("a") is None
    cache.set("a", b"1")
    cache.set("b", b"22")
    assert cache.get("a") == b"1"
    cache.set("c", b"333")
    cache.prune()
    files = [p for p in (tmp_path / "c").iterdir() if not p.name.startswith(".")]
    assert len(files) == 2
    cache.discard("c")
    assert cache.get("c") is None


def test_disk_cache_survives_unwritable_dir(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    cache = DiskCache(blocker / "sub")
    cache.set("a", b"1")  # no exception
    assert cache.get("a") is None


async def test_thumbnails_persist_across_sessions(tmp_path: Path) -> None:
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return image_bytes()

    disk = DiskCache(tmp_path / "thumbs")
    first = await ArtworkService(fetch, disk=disk).artwork_for(SONGS[0], 8, 4)
    second = await ArtworkService(fetch, disk=disk).artwork_for(SONGS[0], 8, 4)
    assert not first.art.placeholder and not second.art.placeholder
    assert len(calls) == 1  # second session read the disk cache


async def test_corrupt_disk_entry_is_discarded(tmp_path: Path) -> None:
    disk = DiskCache(tmp_path / "thumbs")
    disk.set(SONGS[0].thumbnail_url or "", b"garbage")
    result = await ArtworkService(lambda url: b"", disk=disk).artwork_for(
        SONGS[0], 8, 4
    )
    assert result.art.placeholder
    assert disk.get(SONGS[0].thumbnail_url or "") is None


# ---------------------------------------------------------------- the app


async def test_layout_toggle_resizes_artwork() -> None:
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        art = app.query_one("#deck-art", AlbumArt)
        assert not app.screen.has_class("-compact")
        assert (art.art_width, art.art_height) == (32, 16)  # 40 rows: large deck
        await pilot.press("escape", "v")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.screen.has_class("-compact")
        assert (art.art_width, art.art_height) == (10, 5)
        assert art.art is not None and (art.art.width, art.art.height) == (10, 5)
        assert art.region.width == 10 and art.region.height == 5
        await pilot.press("v")
        assert (art.art_width, art.art_height) == (32, 16)


@pytest.mark.parametrize(
    "size,compact", [((120, 40), False), ((70, 40), True), ((120, 25), True)]
)
async def test_auto_layout_follows_terminal_size(
    size: tuple[int, int], compact: bool
) -> None:
    app = make_app()
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert app.screen.has_class("-compact") is compact


async def test_level_meter_shows_real_levels() -> None:
    player, spawner = ffplay_engine()
    app = make_app(player=player, effects=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await play_first(app, pilot)
        await until(lambda: bool(spawner.processes), pilot)
        proc = spawner.last
        for i in range(21):
            proc.emit(f"frame:{i} pts:0 pts_time:{i * 0.05:.2f}\n")
            proc.emit(
                "lavfi.astats.1.RMS_level=-12.0\nlavfi.astats.2.RMS_level=-12.0\n"
            )
        proc.progress(1.0)
        await pilot.press("1")  # the loudness strip lives in the booth
        meter = app.query_one("#loudness", LevelMeter)
        await until(lambda: len(meter.levels) > 0, pilot)
        assert {round(level, 6) for level in meter.levels} == {-12.0}


async def test_reduced_effects_disable_animation_and_meter() -> None:
    player, spawner = ffplay_engine()
    app = make_app(player=player, effects=False)
    async with app.run_test(size=(120, 40)) as pilot:
        await play_first(app, pilot)
        await until(lambda: bool(spawner.processes), pilot)
        assert app.query_one(StatusBar).animate is False
        proc = spawner.last
        proc.emit("frame:0 pts:0 pts_time:0.0\nlavfi.astats.1.RMS_level=-12.0\n")
        proc.emit("frame:1 pts:1 pts_time:0.05\n")
        proc.progress(1.0)
        await pilot.pause(0.3)
        assert app.query_one("#loudness", LevelMeter).levels == ()
        await pilot.press("V")
        assert "Reduced effects" in app.query_one(StatusBar).message


async def test_no_color_uses_ascii_artwork(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    app = make_app()
    assert app.artwork_mode is ArtworkMode.MONO
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.is_running


async def test_ctrl_c_quits_cleanly_even_while_typing() -> None:
    player, spawner = ffplay_engine()
    app = make_app(player=player)
    async with app.run_test(size=(120, 40)) as pilot:
        await play_first(app, pilot)
        await until(lambda: bool(spawner.processes), pilot)
        await pilot.press("slash", "a", "ctrl+c")
        await pilot.pause()
    assert not app.is_running
    assert spawner.live == []


async def test_first_run_welcome_once(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    app = make_app(library=Library.open(path))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, WelcomeScreen)
        await pilot.press("enter")
        assert not isinstance(app.screen, WelcomeScreen)
    again = make_app(library=Library.open(path))
    async with again.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert not isinstance(again.screen, WelcomeScreen)


async def test_no_welcome_for_memory_library() -> None:
    app = make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert not isinstance(app.screen, WelcomeScreen)


# ------------------------------------------------------------------- CLI


def test_cli_check_reports_paths(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = main(["--check"])
    out = capsys.readouterr().out
    assert code in (0, 1)
    assert "dependency check" in out
    assert str(tmp_path / "data" / "library.sqlite3") in out


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert "PixelFM" in capsys.readouterr().out
