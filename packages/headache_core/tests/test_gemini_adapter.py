from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import aiohttp
import httpx
import pytest
from google.genai import errors, types
from pydantic import SecretStr

from headache_core.adapters.gemini import SKIP_SIGNATURE, GeminiLLM, to_llm_error
from headache_core.adapters.gemini_cache import ExplicitCache
from headache_core.config import GcpSettings, GeminiSettings
from headache_core.ports import (
    ChatMessage,
    LLMContentFilterError,
    LLMError,
    LLMRateLimitError,
    LLMResponse,
    LLMTransientError,
    ResponseSchema,
    Role,
    TextDelta,
    TokenUsage,
    ToolCall,
    ToolConfig,
    ToolSpec,
)

MODEL = "gemini-4-hydrogen"
SIGNATURE = b"\x01firma-opaca\xff"
SCHEMA = ResponseSchema(
    "Answer", {"type": "object", "properties": {"ok": {"type": "boolean"}}}, "Respuesta"
)
TOOLS = ToolConfig(
    (ToolSpec("get_order", "Estado de un pedido.", {"type": "object"}),), choice="required"
)


def _response(
    parts: list[dict[str, Any]],
    *,
    finish: str = "STOP",
    usage: dict[str, int] | None = None,
    feedback: dict[str, Any] | None = None,
) -> types.GenerateContentResponse:
    body: dict[str, Any] = {
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
        "usageMetadata": usage or {"promptTokenCount": 50, "candidatesTokenCount": 5},
        "modelVersion": MODEL,
    }
    if feedback is not None:
        body = {"promptFeedback": feedback, "usageMetadata": body["usageMetadata"]}
    return types.GenerateContentResponse.model_validate(body)


