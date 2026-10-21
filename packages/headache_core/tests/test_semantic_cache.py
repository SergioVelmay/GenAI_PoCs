"""Caché semántica (fase 10.3): reglas del dominio y caso de uso, sin red ni coste."""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from headache_core.adapters.memory import HashingEmbedding, InMemorySemanticCache
from headache_core.application.semantic_cache import SemanticCache
from headache_core.domain.semantic_cache import (
    CacheScope,
    QueryFacets,
    detect_language,
    extract_facets,
    is_cacheable_answer,
)
from headache_core.ports.semantic_cache import (
    CacheCandidate,
    CacheEntry,
    SemanticCacheUnavailableError,
)

PUBLIC_ES = CacheScope(("public",), "es", "v1", "extractive")


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now


class DownStore:
    async def nearest(
        self, scope: str, embedding: Sequence[float], *, limit: int
    ) -> list[CacheCandidate]:
        raise SemanticCacheUnavailableError("connection refused")

    async def put(self, entry: CacheEntry, *, ttl_seconds: int) -> None:
        raise SemanticCacheUnavailableError("connection refused")


def _cache(threshold: float = 0.55, **kwargs: object) -> SemanticCache:
    store = kwargs.pop("store", InMemorySemanticCache())
    return SemanticCache(store, HashingEmbedding(256), threshold=threshold, **kwargs)  # type: ignore[arg-type]


async def _warm(cache: SemanticCache, question: str, scope: CacheScope = PUBLIC_ES) -> None:
    lookup = await cache.lookup(question, scope)
    assert await cache.store_answer(lookup, json.dumps({"answer": question}))


# --- Dominio -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("¿A qué hora abren las puertas en Lisboa?", {"cities": ["Lisbon"]}),
        ("What time do doors open in New York?", {"cities": ["New York"]}),
        ("¿Ciudad de México o CDMX?", {"cities": ["Mexico City"]}),
        ("¿Puedo pedir un reembolso del teatro?", {"categories": ["theatre"]}),
        ("¿Me devuelven el dinero?", {"operations": ["refund"]}),
        ("Can I reschedule?", {"operations": ["date_change"]}),
        ("¿Qué incluye evt_000123?", {"ids": ["evt_000123"]}),
        ("¿Cuánto cuestan 2 entradas de 17,50?", {"numbers": ["17.50", "2"]}),
    ],
)
def test_facets_capture_what_changes_the_answer(
    question: str, expected: dict[str, list[str]]
) -> None:
    facets = extract_facets(question).to_dict()

    for name, values in expected.items():
        assert facets[name] == values


def test_la_as_spanish_article_is_not_los_angeles() -> None:
    assert extract_facets("¿A qué hora abre la taquilla?").cities == frozenset()


@pytest.mark.parametrize(
    "question",
    [
        "¿Dónde está mi pedido ord_000123?",
        "What is the status of my order?",
        "Reenviad las entradas a ana@example.com",
        "¿Cuándo me llega mi reembolso?",
        "Mi teléfono es 600123456",
    ],
)
def test_personal_questions_are_detected(question: str) -> None:
    assert extract_facets(question).personal


@pytest.mark.parametrize(
    "question",
    ["¿Dónde están mis entradas en la app?", "Do I need to print my ticket?"],
)
def test_generic_questions_with_possessives_are_not_personal(question: str) -> None:
    # Control benigno: "mis entradas" en la app es la misma respuesta para todos.
    assert not extract_facets(question).personal


def test_facets_round_trip_and_compare() -> None:
    facets = extract_facets("¿Reembolso del tour de Madrid para 2?")

    assert QueryFacets.from_dict(json.loads(json.dumps(facets.to_dict()))).matches(facets)
    assert not facets.matches(extract_facets("¿Reembolso del tour de Madrid para 3?"))


@pytest.mark.parametrize(
    ("question", "language"),
    [
        ("¿Necesito imprimir la entrada?", "es"),
        ("Do I need to print the ticket?", "en"),
        ("Madrid", "unknown"),
    ],
)
def test_language_detection(question: str, language: str) -> None:
    assert detect_language(question) == language


def test_scope_key_separates_acl_language_index_and_mode() -> None:
    keys = {
        PUBLIC_ES.key(),
        CacheScope(("internal", "public"), "es", "v1", "extractive").key(),
        CacheScope(("public",), "en", "v1", "extractive").key(),
        CacheScope(("public",), "es", "v2", "extractive").key(),
        CacheScope(("public",), "es", "v1", "llm-extractive").key(),
    }

    assert len(keys) == 5
    # El orden de las ACL no cambia el espacio.
    assert (
        CacheScope(("public", "internal"), "es", "v1", "x").key()
        == CacheScope(("internal", "public"), "es", "v1", "x").key()
    )


