import json
from collections.abc import Mapping, Sequence
from importlib.util import find_spec
from typing import Any

import pytest

from genaipocs_core.adapters.memory import FakeLLM, HashingEmbedding
from genaipocs_core.application.llm.structured import response_schema_for
from genaipocs_core.ports.llm import (
    ChatMessage,
    LLMResponse,
    ResponseSchema,
    TokenUsage,
    ToolConfig,
)
from rag_evals.judges import JudgeError, JudgeSample, JudgeVerdict, NativeJudge

SAMPLE = JudgeSample(
    question="How many hours before can I get a refund?",
    answer="Refunds are allowed up to 48 hours before. Parking is free.",
    contexts=("Refundable: yes, up to 48 hours before the session.", "Venue opens at 7 pm."),
    reference="Up to 48 hours before the session.",
)
# Sin el extra `evals` (CI) se saltan; con el extra instalado, un import roto debe fallar.
needs_ragas = pytest.mark.skipif(find_spec("ragas") is None, reason="extra evals")
needs_deepeval = pytest.mark.skipif(find_spec("deepeval") is None, reason="extra evals")

# Subconjunto de JSON Schema que admite `response_json_schema` de Gemini (documentacion oficial,
# 20-11-2026). Las restricciones de longitud las descarta sin error (Pydantic las valida igual).
GEMINI_SCHEMA_KEYWORDS = frozenset(
    {
        "$defs", "$ref", "additionalProperties", "anyOf", "description", "enum", "format",
        "items", "maxItems", "maximum", "minItems", "minimum", "prefixItems", "properties",
        "required", "title", "type",
        "maxLength", "minLength", "maxProperties", "minProperties", "uniqueItems",
    }
)  # fmt: skip


def gemini_unknown_keywords(schema: Mapping[str, Any]) -> set[str]:
    """Palabras clave del esquema fuera del subconjunto de Gemini (los nombres de propiedades y
    de `$defs` son datos, no palabras clave)."""
    unknown: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, Mapping):
            return
        for key, value in node.items():
            if key not in GEMINI_SCHEMA_KEYWORDS:
                unknown.add(key)
            if key in {"properties", "$defs"} and isinstance(value, Mapping):
                for child in value.values():
                    visit(child)
            elif key not in {"enum", "required", "description", "title"}:
                visit(value)

    visit(schema)
    return unknown


class SchemaRoutedLLM:
    """Responde segun el nombre del esquema pedido: robusto a llamadas concurrentes."""

    def __init__(self, routes: Mapping[str, str]) -> None:
        self._routes = routes
        self.seen: list[str] = []
        self.schemas: list[ResponseSchema] = []

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
        tools: ToolConfig | None = None,
    ) -> LLMResponse:
        name = response_schema.name if response_schema is not None else "text"
        if response_schema is not None:
            self.schemas.append(response_schema)
        self.seen.append(name)
        return LLMResponse(self._routes[name], "gpt-4.1-nano", TokenUsage(10, 5), "fake")


async def test_native_judge_computes_scores_from_labels() -> None:
    verdict = {
        "answer_claims": [
            {"claim": "Refunds up to 48 hours before", "supported": True},
            {"claim": "Parking is free", "supported": False},
        ],
        "relevance": 2,
        "contexts": [{"index": 1, "relevant": False}, {"index": 0, "relevant": True}],
        "reference_claims": [{"claim": "Up to 48 hours before", "supported": True}],
    }
    llm = FakeLLM([json.dumps(verdict)])
    scores = await NativeJudge(llm).score(SAMPLE)
    assert scores.faithfulness == 0.5
    assert scores.answer_relevancy == pytest.approx(2 / 3)
    assert scores.context_precision == 1.0
    assert scores.context_recall == 1.0
    assert llm.schemas[0] is not None and llm.schemas[0].name == "JudgeVerdict"
    payload = json.loads(llm.calls[0][-1].content)
    assert [context["index"] for context in payload["contexts"]] == [0, 1]


