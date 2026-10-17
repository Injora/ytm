from __future__ import annotations

from pathlib import Path

import pytest

from pixelfm.config import (
    Config,
    ConfigError,
    config_path,
    detect_truecolor,
    detect_unicode,
    load_config,
    parse_config,
)


def test_defaults() -> None:
    assert parse_config({}) == Config()


def test_parse_all_fields() -> None:
    config = parse_config(
        {
            "theme": "nord",
            "unicode": "never",
            "provider": "mock",
            "search_limit": 5,
            "search_timeout": 3,
            "mock_latency": 0,
            "artwork_mode": "glyph",
            "artwork_width": 20,
            "artwork_height": 10,
            "color": "256",
        }
    )
    assert config.provider == "mock"
    assert config.artwork_mode == "glyph"
    assert config.search_timeout == 3.0


@pytest.mark.parametrize(
    "data",
    [
        {"nope": 1},
        {"theme": ""},
        {"unicode": "sometimes"},
        {"provider": "spotify"},
        {"search_limit": 0},
        {"search_limit": True},
        {"search_timeout": 0},
        {"artwork_mode": "sepia"},
        {"artwork_width": 1},
        {"artwork_height": 100},
        {"color": "16"},
    ],
)
def test_invalid_values(data: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        parse_config(data)


def test_load_missing_file(tmp_path: Path) -> None:
    assert load_config(tmp_path / "missing.toml") == (Config(), [])


def test_load_valid_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('artwork_mode = "mono"\nsearch_limit = 7\n')
    config, warnings = load_config(path)
    assert (config.artwork_mode, config.search_limit, warnings) == ("mono", 7, [])


def test_load_invalid_file_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("search_limit = = 3")
    config, warnings = load_config(path)
    assert config == Config()
    assert len(warnings) == 1


def test_config_path_respects_env(tmp_path: Path) -> None:
    assert (
        config_path({"PIXELFM_CONFIG": str(tmp_path / "x.toml")}) == tmp_path / "x.toml"
    )
    assert config_path({"XDG_CONFIG_HOME": str(tmp_path)}) == (
        tmp_path / "pixelfm" / "config.toml"
    )


def test_detect_unicode() -> None:
    assert detect_unicode("auto", {}, "utf-8") is True
    assert detect_unicode("auto", {}, "ascii") is False
    assert detect_unicode("auto", {"TERM": "linux"}, "utf-8") is False
    assert detect_unicode("never", {}, "utf-8") is False
    assert detect_unicode("always", {}, "ascii") is True
    assert detect_unicode("always", {"PIXELFM_ASCII": "1"}, "utf-8") is False


def test_detect_truecolor() -> None:
    assert detect_truecolor("auto", {"COLORTERM": "truecolor"}) is True
    assert detect_truecolor("auto", {"COLORTERM": "24bit"}) is True
    assert detect_truecolor("auto", {"TERM": "xterm-256color"}) is False
    assert detect_truecolor("256", {"COLORTERM": "truecolor"}) is False
    assert detect_truecolor("truecolor", {}) is True
