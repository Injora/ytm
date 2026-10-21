"""The npm package: what it contains and what it must never contain."""

from __future__ import annotations

import json
import re
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = json.loads((ROOT / "package.json").read_text())
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())


def test_versions_agree() -> None:
    from pixelfm import __version__

    assert PACKAGE["version"] == PYPROJECT["project"]["version"] == __version__


def test_the_command_is_party() -> None:
    assert PACKAGE["bin"] == {"pixelfm": "bin/pixelfm.js", "pfm": "bin/pixelfm.js"}
    assert (ROOT / "bin" / "pixelfm.js").is_file()
    assert PYPROJECT["project"]["scripts"] == {
        "pixelfm": "pixelfm.__main__:main",
        "pfm": "pixelfm.__main__:main",
    }


def test_package_files_and_pins() -> None:
    files = PACKAGE["files"]
    assert {"bin/", "lib/", "python/requirements.lock", "python/*.whl"} <= set(files)
    lock = (ROOT / "python" / "requirements.lock").read_text()
    pins = dict(re.findall(r"^([A-Za-z0-9_.-]+)==(\S+)$", lock, re.MULTILINE))
    for requirement in PYPROJECT["project"]["dependencies"]:
        name = re.split(r"[<>=\[ ]", requirement)[0].casefold()
        assert any(p.casefold() == name for p in pins), f"{name} is not pinned"


def built_wheel() -> Path | None:
    wheels = sorted((ROOT / "python").glob(f"pixelfm-{PACKAGE['version']}-*.whl"))
    return wheels[-1] if wheels else None


@pytest.mark.skipif(
    built_wheel() is None, reason="wheel not built (npm run build:wheel)"
)
def test_built_wheel_contents() -> None:
    wheel = built_wheel()
    assert wheel is not None
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "pixelfm/__main__.py" in names
        assert "pixelfm/services/spotify.py" in names
        assert not any("test" in n.split("/")[0] for n in names)
        helper = "pixelfm/native/PixelFMMedia.app/Contents/MacOS/pixelfm-nowplaying"
        if sys.platform == "darwin":
            assert helper in names
            mode = (archive.getinfo(helper).external_attr >> 16) & 0o777
            assert mode & 0o111  # executable after install
        for name in names:
            data = archive.read(name)
            for secret in (b"client_secret=", b"BEGIN PRIVATE KEY"):
                assert secret not in data, name
            assert b"/Users/" not in data or name.endswith(".py"), name
