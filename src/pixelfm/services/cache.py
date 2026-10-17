"""In-memory LRU cache and a small on-disk byte cache."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from pathlib import Path

log = logging.getLogger(__name__)


class LRUCache[K: Hashable, V]:
    """Least-recently-used cache.

    Args:
        maxsize: Maximum number of entries kept.
        ttl: Seconds an entry stays valid, or ``None`` to never expire.
        clock: Monotonic time source; injectable for deterministic tests.
    """

    def __init__(
        self,
        maxsize: int,
        ttl: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1")
        self.maxsize = maxsize
        self.ttl = ttl
        self._clock = clock
        self._data: OrderedDict[K, tuple[float, V]] = OrderedDict()

    def get(self, key: K) -> V | None:
        """Return the cached value, or ``None`` if missing or expired."""
        item = self._data.get(key)
        if item is None:
            return None
        stored_at, value = item
        if self.ttl is not None and self._clock() - stored_at >= self.ttl:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: K, value: V) -> None:
        """Store ``value``, evicting the least-recently-used entry if full."""
        self._data[key] = (self._clock(), value)
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        return self.get(key) is not None  # type: ignore[arg-type]


class DiskCache:
    """Size-capped byte store in a cache directory.

    Entries are named by the SHA-256 of their key, written atomically, and
    evicted least-recently-used first. Contents are opaque data: nothing here
    is ever executed, and callers validate bytes when they read them. Every
    I/O error is swallowed (a cache miss), so a broken cache never breaks the
    app. Safe to delete the directory at any time.
    """

    def __init__(
        self, directory: Path, *, max_bytes: int = 50_000_000, max_files: int = 2000
    ) -> None:
        self.directory = directory
        self.max_bytes = max_bytes
        self.max_files = max_files
        self._writes = 0

    def _path(self, key: str) -> Path:
        return self.directory / hashlib.sha256(key.encode()).hexdigest()

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            data = path.read_bytes()
            os.utime(path)  # mark as recently used
        except OSError:
            return None
        return data

    def set(self, key: str, data: bytes) -> None:
        path = self._path(key)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(data)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except OSError as exc:
            log.info("disk cache write failed: %s", exc)
            with contextlib.suppress(OSError):
                tmp.unlink()
            return
        self._writes += 1
        if self._writes % 25 == 1:
            self.prune()

    def discard(self, key: str) -> None:
        with contextlib.suppress(OSError):
            self._path(key).unlink()

    def prune(self) -> int:
        """Evict least-recently-used entries over the limits. Returns count."""
        try:
            entries = [
                (entry.stat().st_mtime, entry.stat().st_size, entry)
                for entry in self.directory.iterdir()
                if entry.is_file() and not entry.name.startswith(".")
            ]
        except OSError:
            return 0
        entries.sort(key=lambda item: item[0])
        total = sum(size for _, size, _ in entries)
        removed = 0
        while entries and (total > self.max_bytes or len(entries) > self.max_files):
            _, size, entry = entries.pop(0)
            with contextlib.suppress(OSError):
                entry.unlink()
                removed += 1
            total -= size
        return removed