class FakeModels:
    """Doble de `Client.aio.models`: guarda la petición y devuelve respuestas preparadas."""

    def __init__(
        self,
        response: types.GenerateContentResponse | None = None,
        *,
        chunks: list[types.GenerateContentResponse] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.response = response
        self.chunks = chunks or []
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def generate_content(
        self, *, model: str, contents: list[types.Content], config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response

    async def generate_content_stream(
        self, *, model: str, contents: list[types.Content], config: types.GenerateContentConfig
    ) -> AsyncIterator[types.GenerateContentResponse]:
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.error is not None:
            raise self.error

        async def chunks() -> AsyncIterator[types.GenerateContentResponse]:
            for chunk in self.chunks:
                yield chunk

        return chunks()


async def test_text_turn_uses_system_instruction_thinking_level_and_normalized_usage() -> None:
    models = FakeModels(
        _response(
            [{"text": "Sí, hasta 24 h antes."}, {"text": "", "thoughtSignature": "c2ln"}],
            usage={
                "promptTokenCount": 5000,
                "cachedContentTokenCount": 4096,
                "candidatesTokenCount": 8,
                "thoughtsTokenCount": 30,
            },
        )
    )

    response = await GeminiLLM(models, MODEL, thinking_level="low").complete(
        [
            ChatMessage(Role.SYSTEM, "Manual de políticas", cache_checkpoint=True),
            ChatMessage(Role.USER, "¿Puedo cambiar la fecha?"),
        ],
        temperature=0.0,
        max_tokens=100,
    )

    assert response.content == "Sí, hasta 24 h antes."
    # Los cacheados ya van dentro de la entrada; el razonamiento se factura como salida.
    assert response.usage == TokenUsage(5000, 38, cached_input_tokens=4096)
    assert (response.provider, response.model, response.finish_reason) == ("gcp", MODEL, "stop")
    config = models.calls[0]["config"]
    assert config.system_instruction == "Manual de políticas"
    assert config.max_output_tokens == 100
    assert config.thinking_config.thinking_level is types.ThinkingLevel.LOW
    # Los modelos 3.5+ ignoran la temperatura (y la rechazarán): no se envía.
    assert config.temperature is None
    assert [c.role for c in models.calls[0]["contents"]] == ["user"]


async def test_thought_parts_are_not_part_of_the_answer() -> None:
    models = FakeModels(_response([{"text": "Pienso...", "thought": True}, {"text": "Hecho."}]))

    response = await GeminiLLM(models, MODEL).complete([ChatMessage(Role.USER, "hola")])

    assert response.content == "Hecho."
    assert models.calls[0]["config"].thinking_config is None  # Vacío = valor del modelo.


async def test_parallel_tool_calls_keep_the_signature_of_the_first_call() -> None:
    encoded = base64.b64encode(SIGNATURE).decode()
    models = FakeModels(
        _response(
            [
                {"text": "Consulto los dos."},
                {
                    "functionCall": {"id": "c1", "name": "get_order", "args": {"id": "1"}},
                    "thoughtSignature": encoded,
                },
                {"functionCall": {"id": "c2", "name": "get_order", "args": {"id": "2"}}},
            ]
        )
    )

    response = await GeminiLLM(models, MODEL).complete(
        [ChatMessage(Role.USER, "Mis pedidos 1 y 2")], tools=TOOLS
    )

    assert response.content == "Consulto los dos."
    assert response.tool_calls == (
        ToolCall("c1", "get_order", '{"id": "1"}', signature=encoded),
        ToolCall("c2", "get_order", '{"id": "2"}'),
    )
    config = models.calls[0]["config"]
    declaration = config.tools[0].function_declarations[0]
    assert (declaration.name, declaration.parameters_json_schema) == (
        "get_order",
        {"type": "object"},
    )
    calling = config.tool_config.function_calling_config
    assert calling.mode is types.FunctionCallingConfigMode.ANY


async def test_history_round_trip_returns_signatures_and_groups_parallel_results() -> None:
    encoded = base64.b64encode(SIGNATURE).decode()
    models = FakeModels(_response([{"text": "El 1 está pagado; el 2 no existe."}]))

    await GeminiLLM(models, MODEL).complete(
        [
            ChatMessage(Role.SYSTEM, "Asistente"),
            ChatMessage(Role.USER, "Mis pedidos 1 y 2"),
            ChatMessage(
                Role.ASSISTANT,
                "",
                tool_calls=(
                    ToolCall("c1", "get_order", '{"id": "1"}', signature=encoded),
                    # Argumentos inválidos (p. ej. tras un fallback): viajan como objeto vacío.
                    ToolCall("c2", "get_order", "{roto"),
                ),
            ),
            ChatMessage(Role.TOOL, '{"status": "paid"}', tool_call_id="c1"),
            ChatMessage(Role.TOOL, "pedido no encontrado", tool_call_id="c2", is_error=True),
        ],
        tools=TOOLS,
    )

    user, model, results = models.calls[0]["contents"]
    assert (user.role, model.role, results.role) == ("user", "model", "user")
    first, second = model.parts
    assert first.function_call.args == {"id": "1"}
    assert first.thought_signature == SIGNATURE
    # Solo la primera llamada lleva firma: duplicarla da 400.
    assert (second.function_call.args, second.thought_signature) == ({}, None)
    # Las respuestas en paralelo, juntas, en orden y con id y nombre.
    assert [
        (p.function_response.id, p.function_response.name, p.function_response.response)
        for p in results.parts
    ] == [
        ("c1", "get_order", {"output": {"status": "paid"}}),
        ("c2", "get_order", {"error": "pedido no encontrado"}),
    ]


async def test_history_from_another_provider_skips_signature_validation() -> None:
    """Fallback a mitad de conversación: Azure no genera firmas y Gemini daría 400."""
    models = FakeModels(_response([{"text": "Listo."}]))

    await GeminiLLM(models, MODEL).complete(
        [
            ChatMessage(Role.USER, "Mi pedido 1"),
            ChatMessage(
                Role.ASSISTANT,
                "Lo miro.",
                tool_calls=(ToolCall("call_azure", "get_order", '{"id": "1"}'),),
            ),
            ChatMessage(Role.TOOL, '{"status": "paid"}', tool_call_id="call_azure"),
        ],
        tools=TOOLS,
    )

    model = models.calls[0]["contents"][1]
    assert model.parts[0].text == "Lo miro."
    assert model.parts[1].thought_signature == SKIP_SIGNATURE


async def test_tool_result_without_its_call_is_rejected() -> None:
    with pytest.raises(LLMError, match="no responde"):
        await GeminiLLM(FakeModels(), MODEL).complete(
            [ChatMessage(Role.USER, "hola"), ChatMessage(Role.TOOL, "{}", tool_call_id="x")]
        )


async def test_choice_of_a_named_tool_restricts_the_allowed_functions() -> None:
    models = FakeModels(_response([{"text": "ok"}]))
    tools = ToolConfig(TOOLS.tools, choice="get_order")

    await GeminiLLM(models, MODEL).complete([ChatMessage(Role.USER, "hola")], tools=tools)

    calling = models.calls[0]["config"].tool_config.function_calling_config
    assert calling.mode is types.FunctionCallingConfigMode.ANY
    assert calling.allowed_function_names == ["get_order"]


async def test_structured_output_uses_the_json_schema() -> None:
    models = FakeModels(_response([{"text": '{"ok": true}'}]))

    response = await GeminiLLM(models, MODEL).complete(
        [ChatMessage(Role.USER, "hola")], response_schema=SCHEMA
    )

    assert json.loads(response.content) == {"ok": True}
    config = models.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == dict(SCHEMA.json_schema)


def _api_error(code: int, status: str, details: list[dict[str, str]] | None = None) -> Exception:
    body = {"error": {"code": code, "message": status, "status": status, "details": details or []}}
    return errors.APIError(code, body)


@pytest.mark.parametrize(
    ("error", "expected", "retry_after"),
    [
        (
            _api_error(
                429,
                "RESOURCE_EXHAUSTED",
                [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "31s"}],
            ),
            LLMRateLimitError,
            31.0,
        ),
        (_api_error(503, "UNAVAILABLE"), LLMTransientError, None),
        (_api_error(504, "DEADLINE_EXCEEDED"), LLMTransientError, None),
        (_api_error(400, "INVALID_ARGUMENT"), LLMError, None),
        (_api_error(403, "PERMISSION_DENIED"), LLMError, None),
        (httpx.ReadTimeout("lento"), LLMTransientError, None),
        (aiohttp.ServerDisconnectedError(), LLMTransientError, None),
    ],
)
async def test_maps_sdk_errors(
    error: Exception, expected: type[LLMError], retry_after: float | None
) -> None:
    with pytest.raises(LLMError) as info:
        await GeminiLLM(FakeModels(error=error), MODEL).complete([ChatMessage(Role.USER, "hola")])

    assert type(info.value) is expected
    assert getattr(info.value, "retry_after", None) == retry_after


def test_credentials_errors_are_not_retried() -> None:
    from google.auth.exceptions import DefaultCredentialsError

    error = DefaultCredentialsError("sin ADC")  # type: ignore[no-untyped-call]

    assert type(to_llm_error(error)) is LLMError


@pytest.mark.parametrize(
    "response",
    [
        _response([], feedback={"blockReason": "PROHIBITED_CONTENT"}),
        _response([{"text": "..."}], finish="SAFETY"),
        _response([], feedback={"blockReason": "MODEL_ARMOR"}),
    ],
)
async def test_safety_blocks_are_content_filter_errors(
    response: types.GenerateContentResponse,
) -> None:
    with pytest.raises(LLMContentFilterError):
        await GeminiLLM(FakeModels(response), MODEL).complete([ChatMessage(Role.USER, "hola")])


async def test_malformed_function_call_is_transient() -> None:
    response = _response([], finish="MALFORMED_FUNCTION_CALL")

    with pytest.raises(LLMTransientError):
        await GeminiLLM(FakeModels(response), MODEL).complete(
            [ChatMessage(Role.USER, "hola")], tools=TOOLS
        )


async def test_stream_yields_text_and_a_final_response_with_usage() -> None:
    models = FakeModels(
        chunks=[
            _response([{"text": "Hasta "}], finish="FINISH_REASON_UNSPECIFIED"),
            _response([{"text": "24 h."}], finish="FINISH_REASON_UNSPECIFIED"),
            # La firma puede llegar en una parte de texto vacía al final: no se emite.
            _response(
                [{"text": "", "thoughtSignature": "c2ln"}],
                usage={"promptTokenCount": 40, "candidatesTokenCount": 4, "thoughtsTokenCount": 6},
            ),
        ]
    )

    events = [e async for e in GeminiLLM(models, MODEL).stream([ChatMessage(Role.USER, "hola")])]

    assert events[:2] == [TextDelta("Hasta "), TextDelta("24 h.")]
    final = events[-1]
    assert isinstance(final, LLMResponse)
    assert (final.content, final.usage, final.finish_reason) == (
        "Hasta 24 h.",
        TokenUsage(40, 10),
        "stop",
    )
    assert len(events) == 3


async def test_stream_cut_by_safety_raises_after_the_text() -> None:
    models = FakeModels(chunks=[_response([{"text": "Empiezo"}], finish="SAFETY")])

    with pytest.raises(LLMContentFilterError):
        [e async for e in GeminiLLM(models, MODEL).stream([ChatMessage(Role.USER, "hola")])]


def test_enterprise_needs_a_project_and_developer_api_needs_a_key() -> None:
    with pytest.raises(LLMError, match="GCP__PROJECT"):
        GeminiLLM.from_settings(GcpSettings(), GeminiSettings(), model=MODEL)
    with pytest.raises(LLMError, match="GEMINI__API_KEY"):
        GeminiLLM.from_settings(GcpSettings(), GeminiSettings(enterprise=False), model=MODEL)

    # Sin red ni credenciales: el cliente las resuelve en la primera llamada.
    assert isinstance(
        GeminiLLM.from_settings(GcpSettings(project="headache-dev"), GeminiSettings(), model=MODEL),
        GeminiLLM,
    )
    assert isinstance(
        GeminiLLM.from_settings(
            GcpSettings(), GeminiSettings(enterprise=False, api_key=SecretStr("k")), model=MODEL
        ),
        GeminiLLM,
    )


# --- Caché explícita (2.G3) ---

CHEAP = "gemini-3.5-flash-lite"
MANUAL = [
    ChatMessage(Role.SYSTEM, "Manual de políticas", cache_checkpoint=True),
    ChatMessage(Role.USER, "¿Puedo cambiar la fecha?"),
]


class FakeCaches:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.created: list[types.CreateCachedContentConfig] = []

    async def create(
        self, *, model: str, config: types.CreateCachedContentConfig
    ) -> types.CachedContent:
        if self.error is not None:
            raise self.error
        self.created.append(config)
        return types.CachedContent(
            name="cachedContents/manual",
            usage_metadata=types.CachedContentUsageMetadata(total_token_count=4500),
        )

    async def delete(self, *, name: str) -> object:
        return None


def _explicit(models: FakeModels, caches: FakeCaches, model: str = CHEAP) -> GeminiLLM:
    return GeminiLLM(models, model, explicit_cache=ExplicitCache(caches, ttl_seconds=600))


def _cached_response() -> types.GenerateContentResponse:
    usage = {"promptTokenCount": 4520, "cachedContentTokenCount": 4500, "candidatesTokenCount": 5}
    return _response([{"text": "Sí."}], usage=usage)


async def test_explicit_cache_serves_the_prefix_by_name_and_bills_the_write_once() -> None:
    models, caches = FakeModels(_cached_response()), FakeCaches()
    llm = _explicit(models, caches)

    first = await llm.complete(MANUAL)
    second = await llm.complete(MANUAL)

    config = models.calls[0]["config"]
    assert config.cached_content == "cachedContents/manual"
    # Sistema y tools viven en la caché: mandarlos junto a `cached_content` da 400.
    assert (config.system_instruction, config.tools, config.tool_config) == (None, None, None)
    assert [c.parts[0].text for c in models.calls[0]["contents"]] == ["¿Puedo cambiar la fecha?"]
    assert caches.created[0].system_instruction == "Manual de políticas"
    # 1.ª: escribe 4500 (precio de entrada) y además los lee; 2.ª: solo los lee.
    assert first.usage == TokenUsage(9020, 5, cached_input_tokens=4500, cache_write_tokens=4500)
    assert second.usage == TokenUsage(4520, 5, cached_input_tokens=4500)
    assert len(caches.created) == 1


async def test_explicit_cache_freezes_the_tools_with_the_prefix() -> None:
    models, caches = FakeModels(_cached_response()), FakeCaches()

    await _explicit(models, caches).complete(MANUAL, tools=TOOLS)

    assert caches.created[0].tools is not None
    assert caches.created[0].tool_config is not None
    assert models.calls[0]["config"].tools is None


@pytest.mark.parametrize(
    "messages",
    [
        # Sin marca: no hay prefijo que congelar.
        [ChatMessage(Role.SYSTEM, "Manual"), ChatMessage(Role.USER, "hola")],
        # Un SYSTEM después de la marca cambiaría la instrucción de sistema de la caché.
        [*MANUAL, ChatMessage(Role.SYSTEM, "Extra")],
        # Nada detrás de la marca: no queda qué preguntar.
        [ChatMessage(Role.USER, "solo prefijo", cache_checkpoint=True)],
    ],
)
async def test_without_an_exact_prefix_it_falls_back_to_the_implicit_cache(
    messages: list[ChatMessage],
) -> None:
    models, caches = FakeModels(_response([{"text": "ok"}])), FakeCaches()

    await _explicit(models, caches).complete(messages)

    assert caches.created == []
    assert models.calls[0]["config"].cached_content is None


@pytest.mark.parametrize(
    "error",
    [
        _api_error(400, "INVALID_ARGUMENT"),  # p. ej., Gemini 4: sin caché explícita.
        httpx.ConnectError("sin red"),
    ],
)
async def test_a_cache_that_cannot_be_created_never_fails_the_request(error: Exception) -> None:
    models = FakeModels(_response([{"text": "ok"}]))

    response = await _explicit(models, FakeCaches(error=error), MODEL).complete(MANUAL)

    assert response.content == "ok"
    config = models.calls[0]["config"]
    assert (config.cached_content, config.system_instruction) == (None, "Manual de políticas")


async def test_stream_uses_the_explicit_cache_too() -> None:
    models, caches = FakeModels(chunks=[_cached_response()]), FakeCaches()

    events = [e async for e in _explicit(models, caches).stream(MANUAL)]

    final = events[-1]
    assert isinstance(final, LLMResponse)
    assert final.usage.cache_write_tokens == 4500
    assert models.calls[0]["config"].cached_content == "cachedContents/manual"


def test_settings_turn_the_explicit_cache_on() -> None:
    gemini = GeminiSettings(cache="explicit", cache_ttl_seconds=300)

    llm = GeminiLLM.from_settings(GcpSettings(project="headache-dev"), gemini, model=CHEAP)

    assert llm.explicit_cache is not None
    assert (
        GeminiLLM.from_settings(
            GcpSettings(project="headache-dev"), GeminiSettings(), model=CHEAP
        ).explicit_cache
        is None
    )
