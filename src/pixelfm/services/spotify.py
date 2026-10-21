"""Spotify playlist metadata: links, sign-in, the Web API, and export files.

What Spotify allows (checked October 2026, Web API in Development Mode):

* Every user needs their own Spotify developer app. It's free to create, but
  its owner needs Spotify Premium, and it allows up to 5 users. Organisations
  with 250k monthly users can apply for more; PixelFM cannot.
* Sign-in uses the Authorization Code flow with PKCE, so no client secret is
  needed or stored. Users sign in on spotify.com in their browser; PixelFM
  never sees their password. Tokens are kept in the macOS Keychain.
* ``GET /playlists/{id}/items`` only returns tracks for playlists the user
  owns or collaborates on (since March 2026). For other playlists Spotify
  returns metadata only. PixelFM reports this instead of working around it;
  the user can copy the playlist into one they own, or import an export file.

Only metadata is read: titles, artists, album, duration, artwork, ids.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import csv
import hashlib
import json
import logging
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pixelfm.services.artwork import _ssl_context
from pixelfm.services.credentials import TokenStore
from pixelfm.services.threads import run_in_thread

log = logging.getLogger(__name__)

API = "https://api.spotify.com/v1"
ACCOUNTS = "https://accounts.spotify.com"
SCOPES = "playlist-read-private playlist-read-collaborative"
#: Spotify accepts http only for loopback IPs; register exactly this URI.
REDIRECT_PORT = 8898
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
PAGE_SIZE = 50  # the API maximum for playlist items
MAX_TRACKS = 10_000
LOGIN_TIMEOUT = 180.0
MAX_RETRIES = 4
MAX_RETRY_WAIT = 30.0
_BASE62 = r"[0-9A-Za-z]{22}"
_LINK = re.compile(
    rf"^https?://open\.spotify\.com/(?:intl-[a-z]{{2}}(?:-[A-Za-z]{{2}})?/)?"
    rf"(?:embed/)?(playlist|album|track|artist|show|episode)/({_BASE62})(?:[/?#].*)?$"
)
_URI = re.compile(rf"^spotify:(playlist|album|track|artist|show|episode):({_BASE62})$")
SIGNED_IN_PAGE = (
    "<h1>Spotify sign-in received.</h1>"
    "<p>Close this tab and go back to PixelFM; it finishes signing in and shows "
    "the playlist there.</p>"
)
NOT_SIGNED_IN_PAGE = (
    "<h1>Spotify sign-in did not finish.</h1><p>Go back to PixelFM.</p>"
)
DEV_MODE_HELP = (
    "Spotify only gives the track list of playlists you own or collaborate on "
    "to personal apps (a Spotify rule since March 2026). To import this one: in "
    "Spotify, open it, select all tracks, choose Add to playlist > New playlist, "
    "and import your copy. Or import an export file (see the README)."
)


class SpotifyError(Exception):
    """A user-facing Spotify problem. ``retryable``: worth trying again."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class NeedsLogin(SpotifyError):
    """Not signed in, or the sign-in expired or was revoked."""


class NotAllowed(SpotifyError):
    """Spotify refuses this playlist's tracks to this app (see DEV_MODE_HELP)."""


# ------------------------------------------------------------------ links


def parse_playlist_link(text: str) -> str:
    """The playlist id from a link or ``spotify:playlist:`` URI.

    Raises:
        SpotifyError: Not a Spotify playlist link (with what it is instead).
    """
    text = text.strip()
    match = _LINK.match(text) or _URI.match(text)
    if match is None:
        raise SpotifyError(
            "That isn't a Spotify playlist link. Copy it in Spotify with "
            "Share > Copy link to playlist (https://open.spotify.com/playlist/…)."
        )
    kind, playlist_id = match.groups()
    if kind != "playlist":
        raise SpotifyError(f"That is a Spotify {kind} link, not a playlist link.")
    return playlist_id


# ----------------------------------------------------------------- models


@dataclass(frozen=True, slots=True)
class SpotifyTrack:
    """One playlist entry, as much as Spotify tells us about it."""

    position: int
    title: str
    artists: tuple[str, ...]
    album: str = ""
    duration: float = 0.0  # seconds; 0 if unknown
    spotify_id: str | None = None
    artwork_url: str | None = None
    explicit: bool = False
    is_local: bool = False  # a local file in the user's Spotify app
    available: bool = True  # False: removed from Spotify, or not a track
    note: str = ""

    @property
    def artist(self) -> str:
        return ", ".join(self.artists) or "Unknown artist"


