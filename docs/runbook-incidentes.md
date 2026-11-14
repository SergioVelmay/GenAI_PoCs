# Runbook de incidentes · asistente y agentes de Headache

Qué hacer cuando salta una alerta. Cada alerta de [headache.rules.yml](../infra/local/telemetry/rules/headache.rules.yml)
enlaza a su sección (`runbook_url`). Las reglas se generan desde los SLO del dominio
(`domain/slo.py`) con `scripts/build_observability.py`; no se editan a mano.

## Cómo se trabaja una alerta

1. **Reconocer** la alerta (quién la lleva) y abrir el canal del incidente si es una página.
2. **Medir el impacto** en el dashboard `Headache · GenAI` (Grafana :3001 en local): qué plan, qué ruta, qué agente, desde cuándo.
3. **Ir del síntoma a la causa con una traza**: en Jaeger (:16686) buscar por `service.name` y por `error=true` o por el atributo de la sección; en Langfuse, si el problema es de calidad o de coste. Los logs JSON llevan `trace_id`: desde un error en los logs se salta a su traza.
4. **Mitigar primero, arreglar después**: las palancas están en cada sección y casi todas son configuración (no hace falta desplegar código).
5. **Cerrar** con una nota corta: qué pasó, cuánto presupuesto de error se gastó, qué se cambia para que no se repita (postmortem sin culpables si fue página).

| Severidad | Qué significa | Respuesta |
|---|---|---|
| `page` | El presupuesto se acaba en horas o días a este ritmo (2 % en 1 h o 5 % en 6 h), o hay un riesgo de coste o de bloqueo inmediato | Alguien de guardia, ya |
| `ticket` | Se gasta más de lo sostenible, pero hay margen (10 % en 3 días) | En horario laboral |

## SLO

<a id="assistant-availability"></a>
### `assistant-availability` · preguntas sin respuesta

- **Qué mide**: preguntas admitidas en `/ask` y `/ask/stream` que no acaban en respuesta (5xx, 504 por presupuesto de tiempo, stream roto tras el 200). Objetivo 99,5 % en 28 días. Un 429 por abuso o un 503 por saturación no cuentan aquí.
- **Mirar**: panel *Errores del modelo por tipo*; `headache.stream.errors`; en Jaeger, spans SERVER de `edge_api` con `error=true` y su `error.type` (`stream_model_unavailable`, `LLMDeadlineExceededError`).
- **Causas típicas**: proveedor con 429 o 5xx; presupuesto de tiempo corto para el modelo actual; retrieval caído (`RetrievalUnavailableError`).
- **Mitigar**:
  - Activar la degradación si no lo está (`EDGE__DEGRADE_ON_SATURATION=true`): mejor citas literales que un error.
  - Pasar a la cadena de respaldo (`LLM_GATEWAY__FALLBACK_PROVIDERS`, p. ej. `aws,gcp`).
  - Subir `EDGE__REQUEST_BUDGET_SECONDS` solo si el p95 del modelo ha subido de forma estable.

<a id="assistant-ttft"></a>
### `assistant-ttft` · el primer trozo tarda

- **Qué mide**: respuestas calculadas (sin caché) cuyo primer trozo llega en más de 1,5 s. Objetivo 95 %.
- **Mirar**: panel *TTFT por caché* y *Modelo p95 por despliegue*; `headache.llm.time_to_first_token`; en la traza, el evento `gen_ai.first_token` del span `chat` frente a `headache.first_chunk` del edge (si la diferencia es grande, el tiempo se va antes del modelo: retrieval o espera de cuota).
- **Causas típicas**: espera de cuota (bulkhead o TPM al límite), modelo potente donde bastaría el barato, prompt más largo tras un cambio.
- **Mitigar**: revisar el router (más tráfico al modelo barato), recortar contexto (menos fragmentos), más cuota o un segundo despliegue.

<a id="public-admission"></a>
### `public-admission` · rechazos por saturación al público

- **Qué mide**: peticiones del plan público rechazadas con 503 `shed`. Objetivo 99 %.
- **Mirar**: panel *Admisión por plan*; `/ready` del edge (`in_flight` frente a `capacity`).
- **Causas típicas**: pico de venta (festival), réplicas insuficientes, peticiones lentas que ocupan los huecos.
- **Mitigar**: escalar el edge (más réplicas; el estado ya está en Redis); revisar si la caché semántica está activa (`headache.cache.lookups` por motivo); bajar el presupuesto de tiempo para liberar huecos antes.

<a id="assistant-not-degraded"></a>
### `assistant-not-degraded` · demasiadas respuestas degradadas

- **Qué mide**: respuestas servidas como citas literales porque el modelo estaba saturado. Objetivo 95 % sin degradar.
- **Mirar**: panel *Respuestas degradadas*; `headache.llm.rejections` por motivo (`circuit_open`, `bulkhead`, `quota`).
- **Mitigar**: igual que la cuota del modelo (más TPM, otro despliegue, fallback). La degradación ya está protegiendo al usuario: es un ticket, no una página.

<a id="case-processing"></a>
### `case-processing` · casos que fallan en la cola

