---
applyTo: "packages/headache_core/src/headache_core/application/llm/**,packages/headache_core/src/headache_core/application/tools/**,packages/headache_core/src/headache_core/ports/llm.py,packages/headache_core/src/headache_core/adapters/azure_openai.py,packages/headache_core/src/headache_core/adapters/bedrock.py,packages/headache_core/src/headache_core/adapters/litellm_gateway.py,packages/headache_core/src/headache_core/adapters/llm_factory.py,packages/headache_core/src/headache_core/adapters/langfuse_tracer.py,packages/headache_core/src/headache_core/adapters/ticketing_*.py,scripts/llm_*.py,scripts/tool_loop_demo.py"
description: "Capa de modelos, LLM gateway y tool calling manual (fases 2 y 6.1)."
---

# Capa de modelos y tool calling (fases 2 y 6.1)

Doc: `docs/fase-2-modelos.md`, `docs/fase-6-tools-mcp.md`.

## Gateway

- Todo pasa por `LLMPort`. Nadie importa `openai`/`boto3` fuera de los adaptadores.
- `adapters/llm_factory.build_gateway`: `RoutingLLM` → `FallbackLLM` → `CircuitBreakerLLM` → `ConcurrencyLimitedLLM` (bulkhead) → `ObservedLLM` (trazas/coste) → `RateLimitedLLM` → adaptador (`_resilient` monta la pila de un despliegue).
- Resiliencia (fase 10.1, `application/llm/resilience.py`): ninguna capa espera sin límite; si no hay cuota, hueco o circuito, `LLMOverloadedError` con `retry_after` y el fallback salta de proveedor. `time_budget(s)` acota reintentos, esperas y la llamada (`LLMDeadlineExceededError`: ni se reintenta ni se salta). Los 429 y 5xx llevan `retry_after` (cabeceras `retry-after-ms`/`retry-after` en Azure). El breaker no cuenta errores no transitorios ni rechazos locales. `LLM_GATEWAY__ENGINE=native|litellm`, `LLM_GATEWAY__ROUTER=heuristic|llm`. Antes del clasificador, `requires_strong`: un turno que ofrece una tool con `read_only=False` va siempre al modelo potente (`ToolSpec.read_only`, que no viaja al proveedor).
- Tools para modelos: IDs con prefijo exacto por tipo (`^evt_`, `^ord_`), sin IDs de ejemplo copiables en las descripciones; cambiar una descripción cambia el contrato MCP (`python -m headache_mcp.contracts --update`, deliberado).
- Reintentos solo en el gateway (SDKs con `max_retries=0`). Un 429 salta de proveedor; los errores no transitorios se propagan.
- Todo cliente de un despliegue (también scripts y benchmarks) pasa por `RateLimitedLLM`. `LLM_GATEWAY__TOKENS_PER_MINUTE` va por debajo de la capacidad del despliegue (Terraform `foundry_models`, 200 K TPM; hoy 150 K), porque el *token bucket* admite ráfaga + recarga.
- Streaming (fase 10.4): `StreamingLLMPort.stream()` en adaptadores y en todas las capas del gateway (detalle en `scale.instructions.md`). Con `LLM_GATEWAY__ENGINE=litellm` no hay streaming real (`stream_from` lo degrada a un trozo).
- Errores tipados `LLMError` → `LLMTransientError` (`retry_after`) → `LLMRateLimitError` / `LLMOverloadedError` / `LLMDeadlineExceededError`; cada adaptador traduce los suyos.
- Coste con `application/llm/pricing.py` (`PriceTable`, prefijo más largo). Un modelo nuevo necesita su precio (entrada, salida, cacheada, escritura).
- Prompt caching: `ChatMessage(cache_checkpoint=True)` al final del prefijo estable (Bedrock `cachePoint`; Azure automático ≥ 1024 tokens). Prefijo estable primero, siempre.
- Structured outputs: `complete_structured` (strict en Azure, tool use forzado en Bedrock) con reintento con feedback; `StructuredResult.responses` para el coste real.

## Tool calling (6.1)

- `LLMPort.complete(..., tools=ToolConfig)`; mensajes `Role.TOOL` con `tool_call_id`/`is_error` y `ChatMessage.tool_calls`. Bedrock agrupa resultados consecutivos en un turno `user`.
- Bucle manual `application/tools/loop.py` (`ToolLoop`): lecturas en paralelo acotado, escrituras en serie, timeouts, errores tipados que vuelven al modelo, `max_iterations` → respuesta de escalado.
- Identidad en `ToolContext`, **nunca** como argumento del modelo. Salidas proyectadas (solo lo necesario) y saneadas (`domain/tool_output.py`).
- `TicketingPort`: `ticketing_http` (httpx, `TICKETING_API__BASE_URL`) y `ticketing_local` (UoW).

## Scripts con coste (pedir confirmación; ver `docs/pendientes-cloud.md`)

- `scripts/llm_cache_benchmark.py`, `scripts/llm_gateway_demo.py`, `scripts/tool_loop_demo.py`.
