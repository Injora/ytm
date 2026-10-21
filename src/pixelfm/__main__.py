"""Entry point for ``python -m pixelfm`` and the ``pixelfm``/``pfm`` commands."""

from __future__ import annotations

import argparse
import atexit
import contextlib
import dataclasses
import logging
import os
import sys

from pixelfm import __version__
from pixelfm.config import load_config
from pixelfm.logs import default_log_path, setup_logging
from pixelfm.paths import cache_dir, library_path
from pixelfm.services.deps import check_dependencies, format_report, startup_warnings
from pixelfm.services.downloads import default_downloads_dir

log = logging.getLogger("pixelfm")


#: Commands of the npm launcher (lib/launcher.js), not of the app itself.
LAUNCHER_ONLY = (
    "--install-deps",
    "--update-ytdlp",
    "--runtime-info",
    "--reinstall-runtime",
)


def launcher_only(command: str) -> int:
    """Explain a launcher command when PixelFM runs without the launcher
    (a source checkout or pipx), with what to do here instead."""
    print(f"pixelfm: {command} belongs to the npm launcher (npm install -g pixelfm).")
    if command == "--install-deps":
        missing = [d for d in check_dependencies(versions=False) if not d.found]
        if not missing:
            print("Everything PixelFM uses is installed.")
            return 0
        print("Here, install what is missing yourself:")
        for dep in missing:
            print(f"  {dep.name:<20} {dep.install}")
        return 1
    if command == "--update-ytdlp":
        print(f"Here: {sys.executable} -m pip install -U 'yt-dlp[default]'")
    else:
        print("This installation runs from its own Python environment:")
        print(f"  {sys.prefix}")
    return 2


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the app, and always stop audio on the way out."""
    args_in = sys.argv[1:] if argv is None else argv
    if args_in and args_in[0] in LAUNCHER_ONLY:
        return launcher_only(args_in[0])
    parser = argparse.ArgumentParser(
        prog="pixelfm", description="PixelFM terminal player"
    )
    parser.add_argument(
        "--mock", action="store_true", help="offline sample data, simulated playback"
    )
    parser.add_argument(
        "--art", choices=("color", "mono", "glyph"), help="artwork mode"
    )
    parser.add_argument(
        "--layout", choices=("auto", "full", "compact"), help="screen layout"
    )
    parser.add_argument(
        "--effects",
        choices=("auto", "full", "reduced"),
        help="animations, meters, and Party Mode (auto follows Reduce motion)",
    )
    parser.add_argument(
        "--no-color", action="store_true", help="monochrome output (same as NO_COLOR=1)"
    )
    parser.add_argument(
        "--images",
        choices=("auto", "off", "sixel", "kitty"),
        help="real cover images in terminals that can draw them (default auto)",
    )
    parser.add_argument(
        "--check", action="store_true", help="check yt-dlp/ffplay/JS runtime and exit"
    )
    parser.add_argument(
        "--media-test",
        action="store_true",
        help="macOS: check media keys / Control Centre control for 60 s and exit",
    )
    parser.add_argument("--version", action="version", version=f"PixelFM {__version__}")
    args = parser.parse_args(argv)

    if args.media_test:
        return media_test()
    config, warnings = load_config()
    ffplay = config.ffplay_path or "ffplay"
    if args.check:
        deps = check_dependencies(ffplay=ffplay)
        print(format_report(deps))
        print()
        print(f"  library  {library_path()}")
        print(f"  downloads {config.downloads_dir or default_downloads_dir()}")
        print(f"  caches   {cache_dir()}")
        print(f"  log      {default_log_path()}")
        return 0 if all(d.found for d in deps if d.required) else 1
    overrides: set[str] = set()
    if args.mock:
        config = dataclasses.replace(config, provider="mock")
    if args.art:
        config = dataclasses.replace(config, artwork_mode=args.art)
        overrides.add("artwork_mode")
    if args.layout:
        config = dataclasses.replace(config, layout=args.layout)
        overrides.add("layout")
    if args.effects:
        config = dataclasses.replace(config, effects=args.effects)
        overrides.add("effects")
    if args.no_color:
        os.environ["NO_COLOR"] = "1"
    if config.provider == "youtube":
        warnings += startup_warnings(ffplay=ffplay)

    log_path = setup_logging()
    log.info("PixelFM %s starting (log: %s)", __version__, log_path)

    from pixelfm.app import PixelFMApp  # imported late: --check stays fast
    from pixelfm.services.graphics import detect

    if args.images:
        os.environ["PIXELFM_GRAPHICS"] = args.images
    graphics = detect()  # must run before Textual takes over the terminal

    app = PixelFMApp(
        config=config,
        startup_warnings=warnings,
        cli_overrides=overrides,
        graphics=graphics,
    )
    atexit.register(app.player.shutdown)
    interrupted = False
    try:
        app.run()
    except KeyboardInterrupt:
        # Inside the app Ctrl+C is a key (it saves and quits); this is a
        # SIGINT outside Textual's raw mode, e.g. during start-up or exit.
        interrupted = True
    finally:
        # Last-chance save (crash, interrupt): the position is read before the
        # player is killed. A no-op if the app already saved this state, and
        # skipped entirely if the session was never restored.
        with contextlib.suppress(Exception):
            app.save_session()
        app.player.shutdown()
        with contextlib.suppress(Exception):
            app.library.close()
        log.info("PixelFM exited")
    if interrupted:
        return 130
    return app.return_code or 0


def media_test(seconds: float = 60.0) -> int:
    """Publish a test track through the real helper and print every system
    media command it receives (for checking on a real Mac)."""
    import asyncio

    from pixelfm.services.media_session import (
        MediaSession,
        NowPlayingInfo,
        helper_path,
        unavailable_reason,
    )

    if reason := unavailable_reason():
        print(f"pixelfm: {reason}")
        return 1

    async def run() -> int:
        received: list[str] = []
        playing = True

        def on_command(name: str, position: float | None) -> None:
            nonlocal playing
            note = f" to {position:.0f}s" if position is not None else ""
            print(f"  received: {name}{note}", flush=True)
            received.append(name)
            if name in ("toggle", "play", "pause"):
                playing = {"toggle": not playing, "play": True, "pause": False}[name]
                show()

        session = MediaSession(on_command, helper_path())

        def show() -> None:
            session.publish(
                NowPlayingInfo(
                    "PixelFM media test",
                    "PixelFM",
                    "Control check",
                    240.0,
                    30.0,
                    playing,
                )
            )

        if not await session.start():
            print("pixelfm: the media helper could not start (see the log).")
            return 1
        await asyncio.wait_for(session.ready.wait(), 5)
        show()
        print(
            "PixelFM media test is now the Mac's Now Playing item.\n"
            "  1. Press the keyboard's play/pause, next, and previous keys.\n"
            "  2. Open Control Centre (or the menu-bar Now Playing item): it should\n"
            "     show 'PixelFM media test'; try its buttons too.\n"
            f"Listening for {seconds:.0f} s (ctrl+c to stop)…",
            flush=True,
        )
        try:
            await asyncio.sleep(seconds)
        finally:
            await session.aclose()
        names = ", ".join(received) or "none"
        print(f"Done: {len(received)} command(s) received: {names}.")
        return 0 if received else 2

    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
