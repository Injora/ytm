"""Where Spotify sign-in tokens live: the system keychain, never the library.

* macOS: the login Keychain, through ``/usr/bin/security``. The token goes
  through stdin, never the command line (which other processes can read).
* Linux: the Secret Service (GNOME Keyring, KWallet) via ``secret-tool``.
* Otherwise: a file only you can read (mode 0600) in PixelFM's data folder.

Only tokens are stored. PixelFM never sees or stores a Spotify password, and
a client secret is not needed (PKCE).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

from pixelfm.paths import data_dir

log = logging.getLogger(__name__)

SERVICE = "PixelFM Spotify"
ACCOUNT = "spotify-tokens"
MAX_COMMAND = 4000  # `security -i` reads each command into a 4 KiB buffer


class TokenStore(Protocol):
    name: str

    def get(self) -> dict[str, Any] | None: ...
    def set(self, tokens: dict[str, Any]) -> None: ...
    def delete(self) -> None: ...


def _decode(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a keychain tool detached from the terminal.

    In a terminal, ``security`` reads secrets from the terminal itself
    (``/dev/tty``), not stdin, so it would hang behind PixelFM's full-screen
    UI. In a new session it has no terminal and reads stdin as intended.
    """
    try:
        return subprocess.run(
            args,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired:
        raise OSError(
            f"{Path(args[0]).name} did not answer within 10 seconds"
        ) from None


class MemoryStore:
    name = "memory"

    def __init__(self, tokens: dict[str, Any] | None = None) -> None:
        self.tokens = dict(tokens) if tokens else None

    def get(self) -> dict[str, Any] | None:
        return dict(self.tokens) if self.tokens else None

    def set(self, tokens: dict[str, Any]) -> None:
        self.tokens = dict(tokens)

    def delete(self) -> None:
        self.tokens = None


class KeychainStore:
    """macOS login Keychain (generic password item)."""

    name = "macOS Keychain"

    def __init__(self, tool: str = "/usr/bin/security") -> None:
        self.tool = tool

    def get(self) -> dict[str, Any] | None:
        result = _run(
            [self.tool, "find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w"]
        )
        return _decode(result.stdout.strip()) if result.returncode == 0 else None

    def set(self, tokens: dict[str, Any]) -> None:
        secret = json.dumps(tokens)
        # Not "-w" on stdin: security keeps only the first 128 characters of a
        # password read that way, and the tokens are longer. In interactive
        # mode (-i) the whole command comes through stdin instead, so the
        # secret (hex-encoded with -X) still stays off the command line.
        command = (
            f'add-generic-password -U -s "{SERVICE}" -a "{ACCOUNT}" '
            f'-l "{SERVICE}" -X {secret.encode().hex()}\n'
        )
        if len(command) > MAX_COMMAND:
            raise OSError("the Spotify sign-in is too long for the Keychain")
        result = _run([self.tool, "-i"], stdin=command)
        if result.returncode != 0:
            raise OSError(
                f"Keychain refused to save the Spotify sign-in: {result.stderr.strip()}"
            )
        if self.get() != tokens:
            raise OSError("the Keychain didn't keep the Spotify sign-in intact")

    def delete(self) -> None:
        with contextlib.suppress(OSError):
            _run([self.tool, "delete-generic-password", "-s", SERVICE, "-a", ACCOUNT])


class SecretToolStore:
    """Linux Secret Service through libsecret's ``secret-tool``."""

    name = "Secret Service"

    def __init__(self, tool: str) -> None:
        self.tool = tool
        self._attrs = ["service", SERVICE, "account", ACCOUNT]

    def get(self) -> dict[str, Any] | None:
        result = _run([self.tool, "lookup", *self._attrs])
        return _decode(result.stdout.strip()) if result.returncode == 0 else None

    def set(self, tokens: dict[str, Any]) -> None:
        result = _run(
            [self.tool, "store", f"--label={SERVICE}", *self._attrs],
            stdin=json.dumps(tokens),
        )
        if result.returncode != 0:
            raise OSError(
                f"Secret Service refused to save the sign-in: {result.stderr.strip()}"
            )

    def delete(self) -> None:
        with contextlib.suppress(OSError):
            _run([self.tool, "clear", *self._attrs])


class FileStore:
    """Last resort: a 0600 file. Used only without a system keychain."""

    name = "private file"

    def __init__(self, path: Path) -> None:
        self.path = path

    def get(self) -> dict[str, Any] | None:
        with contextlib.suppress(OSError):
            return _decode(self.path.read_text())
        return None

    def set(self, tokens: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(tokens))
        tmp.replace(self.path)

    def delete(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()


def default_store() -> TokenStore:
    if os.environ.get("PIXELFM_TOKEN_STORE") == "memory":  # tests
        return MemoryStore()
    if sys.platform == "darwin" and Path("/usr/bin/security").exists():
        return KeychainStore()
    if tool := shutil.which("secret-tool"):
        return SecretToolStore(tool)
    log.warning("no system keychain found; storing Spotify tokens in a private file")
    return FileStore(data_dir() / "spotify-tokens.json")
