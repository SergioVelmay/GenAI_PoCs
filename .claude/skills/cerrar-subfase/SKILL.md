---
name: cerrar-subfase
description: Verifica, hace commit y push a la rama de la fase, abre o actualiza la PR en draft y vigila la CI de una subfase terminada. Úsala al terminar cada subfase, o cuando Sergio diga "revisado", "prepara el commit" o "haz push y comprueba la CI".
---

# Cerrar una subfase

Desde el 20-11-2026 el commit y el push **a la rama de la fase** no necesitan permiso: al terminar y
verificar una subfase, se hacen y después se informa a Sergio de lo hecho. Cualquier otra acción
con coste o destructiva sigue necesitando confirmación. `main` está protegida: nunca se hace push a
`main` ni merge de la PR (lo hace Sergio a mano, tras revisarla).

## 0. Rama

`git branch --show-current` debe ser la rama de la fase (`fase/<N>` o `fase/<N>-g`), nunca `main`.
Si se está en `main` con cambios sin commit, `git switch -c fase/<...>` (los cambios viajan con la
rama) y avisar a Sergio.

## 1. Verificar (los mismos comandos que la CI)

```powershell
uv run ruff check . ; uv run ruff format --check . ; uv run mypy ; uv run pytest
```

Además, según lo que se haya tocado:

- Terraform: `terraform fmt -check -recursive`, `init -backend=false`, `validate` en cada módulo de `infra/terraform/` (`aws`, `azure` y `gcp`).
- Evals o agentes: `uv run python -m rag_evals.gate --mode report` y `uv run python -m rag_evals.attacks --check`.
- SQL o migraciones: `uv run pytest -m integration` (requiere `docker compose up -d`).
- Contratos MCP: `uv run python -m genaipocs_mcp.contracts`.
- Workflows: `uvx --from actionlint-py actionlint -ignore 'ubuntu-26.04'`.
- `.github/instructions/`: `uv run python scripts/sync_agent_rules.py` (regenera los punteros de `.claude/rules/`).
- Ficheros con texto en español: escanear las líneas añadidas en busca de tildes rotas (`?` en lugar de tilde, mojibake `Ã`, `Â`), ver `docs/lecciones-aprendidas.md`.
- Tras ediciones múltiples en un fichero, revisar su diff entero: la tool de edición también puede dejar líneas mezcladas.

Si algo falla, arreglar la causa raíz (no silenciar) y volver a verificar.

## 2. Documentación y estado

- Si cambian comandos, variables o convenciones: actualizar la instrucción de su área (`.github/instructions/`) y, si aplica siempre, `AGENTS.md`.
- Si cambian variables: alinear el `.env` local de Sergio con `.env.example` (lo hace el agente, sin mostrar valores ni poner valores con coste) e informarle de qué se ha añadido o modificado, en qué bloque y para qué.
- Lo aplazado con coste: `docs/pendientes-cloud.md`. Si cambió el alcance de la fase: `docs/plan-fases.md`.
- Informes regenerados (`evals/reports/*`): si solo cambian latencias, restaurarlos siempre con `uv run python scripts/restore_latency_reports.py` (sin preguntar: decidido el 20-11-2026; se niega si cambia algo más que latencias) e informar; si cambian campos o resultados, entran en el commit.

## 3. Commit

1. `git status --short` y `git --no-pager diff --stat`: solo entra lo de esta subfase (nunca `.env`, `.secrets/`, `data/`). Un arreglo ajeno a la subfase va en un commit `fix:` aparte.
2. `git add <rutas concretas>`.
3. Mensaje Conventional Commits en español: `feat: fase N.M - <qué>` (`fix:`, `docs:`, `ci:`, `chore:` según el caso), con cuerpo breve si aporta, y el trailer del agente que hizo el trabajo (Copilot: `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`; Claude Code: `Co-authored-by: Claude <noreply@anthropic.com>`). El mensaje, sin ficheros fuera del repo: en Claude Code (Git Bash), heredoc directo `git commit -F - <<'EOF'` ... `EOF` (comillas simples en `'EOF'`: sin expansión de `$` ni de barras); en Copilot (PowerShell 5.1, sin heredocs), fichero `.tmp/commit-msg.txt` creado con la tool de edición y `git commit -F .tmp/commit-msg.txt` (el here-string por tubería rompe tildes y comillas).
4. El hook `pre-commit` comprueba los términos prohibidos; si falla, quitar el término (nunca `--no-verify`).
5. Comprobar con `git --no-pager log --oneline -1` que el commit existe (el terminal a veces mete un carácter espurio).

## 4. Push, PR y CI

1. Primer push de la rama: `git push -u origin <rama>`; después, `git push`. Comprobar que no hay error.
2. PR: si la rama aún no tiene (`gh pr view --json number,isDraft` falla), abrirla en **draft**
   contra `main`: `gh pr create --draft --base main --title "<fase>: <tema>" --body-file .tmp/pr-<fase>.md`
   (p. ej. `.tmp/pr-5-g.md`, creado con la tool de edición; `.tmp/` está ignorado por Git).
   El cuerpo es el **estado de la fase para la próxima sesión** (la lee `retomar-fase`): lista de
   subfases con casilla (`- [x] 2.G1 ...` con su commit, `- [ ] 2.G2 ...` con sus entregables),
   decisiones tomadas, qué se verificó y lo que va a `pendientes-cloud.md`; termina con el trailer
   del agente. Si ya existe, marcar la subfase cerrada en ese mismo fichero y `gh pr edit --body-file .tmp/pr-<fase>.md` (si el fichero no está, por ser otra sesión, partir de `gh pr view --json body`).
3. La CI corre por `pull_request` (no por push a la rama): `gh run list --branch <rama> --limit 1` y `gh run watch <id> --exit-status`. Si la API devuelve jobs en `in_progress` con el run ya terminado, fiarse de la conclusión del run (exige `release-gate`).
4. Informar por job (tabla) con el enlace a la ejecución y a la PR. Si falla, diagnosticar con `gh run view <id> --log-failed`, arreglar en un commit nuevo, informar y repetir.

## 5. Siguiente paso

Resumir en 3–5 líneas lo hecho y proponer la siguiente subfase, sin empezarla salvo que Sergio ya lo haya pedido ("dale"). La fase entera se hace en la misma sesión (un commit por subfase en la PR de la fase); proponer una sesión nueva con `retomar-fase` solo si el contexto pasa del ~70 %.
