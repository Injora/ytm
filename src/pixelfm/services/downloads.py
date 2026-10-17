"""Explicit, user-initiated audio downloads with progress and cancellation.

Downloads go through yt-dlp, which writes to ``<name>.part`` and renames only
when the transfer completes, so an interrupted download never looks finished.
A download is recorded in the library only after the final file exists and is
non-empty. yt-dlp never selects DRM-protected formats, and PixelFM does not
bypass sign-in, age, region, or other access restrictions.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from pixelfm.models import Song
from pixelfm.paths import display_path
from pixelfm.services.deps import js_runtimes
from pixelfm.services.streams import AUDIO_FORMAT, classify_stream_error
from pixelfm.services.threads import run_in_thread

log = logging.getLogger(__name__)

#: ``(downloaded_bytes, total_bytes_or_None, speed_or_None) -> None``
ProgressFn = Callable[[int, int | None, float | None], None]


class DownloadStatus(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_STATUSES = frozenset({DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING})


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    song: Song
    status: DownloadStatus = DownloadStatus.QUEUED
    downloaded: int = 0
    total: int | None = None
    speed: float | None = None
    error: str | None = None
    path: Path | None = None

    @property
    def fraction(self) -> float | None:
        """0-1 when the total size is known (real progress only)."""
        if self.status is DownloadStatus.DONE:
            return 1.0
        if not self.total:
            return None
        return max(0.0, min(1.0, self.downloaded / self.total))


@dataclass(frozen=True, slots=True)
class DownloadResult:
    path: Path
    size: int
    format: str | None


class DownloadCancelled(Exception):
    """Raised inside the downloader when the user cancels."""


class DownloadError(Exception):
    """A user-facing download failure."""


def default_downloads_dir(env: Mapping[str, str] | None = None) -> Path:
    """``$PIXELFM_DOWNLOADS_DIR``, else ``~/Music/PixelFM`` (macOS and Linux)."""
    env = os.environ if env is None else env
    if override := env.get("PIXELFM_DOWNLOADS_DIR"):
        return Path(override).expanduser()
    if sys.platform != "darwin" and (music := env.get("XDG_MUSIC_DIR")):
        return Path(music).expanduser() / "PixelFM"
    return Path.home() / "Music" / "PixelFM"


def eligible_for_download(song: Song) -> str | None:
    """``None`` if ``song`` can be downloaded, else the reason it can't."""
    if not song.duration:
        return "Live streams and tracks of unknown length can't be downloaded."
    if not song.source_url.startswith(("https://", "http://")):
        return "This track has no downloadable source."
    return None


def _cleanup_partial(tmp: str | None) -> None:
    """Remove yt-dlp's partial files. Only ``.part``/``.ytdl`` files are ever
    deleted, so a completed download can never be removed by accident."""
    if not tmp:
        return
    base = tmp.removesuffix(".part")
    for candidate in (tmp, f"{base}.part", f"{base}.ytdl", f"{tmp}.ytdl"):
        if candidate.endswith((".part", ".ytdl")):
            with contextlib.suppress(OSError):
                Path(candidate).unlink()


def ytdlp_download(
    song: Song,
    directory: Path,
    on_progress: ProgressFn,
    cancelled: Callable[[], bool],
) -> DownloadResult:
    """Blocking audio download with yt-dlp (runs in a worker thread)."""
    import yt_dlp
    from yt_dlp.utils import DownloadCancelled as YtCancelled

    directory.mkdir(parents=True, exist_ok=True)
    partial: dict[str, str | None] = {"tmp": None}

    def hook(d: dict[str, Any]) -> None:
        if cancelled():
            raise YtCancelled("cancelled by user")
        partial["tmp"] = d.get("tmpfilename") or partial["tmp"]
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            on_progress(
                int(d.get("downloaded_bytes") or 0),
                int(total) if total else None,
                d.get("speed"),
            )

    class _Logger:
        def debug(self, msg: str) -> None:
            log.debug("yt-dlp: %s", msg)

        def info(self, msg: str) -> None:
            log.debug("yt-dlp: %s", msg)

        def warning(self, msg: str) -> None:
            log.info("yt-dlp warning: %s", msg)

        def error(self, msg: str) -> None:
            log.warning("yt-dlp error: %s", msg)

    options: dict[str, Any] = {
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
        "format": AUDIO_FORMAT,
        # Named by PixelFM's stable track id (URL-safe characters only), so two
        # tracks can never share a file and nothing is overwritten.
        "outtmpl": str(
            directory / f"%(uploader,channel|Unknown)s - %(title)s [{song.id}].%(ext)s"
        ),
        "overwrites": False,  # never replace an existing file
        "continuedl": True,
        "socket_timeout": 20,
        "progress_hooks": [hook],
        "logger": _Logger(),
        "postprocessors": [],  # keep the original audio; no re-encoding
    }
    if runtimes := js_runtimes():
        options["js_runtimes"] = runtimes
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(song.source_url, download=True)
    except YtCancelled:
        _cleanup_partial(partial["tmp"])
        raise DownloadCancelled from None
    except Exception as exc:
        if cancelled():
            _cleanup_partial(partial["tmp"])
            raise DownloadCancelled from None
        _cleanup_partial(partial["tmp"])
        raise DownloadError(classify_stream_error(exc).message) from exc

    downloads = (info or {}).get("requested_downloads") or []
    filepath = downloads[0].get("filepath") if downloads else None
    path = Path(filepath) if filepath else None
    if path is None or not path.is_file() or path.stat().st_size == 0:
        raise DownloadError("The download did not produce a complete file.")
    fmt = " ".join(str(info.get(k)) for k in ("ext", "acodec") if info.get(k))
    return DownloadResult(path, path.stat().st_size, fmt or None)


