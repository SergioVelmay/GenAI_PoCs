---
applyTo: ".github/workflows/**,.github/dependabot.yml,.githooks/**"
description: "GitHub Actions y hooks: acciones por SHA, runner fijado y jobs sin coste."
---

# CI (`.github/workflows/ci.yml`)

- Jobs: `quality` (ruff, formato, mypy, pytest, smoke tests), `evals` (golden, retrieval, generación, gate en modo `enforce` desde el checkpoint 8→9, ataques `--check`), `mcp-image` (build + health), `agents-image` (especialistas A2A: build, Agent Card, 401 sin token y 200 con token de desarrollo generado dentro de la imagen), `terraform` (matriz aws/azure: fmt/init/validate), `integration` (PostgreSQL + RabbitMQ + ElasticMQ, migraciones arriba-abajo-arriba, `alembic check`, `pytest -m integration`, carga y benchmark).
- Acciones fijadas por **SHA de commit** con la versión en un comentario (`uses: owner/action@<sha> # vX.Y.Z`). `setup-uv` ya no publica tags mayores. Dependabot propone subidas en una PR mensual agrupada.
- Runner fijado (`ubuntu-26.04`), nunca `ubuntu-latest`.
- `uv sync --all-packages --locked`; `UV_LINK_MODE=copy` (NLTK rechaza hardlinks). La CI no instala extras opcionales (`evals`, `pii`).
- Sin secretos ni coste en la CI: nada de LLM ni nube. Cuando lleguen (fases 13/14), OIDC con `permissions: id-token: write` por job.
- Validar workflows con `uvx --from actionlint-py actionlint -ignore 'ubuntu-26.04'` (su lista de runners va por detrás de GitHub: `ubuntu-26.04` es válido).
- Tras cada push, vigilar la ejecución hasta el final: `gh run watch <id> --exit-status`, y dar el resultado por job.
- `.githooks/pre-commit` (activado con `git config core.hooksPath .githooks`) ejecuta `scripts/check_forbidden_terms.py` sobre lo preparado para el commit.
