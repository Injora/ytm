"""Shuffle Playlist: the algorithm, queue integration, and the real UI."""

from __future__ import annotations

import asyncio
import itertools
import random
from collections import Counter
from collections.abc import Sequence

import pytest
from textual.widgets import Button

from pixelfm.app import PixelFMApp
from pixelfm.config import Config
from pixelfm.models import Song
from pixelfm.queue import PlayQueue, RepeatMode
from pixelfm.services.artwork import ArtworkService
from pixelfm.services.library import Library
from pixelfm.services.playback import SimulatedPlayer
from pixelfm.shuffle import fisher_yates, shuffle_songs, unique_songs
from pixelfm.state import PlaybackStatus
from pixelfm.widgets import PlaylistNav, StatusBar
from tests.conftest import image_bytes, make_song

S = PlaybackStatus
SONGS = [make_song(n) for n in range(1, 9)]


def ids(songs: Sequence[Song]) -> list[str]:
    return [s.id for s in songs]


# ------------------------------------------------------------ algorithm


def test_fisher_yates_is_a_permutation_and_uniform() -> None:
    rng = random.Random(7)
    items = ["a", "b", "c"]
    counts = Counter(tuple(fisher_yates(items, rng)) for _ in range(12_000))
    assert set(counts) == set(itertools.permutations(items))  # all 6 orders
    for count in counts.values():  # each about 2000 (unbiased)
        assert 1700 < count < 2300
    assert items == ["a", "b", "c"]  # the input is never modified


def test_every_track_exactly_once() -> None:
    rng = random.Random(1)
    for n in range(0, 9):
        songs = SONGS[:n]
        order = shuffle_songs(songs, rng)
        assert sorted(ids(order)) == sorted(ids(songs))
        assert len(set(ids(order))) == len(order)


def test_duplicates_in_input_are_dropped() -> None:
    songs = [SONGS[0], SONGS[1], SONGS[0]]
    assert unique_songs(songs) == [SONGS[0], SONGS[1]]
    assert sorted(ids(shuffle_songs(songs, random.Random(0)))) == sorted(ids(SONGS[:2]))


def test_empty_and_single_track() -> None:
    assert shuffle_songs([], random.Random(0)) == []
    assert shuffle_songs([SONGS[0]], random.Random(0), avoid_first=SONGS[0].id) == [
        SONGS[0]
    ]


def test_two_tracks_always_differ_from_the_playlist_order() -> None:
    for seed in range(50):
        order = shuffle_songs(SONGS[:2], random.Random(seed))
        assert order == [SONGS[1], SONGS[0]]  # the only other order


@pytest.mark.parametrize("seed", range(40))
def test_avoids_original_order_and_current_track(seed: int) -> None:
    rng = random.Random(seed)
    songs = SONGS[:4]
    order = shuffle_songs(songs, rng, avoid_first=SONGS[2].id)
    assert order != songs
    assert order[0] != SONGS[2]  # never the track playing right now


def test_reshuffle_gives_a_new_order() -> None:
    rng = random.Random(3)
    first = shuffle_songs(SONGS[:5], rng)
    for _ in range(30):
        again = shuffle_songs(SONGS[:5], rng, previous=ids(first))
        assert again != first
        first = again


def test_two_tracks_conflict_prefers_not_repeating_the_current_track() -> None:
    # Playing A; last shuffle was [B, A]. The only order not starting with A
    # is [B, A] again, which wins over "differ from the previous shuffle".
    order = shuffle_songs(
        SONGS[:2],
        random.Random(0),
        avoid_first=SONGS[0].id,
        previous=ids([SONGS[1], SONGS[0]]),
    )
    assert order == [SONGS[1], SONGS[0]]


def test_seeded_rng_is_repeatable() -> None:
    a = shuffle_songs(SONGS, random.Random(42))
    b = shuffle_songs(SONGS, random.Random(42))
    assert a == b and a != SONGS


def test_not_a_rotation_or_reversal() -> None:
    """Over many shuffles, orders other than rotations/reversals show up."""
    rng = random.Random(5)
    songs = SONGS[:6]
    trivial = {tuple(ids(songs[i:] + songs[:i])) for i in range(6)}
    trivial.add(tuple(ids(songs[::-1])))
    orders = {tuple(ids(shuffle_songs(songs, rng))) for _ in range(50)}
    assert len(orders - trivial) > 40


# ----------------------------------------------------------------- queue


def test_play_many_drops_upcoming_duplicates_and_keeps_others() -> None:
    q = PlayQueue()
    q.restore([SONGS[0], SONGS[1], SONGS[5], SONGS[2]], current=0)
    entry = q.play_many([SONGS[2], SONGS[1], SONGS[3]])
    assert entry is not None and entry.song == SONGS[2]
    assert [e.song for e in q.entries] == [
        SONGS[0],  # history
        SONGS[2],  # now playing (first of the block)
        SONGS[1],
        SONGS[3],
        SONGS[5],  # unrelated upcoming track stays queued after the block
    ]