- **Qué mide**: entregas de casos (triaje, especialistas) con error o a la DLQ. Objetivo 99 %.
- **Mirar**: panel *Colas*; `messaging.process.duration` con `error.type`; en Jaeger, spans `process {cola}` con error (cada reintento es otro `process` bajo el mismo `send`). `error.type=dead_letter` = mensaje inválido.
- **Causas típicas**: modelo o especialista A2A caídos (transitorio, se reintenta); traspaso inválido tras un cambio de contrato (DLQ).
- **Mitigar**: si es transitorio, el broker reintenta con backoff; vigilar la DLQ. Si es la DLQ, revisar el contrato del traspaso y reencolar tras arreglarlo.

<a id="llm-calls"></a>
### `llm-calls` · fallos del proveedor

- **Qué mide**: llamadas al modelo con error (429, 5xx, cuota del cliente, circuito abierto). Los cortes del usuario (`CancelledError`) no cuentan.
- **Mirar**: panel *Errores del modelo por tipo*; estado del proveedor; `headache.llm.rejections`.
- **Mitigar**: fallback de proveedor; bajar `LLM_GATEWAY__TOKENS_PER_MINUTE` si llegan 429 del proveedor (el cliente pide más de lo que hay); ampliar cuota.

## Operación

<a id="coste"></a>
### `LlmCostBudget` y `LlmCostRunaway` · gasto en modelos

- **Qué mide**: el ritmo de gasto proyectado a un día frente al presupuesto (`COST_BUDGET`, 20 $/día en el PoC). Ticket al 80 %; página al triple del presupuesto en 15 min.
- **Mirar**: panel *Coste por agente* y *Coste por modelo*; en Langfuse, las trazas más caras de la última hora.
- **Causas típicas**: un agente en bucle (`max_iterations` alto, una tool que siempre falla y el modelo reintenta), un cliente abusivo, un cambio de router que manda todo al modelo potente, la caché semántica caída.
- **Mitigar**: bajar el rate limit del plan afectado; desactivar el agente o la ruta (feature flag); forzar el modelo barato; revisar `headache.guardrail.triggers` (un `max_calls` que se dispara mucho es un bucle).

<a id="outbox"></a>
### `OutboxBacklogGrowing` · los casos no avanzan

- **Qué mide**: más de 100 eventos sin publicar durante 10 min.
- **Mirar**: logs del relay (`Fallo del relay del outbox`); `last_error` en `outbox_events`; estado del broker.
- **Mitigar**: rearrancar el relay (`python -m orchestrator.worker relay`); si el broker rechaza, revisar credenciales y cuotas de la cola. Los eventos no se pierden: están en la base de datos.

<a id="faithfulness"></a>
### `FaithfulnessDrop` · las respuestas se apoyan menos en las fuentes

- **Qué mide**: faithfulness media de la última hora por debajo de 0,8 en la evaluación online (LLM-juez sobre una muestra), con al menos 20 muestras.
- **Mirar**: en Langfuse, filtrar por la puntuación `online-faithfulness` baja y abrir las trazas; comparar con el último cambio de prompt, índice o modelo.
- **Causas típicas**: reindexado con otro chunking, un prompt nuevo que invita a completar con conocimiento propio, un modelo distinto tras un cambio de router.
- **Mitigar**: volver a la versión anterior del prompt o del índice; pasar el caso al gate offline (fase 5) antes de volver a desplegar.

<a id="inyeccion"></a>
### `PromptInjectionSpike` · intentos de inyección

- **Qué mide**: bloqueos del harness por `prompt_injection` o `indirect_injection` por encima de 0,05/s durante 10 min.
- **Mirar**: `headache.guardrail.triggers` por código; en Jaeger, turnos con `headache.agent.guardrails` y su `headache.caller.plan`.
- **Mitigar**: el harness ya bloquea. Si viene de un usuario o de un partner concreto (descripciones de eventos), limitarlo y avisar a Seguridad; si es una campaña, endurecer el rate limit del plan anónimo.

<a id="drift"></a>
### `DistributionDrift` · la mezcla de hoy no se parece a la de la semana

- **Qué mide**: PSI entre el último día y los 7 anteriores por encima de 0,25, durante 1 h y con muestra mínima (200 casos o tools; 50 veredictos del juez). Señales (`domain/drift.py`): `case-intents` (intenciones del triaje), `agent-tools` (tools ejecutadas) y `agent-resolution` (resolución según el juez online). Entre 0,1 y 0,25 no alerta, pero se ve en el panel «Drift» del dashboard.
- **Mirar**: qué categoría se mueve (`sum by (headache_case_intent)(increase(headache_cases_triaged_total[1d]))` frente a la semana); si coincide con un despliegue (prompt, modelo, router) o con algo de fuera (una ola de cancelaciones, una campaña, un partner nuevo).
- **Interpretar**: el drift **no es un error**, es una pregunta. Si cambian las intenciones y no la resolución, el sistema aguanta el cambio; si cambia la resolución o las tools sin cambiar las intenciones, algo se ha roto por dentro (modelo del proveedor, prompt, índice).
- **Mitigar**: si es interno, volver a la versión anterior del prompt o del modelo (fase 12.6) y pasar el gate offline. Si es externo y dura, ampliar los datasets de evals con los casos nuevos (`evals/datasets/`) y recalibrar el juez: la referencia de "normal" ha cambiado.
