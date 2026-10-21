"""Fase 11.2: el span de cada petición del edge cuenta qué se decidió (plan, admisión, caché,
modo de respuesta, primer trozo) para poder filtrar y medir por esas dimensiones."""

from __future__ import annotations

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import SpanKind, StatusCode

from edge_api.testing import ASK, FakeAssistant, bearer, client, token
from headache_core.adapters.otel import RecordedTelemetry, recording_telemetry
from headache_core.application.telemetry import genai, metrics


def _server(recorded: RecordedTelemetry, name: str) -> list[ReadableSpan]:
    return [
        s
        for s in recorded.exporter.get_finished_spans()
        if s.name == name and s.kind is SpanKind.SERVER
    ]


async def test_ask_span_carries_plan_admission_and_answer() -> None:
    with recording_telemetry() as recorded:
        async with client(tracer_provider=recorded.provider) as http:
            ok = await http.post("/ask", json=ASK, headers=bearer(token("usr_1", plan="public")))

    assert ok.status_code == 200
    (span,) = _server(recorded, "POST /ask")
    attributes = dict(span.attributes or {})
    assert attributes[genai.HEADACHE_PLAN] == "public"
    assert attributes[genai.HEADACHE_ADMISSION] == "admitted"
    assert attributes[genai.HEADACHE_ANSWER_MODE] == "extractive"
    assert attributes[genai.HEADACHE_DEGRADED] is False
    assert attributes["http.route"] == "/ask"
    # El rate limit cuelga de la petición.
    (limit,) = [s for s in recorded.exporter.get_finished_spans() if s.name == "rate_limit"]
    assert limit.parent is not None
    assert limit.parent.span_id == span.context.span_id


async def test_answers_and_ttft_are_measured_by_plan_and_cache() -> None:
    with recording_telemetry() as recorded:
        async with client() as http:
            headers = bearer(token("usr_1", plan="public"))
            await http.post("/ask", json=ASK, headers=headers)
            await http.post("/ask/stream", json=ASK, headers=headers)

    public = {genai.HEADACHE_PLAN: "public", genai.HEADACHE_DEGRADED: "false"}
    assert recorded.total(metrics.ANSWERS, **public) == 2
    assert recorded.total(metrics.ANSWER_DURATION, **{"headache.route": "/ask"}) == 1
    # El TTFT solo en streaming, y con la caché como atributo (acierto y cálculo no se mezclan).
    [(attributes, _)] = recorded.points(metrics.ANSWER_TTFT)
    assert attributes["headache.route"] == "/ask/stream"
    assert attributes[genai.HEADACHE_CACHE] == "miss"


async def test_rejections_are_visible_by_reason() -> None:
    with recording_telemetry() as recorded:
        async with client(tracer_provider=recorded.provider) as http:
            for _ in range(3):  # Anónimo: 2 por minuto.
                await http.post("/ask", json=ASK)

    outcomes = [
        str(dict(s.attributes or {})[genai.HEADACHE_ADMISSION])
        for s in _server(recorded, "POST /ask")
    ]
    assert sorted(outcomes) == ["admitted", "admitted", "rate_limited"]


async def test_stream_marks_first_chunk_and_mid_stream_errors() -> None:
    assistant = FakeAssistant()
    assistant.fail_after = 2
    with recording_telemetry() as recorded:
        async with client(assistant, tracer_provider=recorded.provider) as http:
            response = await http.post(
                "/ask/stream", json=ASK, headers=bearer(token("usr_1", plan="public"))
            )

    assert response.status_code == 200
    assert "event: error" in response.text
    (span,) = _server(recorded, "POST /ask/stream")
    assert genai.EVENT_FIRST_CHUNK in [e.name for e in span.events]
    # Las cabeceras dijeron 200, pero la traza sabe que la respuesta se rompió.
    assert span.status.status_code is StatusCode.ERROR
    assert dict(span.attributes or {})[genai.ERROR_TYPE] == "stream_internal"
    # Y cuenta para el SLO de disponibilidad aunque el código HTTP fuera 200.
    assert recorded.total(metrics.STREAM_ERRORS, **{genai.ERROR_TYPE: "stream_internal"}) == 1
