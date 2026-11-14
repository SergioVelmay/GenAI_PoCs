"""Adaptador Gemini (Gemini Enterprise Agent Platform, antes Vertex AI) con `google-genai`.

Prompt caching: **implícito** por defecto (prefijos idénticos de >= 4096 tokens en los modelos
actuales, *best-effort* y sin coste de escritura). Se lee en `cached_content_token_count`, que
ya va incluido en `prompt_token_count` (la semántica de OpenAI: no hay que sumar nada). La caché
explícita (`CachedContent`, con TTL y almacenamiento por hora) llega en la 2.G3.

Tres diferencias con Azure y Bedrock que el adaptador absorbe:

- ***Thought signatures***: Gemini 3/4 devuelven un estado opaco de su razonamiento en la
  primera `functionCall` de cada paso y exigen recibirlo de vuelta (si falta, 400). Viaja en
  `ToolCall.signature`. Si el historial lo generó otro proveedor (fallback a mitad de una
  conversación), se usa el valor que Google documenta para saltar la validación.
- **Sin temperatura**: los modelos de 3.5 en adelante ignoran `temperature`, `top_p` y `top_k`
  (y anuncian 400 en generaciones futuras). La profundidad se controla con `thinking_level`.
- **Razonamiento facturado como salida**: `thoughts_token_count` se suma a los tokens de salida
  y también consume `max_output_tokens`.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from typing import Any, Protocol, Self

import aiohttp
import httpx
from google.auth.exceptions import GoogleAuthError
from google.genai import Client, errors, types

from headache_core.adapters.gcp_auth import gcp_credentials
from headache_core.config import GcpSettings, GeminiSettings, ThinkingLevel
from headache_core.ports import (
    ChatMessage,
    LLMContentFilterError,
    LLMError,
    LLMRateLimitError,
    LLMResponse,
    LLMStreamEvent,
    LLMTransientError,
    ResponseSchema,
    Role,
    TextDelta,
    TokenUsage,
    ToolCall,
    ToolConfig,
)

# Valor documentado por Google para `functionCall` que Gemini no generó (historial de otro
# proveedor). Es el "último recurso": degrada el razonamiento, pero sin él la petición da 400.
SKIP_SIGNATURE = b"skip_thought_signature_validator"

_TRANSIENT_STATUS = frozenset({408, 500, 502, 503, 504})
# El SDK usa aiohttp en asíncrono si está instalado (lo está: lo trae LiteLLM) y httpx si no.
_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    aiohttp.ClientConnectionError,
    TimeoutError,
)
_SDK_ERRORS: tuple[type[BaseException], ...] = (
    errors.APIError,
    GoogleAuthError,
    *_TRANSPORT_ERRORS,
)

_FILTERED = frozenset(
    {
        types.FinishReason.SAFETY,
        types.FinishReason.PROHIBITED_CONTENT,
        types.FinishReason.BLOCKLIST,
        types.FinishReason.SPII,
        types.FinishReason.RECITATION,
        types.FinishReason.IMAGE_SAFETY,
        types.FinishReason.IMAGE_PROHIBITED_CONTENT,
    }
)
_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s$")


class GeminiModels(Protocol):
    """Lo que el adaptador usa de `Client.aio.models` (permite un doble en los tests)."""

    async def generate_content(
        self, *, model: str, contents: list[types.Content], config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse: ...

    async def generate_content_stream(
        self, *, model: str, contents: list[types.Content], config: types.GenerateContentConfig
    ) -> AsyncIterator[types.GenerateContentResponse]: ...


def _retry_after(exc: errors.APIError) -> float | None:
    """`RetryInfo.retryDelay` ("31s") de los detalles del error de Google."""
    body = exc.details if isinstance(exc.details, Mapping) else {}
    error = body.get("error", body)
    details = error.get("details", []) if isinstance(error, Mapping) else []
    for detail in details if isinstance(details, list) else []:
        if isinstance(detail, Mapping) and str(detail.get("@type", "")).endswith("RetryInfo"):
            match = _DELAY.match(str(detail.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return None


def to_llm_error(exc: BaseException) -> LLMError:
    """Traduce los errores del SDK (también los de transporte y credenciales)."""
    if isinstance(exc, errors.APIError):
        if exc.code == 429:  # RESOURCE_EXHAUSTED: cuota del proyecto o capacidad compartida.
            return LLMRateLimitError(str(exc), retry_after=_retry_after(exc))
        if exc.code in _TRANSIENT_STATUS:  # Incluye 504 DEADLINE_EXCEEDED.
            return LLMTransientError(str(exc), retry_after=_retry_after(exc))
        return LLMError(str(exc))
    if isinstance(exc, _TRANSPORT_ERRORS):
        return LLMTransientError(str(exc) or type(exc).__name__)
    return LLMError(str(exc))  # Credenciales, proyecto...: no se arreglan reintentando.


def _signature(value: str | None) -> bytes | None:
    if value is None:
        return None
    try:
        return base64.b64decode(value, validate=True)
    except binascii.Error:
        return None


def _args(arguments: str) -> dict[str, Any]:
    # Gemini exige un objeto; unos argumentos inválidos (p. ej. tras un fallback) viajan vacíos
    # y el error de validación ya está en la respuesta de la tool siguiente.
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _tool_response(message: ChatMessage) -> dict[str, Any]:
    # Convención de Gemini: la salida va en `output` y un fallo en `error`.
    try:
        value: Any = json.loads(message.content)
    except json.JSONDecodeError:
        value = message.content
    return {"error" if message.is_error else "output": value}


def _model_parts(message: ChatMessage, names: dict[str, str]) -> list[types.Part]:
    parts = [types.Part(text=message.content)] if message.content else []
    for index, call in enumerate(message.tool_calls):
        names[call.id] = call.name
        signature = _signature(call.signature)
        # Solo la primera llamada de cada paso lleva firma (duplicarla también da 400).
        if index == 0 and signature is None:
            signature = SKIP_SIGNATURE
        parts.append(
            types.Part(
                function_call=types.FunctionCall(
                    id=call.id, name=call.name, args=_args(call.arguments)
                ),
                thought_signature=signature,
            )
        )
    return parts


def to_contents(messages: Sequence[ChatMessage]) -> tuple[str | None, list[types.Content]]:
    """`ChatMessage` → instrucción de sistema + turnos `user`/`model` de Gemini.

    `cache_checkpoint` no se traduce: la caché implícita no necesita marca. Las respuestas de
    llamadas en paralelo van juntas en un solo turno `user` y en el mismo orden (si no, 400).
    """
    system: list[str] = []
    contents: list[types.Content] = []
    names: dict[str, str] = {}  # id de la llamada → nombre (FunctionResponse exige los dos).
    for message in messages:
        if message.role is Role.SYSTEM:
            system.append(message.content)
            continue
        if message.role is Role.TOOL:
            if message.tool_call_id is None:
                raise LLMError("Un mensaje TOOL necesita tool_call_id.")
            name = names.get(message.tool_call_id)
            if name is None:
                raise LLMError(f"El resultado {message.tool_call_id!r} no responde a ninguna tool.")
            part = types.Part(
                function_response=types.FunctionResponse(
                    id=message.tool_call_id, name=name, response=_tool_response(message)
                )
            )
            previous = contents[-1] if contents else None
            if previous and previous.parts and previous.parts[-1].function_response:
                previous.parts.append(part)
            else:
                contents.append(types.Content(role="user", parts=[part]))
            continue
        if message.role is Role.ASSISTANT:
            parts = _model_parts(message, names)
            role = "model"
        else:
            parts = [types.Part(text=message.content)]
            role = "user"
        if parts:  # Gemini rechaza turnos sin partes (p. ej. un asistente vacío).
            contents.append(types.Content(role=role, parts=parts))
    return ("\n\n".join(system) if system else None), contents


def _tools(config: ToolConfig) -> tuple[list[types.Tool], types.ToolConfig]:
    declarations = [
        types.FunctionDeclaration(
            name=spec.name,
            description=spec.description,
            parameters_json_schema=dict(spec.json_schema),
        )
        for spec in config.tools
    ]
    if config.choice == "auto":
        calling = types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.AUTO)
    elif config.choice == "required":
        calling = types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.ANY)
    else:
        calling = types.FunctionCallingConfig(
            mode=types.FunctionCallingConfigMode.ANY, allowed_function_names=[config.choice]
        )
    # `parallel=False` no tiene equivalente en Gemini (como en Bedrock): se ignora.
    return [types.Tool(function_declarations=declarations)], types.ToolConfig(
        function_calling_config=calling
    )


def _check_blocked(response: types.GenerateContentResponse) -> None:
    feedback = response.prompt_feedback
    if feedback is not None and feedback.block_reason is not None:
        raise LLMContentFilterError(f"Gemini bloqueó la petición: {feedback.block_reason.name}")


def _check_finish(reason: types.FinishReason | None) -> None:
    if reason in _FILTERED:
        raise LLMContentFilterError(f"Gemini cortó la respuesta: {reason.name}")
    if reason is types.FinishReason.MALFORMED_FUNCTION_CALL:
        # Fallo del modelo al generar la llamada: suele salir bien al repetir.
        raise LLMTransientError("Gemini generó una llamada a tool mal formada.")


def _text(parts: Sequence[types.Part]) -> str:
    # Las partes `thought` son el razonamiento resumido, no la respuesta.
    return "".join(part.text for part in parts if part.text and not part.thought)


def _tool_calls(parts: Sequence[types.Part]) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    for part in parts:
        call = part.function_call
        if call is None or not call.name:
            continue
        calls.append(
            ToolCall(
                # Gemini 3.5+ siempre envía id; si faltara, se inventa uno estable para el turno.
                id=call.id or f"call_{uuid.uuid4().hex[:12]}",
                name=call.name,
                arguments=json.dumps(call.args or {}, ensure_ascii=False),
                signature=(
                    base64.b64encode(part.thought_signature).decode("ascii")
                    if part.thought_signature
                    else None
                ),
            )
        )
    return tuple(calls)


def _usage(meta: types.GenerateContentResponseUsageMetadata | None) -> TokenUsage:
    if meta is None:
        return TokenUsage(0, 0)
    return TokenUsage(
        # `tool_use_prompt_token_count`: resultados de tools integradas, facturados como entrada.
        input_tokens=(meta.prompt_token_count or 0) + (meta.tool_use_prompt_token_count or 0),
        output_tokens=(meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0),
        cached_input_tokens=meta.cached_content_token_count or 0,
    )


def _finish_name(reason: types.FinishReason | None) -> str | None:
    return reason.name.lower() if reason is not None else None


class GeminiLLM:
    def __init__(
        self, models: GeminiModels, model: str, *, thinking_level: ThinkingLevel = ""
    ) -> None:
        self._models = models
        self._model = model
        self._thinking_level = thinking_level

    @classmethod
    def from_settings(
        cls,
        gcp: GcpSettings,
        gemini: GeminiSettings,
        *,
        model: str,
        thinking_level: ThinkingLevel = "",
    ) -> Self:
        # Sin `retry_options` el SDK hace un solo intento: los reintentos son del gateway.
        http_options = types.HttpOptions(timeout=int(gemini.timeout_seconds * 1000))
        if gemini.enterprise:
            if not gcp.project:
                raise LLMError("Falta GCP__PROJECT (Gemini en Agent Platform).")
            client = Client(
                enterprise=True,
                project=gcp.project,
                location=gemini.location,
                credentials=gcp_credentials(gcp),
                http_options=http_options,
            )
        else:
            key = gemini.api_key.get_secret_value() if gemini.api_key else ""
            if not key:
                raise LLMError("Falta GEMINI__API_KEY (o usa GEMINI__ENTERPRISE=true).")
            client = Client(api_key=key, http_options=http_options)
        return cls(client.aio.models, model, thinking_level=thinking_level)

    def _request(
        self,
        messages: Sequence[ChatMessage],
        max_tokens: int | None,
        response_schema: ResponseSchema | None,
        tools: ToolConfig | None,
    ) -> tuple[list[types.Content], types.GenerateContentConfig]:
        system, contents = to_contents(messages)
        level = self._thinking_level
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            thinking_config=(
                types.ThinkingConfig(thinking_level=types.ThinkingLevel(level.upper()))
                if level
                else None
            ),
        )
        if tools is not None:
            declared, config.tool_config = _tools(tools)
            config.tools = list(declared)
        if response_schema is not None:
            config.response_mime_type = "application/json"
            config.response_json_schema = dict(response_schema.json_schema)
        return contents, config

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,  # Ignorada: ver el docstring del módulo.
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
        tools: ToolConfig | None = None,
    ) -> LLMResponse:
        contents, config = self._request(messages, max_tokens, response_schema, tools)
        started = time.perf_counter()
        try:
            response = await self._models.generate_content(
                model=self._model, contents=contents, config=config
            )
        except _SDK_ERRORS as exc:
            raise to_llm_error(exc) from exc
        latency_ms = (time.perf_counter() - started) * 1000

        _check_blocked(response)
        if not response.candidates:
            raise LLMError("Gemini devolvió una respuesta sin candidatos.")
        candidate = response.candidates[0]
        _check_finish(candidate.finish_reason)
        parts = candidate.content.parts if candidate.content and candidate.content.parts else []
        return LLMResponse(
            content=_text(parts),
            model=response.model_version or self._model,
            usage=_usage(response.usage_metadata),
            provider="gcp",
            latency_ms=latency_ms,
            finish_reason=_finish_name(candidate.finish_reason),
            tool_calls=_tool_calls(parts),
        )

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        """`generate_content_stream`: el uso llega acumulado en los trozos (vale el último) y la
        firma puede venir en una parte de texto vacía al final, que no se emite."""
        contents, config = self._request(messages, max_tokens, None, None)
        started = time.perf_counter()
        chunks_text: list[str] = []
        model = self._model
        usage: types.GenerateContentResponseUsageMetadata | None = None
        reason: types.FinishReason | None = None
        try:
            chunks = await self._models.generate_content_stream(
                model=self._model, contents=contents, config=config
            )
            async for chunk in chunks:
                _check_blocked(chunk)
                model = chunk.model_version or model
                usage = chunk.usage_metadata or usage
                if not chunk.candidates:
                    continue
                candidate = chunk.candidates[0]
                reason = candidate.finish_reason or reason
                parts = candidate.content.parts if candidate.content else None
                text = _text(parts or [])
                if text:
                    chunks_text.append(text)
                    yield TextDelta(text)
        except _SDK_ERRORS as exc:
            raise to_llm_error(exc) from exc
        _check_finish(reason)
        yield LLMResponse(
            content="".join(chunks_text),
            model=model,
            usage=_usage(usage),
            provider="gcp",
            latency_ms=(time.perf_counter() - started) * 1000,
            finish_reason=_finish_name(reason),
        )