def test_only_answers_with_sources_are_reused() -> None:
    assert is_cacheable_answer(abstained=False, citations=2)
    assert not is_cacheable_answer(abstained=True, citations=0)
    assert not is_cacheable_answer(abstained=False, citations=0)


# --- Caso de uso -------------------------------------------------------------------------


async def test_paraphrase_is_a_hit_and_carries_the_original_question() -> None:
    cache = _cache()
    await _warm(cache, "¿A qué hora abren las puertas en Barcelona?")

    lookup = await cache.lookup("¿Cuándo abren las puertas en Barcelona?", PUBLIC_ES)

    assert lookup.reason == "hit"
    assert json.loads(lookup.payload or "{}")["answer"].endswith("Barcelona?")
    assert lookup.matched_question == "¿A qué hora abren las puertas en Barcelona?"


async def test_similar_question_about_another_city_is_rejected_by_the_facets() -> None:
    cache = _cache()
    await _warm(cache, "¿A qué hora abren las puertas en Barcelona?")

    lookup = await cache.lookup("¿A qué hora abren las puertas en Chicago?", PUBLIC_ES)

    assert lookup.reason == "facet_mismatch"
    assert lookup.similarity > 0.8  # Por similitud habría sido un acierto (y un error).


async def test_without_the_guard_the_same_question_is_a_false_hit() -> None:
    cache = _cache(facet_guard=False)
    await _warm(cache, "¿A qué hora abren las puertas en Barcelona?")

    assert (await cache.lookup("¿A qué hora abren las puertas en Chicago?", PUBLIC_ES)).hit


async def test_guard_skips_a_wrong_neighbour_and_takes_the_next_valid_one() -> None:
    cache = _cache()
    await _warm(cache, "¿A qué hora abren las puertas en Chicago?")
    await _warm(cache, "¿Cuándo abren las puertas en Barcelona?")

    lookup = await cache.lookup("¿A qué hora abren las puertas en Barcelona?", PUBLIC_ES)

    assert lookup.hit
    assert lookup.matched_question == "¿Cuándo abren las puertas en Barcelona?"


async def test_below_threshold_is_a_miss() -> None:
    cache = _cache(threshold=0.95)
    await _warm(cache, "¿A qué hora abren las puertas en Barcelona?")

    lookup = await cache.lookup("¿Cuándo abren las puertas en Barcelona?", PUBLIC_ES)

    assert lookup.reason == "below_threshold"
    assert 0 < lookup.similarity < 0.95


async def test_other_scope_never_sees_the_entry() -> None:
    cache = _cache()
    internal = CacheScope(("internal", "public"), "es", "v1", "extractive")
    await _warm(cache, "¿Cuál es el procedimiento interno de reembolsos?", internal)

    lookup = await cache.lookup("¿Cuál es el procedimiento interno de reembolsos?", PUBLIC_ES)

    assert lookup.reason == "miss"


async def test_personal_questions_are_neither_looked_up_nor_stored() -> None:
    embeddings = HashingEmbedding(256)
    store = InMemorySemanticCache()
    cache = SemanticCache(store, embeddings, threshold=0.5)

    lookup = await cache.lookup("¿Dónde está mi pedido ord_000123?", PUBLIC_ES)

    assert lookup.reason == "personal"
    assert lookup.embedding == ()  # Ni siquiera se paga el embedding.
    assert not await cache.store_answer(lookup, "{}")
    assert len(store) == 0


async def test_entries_expire() -> None:
    time = FakeTime()
    cache = _cache(store=InMemorySemanticCache(clock=time.clock), ttl_seconds=60)
    await _warm(cache, "¿A qué hora abren las puertas en Barcelona?")

    time.now = 61
    lookup = await cache.lookup("¿A qué hora abren las puertas en Barcelona?", PUBLIC_ES)

    assert lookup.reason == "miss"


async def test_store_outage_degrades_to_a_miss_without_failing() -> None:
    cache = _cache(store=DownStore())

    lookup = await cache.lookup("¿A qué hora abren las puertas en Barcelona?", PUBLIC_ES)

    assert lookup.reason == "unavailable"
    assert not await cache.store_answer(lookup, "{}")
    assert cache.outcomes == {"unavailable": 1}


async def test_a_hit_is_not_stored_again() -> None:
    store = InMemorySemanticCache()
    cache = _cache(store=store)
    await _warm(cache, "¿A qué hora abren las puertas en Barcelona?")

    lookup = await cache.lookup("¿A qué hora abren las puertas en Barcelona?", PUBLIC_ES)

    assert not await cache.store_answer(lookup, "{}")
    assert len(store) == 1


def test_threshold_must_be_a_similarity() -> None:
    with pytest.raises(ValueError, match="umbral"):
        _cache(threshold=0)
