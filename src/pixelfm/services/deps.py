"""Detect external tools and explain how to install missing ones.

PixelFM never installs anything itself; it only reports what is missing.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

Which = Callable[[str], str | None]

#: yt-dlp runtimes in order of preference (deno is yt-dlp's default).
JS_RUNTIMES = ("deno", "node", "bun")


@dataclass(frozen=True, slots=True)
class Dependency:
    """Availability of one external requirement."""

    name: str
    found: bool
    detail: str
    install: str
    purpose: str


def js_runtimes(which: Which = shutil.which) -> dict[str, dict[str, str]]:
    """yt-dlp ``js_runtimes`` option enabling every runtime found on PATH."""
    return {name: {} for name in JS_RUNTIMES if which(name)}


def _version(command: list[str]) -> str:
    try:
        out = subprocess.run(
            command, capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "found"
    return out.splitlines()[0][:60] if out else "found"


def check_dependencies(
    which: Which = shutil.which, ffplay: str = "ffplay", versions: bool = True
) -> list[Dependency]:
    """Report yt-dlp, ffplay, and a JavaScript runtime."""
    deps = []
    try:
        from yt_dlp.version import __version__ as ytdlp_version

        deps.append(
            Dependency(
                "yt-dlp",
                True,
                f"yt-dlp {ytdlp_version}",
                "pip install -U 'yt-dlp[default]'",
                "search and stream resolution",
            )
        )
    except ImportError:
        deps.append(
            Dependency(
                "yt-dlp",
                False,
                "not importable",
                "pip install -U 'yt-dlp[default]'",
                "search and stream resolution",
            )
        )

    path = which(ffplay)
    deps.append(
        Dependency(
            "ffplay",
            path is not None,
            (_version([path, "-version"]) if versions else path)
            if path
            else "not on PATH",
            "brew install ffmpeg",
            "audio playback (part of FFmpeg)",
        )
    )

    runtimes = list(js_runtimes(which))
    deps.append(
        Dependency(
            "JavaScript runtime",
            bool(runtimes),
            ", ".join(runtimes) if runtimes else "none of deno/node/bun on PATH",
            "brew install deno",
            "required by yt-dlp to resolve YouTube streams",
        )
    )
    return deps


def startup_warnings(which: Which = shutil.which, ffplay: str = "ffplay") -> list[str]:
    """One-line warnings for the status bar about missing playback tools."""
    return [
        f"{d.name} missing ({d.purpose}). Install: {d.install}"
        for d in check_dependencies(which, ffplay, versions=False)
        if not d.found
    ]


def format_report(deps: list[Dependency]) -> str:
    """Human-readable report for ``ytm --check``."""
    lines = ["PixelFM dependency check", ""]
    for dep in deps:
        mark = "ok " if dep.found else "MISSING"
        lines.append(f"  [{mark:^7}] {dep.name:<20} {dep.detail}")
        if not dep.found:
            lines.append(f"            needed for {dep.purpose}")
            lines.append(f"            install:  {dep.install}")
    if all(d.found for d in deps):
        lines += ["", "Everything needed for playback is installed."]
    else:
        lines += ["", "PixelFM never installs software itself; run the commands above."]
    return "\n".join(lines)