@dataclass(frozen=True, slots=True)
class SpotifyPlaylist:
    name: str
    owner: str = ""
    total: int = 0
    playlist_id: str | None = None
    url: str | None = None
    source: str = "spotify"  # or "file"
    tracks: tuple[SpotifyTrack, ...] = field(default_factory=tuple)


def _artwork(images: Any) -> str | None:
    """The image closest to 300px wide (big enough, not huge)."""
    if not isinstance(images, list):
        return None
    valid = [i for i in images if isinstance(i, dict) and isinstance(i.get("url"), str)]
    if not valid:
        return None
    return min(valid, key=lambda i: abs((i.get("width") or 300) - 300))["url"]


def parse_item(position: int, row: Any) -> SpotifyTrack:
    """A ``PlaylistTrackObject`` (2026 shape: ``item``; older: ``track``)."""
    row = row if isinstance(row, dict) else {}
    item = row.get("item")
    if not isinstance(item, dict):
        item = row.get("track") if isinstance(row.get("track"), dict) else None
    if item is None:
        return SpotifyTrack(
            position,
            "Unavailable track",
            (),
            available=False,
            note="removed from Spotify",
        )
    if item.get("type") == "episode":
        return SpotifyTrack(
            position,
            str(item.get("name") or "Podcast episode"),
            (),
            available=False,
            note="podcast episode (not imported)",
        )
    artists = tuple(
        str(a["name"])
        for a in item.get("artists") or []
        if isinstance(a, dict) and a.get("name")
    )
    album = item.get("album") if isinstance(item.get("album"), dict) else {}
    duration_ms = item.get("duration_ms")
    is_local = bool(row.get("is_local") or item.get("is_local"))
    spotify_id = item.get("id") if isinstance(item.get("id"), str) else None
    title = str(item.get("name") or "").strip()
    return SpotifyTrack(
        position=position,
        title=title or "Untitled",
        artists=artists,
        album=str(album.get("name") or ""),
        duration=duration_ms / 1000 if isinstance(duration_ms, int | float) else 0.0,
        spotify_id=None if is_local else spotify_id,
        artwork_url=_artwork(album.get("images")),
        explicit=bool(item.get("explicit")),
        is_local=is_local,
        available=bool(title),
        note="local file in Spotify" if is_local else "",
    )


# ------------------------------------------------------------- export files


def _split_artists(text: str) -> tuple[str, ...]:
    parts = re.split(r"\s*[,;]\s*", text.strip()) if text.strip() else []
    return tuple(p for p in parts if p)


def read_export(path: Path) -> list[SpotifyPlaylist]:
    """Playlists from an export file the user made themselves.

    * Spotify's own account-data export (Account > Privacy > Download your
      data): ``Playlist1.json`` and friends.
    * A CSV with a header row: Exportify-style (``Track Name``,
      ``Artist Name(s)``, ``Album Name``, ``Duration (ms)``, ``Track URI``,
      ``Explicit``) or simply ``title``/``artist``/``album``/``duration``.
    """
    try:
        text = path.expanduser().read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise SpotifyError(f"Can't read {path}: {exc.strerror or exc}") from None
    if path.suffix.lower() == ".json" or text.lstrip().startswith("{"):
        return _read_account_json(text)
    return [_read_csv(text, path.stem)]


def _read_account_json(text: str) -> list[SpotifyPlaylist]:
    try:
        data = json.loads(text)
    except ValueError:
        raise SpotifyError("That file isn't valid JSON.") from None
    playlists = data.get("playlists") if isinstance(data, dict) else None
    if not isinstance(playlists, list):
        raise SpotifyError(
            'That JSON has no "playlists" list. Use Playlist1.json from '
            "Spotify's account-data download."
        )
    result = []
    for entry in playlists:
        if not isinstance(entry, dict):
            continue
        tracks = []
        for i, item in enumerate(entry.get("items") or []):
            item = item if isinstance(item, dict) else {}
            track = item.get("track") or item.get("localTrack")
            if not isinstance(track, dict):
                kind = "podcast episode" if item.get("episode") else "unsupported item"
                tracks.append(
                    SpotifyTrack(
                        i,
                        f"({kind})",
                        (),
                        available=False,
                        note=f"{kind} (not imported)",
                    )
                )
                continue
            uri = str(track.get("trackUri") or track.get("uri") or "")
            match = _URI.match(uri)
            tracks.append(
                SpotifyTrack(
                    position=i,
                    title=str(
                        track.get("trackName") or track.get("name") or "Untitled"
                    ),
                    artists=_split_artists(str(track.get("artistName") or "")),
                    album=str(track.get("albumName") or ""),
                    spotify_id=match.group(2)
                    if match and match.group(1) == "track"
                    else None,
                    is_local="localTrack" in item and not item.get("track"),
                )
            )
        result.append(
            SpotifyPlaylist(
                name=str(entry.get("name") or "Imported playlist"),
                total=len(tracks),
                source="file",
                tracks=tuple(tracks),
            )
        )
    if not result:
        raise SpotifyError("That export contains no playlists.")
    return result


