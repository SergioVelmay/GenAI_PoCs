---
applyTo: "services/orchestrator/**,packages/headache_core/src/headache_core/domain/orchestration.py,packages/headache_core/src/headache_core/application/orchestration/**,packages/headache_core/src/headache_core/ports/cases.py,packages/headache_core/src/headache_core/adapters/postgres/cases.py,packages/harness/src/harness/rules/case_reply.py"
description: "Orquestación multi-agente: Triaje → Reembolsos / Creativo / Conocimiento (fase 9)."
---

# Orquestación multi-agente (fase 9)

## Contratos (no romperlos)

- El LLM clasifica y el dominio enruta: `domain/orchestration.py` (`ROUTES` total sobre `SupportIntent`, `CaseStatus` con transiciones, `CaseEnvelope`, `CaseOutcome`). `customer_id` sale del sistema de correo; el pedido del sistema manda sobre el extraído.
- Traspaso mínimo: `render_handoff` (hechos + resumen, sin email ni `customer_id`); identidad en `tool_context(envelope)`. Serialización con `envelope_to_payload`/`envelope_from_payload` (rechaza rutas que contradicen `ROUTES` con `InvalidHandoffError`).
- Todo especialista cumple `SpecialistAgent.handle(envelope) -> CaseOutcome`, se ejecute en proceso, por cola o por A2A.

## Especialistas (`orchestrator/specialists/`)

- Reembolsos y Conocimiento: `AgentSpecialist` sobre cualquier sabor de `booking_agent` (`create_agent`), con `guarded_executor` + `guarded_agent` de la fase 8. Agente nuevo por caso (`thread_id = case_id`). El estado sale del turno (`outcome_from_turn`): `stop_reason != "answer"` → escalado; `AgentTurn.approvals` → pendiente de aprobación.
- Mínimo privilegio en `specs.py`: Reembolsos sin catálogo ni memoria de escritura; Conocimiento sin `request_refund`; solo Conocimiento escribe preferencias.
- Creativo: workflow, no agente (`draft_case_reply` + `submit_case_reply` de `harness/rules/case_reply.py`): plantilla por audiencia (cliente/partner), sin promesas de reembolso, y siempre aprobación humana antes de enviar (`send_case_reply`).
- Composición: `build_specialists(SpecialistDeps)`; `build_approval_service` combina las tools que solo ejecuta un revisor.
- Tests y demos: `orchestrator.testing` (`local_world`, `support_email`, `Outbox`).

## LangGraph (`graph.py`, `supervisor.py`, `checkpoints.py`)

- `RouterGraph`: triage → arista condicional por `envelope.specialist` → subgrafo del especialista → `human_review` (`interrupt`) si queda pendiente. `thread_id = case_id = case_<email_id>`. Estado solo con JSON plano (`*_to_payload`).
- `start(email)` es idempotente (duplicado = estado actual) y reanuda un caso a medias; `review(case_id, reviewer, approve)` valida antes de reanudar. En el nodo, una decisión inválida vuelve a pausar (no lanzar: LangGraph guarda los valores de reanudación). Nada con efectos antes de `interrupt`.
- `RetryPolicy` solo para `LLMTransientError`; reintentar un especialista es seguro porque las aprobaciones son idempotentes.
- `SupervisorGraph` (comparativa): el LLM elige la siguiente intención; el dominio enruta, corta repeticiones y limita pasos.
- Revisión humana compartida por LangGraph y MAF: `review.py` (`parse_decision`, `apply_review`, `ReviewError`).
- PostgreSQL: `postgres_checkpointer` en el esquema `langgraph` (fuera de Alembic). En Windows, psycopg async necesita el bucle Selector: lo pone `conftest.py` de la raíz para los tests `integration`.

## A2A (`remote/`, `serve.py`)

- Servidor: `create_specialist_app` (a2a-sdk 1.x, JSON-RPC en `/a2a`, Agent Card pública en `/.well-known/agent-card.json`, `/health`). El traspaso va en un único DataPart; inválido = tarea `REJECTED`; resultado = artefacto `case_outcome`.
- Cliente: `RemoteSpecialist` cumple `SpecialistAgent`; comprueba el nombre de la Agent Card, el estado de la tarea y que el resultado es del mismo caso y especialista.
- OAuth (`remote/auth.py`): token de servicio (`sub` en `A2A__ALLOWED_CALLERS`, audience por especialista, scope `cases:handle`); sin token o audience incorrecto = 401, sin scope o llamante no permitido = 403. Sin `verifier` solo en tests en proceso.
- Contenedores: `services/orchestrator/Dockerfile`, perfil `agents` (8201-8203). `build_specialist` construye solo lo que necesita cada contenedor (el Creativo no carga conocimiento). Prueba sin coste: `uv run python scripts/a2a_smoke.py`.

## Asíncrono (`async_flow.py`, `worker.py`, `intake.py`, `cases.py`)

- Coreografía por eventos: `support.emails` → `TriageHandler` → `case.triaged.<especialista>` → `SpecialistHandler` → `case.outcomes`; revisión con `CaseReviewService`. Topics con prefijo opcional (`CaseTopics`) para aislar tests y entornos. `correlation_id = case_id` en cada evento.
- Estado en `CaseStore` (`ports/cases.py`; `InMemoryCaseStore`, `PostgresCaseStore`, migración 0006 `support_cases`): alta única por email y transiciones compare-and-set. El triaje **reclama** el caso (`received`) antes de llamar al LLM; una primera entrega que lo encuentra reclamado se descarta y una reentrega (`delivery_count > 1`) lo retoma.
- Idempotencia: caso `triaged` repetido ⇒ solo se republica; especialista ⇒ solo actúa si el caso sigue `triaged` y el traspaso coincide con el guardado. Mal formado, ruta inválida o traspaso alterado ⇒ `PermanentMessageError` (DLQ). El resto, transitorio (reintento del broker).
- Workers por rol (`python -m orchestrator.worker [triage|refunds|creative|knowledge]`), especialistas en proceso o A2A (`ORCHESTRATOR__SPECIALISTS`). Límite: guardar y publicar no son atómicos (transactional outbox pendiente).

## MAF (`maf_workflow.py`)

- `MafCaseWorkflow`: mismo flujo con executors tipados, `add_switch_case_edge_group` (`Default` → `unroutable`) y `request_info` + `@response_handler`. Un `Workflow` nuevo por ejecución (`build()`); `review(run)` restaura desde `run.checkpoint_id`.
- Checkpoints en disco con `file_checkpoint_storage` (pickle con `CHECKPOINT_TYPES` permitidos; añadir ahí cualquier tipo nuevo que viaje entre executors). MAF solo avisa si no guarda un checkpoint: una pausa sin checkpoint lanza `CheckpointNotSavedError`.
- Debe dar el mismo resultado que `RouterGraph` con el mismo guion (`test_same_result_as_langgraph`).
