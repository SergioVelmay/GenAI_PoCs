---
applyTo: "services/edge_api/**,packages/headache_core/src/headache_core/application/admission.py,packages/headache_core/src/headache_core/domain/admission.py,packages/headache_core/src/headache_core/ports/rate_limit.py,packages/headache_core/src/headache_core/adapters/redis_*.py,packages/headache_core/src/headache_core/application/llm/resilience.py"
description: "Escala: edge, admisión, rate limiting distribuido y resiliencia del cliente LLM (fase 10)."
---

# Escala y patrones asíncronos bajo carga (fase 10)

## Principios

- **Nada espera sin límite.** Toda cola o espera tiene máximo (`max_wait`, `capacity`, `time_budget`); si no cabe, se rechaza con `Retry-After` (429 por usuario, 503 por saturación, 504 por presupuesto agotado). Una cola infinita solo convierte la sobrecarga en timeouts.
- **Decidir pronto y barato**, de lo más barato a lo más caro: validación (422) → identidad (401) → *load shedding* por prioridad en memoria (503) → rate limit por usuario en Redis (429) → modelo.
- La identidad y el plan salen de un **token firmado** (`sub` y claim `plan`), nunca de una cabecera del cliente. Token inválido = 401, no anónimo. Plan desconocido = `public`.
- Prioridad por plan (dominio, `CAPACITY_SHARE`): internos 100 % de la capacidad, públicos 80 %, anónimos 60 %.
- Estado de límites compartido entre réplicas en **Redis** (`RedisRateLimiter`, GCRA en Lua atómico, reloj de Redis, hash tag `{headache-rl}`). El de memoria (`InMemoryRateLimiter`) tiene la misma semántica: tests y plan B.
- Si Redis cae: **fail-open** con límite local por réplica (edge) o con el `RateLimitedLLM` local (modelo). El adaptador no reintenta (`Retry(NoBackoff(), 0)`) y se enfría `cooldown_seconds`.

## Edge (`services/edge_api`)

```powershell
docker compose --profile scale up -d                       # Redis 8 en 127.0.0.1:6380
$env:RATE_LIMIT__BACKEND = "redis"
uv run uvicorn edge_api.app:create_app --factory --port 8200
uv run python -m edge_api.tokens --user ops-ana --plan internal   # token de desarrollo
```

- `create_app(edge, settings=..., assistant=..., controller=..., resolver=...)`: todo inyectable; los tests usan `FakeAssistant`, `InMemoryRateLimiter` y claves RSA generadas.
- `/health` (liveness) y `/ready` (readiness: 503 con la réplica llena, más contadores).
- El asistente es un puerto (`Assistant`); en local, `RagAssistant` sobre el `RetrievalService` en proceso. El ACL lo decide el plan (`access_scope`).
- Variables: `EDGE__*`, `RATE_LIMIT__*`, `SCALE_REDIS__*` (`.env.example`).

## Tests

- Unitarios deterministas con reloj falso (`FakeTime`) y sin Redis.
- Integración (`-m integration`): `RedisRateLimiter` contra Redis real (concurrencia entre réplicas, paridad con GCRA, TTL, fallo rápido). En la CI, Redis sin contraseña (los *services* no admiten `command`).
