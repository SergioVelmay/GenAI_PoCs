---
applyTo: "services/booking_agent/**,packages/headache_core/src/headache_core/application/agents/**,packages/headache_core/src/headache_core/domain/preferences.py,packages/headache_core/src/headache_core/adapters/agentcore_memory.py,scripts/provision_foundry_agent.py"
description: "Agente en 7 sabores, context engineering y carril A (fase 7)."
---

# Booking agent (fase 7)

Doc: `docs/fase-7-agentes.md`.

## Contratos comunes (no romperlos)

- `AgentSpec` único (`spec.py`): prompt, tools de ticketing + `search_knowledge` + `load_skill` + `remember_preference`, skills en `booking_agent/skills/*/SKILL.md`, `ContextPolicy`, `fallback_answer`.
- `AgentRuntime.respond(thread_id, message, ToolContext) -> AgentTurn` (en `headache_core/application/agents/runtime.py`).
- Todas las tools pasan por `JournaledExecutor` (lock de escrituras + diario), envuelto por `GuardedExecutor` del harness. El modelo, por `LLMPort` (los frameworks usan un puente al gateway, `booking_agent/bridges/`).
- Identidad fuera del modelo en cada sabor (contexto/`invocation_state`/`function_invocation_kwargs`); el dueño del hilo se comprueba antes de invocar.
- Un sabor nuevo se registra en `FLAVORS` y debe pasar la suite genérica y `rag_evals.agents` / `rag_evals.attacks` sin tocar el resto. `MANAGED_FLAVORS` quedan fuera de los tests genéricos.

## Sabores (`booking_agent/flavors/`)

- `native` (`ToolLoop`) · `langgraph` (`StateGraph` prepare→agent⇄tools→fallback, `InMemorySaver`, `context_schema=ToolContext`) · `llamaindex` (`FunctionAgent`, `streaming=False`, `max_iterations` = N + 1) · `maf` (`Agent` + `GatewayChatClient` por capas, `HistoryProvider` sobre `CheckpointStore`) · `strands` (`GatewayModel` Converse con `cachePoint`, `JournaledTool`, `NullConversationManager`; amplía la lista `messages` en sitio).
- Puentes (`booking_agent/bridges/`): `GatewayChatModel` (`BaseChatModel` de LangChain; `bind_tools` traduce formato OpenAI → `ToolSpec` con `strict`) y `GatewayFunctionLLM` (`FunctionCallingLLM` de LlamaIndex; `tool_calls`/`cache_checkpoint` en `additional_kwargs`).
- JSON inválido del modelo viaja crudo hasta el ejecutor (en MAF y Strands, envuelto en `__raw_arguments__`) para que el error tipado vuelva al modelo.

## Context engineering (`headache_core/application/agents/`)

- `build_prompt`/`build_prompt_prefix`: prefijo estable → contexto de sesión → historial → mensaje (maximiza el cache hit).
- Compactación por intercambios con histéresis + resumen; hilos con dueño y concurrencia optimista; preferencias de lista cerrada (`domain/preferences.py`).

## Carril A (`booking_agent/managed/`)

- `foundry`: function tools ejecutadas en el cliente, historial en una `conversation` del servicio; `LocalAgentService` emula el servicio en local. `scripts/provision_foundry_agent.py [--apply]` (dry-run por defecto).
- `agentcore`: contrato `POST /invocations` + `GET /ping` (`create_runtime_app(sabor)`), identidad = `runtimeUserId`, sesión = `runtime_session_id(cliente, hilo)` (sha256, ≥ 33 chars). Memoria en `adapters/agentcore_memory.py`. Terraform `aws/agentcore.tf` desactivado por defecto.

## CLI (con coste)

- `uv run python -m booking_agent --flavor native --customer cus_... ["pregunta"]` (usa el harness por defecto).
