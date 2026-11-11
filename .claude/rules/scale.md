---
paths:
  - "services/edge_api/**"
  - "packages/headache_core/src/headache_core/application/admission.py"
  - "packages/headache_core/src/headache_core/domain/admission.py"
  - "packages/headache_core/src/headache_core/ports/rate_limit.py"
  - "packages/headache_core/src/headache_core/adapters/redis_*.py"
  - "packages/headache_core/src/headache_core/application/llm/resilience.py"
  - "packages/headache_core/src/headache_core/domain/semantic_cache.py"
  - "packages/headache_core/src/headache_core/application/semantic_cache.py"
  - "packages/headache_core/src/headache_core/ports/semantic_cache.py"
  - "evals/src/rag_evals/semantic_cache.py"
  - "packages/headache_core/src/headache_core/application/llm/streaming.py"
  - "packages/headache_core/src/headache_core/application/answer_stream.py"
  - "packages/headache_core/src/headache_core/domain/autoscaling.py"
  - "packages/headache_core/src/headache_core/application/autoscaler.py"
  - "packages/headache_core/src/headache_core/ports/scaling.py"
  - "packages/headache_core/src/headache_core/adapters/queue_metrics.py"
  - "packages/headache_core/src/headache_core/adapters/compose_scaler.py"
  - "scripts/queue_autoscaler.py"
  - "scripts/capacity_plan.py"
  - "services/orchestrator/src/orchestrator/load_worker.py"
  - "packages/headache_core/src/headache_core/adapters/simulated_llm.py"
  - "loadtests/**"
  - "scripts/load_report.py"
  - "evals/reports/load/**"
---

<!-- Generado por scripts/sync_agent_rules.py: no editar a mano. -->

Área **scale**: Escala: edge, admisión, rate limiting distribuido y resiliencia del cliente LLM (fase 10).

Antes de editar, lee y aplica `.github/instructions/scale.instructions.md`.
Es la fuente única de las reglas de esta área, compartida con GitHub Copilot.
