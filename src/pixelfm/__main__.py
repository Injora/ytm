"""Entry point for ``python -m pixelfm`` and the ``party`` console script."""

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


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the app, and always stop audio on the way out."""
    parser = argparse.ArgumentParser(
        prog="party", description="PixelFM terminal player"
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
        "--check", action="store_true", help="check yt-dlp/ffplay/JS runtime and exit"
    )
    parser.add_argument("--version", action="version", version=f"PixelFM {__version__}")
    args = parser.parse_args(argv)

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
        return 0 if all(d.found for d in deps) else 1
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

    app = PixelFMApp(config=config, startup_warnings=warnings, cli_overrides=overrides)
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


if __name__ == "__main__":
    sys.exit(main())
