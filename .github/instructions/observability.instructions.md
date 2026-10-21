---
applyTo: "packages/headache_core/src/headache_core/ports/telemetry.py,packages/headache_core/src/headache_core/application/telemetry/**,packages/headache_core/src/headache_core/application/agents/tracing.py,packages/headache_core/src/headache_core/adapters/otel.py,packages/headache_core/src/headache_core/adapters/langfuse_tracer.py"
description: "Observabilidad (fase 11): OpenTelemetry con la convención GenAI, logs correlacionados y sin PII."
---

# Observabilidad (fase 11)

## Telemetría

- Los casos de uso piden spans a `TelemetryPort` con `get_telemetry()` (global, como `logging`). Nadie fuera de `adapters/otel.py` importa el SDK de OpenTelemetry (salvo los tests, para leer los spans).
- Por defecto `NullTelemetry`. Cada servicio llama a `setup_observability(settings, service_name=...)` al arrancar y, si es FastAPI, a `instrument_fastapi(app, settings)` (Starlette: `traced_asgi`). Con `TELEMETRY__EXPORTER=none` no tocan nada (tests intactos).
- Extra `headache-core[telemetry]` (SDK, OTLP/HTTP, instrumentación de FastAPI y httpx), separado de `observability` (SDK de Langfuse).
- Nombres de atributos solo desde `application/telemetry/genai.py` (convención GenAI de OTel en estado *Development*, más `langfuse.*` y `headache.*`). Nombre de span `{operación} {objetivo}` con objetivo de cardinalidad baja: nunca un ID de caso, de usuario o de hilo (eso va en atributos).
- Spans que existen: `chat {modelo}` (`TelemetryLLMTracer`, con TTFT por `first_token`), `execute_tool {tool}` (en `ToolExecutor.execute`; las subclases sobrescriben `_execute` y solo el ejecutor más externo abre el span), `invoke_agent {nombre}` (`TracedAgent`), `retrieval knowledge` con `search` y `rerank`, `semantic_cache.lookup/store`, `rate_limit`. El edge enriquece el span SERVER con plan, admisión, caché, modo, degradación y `headache.first_chunk`.
- Un error que vuelve al modelo como resultado (tool) o que sale tras el 200 (SSE) se marca con `mark_error`: si no, la traza lo pinta en verde.
- No usar `telemetry.span(...)` atravesando un `yield` de un generador asíncrono (el contexto se desengancha en otra tarea): ahí, `start_span` y cerrar a mano, o anotar `current_span()`.
- Tests con `recording_telemetry()` (spans en memoria, sin global de OTel). FastMCP 4 traza solo con el proveedor global y propaga el contexto en `_meta`.

## Contenido y PII

- Prompts, respuestas, argumentos y resultados solo con `TELEMETRY__CAPTURE_CONTENT=true`, y siempre por `telemetry.content(texto)` (redactado). Errores: `record_error` guarda tipo, mensaje y pila redactados.
- Logs JSON (`TELEMETRY__LOG_FORMAT=json`) con `trace_id`/`span_id` del span activo; mensaje, extras y excepción redactados.
- Con OTel activo no se usa el SDK de Langfuse (`build_tracer`): Langfuse recibe los spans por el collector.
