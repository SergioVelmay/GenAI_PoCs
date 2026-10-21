---
applyTo: "infra/local/telemetry/**,scripts/telemetry_demo.py,scripts/build_observability.py,packages/headache_core/src/headache_core/adapters/prometheus.py,packages/headache_core/src/headache_core/domain/slo.py,packages/headache_core/src/headache_core/application/telemetry/messaging.py,services/orchestrator/src/orchestrator/remote/tracing.py,packages/headache_core/src/headache_core/ports/telemetry.py,packages/headache_core/src/headache_core/application/telemetry/**,packages/headache_core/src/headache_core/application/agents/tracing.py,packages/headache_core/src/headache_core/adapters/otel.py,packages/headache_core/src/headache_core/adapters/langfuse_tracer.py"
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
- Tests con `recording_telemetry()` (spans en memoria, sin global de OTel; `.tree()` pinta el árbol). FastMCP 4 traza solo con el proveedor global y propaga el contexto en `_meta`.

## Propagación (fase 11.3)

- Contexto W3C (`traceparent`, `tracestate`, `baggage`; `PROPAGATED_HEADERS`) fuera del cuerpo: `Message.headers` / `publish(..., headers=)` (cabeceras AMQP, atributos SQS, propiedades de aplicación de Service Bus) y columna `outbox_events.headers` (migración 0008).
- El outbox guarda el contexto del span activo al escribir (`add_events`, `InMemoryOutbox.append`) si el evento no trae el suyo. `OutboxRelay` abre `outbox.relay` con ese contexto como padre.
- `open_message_bus` devuelve un `TracedMessageBus`: `send {cola}` (PRODUCER) y `process {cola}` (CONSUMER, padre = cabeceras; cada reentrega es otro `process`). Padre y no *link*: un caso es una sola traza.
- A2A: `RemoteSpecialist` abre `invoke_agent {especialista}` (CLIENT) e inyecta el contexto con un hook de httpx; el especialista abre el span SERVER con `TraceContextMiddleware`. Sin la instrumentación ASGI genérica (duplicaría el SERVER).
- `approval_required` no marca el span de la tool como error (`PENDING_OUTCOMES`): queda en `headache.tool.outcome`.
- Probar la propagación en contexto vacío (`asyncio.create_task(..., context=contextvars.Context())`): si no, el contexto heredado de asyncio oculta que no viaja.

## Métricas y SLO (fase 11.4)

- Instrumentos por el puerto (`counter`, `histogram`, `gauge`), pero los nombres, unidades, cubos y atributos solo en `application/telemetry/metrics.py` (funciones `llm_call`, `tool_call`, `agent_turn`, `admission`, `answer`, `message_processed`, `outbox_backlog`...). Nadie llama a `telemetry.counter("...")` suelto.
- Histogramas de latencia en segundos con `LATENCY_BUCKETS` / `TTFT_BUCKETS` (los cubos por defecto de OTel son de ms).
- Cardinalidad baja: modelo, nivel, plan, ruta, agente, tool, resultado, `error.type`. Nunca IDs (caso, usuario, hilo). Tool desconocida → `unknown`. Booleanos como texto (`"true"`/`"false"`).
- El agente activo lo fija `TracedAgent` con `agent_scope` (contextvar): modelo y tools lo leen con `current_agent()` (coste por agente sin tocar el gateway). Sin agente: `none`.
- Rechazos del gateway sin llamar al proveedor (`headache.llm.rejections`, motivo `circuit_open`/`bulkhead`/`quota`/`shared_quota`): las capas reciben `name=` del despliegue.
- Un stream SSE roto tras el 200 cuenta en `headache.stream.errors`. El TTFT del edge se registra al final, con la caché como atributo.
- SLO, presupuesto de error, *burn rate* multiventana y presupuesto de coste en `domain/slo.py` (Python puro). Las reglas de alerta (11.6) salen de ese catálogo.
- Tests: `recording_telemetry()` también graba métricas (`recorded.points(nombre)`, `recorded.total(nombre, **atributos)`).

## Stack local (fase 11.5)

- `docker compose --profile telemetry up -d`: collector (`infra/local/telemetry/otel-collector.yaml`, OTLP en 127.0.0.1:4317/4318), Jaeger (:16686), Prometheus (:9095) y Grafana (:3001, dashboard `Headache · GenAI`). Con `OTEL_COLLECTOR_EXTRA=otel-collector.langfuse` y el perfil `observability`, el collector reenvía a Langfuse (Basic Auth con las claves del proyecto; solo spans GenAI y de entrada).
- Los servicios exportan con `TELEMETRY__EXPORTER=otlp`. Tráfico sintético sin coste: `uv run python scripts/telemetry_demo.py --rounds 3 [--minutes 3]`.
- El collector borra prompts y respuestas antes de Jaeger (`attributes/no-content`); el contenido capturado solo llega a Langfuse.
- Prometheus: `translation_strategy: UnderscoreEscapingWithSuffixes` fijado y solo `service.name` y `deployment.environment.name` como etiquetas de recurso (sin id de instancia).
- El dashboard se genera desde `metrics.CATALOG` (`adapters/prometheus.py`, `scripts/build_observability.py`); no editar el JSON a mano. Un test comprueba que está al día y que cada métrica usada existe.

## Contenido y PII

- Prompts, respuestas, argumentos y resultados solo con `TELEMETRY__CAPTURE_CONTENT=true`, y siempre por `telemetry.content(texto)` (redactado). Errores: `record_error` guarda tipo, mensaje y pila redactados.
- Logs JSON (`TELEMETRY__LOG_FORMAT=json`) con `trace_id`/`span_id` del span activo; mensaje, extras y excepción redactados.
- Con OTel activo no se usa el SDK de Langfuse (`build_tracer`): Langfuse recibe los spans por el collector.
