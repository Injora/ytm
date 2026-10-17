"""Real audio measurements from ffplay's own filter graph.

ffplay is asked to print, to stderr, statistics computed on the audio it is
actually playing:

* every 50 ms: RMS and peak level of each channel (``astats``), and
* every 100 ms: RMS level in seven band-pass filtered copies of the signal
  (63 Hz - 12 kHz, about 1.3 octaves wide each). That is a genuine,
  frequency-resolved band analyzer, not an animation.

Each block carries its presentation timestamp, so the UI shows the block that
is audible *now* (ffplay decodes about half a second ahead). Levels are
measured before ffplay's output volume ("pre-fader"), after the EQ.
"""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass

from pixelfm.state import AudioFx

BANDS = (63, 160, 400, 1000, 2500, 6300, 12000)
BAND_LABELS = ("63", "160", "400", "1k", "2.5k", "6.3k", "12k")
SILENCE_DB = -90.0

_FRAME = re.compile(r"^frame:\d+\s+pts:\S+\s+pts_time:(-?[\d.]+)")
_KEY = re.compile(r"^(lavfi\.astats\.[\w.]+|pfm\.band)=(\S+)")


def meter_filter(spectrum: bool = True) -> str:
    """Filter graph that measures the audio and passes it through unchanged."""
    main = (
        "asetnsamples=n=2205:p=0,"
        "astats=metadata=1:reset=1:measure_overall=none"
        ":measure_perchannel=RMS_level+Peak_level,"
        "ametadata=mode=print:file=/dev/stderr:direct=1"
    )
    if not spectrum:
        return main
    labels = "".join(f"[pfm{i}]" for i in range(len(BANDS)))
    branches = ";".join(
        f"[pfm{i}]ametadata=mode=delete,asetnsamples=n=4410:p=0,"
        f"bandpass=f={freq}:width_type=o:w=1.3,"
        "astats=metadata=1:reset=1:measure_overall=RMS_level:measure_perchannel=none,"
        f"ametadata=mode=add:key=pfm.band:value={i},"
        "ametadata=mode=print:file=/dev/stderr:direct=1,anullsink"
        for i, freq in enumerate(BANDS)
    )
    return f"{main},asplit={len(BANDS) + 1}[pfmout]{labels};{branches};[pfmout]anull"


def fx_filter(fx: AudioFx) -> str:
    """The audible effects chain (empty when everything is flat)."""
    parts = []
    if fx.bass:
        parts.append(f"bass=g={fx.bass}:f=100")
    if fx.treble:
        parts.append(f"treble=g={fx.treble}:f=6000")
    if fx.normalize:
        parts.append("dynaudnorm=f=150:g=7")
    if fx.bass > 0 or fx.treble > 0:
        parts.append("alimiter=limit=0.95:level=disabled")  # boosts must not clip
    return ",".join(parts)


def audio_filter(fx: AudioFx, meters: bool, spectrum: bool) -> str | None:
    chain = [
        part
        for part in (fx_filter(fx), meter_filter(spectrum) if meters else "")
        if part
    ]
    return ",".join(chain) or None


def _db(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        return SILENCE_DB
    if math.isnan(value) or value < SILENCE_DB:
        return SILENCE_DB
    return min(0.0, value)


@dataclass(frozen=True, slots=True)
class StereoLevel:
    pts: float
    rms_l: float
    rms_r: float
    peak_l: float
    peak_r: float

    @property
    def loudness(self) -> float:
        """Mean power of both channels, in dBFS."""
        power = (10 ** (self.rms_l / 10) + 10 ** (self.rms_r / 10)) / 2
        return max(SILENCE_DB, 10 * math.log10(power)) if power > 0 else SILENCE_DB


class MeterParser:
    """Turns ffplay's printed metadata into timestamped measurements."""

    def __init__(self, history: int = 400) -> None:
        self.levels: deque[StereoLevel] = deque(maxlen=history)
        self.spectra: deque[tuple[float, tuple[float, ...]]] = deque(maxlen=100)
        self._pts: float | None = None
        self._block: dict[str, str] = {}
        self._bands: dict[float, list[float | None]] = {}

    def reset(self) -> None:
        self.levels.clear()
        self.spectra.clear()
        self._pts, self._block, self._bands = None, {}, {}

    def feed(self, line: str) -> bool:
        """Consume one stderr line; ``True`` if it was measurement output."""
        if line.startswith("frame:"):
            self._commit()
            match = _FRAME.match(line)
            self._pts = float(match.group(1)) if match else None
            return True
        if line.startswith(("lavfi.astats", "pfm.band")):
            if match := _KEY.match(line):
                self._block[match.group(1)] = match.group(2)
            return True
        return False

    def _commit(self) -> None:
        block, pts = self._block, self._pts
        self._block = {}
        if pts is None or not block:
            return
        if "pfm.band" in block:
            try:
                index = int(block["pfm.band"])
            except ValueError:
                return
            if not 0 <= index < len(BANDS):
                return
            slot = self._bands.setdefault(round(pts, 2), [None] * len(BANDS))
            slot[index] = _db(block.get("lavfi.astats.Overall.RMS_level", "-inf"))
            if all(value is not None for value in slot):
                self.spectra.append((pts, tuple(slot)))  # type: ignore[arg-type]
                del self._bands[round(pts, 2)]
            while len(self._bands) > 8:  # drop incomplete sets
                self._bands.pop(next(iter(self._bands)))
        elif "lavfi.astats.1.RMS_level" in block:
            left = _db(block["lavfi.astats.1.RMS_level"])
            left_peak = _db(block.get("lavfi.astats.1.Peak_level", "-inf"))
            right = _db(
                block.get("lavfi.astats.2.RMS_level", block["lavfi.astats.1.RMS_level"])
            )
            right_peak = _db(
                block.get(
                    "lavfi.astats.2.Peak_level",
                    block.get("lavfi.astats.1.Peak_level", "-inf"),
                )
            )
            self.levels.append(StereoLevel(pts, left, right, left_peak, right_peak))

    def flush(self) -> None:
        self._commit()

    # ---------------------------------------------------------------- reads

    def stereo_at(self, position: float) -> StereoLevel | None:
        """The newest measured block audible at ``position``."""
        for level in reversed(self.levels):
            if level.pts <= position + 0.05:
                return level
        return None

    def spectrum_at(self, position: float) -> tuple[float, ...] | None:
        for pts, bands in reversed(self.spectra):
            if pts <= position + 0.05:
                return bands
        return None

    def loudness_history(self, position: float, count: int) -> list[float]:
        audible = [lv.loudness for lv in self.levels if lv.pts <= position + 0.05]
        return audible[-count:] if count > 0 else []
