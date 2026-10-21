"""Detect external tools and explain how to install missing ones.

The app itself never installs anything; it reports what is missing, with the
command for this system. The npm launcher offers to run that command (it
asks first; see lib/system-deps.js and `pixelfm --install-deps`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

Which = Callable[[str], str | None]

#: yt-dlp runtimes in order of preference (deno is yt-dlp's default).
JS_RUNTIMES = ("deno", "node", "bun")

#: Linux package managers and package names. Keep in sync with
#: lib/system-deps.js.
LINUX_PACKAGES = (
    ("apt-get", "sudo apt install", {"ffmpeg": "ffmpeg", "tesseract": "tesseract-ocr"}),
    (
        "dnf",
        "sudo dnf install",
        {"ffmpeg": "ffmpeg-free", "tesseract": "tesseract tesseract-langpack-eng"},
    ),
    (
        "pacman",
        "sudo pacman -S",
        {"ffmpeg": "ffmpeg", "tesseract": "tesseract tesseract-data-eng"},
    ),
    (
        "zypper",
        "sudo zypper install",
        {
            "ffmpeg": "ffmpeg",
            "tesseract": "tesseract-ocr tesseract-ocr-traineddata-english",
        },
    ),
    ("apk", "sudo apk add", {"ffmpeg": "ffmpeg", "tesseract": "tesseract-ocr"}),
)


def install_hint(
    package: str,
    platform: str = sys.platform,
    which: Callable[[str], str | None] = shutil.which,
    env: dict[str, str] | None = None,
) -> str:
    """The command that installs ``package`` ("ffmpeg", "tesseract",
    "deno") on this system."""
    env = dict(os.environ) if env is None else env
    if package == "deno":
        return (
            "brew install deno"
            if platform == "darwin"
            else "see https://deno.land (or install Node.js)"
        )
    if platform == "darwin":
        command = f"brew install {package}"
    else:
        manager = next((m for m in LINUX_PACKAGES if which(m[0])), LINUX_PACKAGES[0])
        command = f"{manager[1]} {manager[2][package]}"
    if env.get("PIXELFM_LAUNCHER") == "npm":
        command += " (or run pixelfm --install-deps)"
    return command


@dataclass(frozen=True, slots=True)
class Dependency:
    """Availability of one external requirement."""

    name: str
    found: bool
    detail: str
    install: str
    purpose: str
    required: bool = True


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
    which: Which = shutil.which,
    ffplay: str = "ffplay",
    versions: bool = True,
    platform: str = sys.platform,
    env: dict[str, str] | None = None,
) -> list[Dependency]:
    """Report yt-dlp, ffplay, a JavaScript runtime, and (optional) the
    screenshot reader."""
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
            install_hint("ffmpeg", platform, which, env),
            "audio playback (part of FFmpeg)",
        )
    )

    runtimes = list(js_runtimes(which))
    deps.append(
        Dependency(
            "JavaScript runtime",
            bool(runtimes),
            ", ".join(runtimes) if runtimes else "none of deno/node/bun on PATH",
            install_hint("deno", platform, which, env),
            "required by yt-dlp to resolve YouTube streams",
        )
    )
    deps.append(_screenshot_reader(which, platform, env))
    return deps


def _screenshot_reader(
    which: Which, platform: str, env: dict[str, str] | None
) -> Dependency:
    """Built-in text recognition (macOS) or Tesseract (Linux): optional."""
    purpose = "importing playlists from screenshots (optional)"
    if platform == "darwin":
        from pixelfm.services.screenshot import ocr_helper

        helper = ocr_helper(env)
        return Dependency(
            "screenshot reader",
            helper is not None,
            "macOS text recognition" if helper else "helper missing",
            "npm run build:helper (in a source checkout)",
            purpose,
            required=False,
        )
    if platform.startswith("win"):
        return Dependency(
            "screenshot reader",
            False,
            "not available on Windows yet",
            "-",
            purpose,
            required=False,
        )
    path = which("tesseract")
    return Dependency(
        "screenshot reader",
        path is not None,
        f"Tesseract ({path})" if path else "Tesseract not on PATH",
        install_hint("tesseract", platform, which, env),
        purpose,
        required=False,
    )


def startup_warnings(which: Which = shutil.which, ffplay: str = "ffplay") -> list[str]:
    """One-line warnings for the status bar about missing playback tools."""
    return [
        f"{d.name} missing ({d.purpose}). Install: {d.install}"
        for d in check_dependencies(which, ffplay, versions=False)
        if not d.found and d.required
    ]


def format_report(deps: list[Dependency]) -> str:
    """Human-readable report for ``pixelfm --check``."""
    lines = ["PixelFM dependency check", ""]
    for dep in deps:
        mark = "ok " if dep.found else "MISSING" if dep.required else "absent"
        lines.append(f"  [{mark:^7}] {dep.name:<20} {dep.detail}")
        if not dep.found:
            lines.append(f"            needed for {dep.purpose}")
            lines.append(f"            install:  {dep.install}")
    if all(d.found for d in deps if d.required):
        lines += ["", "Everything needed for playback is installed."]
    else:
        lines += [
            "",
            "PixelFM never installs software without asking; run the commands "
            "above (with the npm launcher: pixelfm --install-deps).",
        ]
    return "\n".join(lines)