def test_global_shuffle_off_keeps_the_playlist_shuffle_order() -> None:
    q = PlayQueue(rng=random.Random(0))
    q.restore([SONGS[0], SONGS[6], SONGS[7]], current=0)
    q.set_shuffle(True)
    q.play_many([SONGS[3], SONGS[1], SONGS[2]])
    q.set_shuffle(False)  # restores the "unshuffled" order of the rest
    assert [e.song for e in q.upcoming][:2] == [SONGS[1], SONGS[2]]


# ------------------------------------------------------------------- app


class Provider:
    name = "youtube"
    browsable = False

    async def search(self, query: str, limit: int = 20) -> list[Song]:
        return SONGS


def make_app(library: Library, seed: int = 11, **kwargs) -> PixelFMApp:  # type: ignore[no-untyped-def]
    options = dict(
        config=Config(),
        provider=Provider(),
        artwork=ArtworkService(lambda url: image_bytes()),
        player=SimulatedPlayer(load_delay=0),
        library=library,
        unicode=True,
        truecolor=True,
        welcome=False,
        rng=random.Random(seed),
    )
    options.update(kwargs)
    return PixelFMApp(**options)  # type: ignore[arg-type]


def library_with(*playlists: tuple[str, Sequence[Song]]) -> tuple[Library, list[int]]:
    library = Library.memory()
    pids = []
    for name, songs in playlists:
        pid = library.create_playlist(name).id
        for song in songs:
            library.add_to_playlist(pid, song)
        pids.append(pid)
    return library, pids


async def until(predicate, pilot, limit: float = 3.0) -> None:  # type: ignore[no-untyped-def]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while not predicate():
        assert loop.time() < deadline, "condition not reached"
        await pilot.pause(0.02)


def status(app: PixelFMApp) -> str:
    return app.query_one(StatusBar).message


async def test_z_in_playlists_view_queues_shuffled_and_plays() -> None:
    library, [pid] = library_with(("Mix", SONGS[:5]))
    app = make_app(library)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("4", "Z")
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        queued = [e.song for e in app.queue.entries]
        assert sorted(ids(queued)) == sorted(ids(SONGS[:5]))  # each exactly once
        assert queued != SONGS[:5]
        assert app.player.get_state().song == queued[0]
        # The saved playlist is untouched.
        assert [t.song for t in library.playlist_tracks(pid)] == SONGS[:5]
        assert app.queue.shuffle is False  # global shuffle untouched
        assert app._queue_source == {
            "id": pid,
            "name": "Mix",
            "shuffled": True,
            "note": "5 tracks",
        }


async def test_shuffle_buttons_in_playlists_view_and_booth() -> None:
    library, _ = library_with(("Mix", SONGS[:4]))
    app = make_app(library)
    async with app.run_test(size=(140, 42)) as pilot:
        booth_row = app.query_one("#booth-source")
        assert not booth_row.has_class("-visible")  # nothing to shuffle yet
        await pilot.press("4")
        await pilot.pause()
        await pilot.click("#pl-shuffle")
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        first = [e.song for e in app.queue.entries]
        await pilot.press("1")
        await pilot.pause()
        assert booth_row.has_class("-visible")
        assert "SHUFFLED" in str(app.query_one("#source-label").render())
        await pilot.click("#booth-shuffle")  # reshuffle from the booth
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        upcoming = [e.song for e in app.queue.upcoming]
        current = app.queue.current
        assert current is not None
        block = [current.song, *upcoming]
        assert sorted(ids(block)) == sorted(ids(SONGS[:4]))  # no duplicates queued
        assert block != first
        await pilot.press("4")
        await pilot.pause()
        await pilot.click("#pl-play")  # PLAY ALL: playlist order
        await until(lambda: app.player.get_state().song == SONGS[0], pilot)
        assert app._queue_source["shuffled"] is False  # type: ignore[index]


async def test_next_and_previous_follow_the_shuffled_order() -> None:
    library, _ = library_with(("Mix", SONGS[:5]))
    app = make_app(library)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("4", "Z")
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        order = [e.song for e in app.queue.entries]
        for expected in order[1:]:
            await pilot.press("n")
            await until(lambda e=expected: app.player.get_state().song == e, pilot)
        await pilot.press("n")
        assert "End of queue" in status(app)
        for expected in reversed(order[:-1]):
            await pilot.press("b")
            await until(lambda e=expected: app.player.get_state().song == e, pilot)


