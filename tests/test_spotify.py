"""Spotify links, sign-in, the Web API client, export files, token storage.

The API is faked; no test talks to Spotify or needs credentials.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from pixelfm.services.credentials import FileStore, KeychainStore, MemoryStore
from pixelfm.services.spotify import (
    DEV_MODE_HELP,
    NeedsLogin,
    NotAllowed,
    SpotifyClient,
    SpotifyError,
    parse_item,
    parse_playlist_link,
    read_export,
)

PID = "37i9dQZF1DXcBWIGoYBM5M"


# ------------------------------------------------------------------ links


@pytest.mark.parametrize(
    "link",
    [
        f"https://open.spotify.com/playlist/{PID}",
        f"https://open.spotify.com/playlist/{PID}?si=abc123&pt=x",
        f"https://open.spotify.com/intl-de/playlist/{PID}",
        f"https://open.spotify.com/embed/playlist/{PID}",
        f"spotify:playlist:{PID}",
        f"  https://open.spotify.com/playlist/{PID}/  ",
    ],
)
def test_playlist_links_are_accepted(link: str) -> None:
    assert parse_playlist_link(link) == PID


@pytest.mark.parametrize(
    ("link", "message"),
    [
        (f"https://open.spotify.com/album/{PID}", "album link"),
        (f"https://open.spotify.com/track/{PID}", "track link"),
        ("https://open.spotify.com/playlist/short", "isn't a Spotify playlist link"),
        ("https://evil.example.com/playlist/" + PID, "isn't a Spotify playlist link"),
        ("not a link", "isn't a Spotify playlist link"),
        (f"http://open.spotify.com.evil.io/playlist/{PID}", "isn't"),
    ],
)
def test_other_links_are_refused(link: str, message: str) -> None:
    with pytest.raises(SpotifyError, match=message):
        parse_playlist_link(link)


# ------------------------------------------------------------- the API


def api_track(n: int, **extra: Any) -> dict[str, Any]:
    return {
        "type": "track",
        "id": f"{n:022d}",
        "name": f"Song {n}",
        "artists": [{"name": f"Artist {n}"}, {"name": "Guest"}],
        "album": {
            "name": f"Album {n}",
            "images": [
                {"url": f"https://i.scdn.co/{n}-640", "width": 640},
                {"url": f"https://i.scdn.co/{n}-300", "width": 300},
            ],
        },
        "duration_ms": 180_000 + n,
        "explicit": n % 2 == 0,
        **extra,
    }


class FakeSpotify:
    """Routes requests like the Web API (2026 shapes), with failure knobs."""

    def __init__(self, total: int = 120) -> None:
        self.total = total
        self.requests: list[str] = []
        self.rate_limit_once = False
        self.status_for_items: int | None = None
        self.error_message = ""
        self.expire_once = False
        self.outage = 0
        self.metadata_only = False
        self.tokens_issued = 0

    def rows(self, offset: int, limit: int) -> list[Any]:
        rows: list[Any] = []
        for n in range(offset, min(self.total, offset + limit)):
            if n == 3:
                rows.append({"is_local": False, "item": None, "track": False})
            elif n == 5:
                rows.append(
                    {
                        "is_local": True,
                        "item": {
                            "type": "track",
                            "id": None,
                            "name": "My Demo",
                            "artists": [{"name": "Me"}],
                            "album": {"name": ""},
                            "duration_ms": 61_000,
                        },
                    }
                )
            elif n == 7:
                rows.append({"item": {"type": "episode", "name": "A Podcast"}})
            else:
                rows.append({"is_local": False, "item": api_track(n), "track": True})
        return rows

    def __call__(
        self, request: urllib.request.Request
    ) -> tuple[int, dict[str, str], bytes]:
        url = request.full_url
        self.requests.append(url)
        if self.outage:
            self.outage -= 1
            raise OSError("network down")
        if url.endswith("/api/token"):
            self.tokens_issued += 1
            form = dict(urllib.parse.parse_qsl(request.data.decode()))  # type: ignore[union-attr]
            assert "client_secret" not in form  # PKCE: never a secret
            if (
                form["grant_type"] == "refresh_token"
                and form["refresh_token"] == "revoked"
            ):
                return 400, {}, b'{"error": "invalid_grant"}'
            body = {"access_token": f"token{self.tokens_issued}", "expires_in": 3600}
            if form["grant_type"] == "authorization_code":
                assert form["code"] == "the-code" and len(form["code_verifier"]) >= 43
                body["refresh_token"] = "refresh"
            return 200, {}, json.dumps(body).encode()
        assert request.headers["Authorization"].startswith("Bearer ")
        if self.expire_once:
            self.expire_once = False
            return 401, {}, b"{}"
        if self.rate_limit_once:
            self.rate_limit_once = False
            return 429, {"Retry-After": "3"}, b"{}"
        parsed = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        if parsed.path.endswith("/items"):
            if self.status_for_items:
                message = {"error": {"status": 403, "message": self.error_message}}
                return self.status_for_items, {}, json.dumps(message).encode()
            if self.metadata_only:
                return 200, {}, b'{"total": 5}'
            offset, limit = int(query["offset"]), int(query["limit"])
            assert limit <= 50
            nxt = offset + limit
            body = {
                "items": self.rows(offset, limit),
                "total": self.total,
                "next": (
                    f"https://api.spotify.com/v1/playlists/{PID}/items?limit={limit}&offset={nxt}"
                    if nxt < self.total
                    else None
                ),
            }
            return 200, {}, json.dumps(body).encode()
        if parsed.path.endswith(f"/playlists/{PID}"):
            body = {
                "name": "Road Trip",
                "owner": {"display_name": "Anna", "id": "anna"},
                "items": {"total": self.total},
            }
            return 200, {}, json.dumps(body).encode()
        return 404, {}, b"{}"


async def no_sleep(seconds: float) -> None:
    no_sleep.waits.append(seconds)  # type: ignore[attr-defined]


no_sleep.waits = []  # type: ignore[attr-defined]


def signed_in(**tokens: Any) -> MemoryStore:
    return MemoryStore(
        {
            "access_token": "token0",
            "refresh_token": "refresh",
            "expires_at": 10**12,
            "client_id": "cid",
            **tokens,
        }
    )


def client_for(
    api: FakeSpotify, store: MemoryStore | None = None, **kw: Any
) -> SpotifyClient:
    return SpotifyClient("cid", store or signed_in(), http=api, sleep=no_sleep, **kw)


async def test_reads_every_page_in_order() -> None:
    api = FakeSpotify(total=120)
    progress: list[tuple[int, int]] = []
    playlist = await client_for(api).playlist(
        PID, on_progress=lambda d, t: progress.append((d, t))
    )
    assert (playlist.name, playlist.owner, playlist.total) == ("Road Trip", "Anna", 120)
    assert len(playlist.tracks) == 120
    assert [t.position for t in playlist.tracks] == list(range(120))
    assert progress == [(50, 120), (100, 120), (120, 120)]
    first = playlist.tracks[0]
    assert (first.title, first.artists, first.album) == (
        "Song 0",
        ("Artist 0", "Guest"),
        "Album 0",
    )
    assert first.duration == 180.0 and first.explicit and first.spotify_id == "0" * 22
    assert first.artwork_url == "https://i.scdn.co/0-300"  # closest to 300px
    assert not playlist.tracks[3].available  # removed from Spotify
    local = playlist.tracks[5]
    assert local.is_local and local.spotify_id is None and local.available
    assert not playlist.tracks[7].available and "podcast" in playlist.tracks[7].note
    assert sum(1 for u in api.requests if "/items" in u) == 3  # 50 per page


async def test_rate_limit_and_outages_are_retried_with_backoff() -> None:
    api = FakeSpotify(total=10)
    api.rate_limit_once = True
    no_sleep.waits.clear()  # type: ignore[attr-defined]
    playlist = await client_for(api).playlist(PID)
    assert len(playlist.tracks) == 10
    assert 3.0 in no_sleep.waits  # type: ignore[attr-defined]  # Retry-After honoured
    api.outage = 10
    with pytest.raises(SpotifyError, match="network") as raised:
        await client_for(api).playlist(PID)
    assert raised.value.retryable


async def test_expired_token_is_refreshed_once() -> None:
    api = FakeSpotify(total=5)
    store = signed_in(expires_at=0)  # expired: refreshed before the first call
    await client_for(api, store).playlist(PID)
    assert store.get()["access_token"].startswith("token")  # type: ignore[index]
    api.expire_once = True  # rejected mid-way: refresh and retry
    playlist = await client_for(api, store).playlist(PID)
    assert len(playlist.tracks) == 5


async def test_revoked_sign_in_asks_to_sign_in_again() -> None:
    store = signed_in(expires_at=0, refresh_token="revoked")
    with pytest.raises(NeedsLogin):
        await client_for(FakeSpotify(), store).playlist(PID)
    assert store.get() is None  # the dead tokens are removed
    with pytest.raises(NeedsLogin):
        await client_for(FakeSpotify(), MemoryStore()).playlist(PID)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("", NotAllowed),
        (
            "Active premium subscription required for the owner of the app.",
            SpotifyError,
        ),
        ("User not registered in the Developer Dashboard", SpotifyError),
    ],
)
async def test_access_refusals_explain_what_to_do(message: str, expected: type) -> None:
    api = FakeSpotify()
    api.status_for_items = 403
    api.error_message = message
    with pytest.raises(expected) as raised:
        await client_for(api).playlist(PID)
    text = str(raised.value)
    if expected is NotAllowed:
        assert text == DEV_MODE_HELP and "you own" in text
    elif "premium" in message.casefold():
        assert "Premium" in text
    else:
        assert "User Management" in text


async def test_metadata_without_items_is_reported_not_guessed() -> None:
    api = FakeSpotify()
    api.metadata_only = True
    with pytest.raises(NotAllowed):
        await client_for(api).playlist(PID)


async def test_unknown_playlist() -> None:
    with pytest.raises(SpotifyError, match="can't find"):
        await client_for(FakeSpotify()).playlist("0" * 22)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def test_pkce_sign_in_through_the_loopback_callback() -> None:
    api = FakeSpotify(total=3)
    store = MemoryStore()
    port = free_port()
    client = SpotifyClient("cid", store, http=api, sleep=no_sleep, redirect_port=port)
    opened: list[str] = []

    def browser(url: str) -> None:
        opened.append(url)
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        assert query["code_challenge_method"] == "S256"
        assert query["redirect_uri"] == f"http://127.0.0.1:{port}/callback"
        assert "playlist-read-private" in query["scope"]
        assert "client_secret" not in query

        async def spotify_redirects_back() -> None:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            state = query["state"]
            request = f"GET /callback?code=the-code&state={state} HTTP/1.1\r\n\r\n"
            writer.write(request.encode())
            await writer.drain()
            page = await reader.read()
            assert b"signed in to Spotify" in page
            writer.close()

        asyncio.get_running_loop().create_task(spotify_redirects_back())

    await client.login(browser)
    assert opened and client.signed_in
    tokens = store.get()
    assert tokens is not None and tokens["refresh_token"] == "refresh"
    assert len((await client.playlist(PID)).tracks) == 3


async def test_sign_in_rejects_a_forged_callback() -> None:
    port = free_port()
    client = SpotifyClient("cid", MemoryStore(), http=FakeSpotify(), redirect_port=port)

    def browser(url: str) -> None:
        async def forged() -> None:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /callback?code=the-code&state=wrong HTTP/1.1\r\n\r\n")
            await writer.drain()
            writer.close()

        asyncio.get_running_loop().create_task(forged())

    with pytest.raises(SpotifyError, match="not for this request"):
        await client.login(browser)
    assert not client.signed_in


def test_item_shapes() -> None:
    assert parse_item(0, None).available is False
    old_shape = parse_item(1, {"track": api_track(1)})  # pre-2026 field name
    assert old_shape.title == "Song 1" and old_shape.available


# ----------------------------------------------------------- export files


def test_spotify_account_export(tmp_path: Path) -> None:
    data = {
        "playlists": [
            {
                "name": "Gym",
                "items": [
                    {
                        "track": {
                            "trackName": "Run",
                            "artistName": "Fast Band",
                            "albumName": "Speed",
                            "trackUri": "spotify:track:" + "1" * 22,
                        },
                        "episode": None,
                    },
                    {"track": None, "episode": {"episodeName": "Talk"}},
                    {
                        "track": None,
                        "localTrack": {"trackName": "Demo", "artistName": "Me"},
                    },
                ],
            },
            {"name": "Empty", "items": []},
        ]
    }
    path = tmp_path / "Playlist1.json"
    path.write_text(json.dumps(data))
    gym, empty = read_export(path)
    assert gym.name == "Gym" and gym.source == "file" and len(gym.tracks) == 3
    run, talk, demo = gym.tracks
    assert (run.title, run.artists, run.album, run.spotify_id) == (
        "Run",
        ("Fast Band",),
        "Speed",
        "1" * 22,
    )
    assert not talk.available and "podcast" in talk.note
    assert demo.is_local and demo.title == "Demo"
    assert empty.tracks == ()


def test_csv_exports(tmp_path: Path) -> None:
    exportify = tmp_path / "Night Drive.csv"
    exportify.write_text(
        '"Track URI","Track Name","Artist Name(s)","Album Name",'
        '"Duration (ms)","Explicit"\n'
        f'"spotify:track:{"2" * 22}","Neon","A, B","Lights","200500","true"\n'
        '"","Untitled?","","","",""\n'
    )
    [playlist] = read_export(exportify)
    neon, untitled = playlist.tracks
    assert playlist.name == "Night Drive" and untitled.title == "Untitled?"
    assert (neon.title, neon.artists, neon.duration, neon.explicit) == (
        "Neon",
        ("A", "B"),
        200.5,
        True,
    )
    simple = tmp_path / "simple.csv"
    simple.write_text("title,artist,duration\nHello,Adele,4:55\n")
    [playlist] = read_export(simple)
    assert playlist.tracks[0].duration == 295.0
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n")
    with pytest.raises(SpotifyError, match="title and an artist"):
        read_export(bad)
    with pytest.raises(SpotifyError, match="Can't read"):
        read_export(tmp_path / "missing.csv")
    broken = tmp_path / "broken.json"
    broken.write_text("{nope")
    with pytest.raises(SpotifyError, match="valid JSON"):
        read_export(broken)


# ---------------------------------------------------------- token storage


def test_file_store_is_private(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "data" / "tokens.json")
    assert store.get() is None
    store.set({"access_token": "a"})
    assert store.get() == {"access_token": "a"}
    mode = stat.S_IMODE(os.stat(store.path).st_mode)
    assert mode == 0o600
    store.delete()
    assert store.get() is None


def test_keychain_store_keeps_the_token_off_the_command_line(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    vault = tmp_path / "vault.txt"
    tool = tmp_path / "security"
    tool.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "import shlex\n"
        "cmd, args = sys.argv[1], sys.argv[2:]\n"
        "if cmd == '-i':  # interactive: the command arrives on stdin\n"
        "    cmd, *args = shlex.split(sys.stdin.readline())\n"
        f"vault = {str(vault)!r}\n"
        "if cmd == 'add-generic-password' and '-X' in args:\n"
        "    secret = bytes.fromhex(args[args.index('-X') + 1]).decode()\n"
        "    open(vault, 'w').write(secret)\n"
        "elif cmd == 'add-generic-password':\n"
        "    first, second = sys.stdin.read().splitlines()[:2]\n"
        "    assert first == second\n"
        "    open(vault, 'w').write(first[:128])  # like security's -w on stdin\n"
        "elif cmd == 'find-generic-password':\n"
        "    try: print(open(vault).read())\n"
        "    except FileNotFoundError: sys.exit(44)\n"
        "elif cmd == 'delete-generic-password':\n"
        "    import os; os.path.exists(vault) and os.remove(vault)\n"
    )
    tool.chmod(0o755)
    store = KeychainStore(str(tool))
    assert store.get() is None
    # As long as real Spotify tokens: security cuts a -w secret read from
    # stdin to 128 characters, which made the sign-in unreadable.
    secret = {"access_token": "very-secret-token" + "a" * 300, "refresh_token": "r"}
    store.set(secret)
    assert store.get() == secret
    store.delete()
    assert store.get() is None
    calls = log.read_text()
    assert "very-secret-token" not in calls  # never in argv


def test_keychain_store_notices_a_mangled_save(tmp_path: Path) -> None:
    tool = tmp_path / "security"
    tool.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdin.read()\n"
        "if sys.argv[1] == 'find-generic-password':\n"
        "    print('{\"access_tok')  # saved, but cut short\n"
    )
    tool.chmod(0o755)
    with pytest.raises(OSError, match="intact"):
        KeychainStore(str(tool)).set({"access_token": "a" * 300})


def test_keychain_works_inside_a_terminal(tmp_path: Path) -> None:
    """Regression: in a terminal, macOS ``security`` reads secrets from the
    terminal (/dev/tty), not stdin, and hung behind PixelFM's UI. The store
    must run it detached from the terminal."""
    import shlex
    import subprocess
    import sys

    vault = tmp_path / "vault.txt"
    tool = tmp_path / "security"
    tool.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, time\n"
        "try:\n"
        "    os.close(os.open('/dev/tty', os.O_RDWR))\n"
        "    time.sleep(30)  # like security: waits for typing on the terminal\n"
        "except OSError:\n"
        "    pass  # no terminal: read stdin\n"
        f"vault = {str(vault)!r}\n"
        "if sys.argv[1] == '-i':\n"
        "    line = sys.stdin.readline().split()\n"
        "    open(vault, 'w').write(bytes.fromhex(line[-1]).decode())\n"
        "elif sys.argv[1] == 'find-generic-password':\n"
        "    print(open(vault).read())\n"
    )
    tool.chmod(0o755)
    src = str(Path(__file__).resolve().parents[1] / "src")
    script = (
        f"import sys; sys.path.insert(0, {src!r})\n"
        "from pixelfm.services.credentials import KeychainStore\n"
        f"store = KeychainStore({str(tool)!r})\n"
        "store.set({'access_token': 'abc'})\n"
        "print('GOT', store.get())\n"
    )
    # ``script`` runs the child in a new pseudo-terminal (its controlling
    # terminal), like PixelFM inside Terminal.app; no fork from this process.
    if sys.platform == "darwin":
        command = ["script", "-q", "/dev/null", sys.executable, "-c", script]
    else:
        command = [
            "script",
            "-q",
            "-c",
            shlex.join([sys.executable, "-c", script]),
            "/dev/null",
        ]
    result = subprocess.run(
        command, stdin=subprocess.DEVNULL, capture_output=True, timeout=30, check=False
    )
    assert "GOT {'access_token': 'abc'}" in result.stdout.decode(errors="replace"), (
        result
    )