_COLUMNS = {
    "title": ("track name", "title", "name", "track", "song"),
    "artist": ("artist name(s)", "artist names", "artist", "artists"),
    "album": ("album name", "album"),
    "duration": ("duration (ms)", "duration_ms", "duration"),
    "uri": ("track uri", "spotify uri", "uri"),
    "explicit": ("explicit",),
}


def _read_csv(text: str, name: str) -> SpotifyPlaylist:
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        raise SpotifyError("That CSV file is empty.")
    header = [h.strip().casefold() for h in rows[0]]
    index = {
        key: next((header.index(n) for n in names if n in header), None)
        for key, names in _COLUMNS.items()
    }
    if index["title"] is None or index["artist"] is None:
        raise SpotifyError(
            "That CSV needs a header row with a title and an artist column "
            '(e.g. "Track Name" and "Artist Name(s)").'
        )

    def cell(row: list[str], key: str) -> str:
        i = index[key]
        return row[i].strip() if i is not None and i < len(row) else ""

    in_ms = index["duration"] is not None and "ms" in header[index["duration"]]

    def seconds(raw: str) -> float:
        """Duration cell: milliseconds, seconds, or m:ss."""
        if ":" in raw:
            minutes, _, secs = raw.rpartition(":")
            with contextlib.suppress(ValueError):
                return int(minutes or 0) * 60 + float(secs)
            return 0.0
        try:
            value = float(raw)
        except ValueError:
            return 0.0
        return value / 1000 if in_ms or value > 10_000 else value

    tracks = []
    for i, row in enumerate(r for r in rows[1:] if any(c.strip() for c in r)):
        duration = seconds(cell(row, "duration"))
        uri = _URI.match(cell(row, "uri"))
        title = cell(row, "title")
        tracks.append(
            SpotifyTrack(
                position=i,
                title=title or "Untitled",
                artists=_split_artists(cell(row, "artist")),
                album=cell(row, "album"),
                duration=duration,
                spotify_id=uri.group(2) if uri and uri.group(1) == "track" else None,
                explicit=cell(row, "explicit").casefold() in ("true", "yes", "1"),
                available=bool(title),
            )
        )
    return SpotifyPlaylist(
        name=name, total=len(tracks), source="file", tracks=tuple(tracks)
    )


# ------------------------------------------------------------ the Web API


Http = Callable[[urllib.request.Request], tuple[int, dict[str, str], bytes]]


def urllib_http(request: urllib.request.Request) -> tuple[int, dict[str, str], bytes]:
    """Blocking HTTP with certificate checks (run in a worker thread)."""
    try:
        with urllib.request.urlopen(
            request, timeout=20, context=_ssl_context()
        ) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read()


def pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge) for the PKCE flow."""
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


class SpotifyClient:
    """Signs in with PKCE, keeps tokens fresh, and reads playlists."""

    def __init__(
        self,
        client_id: str,
        store: TokenStore,
        *,
        http: Http = urllib_http,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
        redirect_port: int = REDIRECT_PORT,
    ) -> None:
        self.client_id = client_id.strip()
        self.store = store
        self._http = http
        self._sleep = sleep
        self._clock = clock
        self.redirect_uri = f"http://127.0.0.1:{redirect_port}/callback"
        self._port = redirect_port

    # -- sign-in ---------------------------------------------------------

    @property
    def signed_in(self) -> bool:
        return bool(self.store.get())

    def authorize_url(self, challenge: str, state: str) -> str:
        query = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": self.redirect_uri,
                "code_challenge_method": "S256",
                "code_challenge": challenge,
                "scope": SCOPES,
                "state": state,
            }
        )
        return f"{ACCOUNTS}/authorize?{query}"

    async def login(self, open_browser: Callable[[str], object]) -> None:
        """Sign in through the browser; the callback comes to a one-shot
        server on 127.0.0.1 (never reachable from other machines)."""
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(16)
        loop = asyncio.get_running_loop()
        result: asyncio.Future[dict[str, str]] = loop.create_future()

        async def handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                request_line = (await reader.readline()).decode("latin-1")
                while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                    pass
                target = request_line.split(" ")[1] if " " in request_line else ""
                query = dict(
                    urllib.parse.parse_qsl(urllib.parse.urlsplit(target).query)
                )
                ok = urllib.parse.urlsplit(target).path == "/callback"
                body = SIGNED_IN_PAGE if ok and "code" in query else NOT_SIGNED_IN_PAGE
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                    b"Connection: close\r\n\r\n" + body.encode()
                )
                await writer.drain()
                if ok and not result.done():
                    result.set_result(query)
            finally:
                writer.close()

        try:
            server = await asyncio.start_server(handle, "127.0.0.1", self._port)
        except OSError:
            log.warning("spotify sign-in: port %s is busy", self._port)
            raise SpotifyError(
                f"Port {self._port} on this Mac is busy, so the Spotify sign-in "
                "can't come back to PixelFM. Close whatever uses it and try again."
            ) from None
        log.info(
            "spotify sign-in: waiting for the browser on %s (client %s…)",
            self.redirect_uri,
            self.client_id[:6],
        )
        try:
            open_browser(self.authorize_url(challenge, state))
            query = await asyncio.wait_for(result, LOGIN_TIMEOUT)
        except TimeoutError:
            log.warning("spotify sign-in: no answer from the browser in 3 minutes")
            raise SpotifyError(
                "Spotify sign-in timed out without coming back to PixelFM. In your "
                f"Spotify app the redirect URI must be exactly {self.redirect_uri}; "
                "if the browser showed an error, it says why."
            ) from None
        finally:
            server.close()
        log.info(
            "spotify sign-in: browser came back (%s)",
            f"error: {query['error']}" if "error" in query else "with a code",
        )
        if query.get("state") != state:
            raise SpotifyError("Spotify sign-in was not for this request; try again.")
        if "error" in query:
            reason = query["error"]
            raise SpotifyError(
                "Spotify sign-in was cancelled."
                if reason == "access_denied"
                else f"Spotify sign-in failed: {reason}."
            )
        await self._token(
            {
                "grant_type": "authorization_code",
                "code": query.get("code", ""),
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "code_verifier": verifier,
            }
        )

    def logout(self) -> None:
        self.store.delete()

    async def _token(self, form: dict[str, str]) -> str:
        request = urllib.request.Request(
            f"{ACCOUNTS}/api/token",
            data=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        status, _, body = await self._request(request)
        data = _json(body)
        log.info(
            "spotify token (%s): HTTP %s %s",
            form.get("grant_type"),
            status,
            "ok" if status == 200 else data.get("error", ""),
        )
        if status != 200 or "access_token" not in data:
            if form.get("grant_type") == "refresh_token":
                self.store.delete()
                raise NeedsLogin("Your Spotify sign-in expired. Sign in again.")
            description = data.get("error_description") or data.get("error") or status
            raise SpotifyError(
                f"Spotify refused the sign-in ({description}). Check the client ID "
                f"and that {self.redirect_uri} is a redirect URI of your Spotify app."
            )
        previous = self.store.get() or {}
        tokens = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token")
            or previous.get("refresh_token", ""),
            "expires_at": self._clock() + float(data.get("expires_in", 3600)) - 60,
            "client_id": self.client_id,
        }
        try:
            self.store.set(tokens)
        except OSError as exc:
            log.warning("spotify sign-in: could not store the tokens: %s", exc)
            raise SpotifyError(
                f"Signed in, but the sign-in couldn't be saved ({exc}). Try again."
            ) from None
        log.info("spotify sign-in: saved to %s", getattr(self.store, "name", "store"))
        return str(tokens["access_token"])

    async def access_token(self) -> str:
        tokens = self.store.get()
        if not tokens or tokens.get("client_id") != self.client_id:
            raise NeedsLogin("Sign in to Spotify first.")
        if float(tokens.get("expires_at", 0)) > self._clock():
            return str(tokens["access_token"])
        if not tokens.get("refresh_token"):
            raise NeedsLogin("Your Spotify sign-in expired. Sign in again.")
        return await self._token(
            {
                "grant_type": "refresh_token",
                "refresh_token": str(tokens["refresh_token"]),
                "client_id": self.client_id,
            }
        )

    # -- requests --------------------------------------------------------

    async def _request(
        self, request: urllib.request.Request
    ) -> tuple[int, dict[str, str], bytes]:
        """One request with bounded retries for rate limits and outages."""
        for attempt in range(MAX_RETRIES + 1):
            try:
                status, headers, body = await run_in_thread(self._http, request)
            except (OSError, urllib.error.URLError) as exc:
                if attempt == MAX_RETRIES:
                    raise SpotifyError(
                        "Can't reach Spotify (network problem). Nothing was "
                        "imported; try again.",
                        retryable=True,
                    ) from exc
                await self._sleep(min(MAX_RETRY_WAIT, 2.0**attempt))
                continue
            if status == 429 or status >= 500:
                if attempt == MAX_RETRIES:
                    raise SpotifyError(
                        "Spotify is busy or rate-limiting PixelFM; try again in a "
                        "minute.",
                        retryable=True,
                    )
                wait = _retry_after(headers) if status == 429 else 2.0**attempt
                log.info("spotify %s; retrying in %.1fs", status, wait)
                await self._sleep(min(MAX_RETRY_WAIT, wait))
                continue
            return status, headers, body
        raise AssertionError("unreachable")  # pragma: no cover

    async def get(self, url: str, *, retried: bool = False) -> dict[str, Any]:
        token = await self.access_token()
        request = urllib.request.Request(
            url if url.startswith("https://") else f"{API}{url}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        status, _, body = await self._request(request)
        if status != 200:
            error = _json(body).get("error")
            log.info(
                "spotify GET %s: HTTP %s %s",
                urllib.parse.urlsplit(request.full_url).path,
                status,
                error.get("message", "") if isinstance(error, dict) else "",
            )
        if status == 401 and not retried:
            tokens = self.store.get() or {}
            tokens["expires_at"] = 0  # force a refresh, then try once more
            self.store.set(tokens)
            return await self.get(url, retried=True)
        if status == 401:
            raise NeedsLogin(
                "Spotify no longer accepts PixelFM's sign-in. Sign in again."
            )
        if status == 403:
            message = _json(body).get("error", {})
            text = message.get("message", "") if isinstance(message, dict) else ""
            if "premium" in text.casefold():
                raise SpotifyError(
                    "Spotify says the owner of your Spotify developer app needs an "
                    "active Premium subscription for the app to work."
                )
            if "user" in text.casefold() and "registered" in text.casefold():
                raise SpotifyError(
                    "Your Spotify account isn't on your developer app's user list. "
                    "Add it under User Management in the Spotify dashboard."
                )
            raise NotAllowed(DEV_MODE_HELP)
        if status == 404:
            raise SpotifyError(
                "Spotify can't find that playlist: it may be private, deleted, or "
                "the link may be wrong."
            )
        if status != 200:
            raise SpotifyError(f"Spotify returned an error ({status}).", retryable=True)
        return _json(body)

    async def playlist(
        self,
        playlist_id: str,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> SpotifyPlaylist:
        """Metadata and every track, page by page (cancel the task to stop)."""
        meta = await self.get(
            f"/playlists/{playlist_id}?fields="
            "name,owner(display_name,id),public,collaborative,items(total),tracks(total)"
        )
        paging = (
            meta.get("items")
            if isinstance(meta.get("items"), dict)
            else meta.get("tracks")
        )
        total = int((paging or {}).get("total") or 0)
        owner = meta.get("owner") if isinstance(meta.get("owner"), dict) else {}
        tracks: list[SpotifyTrack] = []
        url: str | None = (
            f"{API}/playlists/{playlist_id}/items?limit={PAGE_SIZE}&offset=0"
        )
        while url and len(tracks) < MAX_TRACKS:
            page = await self.get(url)
            rows = page.get("items")
            if not isinstance(rows, list):
                raise NotAllowed(DEV_MODE_HELP)
            tracks.extend(parse_item(len(tracks), row) for row in rows)
            if on_progress is not None:
                on_progress(len(tracks), total)
            url = page.get("next") if isinstance(page.get("next"), str) else None
        return SpotifyPlaylist(
            name=str(meta.get("name") or "Spotify playlist"),
            owner=str(owner.get("display_name") or owner.get("id") or ""),
            total=total or len(tracks),
            playlist_id=playlist_id,
            url=f"https://open.spotify.com/playlist/{playlist_id}",
            tracks=tuple(tracks),
        )


def _retry_after(headers: dict[str, str]) -> float:
    for key, value in headers.items():
        if key.casefold() == "retry-after":
            with contextlib.suppress(ValueError):
                return max(0.5, float(value))
    return 2.0


def _json(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


__all__: Sequence[str] = (
    "DEV_MODE_HELP",
    "NeedsLogin",
    "NotAllowed",
    "SpotifyClient",
    "SpotifyError",
    "SpotifyPlaylist",
    "SpotifyTrack",
    "parse_item",
    "parse_playlist_link",
    "read_export",
)
