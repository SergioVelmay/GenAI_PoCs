# Cómo contribuir

Gracias por el interés. Este repositorio son **pruebas de concepto didácticas** de sistemas
agénticos GenAI en Azure, AWS y GCP, construidas fase a fase ([plan](docs/plan-fases.md)). Lo
mantiene una sola persona, así que la forma más útil de contribuir es:

- **Issues**: errores, partes de un doc que no se entienden, equivalencias entre nubes que han
  cambiado o una idea para una fase. Hay plantillas para cada caso.
- **Pull requests pequeñas** y enfocadas en una sola cosa. Para algo grande, abre antes un issue
  y lo hablamos.

Al participar aceptas el [código de conducta](CODE_OF_CONDUCT.md). Las vulnerabilidades no van
en un issue: ver [SECURITY.md](SECURITY.md).

## Puesta en marcha

Requisitos: Python 3.12 o superior con [uv](https://docs.astral.sh/uv/) y Docker para la infraestructura
local. Ningún paso necesita una cuenta de nube.

```bash
uv sync --all-packages --locked
git config core.hooksPath .githooks        # hook de términos prohibidos
docker compose up -d                       # PostgreSQL + pgvector y RabbitMQ (solo para integración)
```

## Antes de abrir la PR

Los mismos comandos que la CI:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
uv run python -m rag_evals.gate --mode report      # si tocas retrieval o evals
uv run python -m rag_evals.attacks --check         # si tocas agentes o el harness
uv run python -m rag_evals.llmops.prompts check    # si tocas un prompt
uv run python scripts/validate_mermaid.py          # si tocas un diagrama
```

## Reglas del proyecto

Las reglas completas están en [AGENTS.md](AGENTS.md) (sirven igual para personas y para agentes de
IA) y, por área, en `.github/instructions/`. Las imprescindibles:

- **El cliente se llama siempre Genaipocs.** Nada de nombres de empresas reales ni marcas
  comerciales de terceros, tampoco en ejemplos o datos sintéticos.
- **Sin secretos**: ni claves ni `.env` en el repo.
- **Tests deterministas, sin red ni coste**, con los adaptadores en memoria. Lo que tenga coste
  exige `--budget-usd` o va en dry-run por defecto.
- **Clean Architecture**: el dominio es Python puro y decide; el LLM nunca toma decisiones de
  negocio. Los SDK solo se importan en `adapters/`.
- **Prompts versionados**: cambiar el texto de un prompt exige subir su versión (`prompts.lock.json`).
- **Commits** con Conventional Commits en español (`feat: ...`, `fix: ...`, `docs: ...`).
- **Docs** en español, con la estructura de los `docs/fase-N-*.md`.

`main` está protegida: toda contribución entra por PR, con el check `release-gate` en verde y
*merge commit* (los docs citan hashes de commits que no deben reescribirse).

## Licencia

Al contribuir aceptas que tu aportación se publique con la misma [licencia MIT](LICENSE) que el
resto del repositorio.