def test_native_judge_schema_fits_gemini() -> None:
    """El juez con Gemini (5.G1) recibe el mismo esquema strict que Azure: debe caber en el
    subconjunto de Gemini, o la llamada daria 400 solo en la nube."""
    schema = response_schema_for(JudgeVerdict).json_schema

    assert gemini_unknown_keywords(schema) == set()
    # El detector mira palabras clave anidadas, no los nombres de las propiedades.
    assert gemini_unknown_keywords(
        {"type": "object", "properties": {"const": {"const": 1, "type": "integer"}}}
    ) == {"const"}


def test_agent_judge_schema_fits_gemini() -> None:
    """12.G1: el juez de agentes con Gemini recibe el mismo esquema strict que con Azure."""
    from genaipocs_core.application.agent_judge import AgentVerdict

    assert gemini_unknown_keywords(response_schema_for(AgentVerdict).json_schema) == set()


async def test_agent_judge_runs_on_gemini_and_the_domain_decides() -> None:
    """De punta a punta con `GeminiLLM` y un doble de `Client.aio.models`: esquema en la
    petición, prompt de sistema del juez y aceptación calculada por el dominio."""
    from google.genai import types

    from genaipocs_core.adapters.gemini import GeminiLLM
    from genaipocs_core.application.agent_judge import (
        JUDGE_INSTRUCTIONS,
        AgentJudgeSample,
        Evidence,
        LLMAgentJudge,
    )
    from genaipocs_core.domain.agent_eval import Resolution

    verdict = {
        "claims": [{"claim": "El pedido está confirmado", "supported": True}],
        "actions": [{"claim": "He reembolsado el pedido", "completed_in_evidence": False}],
        "resolution": "resuelto",
        "same_language": True,
    }

    class Models:
        config: types.GenerateContentConfig | None = None

        async def generate_content(
            self, *, model: str, contents: list[types.Content], config: types.GenerateContentConfig
        ) -> types.GenerateContentResponse:
            Models.config = config
            return types.GenerateContentResponse.model_validate(
                {
                    "candidates": [
                        {
                            "content": {"role": "model", "parts": [{"text": json.dumps(verdict)}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 60},
                    "modelVersion": model,
                }
            )

        async def generate_content_stream(self, **_: Any) -> Any:
            raise AssertionError("el juez no usa streaming")

    judge = LLMAgentJudge(GeminiLLM(Models(), "gemini-4-argon", thinking_level="low"))
    sample = AgentJudgeSample(
        ("¿Me reembolsas el pedido?",),
        "Tu pedido está confirmado y lo he reembolsado.",
        (Evidence("get_order", "{}", '{"status": "confirmed"}'),),
    )

    judgement = await judge.judge(sample)

    assert Models.config is not None
    assert Models.config.response_json_schema is not None
    assert JUDGE_INSTRUCTIONS in str(Models.config.system_instruction)
    assert judgement.resolution is Resolution.RESOLVED and judgement.grounded == 1.0
    # Afirma un reembolso que la evidencia no respalda: el dominio lo rechaza, no el modelo.
    assert judgement.misstated_actions == 1 and not judgement.acceptable


async def test_native_judge_rejects_verdicts_that_skip_contexts() -> None:
    verdict = {
        "answer_claims": [],
        "relevance": 0,
        "contexts": [{"index": 0, "relevant": True}],
        "reference_claims": [],
    }
    with pytest.raises(JudgeError):
        await NativeJudge(FakeLLM([json.dumps(verdict)])).score(SAMPLE)


async def test_native_judge_without_reference_has_no_recall() -> None:
    verdict = {
        "answer_claims": [],
        "relevance": 3,
        "contexts": [{"index": 0, "relevant": False}, {"index": 1, "relevant": False}],
        "reference_claims": [{"claim": "ignored", "supported": True}],
    }
    sample = JudgeSample(SAMPLE.question, SAMPLE.answer, SAMPLE.contexts)
    scores = await NativeJudge(FakeLLM([json.dumps(verdict)])).score(sample)
    assert scores.faithfulness is None
    assert scores.context_precision == 0.0
    assert scores.context_recall is None


@needs_ragas
async def test_ragas_judge_runs_through_our_llm_port() -> None:
    from rag_evals.frameworks.ragas_judge import RagasJudge

    llm = SchemaRoutedLLM(
        {
            "StatementGeneratorOutput": json.dumps({"statements": ["a", "b"]}),
            "NLIStatementOutput": json.dumps(
                {
                    "statements": [
                        {"statement": "a", "reason": "in context", "verdict": 1},
                        {"statement": "b", "reason": "not in context", "verdict": 0},
                    ]
                }
            ),
            "AnswerRelevanceOutput": json.dumps({"question": SAMPLE.question, "noncommittal": 0}),
        }
    )
    sample = JudgeSample(SAMPLE.question, SAMPLE.answer, SAMPLE.contexts)
    scores = await RagasJudge(llm, HashingEmbedding(64)).score(sample)
    assert scores.faithfulness == 0.5
    assert scores.answer_relevancy == pytest.approx(1.0)
    assert scores.context_precision is None and scores.context_recall is None
    assert set(llm.seen) == {
        "StatementGeneratorOutput",
        "NLIStatementOutput",
        "AnswerRelevanceOutput",
    }
    assert {s.name: gemini_unknown_keywords(s.json_schema) for s in llm.schemas} == {
        name: set() for name in llm.seen
    }


@needs_deepeval
async def test_deepeval_judge_runs_through_our_llm_port() -> None:
    from rag_evals.frameworks.deepeval_judge import DeepEvalJudge

    verdicts = {"verdicts": [{"verdict": "yes", "reason": None}, {"verdict": "no", "reason": "x"}]}
    llm = SchemaRoutedLLM(
        {
            "Truths": json.dumps({"truths": ["Refunds up to 48 hours before."]}),
            "Claims": json.dumps({"claims": ["a", "b"]}),
            "Statements": json.dumps({"statements": ["a", "b"]}),
            "Verdicts": json.dumps(verdicts),
        }
    )
    sample = JudgeSample(SAMPLE.question, SAMPLE.answer, SAMPLE.contexts)
    scores = await DeepEvalJudge(llm).score(sample)
    assert scores.faithfulness == 0.5
    assert scores.answer_relevancy == 0.5
    assert scores.context_precision is None and scores.context_recall is None
    assert "text" not in llm.seen
    assert {s.name: gemini_unknown_keywords(s.json_schema) for s in llm.schemas} == {
        name: set() for name in llm.seen
    }


def _response_models(modules: Sequence[str]) -> dict[str, type]:
    import importlib
    import inspect

    from pydantic import BaseModel

    found: dict[str, type] = {}
    for name in modules:
        module = importlib.import_module(name)
        for cls_name, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, BaseModel) and cls.__module__ == module.__name__:
                found[f"{name.split('.')[-2]}.{cls_name}"] = cls
    return found


def _assert_fit_gemini(models: Mapping[str, type]) -> None:
    unknown = {
        name: gemini_unknown_keywords(response_schema_for(model).json_schema)
        for name, model in models.items()
    }
    assert models, "no se encontraron modelos de respuesta: ¿ha cambiado el paquete?"
    assert {name: keys for name, keys in unknown.items() if keys} == {}


_METRICS = ("context_precision", "context_recall", "faithfulness", "answer_relevancy")


@needs_ragas
def test_every_ragas_response_model_fits_gemini() -> None:
    """Las cuatro métricas, también las que solo corren con referencia (precision y recall)."""
    _assert_fit_gemini(_response_models([f"ragas.metrics.collections.{m}.util" for m in _METRICS]))


@needs_deepeval
def test_every_deepeval_response_model_fits_gemini() -> None:
    names = ("contextual_precision", "contextual_recall", "faithfulness", "answer_relevancy")
    _assert_fit_gemini(_response_models([f"deepeval.metrics.{m}.schema" for m in names]))
