from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind as OTelSpanKind
from opentelemetry.trace import StatusCode
from pydantic import SecretStr

from headache_core.adapters.llm_factory import build_tracer
from headache_core.adapters.memory import FakeLLM
from headache_core.adapters.otel import OTelTelemetry, build_tracer_provider
from headache_core.application.llm import ObservedLLM, PriceTable
from headache_core.application.llm.streaming import stream_from
from headache_core.application.telemetry import (
    JsonLogFormatter,
    NullTelemetry,
    TelemetryLLMTracer,
    genai,
    get_telemetry,
    redact_text,
    set_telemetry,
)
from headache_core.config import LangfuseSettings, Settings, TelemetrySettings
from headache_core.ports import ChatMessage, LLMTransientError, Role, SpanKind, ToolCall

HELLO = [ChatMessage(Role.SYSTEM, "Eres el asistente."), ChatMessage(Role.USER, "hola")]


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def telemetry(exporter: InMemorySpanExporter) -> Iterator[OTelTelemetry]:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry = OTelTelemetry(provider.get_tracer("test"))
    set_telemetry(telemetry)
    yield telemetry
    set_telemetry(NullTelemetry())


def _only(exporter: InMemorySpanExporter) -> ReadableSpan:
    (span,) = exporter.get_finished_spans()
    return span


async def test_generation_span_follows_the_genai_convention(
    telemetry: OTelTelemetry, exporter: InMemorySpanExporter
) -> None:
    llm = ObservedLLM(
        FakeLLM(["una respuesta"], model="gpt-4.1-mini"),
        tracer=TelemetryLLMTracer(),
        prices=PriceTable(),
        name="azure:cheap",
        metadata={"engine": "native", "provider": "azure", "tier": "cheap"},
    )

    with telemetry.span("POST /ask", kind=SpanKind.SERVER):
        await llm.complete(HELLO)

    spans = {s.name: s for s in exporter.get_finished_spans()}
    chat = spans["chat gpt-4.1-mini"]
    attributes = dict(chat.attributes or {})
    assert chat.kind is OTelSpanKind.CLIENT
    assert chat.parent is not None
    assert chat.parent.span_id == spans["POST /ask"].context.span_id
    assert attributes[genai.OPERATION_NAME] == "chat"
    assert attributes[genai.PROVIDER_NAME] == "headache.fake"
    assert attributes[genai.RESPONSE_MODEL] == "gpt-4.1-mini"
    assert attributes[genai.HEADACHE_TIER] == "cheap"
    assert attributes[genai.LANGFUSE_OBSERVATION_TYPE] == "generation"
    assert attributes[genai.USAGE_INPUT_TOKENS] > 0
    assert attributes[genai.USAGE_COST] > 0
    # Sin captura de contenido no viaja ni el prompt ni la respuesta.
    assert genai.INPUT_MESSAGES not in attributes
    assert genai.OUTPUT_MESSAGES not in attributes


async def test_captured_content_is_redacted(
    telemetry: OTelTelemetry, exporter: InMemorySpanExporter
) -> None:
    calls = [ToolCall("c1", "get_order", '{"email": "ana@example.com"}')]
    llm = ObservedLLM(
        FakeLLM([calls]),
        tracer=TelemetryLLMTracer(capture_content=True),
        prices=PriceTable(),
        name="x",
    )

    await llm.complete([ChatMessage(Role.USER, "Soy ana@example.com, tel 612 345 678")])

    attributes = dict(_only(exporter).attributes or {})
    prompt = json.loads(str(attributes[genai.INPUT_MESSAGES]))
    output = json.loads(str(attributes[genai.OUTPUT_MESSAGES]))
    assert prompt[0]["parts"][0]["content"] == "Soy <EMAIL_ADDRESS>, tel <PHONE_NUMBER>"
    assert output[0]["parts"][0]["name"] == "get_order"
    assert "ana@example.com" not in str(attributes)
    assert str(attributes[genai.LANGFUSE_INPUT]) == str(attributes[genai.INPUT_MESSAGES])


async def test_failed_generation_records_type_without_message(
    telemetry: OTelTelemetry, exporter: InMemorySpanExporter
) -> None:
    llm = ObservedLLM(
        FakeLLM([LLMTransientError("503 para ana@example.com")]),
        tracer=TelemetryLLMTracer(),
        prices=PriceTable(),
        name="azure:strong",
    )

    with pytest.raises(LLMTransientError):
        await llm.complete(HELLO)

    span = _only(exporter)
    assert span.status.status_code is StatusCode.ERROR
    assert dict(span.attributes or {})[genai.ERROR_TYPE] == "LLMTransientError"
    (event,) = span.events
    assert "ana@example.com" not in str(event.attributes)


