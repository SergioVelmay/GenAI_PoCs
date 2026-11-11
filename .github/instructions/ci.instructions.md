---
applyTo: ".github/workflows/**,.github/dependabot.yml,.githooks/**"
description: "GitHub Actions y hooks: acciones por SHA, runner fijado y jobs sin coste."
---

# CI (`.github/workflows/ci.yml`)

- Jobs: `quality` (ruff, formato, mypy, pytest, candado de prompts `rag_evals.llmops.prompts check`, smoke tests, dashboard y reglas al día con `scripts/build_observability.py --check`, `promtool check rules` + `test rules` con la imagen `prom/prometheus` fijada, `validate` de los collectors local, Azure y AWS con la imagen `otel/opentelemetry-collector-contrib` fijada, la misma que en `docker-compose.yml`), `evals` (golden, retrieval, generación, gate en modo `enforce` desde el checkpoint 8→9, agentes `--check`, ataques `--check`, red teaming `rag_evals.redteam.mutations --check` y `rag_evals.redteam.promptfoo eval --check` (Node.js del runner) e informe `rag_evals.redteam.report`, multi-agente `--check`, caché semántica `--check`), `mcp-image` (build + health), `agents-image` (especialistas A2A: build, Agent Card, 401 sin token y 200 con token de desarrollo generado dentro de la imagen), `terraform` (matriz aws/azure/gcp: fmt/init/validate), `integration` (PostgreSQL + RabbitMQ con API de gestión + ElasticMQ + Redis, migraciones arriba-abajo-arriba, `alembic check`, `pytest -m integration`, carga y benchmark), `release-gate` (fase 12.1: `if: always()` + `needs` de todos los jobs; falla si alguno no es `success`; es el único check del que dependen los despliegues).
- Acciones fijadas por **SHA de commit** con la versión en un comentario (`uses: owner/action@<sha> # vX.Y.Z`). `setup-uv` ya no publica tags mayores. Dependabot propone subidas en una PR mensual agrupada.
- Runner fijado (`ubuntu-26.04`), nunca `ubuntu-latest`.
- `uv sync --all-packages --locked`; `UV_LINK_MODE=copy` (NLTK rechaza hardlinks). La CI no instala extras opcionales (`evals`, `pii`, `redteam`).
- Sin secretos ni coste en la CI: nada de LLM ni nube. Cuando lleguen (fases 13/14), OIDC con `permissions: id-token: write` por job.
- Validar workflows con `uvx --from actionlint-py actionlint -ignore 'ubuntu-26.04'` (su lista de runners va por detrás de GitHub: `ubuntu-26.04` es válido).
- Disparadores: `push` a `main` (solo llega por merge de PR) y `pull_request`. Las ramas de fase no tienen CI hasta que existe su PR: se abre en draft con el primer push.
- `main` protegida por el ruleset `proteger-main` (desde la 0.G, sin bypass): PR obligatoria con 0 aprobaciones (un único colaborador no puede aprobar su PR), check `release-gate` emitido por GitHub Actions (`integration_id` 15368), solo *merge commit* (título y cuerpo = los de la PR, que termina con el trailer `Co-authored-by`), sin force-push ni borrado; las ramas se borran tras el merge. **No renombrar el job `release-gate`**: el ruleset lo exige por nombre y una PR quedaría bloqueada para siempre.
- Tras cada push, vigilar la ejecución hasta el final: `gh run watch <id> --exit-status`, y dar el resultado por job.
- `.githooks/pre-commit` (activado con `git config core.hooksPath .githooks`) ejecuta `scripts/check_forbidden_terms.py` sobre lo preparado para el commit.
