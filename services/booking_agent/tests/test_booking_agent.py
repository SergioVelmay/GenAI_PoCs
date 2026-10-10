from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel

from booking_agent.flavors import create_agent
from booking_agent.runtime import AgentDeps, JournaledExecutor
from booking_agent.spec import BOOKING_AGENT_PROMPT, SKILLS_DIR, AgentSpec, build_booking_spec
from headache_core.adapters.local_retrieval import LocalRetrieval
from headache_core.adapters.memory import (
    FakeLLM,
    HashingEmbedding,
    InMemoryCheckpointStore,
    InMemoryPreferenceStore,
    InMemoryUnitOfWork,
)
from headache_core.adapters.ticketing_local import UnitOfWorkTicketing
from headache_core.application.agents import ContextPolicy, SkillCatalog
from headache_core.application.retrieval import RetrievalService
from headache_core.application.tools import (
    Tool,
    ToolContext,
    ToolRegistry,
    build_ticketing_tools,
)
from headache_core.domain import Customer, Event, EventCategory, Money, Order, OrderStatus, Session
from headache_core.ports import Role, ToolCall
from headache_core.ports.memory import ThreadAccessError

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PRICE = Money(Decimal("30.00"), "EUR")
ANA = ToolContext(customer_id="cus_1")


def _call(call_id: str, name: str, **arguments: Any) -> ToolCall:
    return ToolCall(call_id, name, json.dumps(arguments))


def _world() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(
        customers=[Customer("cus_1", "Ana Pérez", "ana@example.com", "ES", "es")],
        events=[
            Event(
                "evt_1",
                "Candlelit Strings",
                EventCategory.CANDLELIT,
                "Madrid",
                "ven_1",
                "Velas.",
                PRICE,
                refundable=True,
                refund_window_hours=48,
            )
        ],
        sessions=[Session("ses_1", "evt_1", datetime(2026, 11, 20, 20, tzinfo=UTC), 100, 3)],
        orders=[Order("ord_1", "cus_1", "ses_1", 2, PRICE * 2, OrderStatus.PAID, NOW)],
    )


def _spec(preferences: InMemoryPreferenceStore, context: ContextPolicy | None = None) -> AgentSpec:
    uow = _world()
    embeddings = HashingEmbedding(64)
    retrieval = RetrievalService(LocalRetrieval([], embeddings=embeddings))
    return build_booking_spec(
        ticketing_tools=build_ticketing_tools(UnitOfWorkTicketing(lambda: uow, clock=lambda: NOW)),
        retrieval=retrieval,
        preferences=preferences,
        context=context,
    )


def test_spec_exposes_the_same_tools_and_skills_to_every_flavor() -> None:
    spec = _spec(InMemoryPreferenceStore())

    assert spec.tools.names == [
        "search_events",
        "get_event",
        "list_sessions",
        "list_my_orders",
        "get_order",
        "request_refund",
        "search_knowledge",
        "load_skill",
        "remember_preference",
    ]
    assert spec.skills.names == ["booking-assistance", "knowledge-answers", "refund-handling"]
    assert spec.system_prompt.startswith(BOOKING_AGENT_PROMPT)
    assert "- refund-handling:" in spec.system_prompt
    assert SkillCatalog.from_directory(SKILLS_DIR).get("booking-assistance") is not None


