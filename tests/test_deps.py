from __future__ import annotations

from pathlib import Path

from pixelfm.logs import default_log_path, setup_logging
from pixelfm.services.deps import (
    check_dependencies,
    format_report,
    js_runtimes,
    startup_warnings,
)


def fake_which(*found: str):  # type: ignore[no-untyped-def]
    return lambda name: f"/usr/local/bin/{name}" if name in found else None


def test_all_present() -> None:
    deps = check_dependencies(fake_which("ffplay", "deno"), versions=False)
    assert all(d.found for d in deps)
    assert "Everything needed" in format_report(deps)
    assert startup_warnings(fake_which("ffplay", "deno")) == []


def test_missing_tools_give_install_commands() -> None:
    deps = check_dependencies(fake_which(), versions=False)
    missing = {d.name: d for d in deps if not d.found}
    assert set(missing) == {"ffplay", "JavaScript runtime"}
    assert missing["ffplay"].install == "brew install ffmpeg"
    assert missing["JavaScript runtime"].install == "brew install deno"
    report = format_report(deps)
    assert "MISSING" in report and "never installs" in report
    warnings = startup_warnings(fake_which())
    assert any("brew install ffmpeg" in w for w in warnings)


def test_js_runtimes_preference() -> None:
    assert js_runtimes(fake_which("node", "deno")) == {"deno": {}, "node": {}}
    assert js_runtimes(fake_which()) == {}


def test_custom_ffplay_path() -> None:
    deps = check_dependencies(fake_which("myffplay"), ffplay="myffplay", versions=False)
    assert next(d for d in deps if d.name == "ffplay").found


def test_log_path_override(tmp_path: Path) -> None:
    target = tmp_path / "x.log"
    assert default_log_path({"PIXELFM_LOG_FILE": str(target)}) == target


def test_setup_logging_writes_file(tmp_path: Path) -> None:
    import logging

    path = setup_logging(tmp_path / "logs" / "pixelfm.log", level="DEBUG")
    logger = logging.getLogger("pixelfm.test")
    logger.debug("hello log")
    for handler in logging.getLogger("pixelfm").handlers:
        handler.flush()
    assert path is not None and "hello log" in path.read_text()
    root = logging.getLogger("pixelfm")
    for handler in list(root.handlers):
        if not isinstance(handler, logging.NullHandler):
            root.removeHandler(handler)
            handler.close()
    root.propagate = True
