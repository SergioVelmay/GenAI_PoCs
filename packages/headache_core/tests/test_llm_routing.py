from __future__ import annotations

import pytest

from headache_core.adapters.memory import FakeLLM
from headache_core.application.llm import (
    HeuristicClassifier,
    LLMClassifier,
    ModelTier,
    RoutingLLM,
)
from headache_core.ports import ChatMessage, Role


def _ask(text: str, *, system: str = "Eres el asistente de Headache.") -> list[ChatMessage]:
    return [ChatMessage(Role.SYSTEM, system), ChatMessage(Role.USER, text)]


@pytest.mark.parametrize(
    ("question", "tier"),
    [
        ("¿A qué hora abre el recinto?", ModelTier.CHEAP),
        ("Explica por qué no me devolvéis el dinero", ModelTier.STRONG),
        ("I want to file a complaint about the venue", ModelTier.STRONG),
        ("hola " * 400, ModelTier.STRONG),
    ],
)
async def test_heuristic_classifier(question: str, tier: ModelTier) -> None:
    assert await HeuristicClassifier().classify(_ask(question), response_schema=None) is tier


async def test_long_system_prompt_does_not_make_the_request_harder() -> None:
    messages = _ask("¿Dónde está el recinto?", system="Manual de políticas. " * 2000)

    assert await HeuristicClassifier().classify(messages, response_schema=None) is ModelTier.CHEAP


async def test_routing_llm_dispatches_by_tier() -> None:
    cheap, strong = FakeLLM(["barato"]), FakeLLM(["potente"])
    llm = RoutingLLM({ModelTier.CHEAP: cheap, ModelTier.STRONG: strong}, HeuristicClassifier())

    assert (await llm.complete(_ask("¿Hay parking?"))).content == "barato"
    assert (await llm.complete(_ask("Compara las dos políticas"))).content == "potente"


def test_routing_llm_requires_every_tier() -> None:
    with pytest.raises(ValueError, match="strong"):
        RoutingLLM({ModelTier.CHEAP: FakeLLM()}, HeuristicClassifier())


async def test_llm_classifier_uses_structured_verdict() -> None:
    router = FakeLLM(['{"tier": "strong", "reason": "queja delicada"}'])

    tier = await LLMClassifier(router).classify(_ask("Estoy muy molesto"), response_schema=None)

    assert tier is ModelTier.STRONG
    assert router.schemas[0] is not None
    assert router.schemas[0].name == "RoutingVerdict"


async def test_llm_classifier_falls_back_to_strong_on_failure() -> None:
    router = FakeLLM(["???", "???"])

    tier = await LLMClassifier(router).classify(_ask("hola"), response_schema=None)

    assert tier is ModelTier.STRONG