async def test_streamed_generation_is_one_span(
    telemetry: OTelTelemetry, exporter: InMemorySpanExporter
) -> None:
    llm = ObservedLLM(FakeLLM(["hola"]), tracer=TelemetryLLMTracer(), prices=PriceTable(), name="x")

    events = [event async for event in stream_from(llm, HELLO)]

    assert events
    span = _only(exporter)
    assert dict(span.attributes or {})[genai.HEADACHE_STREAM] == "true"


async def test_exception_inside_a_span_marks_it_and_propagates(
    telemetry: OTelTelemetry, exporter: InMemorySpanExporter
) -> None:
    with pytest.raises(ValueError), telemetry.span("handle"):
        raise ValueError("cliente 12345678Z")

    span = _only(exporter)
    assert span.status.status_code is StatusCode.ERROR
    assert span.events[0].attributes is not None
    assert span.events[0].attributes["exception.message"] == "cliente <ES_NIF>"


def test_current_ids_only_inside_a_span(telemetry: OTelTelemetry) -> None:
    assert telemetry.current_ids() is None
    with telemetry.span("x"):
        ids = telemetry.current_ids()
    assert ids is not None
    assert len(ids.trace_id) == 32
    assert len(ids.span_id) == 16


def test_null_telemetry_is_the_default() -> None:
    assert isinstance(get_telemetry(), NullTelemetry)
    with get_telemetry().span("x") as span:
        span.set_attribute("a", 1)
    assert get_telemetry().current_ids() is None


def test_sampling_respects_the_ratio(exporter: InMemorySpanExporter) -> None:
    provider = build_tracer_provider(
        TelemetrySettings(sample_ratio=0.0),
        service_name="edge",
        environment="local",
        exporters=[exporter],
    )
    telemetry = OTelTelemetry(provider.get_tracer("test"))

    with telemetry.span("x"):
        pass
    provider.force_flush()

    assert exporter.get_finished_spans() == ()


def test_resource_names_the_service(exporter: InMemorySpanExporter) -> None:
    provider = build_tracer_provider(
        TelemetrySettings(), service_name="edge_api", environment="dev", exporters=[exporter]
    )
    with OTelTelemetry(provider.get_tracer("test")).span("x"):
        pass
    provider.force_flush()

    resource = _only(exporter).resource.attributes
    assert resource["service.name"] == "edge_api"
    assert resource["deployment.environment.name"] == "dev"


def test_json_logs_carry_trace_ids_and_no_pii(telemetry: OTelTelemetry) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter(service="edge_api"))
    logger = logging.getLogger("test.telemetry")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        with telemetry.span("x"):
            ids = telemetry.current_ids()
            logger.warning("Reembolso para %s", "ana@example.com", extra={"case_id": "case_1"})
    finally:
        logger.removeHandler(handler)

    line = json.loads(stream.getvalue())
    assert ids is not None
    assert line["trace_id"] == ids.trace_id
    assert line["span_id"] == ids.span_id
    assert line["message"] == "Reembolso para <EMAIL_ADDRESS>"
    assert line["case_id"] == "case_1"
    assert line["service"] == "edge_api"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mail ana.g+x@headache.example", "mail <EMAIL_ADDRESS>"),
        ("tel +34 612 345 678", "tel <PHONE_NUMBER>"),
        ("tarjeta 4111 1111 1111 1111", "tarjeta <CREDIT_CARD>"),
        ("IBAN ES91 2100 0418 4502 0005 1332", "IBAN <IBAN_CODE>"),
        ("Authorization: Bearer abcdefgh12345678", "Authorization: <SECRET>"),
        ("token eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl", "token <JWT>"),
        # Controles benignos: no son PII y deben quedar intactos.
        ("fecha 2026-10-21 a las 14:42", "fecha 2026-10-21 a las 14:42"),
        ("ts 1792587425874 ms", "ts 1792587425874 ms"),
        ("pedido ord_00123 por 120.50 EUR", "pedido ord_00123 por 120.50 EUR"),
        ("aforo 120000 personas", "aforo 120000 personas"),
    ],
)
def test_redaction(text: str, expected: str) -> None:
    assert redact_text(text) == expected


def test_otel_wins_over_the_langfuse_sdk() -> None:
    settings = Settings(
        _env_file=None,
        telemetry=TelemetrySettings(exporter="otlp"),
        langfuse=LangfuseSettings(public_key="pk", secret_key=SecretStr("sk")),
    )

    assert isinstance(build_tracer(settings), TelemetryLLMTracer)
