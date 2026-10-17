"""Real-measurement parsing and the audio filter chain."""

from __future__ import annotations

import pytest

from pixelfm.services.ffplay import FFplayPlayer, build_args
from pixelfm.services.metering import (
    BANDS,
    SILENCE_DB,
    MeterParser,
    audio_filter,
    fx_filter,
    meter_filter,
)
from pixelfm.services.streams import StreamInfo
from pixelfm.state import AudioFx, PlaybackStatus
from tests.conftest import make_song
from tests.fakes import FakeReaper, FakeResolver, FakeSpawner, settle

SONG = make_song(1, duration=200)


def feed(parser: MeterParser, text: str) -> None:
    for line in text.strip().splitlines():
        assert parser.feed(line.strip())
    parser.flush()


def stereo_block(pts: float, left: float, right: float, peak: float = -3.0) -> str:
    return (
        f"frame:1 pts:1 pts_time:{pts}\n"
        f"lavfi.astats.1.Peak_level={peak}\nlavfi.astats.1.RMS_level={left}\n"
        f"lavfi.astats.2.Peak_level={peak}\nlavfi.astats.2.RMS_level={right}\n"
    )


def band_block(pts: float, index: int, db: float) -> str:
    return (
        f"frame:1 pts:1 pts_time:{pts}\n"
        f"lavfi.astats.Overall.RMS_level={db}\npfm.band={index}\n"
    )


def test_stereo_levels_and_peaks() -> None:
    parser = MeterParser()
    feed(parser, stereo_block(0.5, -18.0, -24.0, peak=-6.5))
    level = parser.stereo_at(0.5)
    assert level is not None
    assert (level.rms_l, level.rms_r, level.peak_l) == (-18.0, -24.0, -6.5)
    assert -24.0 < level.loudness < -18.0


def test_mono_source_mirrors_the_channel() -> None:
    parser = MeterParser()
    feed(parser, "frame:1 pts:1 pts_time:0.1\nlavfi.astats.1.RMS_level=-20\n")
    level = parser.stereo_at(1.0)
    assert level is not None and level.rms_l == level.rms_r == -20.0


def test_only_audible_blocks_are_reported() -> None:
    parser = MeterParser()
    feed(parser, stereo_block(1.0, -10, -10) + stereo_block(2.0, -30, -30))
    assert parser.stereo_at(1.2).rms_l == -10.0  # type: ignore[union-attr]
    assert parser.stereo_at(0.5) is None  # nothing audible yet
    assert parser.loudness_history(1.5, 10) == [pytest.approx(-10.0)]


def test_band_sets_complete_into_a_spectrum() -> None:
    parser = MeterParser()
    text = "".join(band_block(0.3, i, -20.0 - i) for i in range(len(BANDS)))
    feed(parser, text)
    spectrum = parser.spectrum_at(0.4)
    assert spectrum == tuple(-20.0 - i for i in range(len(BANDS)))


def test_incomplete_band_set_is_not_reported() -> None:
    parser = MeterParser()
    feed(parser, band_block(0.3, 0, -20) + band_block(0.3, 1, -21))
    assert parser.spectrum_at(1.0) is None


@pytest.mark.parametrize("raw", ["-inf", "nan", "garbage", "-500"])
def test_silence_and_garbage_values(raw: str) -> None:
    parser = MeterParser()
    feed(parser, f"frame:1 pts:1 pts_time:0.1\nlavfi.astats.1.RMS_level={raw}\n")
    assert parser.stereo_at(1).rms_l == SILENCE_DB  # type: ignore[union-attr]


def test_non_meter_lines_are_not_consumed() -> None:
    parser = MeterParser()
    assert not parser.feed("[https @ 0x1] Opening 'x' for reading")
    assert not parser.feed("   12.30 M-A:  0.000 fd=   0")
    assert parser.feed("frame:bogus")  # consumed, then ignored safely
    parser.flush()
    assert parser.stereo_at(10) is None


def test_filter_chain_composition() -> None:
    assert fx_filter(AudioFx()) == ""
    chain = fx_filter(AudioFx(bass=6, treble=-4, normalize=True))
    assert "bass=g=6" in chain and "treble=g=-4" in chain and "dynaudnorm" in chain
    assert "alimiter" in chain  # boosts are limited
    assert "alimiter" not in fx_filter(AudioFx(bass=-6))
    assert audio_filter(AudioFx(), meters=False, spectrum=False) is None
    full = audio_filter(AudioFx(bass=2), meters=True, spectrum=True)
    assert full is not None and full.startswith("bass=") and full.endswith("anull")
    assert full.count("bandpass=") == len(BANDS)
    assert "bandpass" not in meter_filter(spectrum=False)


def test_audio_fx_is_clamped() -> None:
    assert AudioFx(bass=40, treble=-40).clamped() == AudioFx(12, -12, False)


def test_local_file_arguments() -> None:
    stream = StreamInfo(url="file:/Users/a b/Music/x [id].m4a", local=True)
    args = build_args("ffplay", stream, start=12, filters="bass=g=2")
    assert args[-2:] == ["-i", "file:/Users/a b/Music/x [id].m4a"]
    assert args[args.index("-protocol_whitelist") + 1] == "file"
    assert "-reconnect" not in args and "-headers" not in args
    assert args[args.index("-ss") + 1] == "12.00"


async def test_player_reports_measurements_only_while_playing() -> None:
    spawner = FakeSpawner()
    player = FFplayPlayer(
        FakeResolver(),
        spawn=spawner,
        reaper=FakeReaper(),
        which=lambda name: "/bin/ffplay",
        notify_interval=0,
    )
    await player.play(SONG)
    proc = spawner.last
    assert "-af" in proc.args and "bandpass" in proc.arg("-af")  # type: ignore[operator]
    proc.emit(stereo_block(0.05, -15, -16))
    proc.emit("".join(band_block(0.05, i, -30.0) for i in range(len(BANDS))))
    proc.emit("frame:9 pts:9 pts_time:9\n")
    assert player.stereo_level() is None  # still loading: nothing audible
    proc.progress(0.2)
    await settle()
    assert player.get_state().status is PlaybackStatus.PLAYING
    assert player.stereo_level().rms_l == -15.0  # type: ignore[union-attr]
    assert player.spectrum() == (-30.0,) * len(BANDS)
    await player.pause()
    assert player.stereo_level() is None and player.spectrum() is None


async def test_fx_change_restarts_with_new_filters() -> None:
    spawner = FakeSpawner()
    player = FFplayPlayer(
        FakeResolver(),
        spawn=spawner,
        reaper=FakeReaper(),
        which=lambda name: "/bin/ffplay",
        volume_debounce=0,
        notify_interval=0,
    )
    await player.play(SONG)
    spawner.last.progress(40)
    await settle()
    await player.set_fx(AudioFx(bass=4))
    await settle(30)
    assert len(spawner.processes) == 2
    assert spawner.last.arg("-af").startswith("bass=g=4")  # type: ignore[union-attr]
    assert spawner.last.arg("-ss") == "40.00"
    assert player.get_state().fx == AudioFx(bass=4)
