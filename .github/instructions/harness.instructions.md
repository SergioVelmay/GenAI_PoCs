---
applyTo: "packages/harness/**,packages/headache_core/src/headache_core/ports/guardrails.py,packages/headache_core/src/headache_core/ports/sql.py,packages/headache_core/src/headache_core/adapters/azure_content_safety.py,packages/headache_core/src/headache_core/adapters/bedrock_guardrails.py,services/booking_agent/src/booking_agent/guardrails.py,scripts/analytics_sql_demo.py"
description: "Agentic harness y guardrails (fase 8)."
---

# Harness (fase 8, paquete `harness`)

Doc: `docs/fase-8-harness.md`. El modelo **propone**; el código determinista **decide**.

## Piezas

- Validación: `Violation(code, message, path, correctable)`, `Validator[T, C]` (sync o async), `run_validators`, `parse_output` (JSON tolerante a vallas → Pydantic).
- Autocorrección: `generate_validated` (feedback con códigos, `max_attempts`; una violación `correctable=False` escala al momento). `staged` para pagar el juez LLM solo si lo determinista pasa.
- Tools: `GuardedExecutor` (`ToolGuard.check` antes de ejecutar; denegar = error `policy_denied` que vuelve al modelo; `MaxCallsPerTurn`, `ArgumentRules`; `ToolOutputGuard` para salidas de terceros). Se enchufa con `JournaledExecutor(inner=...)`.
- Turno: `GuardedRuntime` (guardas y filtros de entrada/salida sobre cualquier `AgentRuntime`; bloquear = `stop_reason="guardrail"`; **no** reintenta turnos). `TurnTrace` (`ContextVar`) recoge tools y hallazgos del turno.
- Reglas de Headache en `harness/rules/`: `refunds.py`, `analytics.py` (SQL), `partner_email.py` (plantilla por construcción + validadores + juez de tono opcional), `patterns.py`.
- SQL seguro: `harness/sql.py` `check_sql(sql, SqlPolicy)` con `sqlglot` (solo `SELECT`/`UNION`, allow-list de vistas, columnas y funciones, `LIMIT` forzado, esquema `analytics`); firmas de ataque no corregibles. Ejecución con `PostgresReadOnlySql`.
- Seguridad: `ContentShieldPort` (`HeuristicShield`, Azure Content Safety, Bedrock Guardrails), `prompt_guard`, `output_guard`, `DocumentShield` (retiene solo el fragmento con instrucciones, `QUARANTINE_MARK`). PII: `RegexPiiDetector` (checksum NIF/NIE/Luhn) o Presidio (extra `pii`), `pii_filter`.
- Human-in-the-loop: `harness/approvals.py` (`ApprovalGate` como última guarda → solicitud pendiente y error `approval_required`; `ApprovalService.decide` ejecuta los argumentos congelados; cuatro ojos, caducidad, compare-and-set; `PostgresApprovalStore`, migración 0005). La aprobación autoriza, no se salta la política del dominio.
- Composición: `booking_agent/guardrails.py` (`build_shield`, `build_pii_detector`, `UNTRUSTED_TOOLS`). Config `GUARDRAILS__SHIELD=heuristic|azure|bedrock`, `GUARDRAILS__PII=regex|presidio`, `GUARDRAILS__FAIL_OPEN` (por defecto falla cerrado), `CONTENT_SAFETY__*`, `BEDROCK_GUARDRAIL__*`. Terraform desactivado por defecto: `azure/content_safety.tf` (F0) y `aws/guardrails.tf`.
- Email a partners: el LLM rellena solo `PartnerEmailDraft`; `render_partner_email` pone saludo, prefijo `[Headache Partners]`, referencia, firma y pie RGPD (es/en). Solo redacta: el envío pasa por aprobación con cuatro ojos.

## Reglas

- El feedback al modelo nunca repite el dato sensible: describe el problema por código o categoría.
- Las violaciones de ataque (`correctable=False`) escalan al primer intento: reintentar enseña a esquivar.
- Toda guarda nueva lleva tests de ataque **y** controles benignos; `uv run python -m rag_evals.attacks --check` debe seguir contenido 100 % en los 7 sabores.
- Acciones irreversibles (reembolsos, envíos a partners, compras) solo con aprobación humana.