async def test_native_agent_keeps_thread_and_long_term_memory() -> None:
    preferences = InMemoryPreferenceStore()
    spec = _spec(preferences)
    llm = FakeLLM(
        [
            [_call("c1", "remember_preference", key="home_city", value="Madrid")],
            "Anotado.",
            [_call("c2", "search_events", city="Madrid", category=None, limit=5)],
            "Candlelit Strings.",
        ]
    )
    store = InMemoryCheckpointStore()
    executor = JournaledExecutor(spec.tools)
    agent = create_agent("native", AgentDeps(spec, llm, executor, store, preferences))

    first = await agent.respond("t1", "Vivo en Madrid.", ANA)
    second = await agent.respond("t1", "¿Qué hay cerca?", ANA)

    assert (first.answer, second.answer) == ("Anotado.", "Candlelit Strings.")
    assert [e.call.name for e in executor.journal] == ["remember_preference", "search_events"]
    # Turno 2: prefijo estable + contexto de sesión con la preferencia + historial del turno 1.
    prompt = llm.calls[2]
    assert prompt[0].content == spec.system_prompt and prompt[0].cache_checkpoint
    assert prompt[1].role is Role.SYSTEM and "home_city: Madrid" in prompt[1].content
    assert [m.content for m in prompt if m.role is Role.USER] == [
        "Vivo en Madrid.",
        "¿Qué hay cerca?",
    ]
    thread = store.threads["t1"]
    assert thread.version == 2 and thread.customer_id == "cus_1"
    assert len(thread.messages) == 8
    assert not any(m.cache_checkpoint for m in thread.messages)


async def test_native_agent_rejects_threads_of_other_customers() -> None:
    preferences = InMemoryPreferenceStore()
    spec = _spec(preferences)
    store = InMemoryCheckpointStore()
    deps = AgentDeps(
        spec, FakeLLM(["hola", "hola"]), JournaledExecutor(spec.tools), store, preferences
    )
    agent = create_agent("native", deps)
    await agent.respond("t1", "hola", ANA)

    with pytest.raises(ThreadAccessError):
        await agent.respond("t1", "hola", ToolContext(customer_id="cus_2"))


async def test_max_iterations_closes_the_exchange_with_the_fallback() -> None:
    preferences = InMemoryPreferenceStore()
    spec = _spec(preferences)
    loop = [[_call(f"c{i}", "list_my_orders", limit=1)] for i in range(10)]
    store = InMemoryCheckpointStore()
    deps = AgentDeps(spec, FakeLLM(loop), JournaledExecutor(spec.tools), store, preferences)

    turn = await create_agent("native", deps).respond("t1", "pedidos", ANA)

    assert turn.stop_reason == "max_iterations"
    assert turn.answer == spec.fallback_answer
    last = store.threads["t1"].messages[-1]
    assert (last.role, last.content) == (Role.ASSISTANT, spec.fallback_answer)


async def test_native_agent_compacts_long_threads() -> None:
    preferences = InMemoryPreferenceStore()
    spec = _spec(preferences, ContextPolicy(trigger_tokens=20, target_tokens=10))
    llm = FakeLLM(["respuesta larga " * 20, "ok", "El cliente saludó.", "fin"])
    store = InMemoryCheckpointStore()
    agent = create_agent(
        "native", AgentDeps(spec, llm, JournaledExecutor(spec.tools), store, preferences)
    )

    await agent.respond("t1", "hola", ANA)
    # Con un solo intercambio no se compacta: el último se conserva siempre.
    assert not (await agent.respond("t1", "sigo aquí", ANA)).compacted
    turn = await agent.respond("t1", "¿y ahora?", ANA)

    assert turn.compacted
    assert store.threads["t1"].summary == "El cliente saludó."
    assert "El cliente saludó." in llm.calls[3][1].content


class _Args(BaseModel):
    pass


async def test_journaled_executor_serializes_writes() -> None:
    active, peak = 0, 0

    async def write(_: _Args, __: ToolContext) -> str:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "ok"

    executor = JournaledExecutor(ToolRegistry([Tool("write", "w", _Args, write, read_only=False)]))

    await asyncio.gather(
        *(executor.execute(ToolCall(f"c{i}", "write", "{}"), ANA) for i in range(3))
    )

    assert peak == 1
    assert len(executor.journal) == 3


def test_unknown_flavor() -> None:
    preferences = InMemoryPreferenceStore()
    spec = _spec(preferences)
    deps = AgentDeps(
        spec, FakeLLM(), JournaledExecutor(spec.tools), InMemoryCheckpointStore(), preferences
    )
    with pytest.raises(ValueError, match="Sabor desconocido"):
        create_agent("crewai", deps)
