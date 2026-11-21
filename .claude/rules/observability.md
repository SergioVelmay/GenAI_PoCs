---
paths:
  - "packages/genaipocs_core/src/genaipocs_core/domain/drift.py"
  - "packages/genaipocs_core/src/genaipocs_core/application/agent_online_eval.py"
  - "packages/genaipocs_core/src/genaipocs_core/adapters/online_eval_factory.py"
  - "infra/otel/**"
  - "infra/terraform/aws/observability.tf"
  - "infra/terraform/azure/observability.tf"
  - "infra/terraform/gcp/observability.tf"
  - "scripts/check_collector_gcp.py"
  - "packages/genaipocs_core/src/genaipocs_core/application/online_eval.py"
  - "packages/genaipocs_core/src/genaipocs_core/adapters/langfuse_scores.py"
  - "docs/runbook-incidentes.md"
  - "infra/local/telemetry/**"
  - "scripts/telemetry_demo.py"
  - "scripts/build_observability.py"
  - "packages/genaipocs_core/src/genaipocs_core/adapters/prometheus.py"
  - "packages/genaipocs_core/src/genaipocs_core/domain/slo.py"
  - "packages/genaipocs_core/src/genaipocs_core/application/telemetry/messaging.py"
  - "services/orchestrator/src/orchestrator/remote/tracing.py"
  - "packages/genaipocs_core/src/genaipocs_core/ports/telemetry.py"
  - "packages/genaipocs_core/src/genaipocs_core/application/telemetry/**"
  - "packages/genaipocs_core/src/genaipocs_core/application/agents/tracing.py"
  - "packages/genaipocs_core/src/genaipocs_core/adapters/otel.py"
  - "packages/genaipocs_core/src/genaipocs_core/adapters/langfuse_tracer.py"
---

<!-- Generado por scripts/sync_agent_rules.py: no editar a mano. -->

Área **observability**: Observabilidad (fase 11): OpenTelemetry con la convención GenAI, logs correlacionados y sin PII.

Antes de editar, lee y aplica `.github/instructions/observability.instructions.md`.
Es la fuente única de las reglas de esta área, compartida con GitHub Copilot.
