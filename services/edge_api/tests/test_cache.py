"""Caché semántica en el edge (fase 10.3): aciertos, personales, ACL y fusión en un pico."""

from __future__ import annotations

import asyncio

import pytest

from edge_api.app import build_semantic_cache
from edge_api.assistant import Answer
from edge_api.testing import ASK, FakeAssistant, bearer, client, token
from headache_core.adapters.memory import HashingEmbedding, InMemorySemanticCache
from headache_core.application.semantic_cache import SemanticCache
from headache_core.config import Settings
from headache_core.domain.admission import Caller


def semantic_cache() -> SemanticCache:
    return SemanticCache(InMemorySemanticCache(), HashingEmbedding(256), threshold=0.55)


async def test_second_similar_question_is_served_from_the_cache() -> None:
    assistant = FakeAssistant()
    ana = bearer(token("ana"))
    async with client(assistant, cache=semantic_cache()) as http:
        first, second, other_city = [
            await http.post("/ask", json={"question": question}, headers=ana)
            for question in (
                "¿A qué hora abren las puertas en Barcelona?",
                "¿Cuándo abren las puertas en Barcelona?",
                "¿A qué hora abren las puertas en Chicago?",
            )
        ]
        ready = await http.get("/ready")

    assert [r.headers["X-Cache"] for r in (first, second, other_city)] == ["MISS", "HIT", "MISS"]
    assert second.json()["answer"] == first.json()["answer"]
    assert second.json()["cache"] == "hit"
    assert len(assistant.callers) == 2  # El acierto no llegó al asistente.
    assert ready.json()["cache"]["cache"]["facet_mismatch"] == 1
    assert ready.json()["cache"]["answers"] == {"miss": 2, "hit": 1}


async def test_cache_hits_still_count_against_the_user_rate_limit() -> None:
    async with client(FakeAssistant(), cache=semantic_cache()) as http:
        statuses = [(await http.post("/ask", json=ASK)).status_code for _ in range(3)]

    assert statuses == [200, 200, 429]


async def test_personal_questions_bypass_the_cache() -> None:
    assistant = FakeAssistant()
    personal = {"question": "¿Dónde está mi pedido ord_000123?"}
    async with client(assistant, cache=semantic_cache()) as http:
        first = await http.post("/ask", json=personal, headers=bearer(token("ana")))
        second = await http.post("/ask", json=personal, headers=bearer(token("luis")))

    assert [first.headers["X-Cache"], second.headers["X-Cache"]] == ["BYPASS", "BYPASS"]
    assert len(assistant.callers) == 2


async def test_staff_answers_are_not_served_to_the_public() -> None:
    assistant = FakeAssistant()
    question = {"question": "¿Cuál es el procedimiento interno de reembolsos?"}
    async with client(assistant, cache=semantic_cache()) as http:
        staff = await http.post(
            "/ask", json=question, headers=bearer(token("ops", plan="internal"))
        )
        public = await http.post("/ask", json=question, headers=bearer(token("ana")))

    assert [staff.headers["X-Cache"], public.headers["X-Cache"]] == ["MISS", "MISS"]


class AbstainingAssistant(FakeAssistant):
    async def answer(self, question: str, caller: Caller) -> Answer:
        self.callers.append(caller)
        return Answer("No hay evidencia suficiente.", (), True, "extractive")


async def test_abstentions_are_not_cached() -> None:
    assistant = AbstainingAssistant()
    async with client(assistant, cache=semantic_cache()) as http:
        await http.post("/ask", json=ASK, headers=bearer(token("ana")))
        again = await http.post("/ask", json=ASK, headers=bearer(token("ana")))

    assert again.headers["X-Cache"] == "MISS"
    assert len(assistant.callers) == 2


async def test_identical_concurrent_questions_are_computed_once() -> None:
    assistant = FakeAssistant()
    assistant.gate = asyncio.Event()
    async with client(assistant, cache=semantic_cache(), capacity=50) as http:
        burst = [
            asyncio.create_task(http.post("/ask", json=ASK, headers=bearer(token(f"u{i}"))))
            for i in range(10)
        ]
        async with asyncio.timeout(5):
            await assistant.arrivals.get()
        await asyncio.sleep(0.05)  # Que el resto llegue mientras la primera sigue en curso.
        assistant.gate.set()
        responses = await asyncio.gather(*burst)

    caches = sorted(r.headers["X-Cache"] for r in responses)
    assert len(assistant.callers) == 1
    assert caches == ["COALESCED"] * 9 + ["MISS"]
    assert {r.json()["answer"] for r in responses} == {responses[0].json()["answer"]}


async def test_a_failing_leader_fails_its_followers_and_frees_the_key() -> None:
    assistant = FakeAssistant()
    assistant.gate = asyncio.Event()
    assistant.error = RuntimeError("boom")
    async with client(assistant, cache=semantic_cache(), capacity=50) as http:
        burst = [
            asyncio.create_task(http.post("/ask", json=ASK, headers=bearer(token(f"u{i}"))))
            for i in range(3)
        ]
        async with asyncio.timeout(5):
            await assistant.arrivals.get()
        await asyncio.sleep(0.05)
        assistant.gate.set()
        results = await asyncio.gather(*burst, return_exceptions=True)
        assistant.error, assistant.gate = None, None
        retry = await http.post("/ask", json=ASK, headers=bearer(token("later")))

    assert all(isinstance(r, RuntimeError) for r in results)
    assert retry.headers["X-Cache"] == "MISS"


@pytest.mark.parametrize(
    ("embeddings", "threshold", "expected"),
    [("hashing", None, 0.55), ("azure", 0.88, 0.88), ("azure", None, None)],
    ids=["calibrated", "explicit", "uncalibrated"],
)
def test_threshold_comes_from_the_calibration_for_those_embeddings(
    monkeypatch: pytest.MonkeyPatch, embeddings: str, threshold: float | None, expected: float
) -> None:
    if threshold is not None:
        monkeypatch.setenv("SEMANTIC_CACHE__THRESHOLD", str(threshold))
    cache = build_semantic_cache(
        Settings(_env_file=None), HashingEmbedding(64), embeddings_name=embeddings
    )

    assert (cache.threshold if cache else None) == expected