async def test_does_not_interrupt_until_activated_and_keeps_other_queue() -> None:
    library, _ = library_with(("Mix", SONGS[:3]))
    app = make_app(library)
    async with app.run_test(size=(140, 42)) as pilot:
        app._play_entry(app.queue.play_now(SONGS[6]))
        app.queue.add(SONGS[7])
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        await pilot.press("4")  # just browsing playlists changes nothing
        await pilot.pause(0.2)
        assert app.player.get_state().song == SONGS[6]
        await pilot.press("Z")
        await until(lambda: app.player.get_state().song in SONGS[:3], pilot)
        songs = [e.song for e in app.queue.entries]
        assert songs[0] == SONGS[6]  # what was playing is now history
        assert songs[-1] == SONGS[7]  # the unrelated queued track still follows
        assert sorted(ids(songs[1:4])) == sorted(ids(SONGS[:3]))


async def test_avoids_replaying_the_current_track_first() -> None:
    library, _ = library_with(("Mix", SONGS[:3]))
    for seed in range(8):
        app = make_app(library, seed=seed)
        player = app.player
        async with app.run_test(size=(140, 42)) as pilot:
            app._play_entry(app.queue.play_now(SONGS[1]))
            await until(lambda p=player: p.get_state().status is S.PLAYING, pilot)
            await pilot.press("4", "Z")
            await until(lambda p=player: p.get_state().song != SONGS[1], pilot)
            assert app.queue.current.song != SONGS[1]  # type: ignore[union-attr]


async def test_empty_single_and_missing_playlists() -> None:
    library, [_empty, single] = library_with(("Empty", []), ("Solo", SONGS[:1]))
    app = make_app(library)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("Z")  # nothing played from a playlist yet
        assert "Nothing to shuffle yet" in status(app)
        await pilot.press("4")
        nav = app.query_one(PlaylistNav)
        nav.highlighted = 0  # "Empty" (sorted by name)
        await pilot.press("Z")
        assert "is empty" in status(app)
        assert app.player.get_state().status is S.IDLE
        nav.highlighted = 1  # "Solo"
        await pilot.press("Z")
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        assert app.player.get_state().song == SONGS[0]
        assert "one playable track: nothing to shuffle" in status(app)
        library.delete_playlist(single)  # deleted elsewhere meanwhile
        app._shuffle_playlist(single)
        assert "no longer exists" in status(app)
        assert app._queue_source is None


async def test_unavailable_tracks_are_skipped_with_a_message() -> None:
    library, [pid] = library_with(("Mix", SONGS[:4]))
    library.mark_unavailable(SONGS[1], "This video is unavailable.")
    app = make_app(library)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("4", "Z")
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        assert "Now playing" in status(app)
        assert "Skipped 1 unavailable track" in status(app)
        assert SONGS[1] not in [e.song for e in app.queue.entries]
        assert len(app.queue.entries) == 3
        for song in SONGS[:4]:
            library.mark_unavailable(song, "gone")
        app._shuffle_playlist(pid)
        assert "all 4 tracks were unavailable" in status(app)


async def test_global_shuffle_and_repeat_interaction() -> None:
    songs = [make_song(n, duration=2) for n in range(1, 4)]
    library, _ = library_with(("Mix", songs))
    player = SimulatedPlayer(load_delay=0, speed=20, tick=0.02)
    starts: list[Song] = []
    player.subscribe(
        lambda st: st.status is S.LOADING and st.song and starts.append(st.song)
    )
    app = make_app(library, player=player)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("z")  # global shuffle on (empty queue)
        await pilot.press("r")  # repeat all
        await pilot.press("4", "Z")
        await until(lambda: len(starts) >= 4, pilot)
        order = [e.song for e in app.queue.entries]
        # Tracks finish in the shuffled order; repeat-all loops the same order.
        assert starts[:4] == [*order, order[0]]
        assert app.queue.shuffle is True and app.queue.repeat is RepeatMode.ALL
        await pilot.press("z")  # global shuffle off keeps the playlist order
        assert [e.song for e in app.queue.entries] == order
        assert app._safe_db(lambda: library.get_pref("shuffle")) is False


async def test_shuffle_action_is_documented_and_keyboard_reachable() -> None:
    library, _ = library_with(("Mix", SONGS[:3]))
    app = make_app(library)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("4")
        await pilot.pause()
        keys = {b.key for b in app.query_one(PlaylistNav).BINDINGS}  # type: ignore[union-attr]
        assert "Z" in keys
        shuffle = app.query_one("#pl-shuffle", Button)
        shuffle.focus()
        await pilot.press("enter")
        await until(lambda: app.player.get_state().status is S.PLAYING, pilot)
        from pixelfm.widgets.dialogs import HELP

        assert "shuffle playlist" in HELP
