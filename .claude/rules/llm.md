---
paths:
  - "packages/headache_core/src/headache_core/application/llm/**"
  - "packages/headache_core/src/headache_core/application/tools/**"
  - "packages/headache_core/src/headache_core/ports/llm.py"
  - "packages/headache_core/src/headache_core/adapters/azure_openai.py"
  - "packages/headache_core/src/headache_core/adapters/bedrock.py"
  - "packages/headache_core/src/headache_core/adapters/gemini.py"
  - "packages/headache_core/src/headache_core/adapters/gemini_cache.py"
  - "packages/headache_core/src/headache_core/adapters/litellm_gateway.py"
  - "packages/headache_core/src/headache_core/adapters/llm_factory.py"
  - "packages/headache_core/src/headache_core/adapters/langfuse_tracer.py"
  - "packages/headache_core/src/headache_core/adapters/ticketing_*.py"
  - "scripts/llm_*.py"
  - "scripts/tool_loop_demo.py"
---

<!-- Generado por scripts/sync_agent_rules.py: no editar a mano. -->

Área **llm**: Capa de modelos, LLM gateway y tool calling manual (fases 2 y 6.1).

Antes de editar, lee y aplica `.github/instructions/llm.instructions.md`.
Es la fuente única de las reglas de esta área, compartida con GitHub Copilot.
