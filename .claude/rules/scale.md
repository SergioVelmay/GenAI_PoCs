---
paths:
  - "services/edge_api/**"
  - "packages/genaipocs_core/src/genaipocs_core/application/admission.py"
  - "packages/genaipocs_core/src/genaipocs_core/domain/admission.py"
  - "packages/genaipocs_core/src/genaipocs_core/ports/rate_limit.py"
  - "packages/genaipocs_core/src/genaipocs_core/adapters/redis_*.py"
  - "packages/genaipocs_core/src/genaipocs_core/application/llm/resilience.py"
  - "packages/genaipocs_core/src/genaipocs_core/domain/semantic_cache.py"
  - "packages/genaipocs_core/src/genaipocs_core/application/semantic_cache.py"
  - "packages/genaipocs_core/src/genaipocs_core/ports/semantic_cache.py"
  - "evals/src/rag_evals/semantic_cache.py"
  - "packages/genaipocs_core/src/genaipocs_core/application/llm/streaming.py"
  - "packages/genaipocs_core/src/genaipocs_core/application/answer_stream.py"
  - "packages/genaipocs_core/src/genaipocs_core/domain/autoscaling.py"
  - "packages/genaipocs_core/src/genaipocs_core/application/autoscaler.py"
  - "packages/genaipocs_core/src/genaipocs_core/ports/scaling.py"
  - "packages/genaipocs_core/src/genaipocs_core/adapters/queue_metrics.py"
  - "packages/genaipocs_core/src/genaipocs_core/adapters/compose_scaler.py"
  - "scripts/queue_autoscaler.py"
  - "scripts/capacity_plan.py"
  - "services/orchestrator/src/orchestrator/load_worker.py"
  - "packages/genaipocs_core/src/genaipocs_core/adapters/simulated_llm.py"
  - "loadtests/**"
  - "scripts/load_report.py"
  - "evals/reports/load/**"
---

<!-- Generado por scripts/sync_agent_rules.py: no editar a mano. -->

Área **scale**: Escala: edge, admisión, rate limiting distribuido y resiliencia del cliente LLM (fase 10).

Antes de editar, lee y aplica `.github/instructions/scale.instructions.md`.
Es la fuente única de las reglas de esta área, compartida con GitHub Copilot.
