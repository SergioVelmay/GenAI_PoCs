---
name: cerrar-subfase
description: Verifica, hace commit, push y vigila la CI de una subfase revisada. Úsala cuando Sergio diga "revisado, haz commit y push", "prepara el commit" o "haz push y comprueba la CI".
---

# Cerrar una subfase

Sergio ya revisó el cambio. El commit y el push están autorizados por su mensaje; cualquier otra
acción con coste o destructiva sigue necesitando confirmación.

## 1. Verificar (los mismos comandos que la CI)

```powershell
uv run ruff check . ; uv run ruff format --check . ; uv run mypy ; uv run pytest
```

Además, según lo que se haya tocado:

- Terraform: `terraform fmt -check -recursive`, `init -backend=false`, `validate` en `infra/terraform/aws` y `azure`.
- Evals o agentes: `uv run python -m rag_evals.gate --mode report` y `uv run python -m rag_evals.attacks --check`.
- SQL o migraciones: `uv run pytest -m integration` (requiere `docker compose up -d`).
- Contratos MCP: `uv run python -m headache_mcp.contracts`.
- Workflows: `uvx --from actionlint-py actionlint`.

Si algo falla, arreglar la causa raíz (no silenciar) y volver a verificar.

## 2. Documentación y estado

- Si cambian comandos, variables o convenciones: actualizar la instrucción de su área (`.github/instructions/`) y, si aplica siempre, `AGENTS.md`.
- Lo aplazado con coste: `docs/pendientes-cloud.md`.

## 3. Commit

1. `git status --short` y `git --no-pager diff --stat`: solo entra lo de esta subfase (nunca `.env`, `.secrets/`, `data/`).
2. `git add <rutas concretas>`.
3. Mensaje Conventional Commits en español: `feat: fase N.M - <qué>` (`fix:`, `docs:`, `ci:`, `chore:` según el caso), con cuerpo breve si aporta, y el trailer `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.
4. El hook `pre-commit` comprueba los términos prohibidos; si falla, quitar el término (nunca `--no-verify`).
5. Comprobar con `git --no-pager log --oneline -1` que el commit existe (el terminal a veces mete un carácter espurio).

## 4. Push y CI

1. `git push` y comprobar que no hay error.
2. `gh run list --workflow ci.yml --limit 1` y `gh run watch <id> --exit-status`.
3. Informar por job (tabla) con el enlace a la ejecución. Si falla, diagnosticar con `gh run view <id> --log-failed`, arreglar y repetir.

## 5. Siguiente paso

Resumir en 3–5 líneas lo hecho y proponer la siguiente subfase, sin empezarla salvo que Sergio ya lo haya pedido.