Downloader = Callable[[Song, Path, ProgressFn, Callable[[], bool]], DownloadResult]


class DownloadManager:
    """Runs downloads one at a time in a worker thread.

    ``on_update`` is called on the event loop for every state change (progress
    is throttled to about 4 updates per second per download).
    """

    def __init__(
        self,
        directory: Callable[[], Path],
        on_update: Callable[[DownloadProgress], None],
        downloader: Downloader = ytdlp_download,
        progress_interval: float = 0.25,
    ) -> None:
        self._directory = directory
        self._on_update = on_update
        self._downloader = downloader
        self._interval = progress_interval
        self.jobs: dict[str, DownloadProgress] = {}
        self._pending: asyncio.Queue[Song] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._cancel: set[str] = set()

    def is_active(self, song_id: str) -> bool:
        job = self.jobs.get(song_id)
        return job is not None and job.status in ACTIVE_STATUSES

    def enqueue(self, song: Song) -> bool:
        """Queue a download. Returns ``False`` if it is already queued/running."""
        if self.is_active(song.id):
            return False
        if self._pending is None:
            self._pending = asyncio.Queue()
        self._cancel.discard(song.id)
        self._set(DownloadProgress(song))
        self._pending.put_nowait(song)
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())
        return True

    def cancel(self, song_id: str) -> bool:
        if not self.is_active(song_id):
            return False
        self._cancel.add(song_id)
        job = self.jobs[song_id]
        if job.status is DownloadStatus.QUEUED:
            self._set(replace(job, status=DownloadStatus.CANCELLED))
        return True

    async def aclose(self) -> None:
        for song_id, job in list(self.jobs.items()):
            if job.status in ACTIVE_STATUSES:
                self._cancel.add(song_id)
        if self._worker is not None:
            self._worker.cancel()

    def _set(self, progress: DownloadProgress) -> None:
        self.jobs[progress.song.id] = progress
        try:
            self._on_update(progress)
        except Exception:
            log.exception("download listener failed")

    async def _run(self) -> None:
        assert self._pending is not None
        loop = asyncio.get_running_loop()
        while not self._pending.empty():
            song = await self._pending.get()
            if song.id in self._cancel:
                continue
            self._set(replace(self.jobs[song.id], status=DownloadStatus.DOWNLOADING))
            last = 0.0

            def progress(
                done: int,
                total: int | None,
                speed: float | None,
                sid: str = song.id,
            ) -> None:
                nonlocal last
                now = loop.time()
                if now - last < self._interval:
                    return
                last = now
                loop.call_soon_threadsafe(self._progress, sid, done, total, speed)

            try:
                result = await run_in_thread(
                    self._downloader,
                    song,
                    self._directory(),
                    progress,
                    lambda sid=song.id: sid in self._cancel,
                )
            except DownloadCancelled:
                self._set(replace(self.jobs[song.id], status=DownloadStatus.CANCELLED))
            except DownloadError as exc:
                self._set(
                    replace(
                        self.jobs[song.id], status=DownloadStatus.FAILED, error=str(exc)
                    )
                )
            except Exception as exc:
                log.exception("download failed")
                self._set(
                    replace(
                        self.jobs[song.id],
                        status=DownloadStatus.FAILED,
                        error=f"Download failed: {exc}",
                    )
                )
            else:
                self._set(
                    replace(
                        self.jobs[song.id],
                        status=DownloadStatus.DONE,
                        downloaded=result.size,
                        total=result.size,
                        path=result.path,
                    )
                )

    def _progress(
        self, song_id: str, done: int, total: int | None, speed: float | None
    ) -> None:
        job = self.jobs.get(song_id)
        if job is None or job.status is not DownloadStatus.DOWNLOADING:
            return
        self._set(replace(job, downloaded=done, total=total, speed=speed))


def storage_summary(directory: Path, sizes: list[int]) -> str:
    """One line about the downloads folder (runs fine off the UI thread)."""
    used = sum(sizes)
    try:
        free = shutil.disk_usage(
            directory if directory.exists() else directory.parent
        ).free
        free_text = f" · {human_size(free)} free"
    except OSError:
        free_text = ""
    files = f"{len(sizes)} file{'' if len(sizes) == 1 else 's'}"
    return f"{files} · {human_size(used)}{free_text} · {display_path(directory)}"


def human_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"  # pragma: no cover
