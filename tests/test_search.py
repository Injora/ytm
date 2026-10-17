"""Mock provider, result cache, and the LRU cache itself."""

from __future__ import annotations

import pytest

from pixelfm.models import Song
from pixelfm.services.cache import LRUCache
from pixelfm.services.search import (
    MOCK_CATALOG,
    CachedSearchProvider,
    MockSearchProvider,
    SearchError,
    stable_id,
)
from tests.conftest import make_song


async def test_mock_empty_query_returns_catalog() -> None:
    results = await MockSearchProvider().search("", limit=100)
    assert results == list(MOCK_CATALOG)


async def test_mock_limit() -> None:
    assert len(await MockSearchProvider().search("", limit=3)) == 3


async def test_mock_matches_title_and_artist_case_insensitive() -> None:
    results = await MockSearchProvider().search("KASETTE")
    assert results and all(s.artist == "Kasette Kid" for s in results)


async def test_mock_all_terms_must_match() -> None:
    results = await MockSearchProvider().search("neon rain")
    assert [s.title for s in results] == ["Neon Rain on Channel 9"]
    assert await MockSearchProvider().search("neon zebra") == []


async def test_mock_title_matches_rank_first() -> None:
    # "Lumen Drive" songs come earlier in the catalogue but match on artist only.
    results = await MockSearchProvider().search("drive")
    assert results[0].title == "Night Drive (Extended Mix)"
    assert all(s.artist == "Lumen Drive" for s in results[1:])


async def test_mock_is_deterministic() -> None:
    provider = MockSearchProvider()
    assert await provider.search("a") == await provider.search("a")


async def test_mock_rejects_bad_limit() -> None:
    with pytest.raises(SearchError):
        await MockSearchProvider().search("x", limit=0)


def test_catalog_ids_are_stable_and_unique() -> None:
    assert stable_id("A", "B") == stable_id("A", "B")
    assert len({s.id for s in MOCK_CATALOG}) == len(MOCK_CATALOG)


class CountingProvider:
    name = "counting"
    browsable = False

    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def search(self, query: str, limit: int = 20) -> list[Song]:
        self.calls += 1
        if self.fail:
            raise SearchError("boom")
        return [make_song(len(query))]


async def test_cached_provider_reuses_normalised_queries() -> None:
    inner = CountingProvider()
    cached = CachedSearchProvider(inner)
    first = await cached.search("Daft  Punk")
    second = await cached.search("daft punk ")
    assert first == second
    assert inner.calls == 1
    await cached.search("daft punk", limit=5)  # different limit, new entry
    assert inner.calls == 2


async def test_cached_provider_does_not_cache_failures() -> None:
    inner = CountingProvider(fail=True)
    cached = CachedSearchProvider(inner)
    for _ in range(2):
        with pytest.raises(SearchError):
            await cached.search("x")
    assert inner.calls == 2


async def test_cached_results_are_copies() -> None:
    cached = CachedSearchProvider(CountingProvider())
    (await cached.search("abc")).clear()
    assert len(await cached.search("abc")) == 1


def test_lru_eviction_and_recency() -> None:
    cache: LRUCache[str, int] = LRUCache(2)
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1  # "a" is now most recent
    cache.set("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 1 and cache.get("c") == 3
    assert len(cache) == 2


def test_lru_ttl_with_fake_clock() -> None:
    now = [0.0]
    cache: LRUCache[str, int] = LRUCache(4, ttl=10, clock=lambda: now[0])
    cache.set("a", 1)
    now[0] = 9.9
    assert cache.get("a") == 1
    now[0] = 10.0
    assert cache.get("a") is None
    assert len(cache) == 0


def test_lru_rejects_zero_size() -> None:
    with pytest.raises(ValueError):
        LRUCache(0)
