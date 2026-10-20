---
applyTo: "services/orchestrator/**,packages/headache_core/src/headache_core/domain/orchestration.py,packages/headache_core/src/headache_core/application/orchestration/**,packages/harness/src/harness/rules/case_reply.py"
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
