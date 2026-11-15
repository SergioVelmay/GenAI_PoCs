---
paths:
  - "packages/genaipocs_core/src/genaipocs_core/application/llm/**"
  - "packages/genaipocs_core/src/genaipocs_core/application/tools/**"
  - "packages/genaipocs_core/src/genaipocs_core/ports/llm.py"
  - "packages/genaipocs_core/src/genaipocs_core/adapters/azure_openai.py"
  - "packages/genaipocs_core/src/genaipocs_core/adapters/bedrock.py"
  - "packages/genaipocs_core/src/genaipocs_core/adapters/gemini.py"
  - "packages/genaipocs_core/src/genaipocs_core/adapters/gemini_cache.py"
  - "packages/genaipocs_core/src/genaipocs_core/adapters/litellm_gateway.py"
  - "packages/genaipocs_core/src/genaipocs_core/adapters/llm_factory.py"
  - "packages/genaipocs_core/src/genaipocs_core/adapters/langfuse_tracer.py"
  - "packages/genaipocs_core/src/genaipocs_core/adapters/ticketing_*.py"
  - "scripts/llm_*.py"
  - "scripts/tool_loop_demo.py"
---

<!-- Generado por scripts/sync_agent_rules.py: no editar a mano. -->

Área **llm**: Capa de modelos, LLM gateway y tool calling manual (fases 2 y 6.1).

Antes de editar, lee y aplica `.github/instructions/llm.instructions.md`.
Es la fuente única de las reglas de esta área, compartida con GitHub Copilot.
