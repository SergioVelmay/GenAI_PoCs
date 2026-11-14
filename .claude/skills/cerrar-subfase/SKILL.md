---
name: cerrar-subfase
description: Verifica, hace commit y push a la rama de la fase, abre o actualiza la PR en draft y vigila la CI de una subfase revisada. Úsala cuando Sergio diga "revisado", "revisado, haz commit y push", "prepara el commit" o "haz push y comprueba la CI".
---

# Cerrar una subfase

Sergio ya revisó el cambio. "Revisado" o "revisado, haz commit y push" autoriza el commit y el push
**a la rama de la fase**; "haz commit" autoriza solo el commit (no hacer push). Cualquier otra acción
con coste o destructiva sigue necesitando confirmación. `main` está protegida: nunca se hace push a
`main` ni merge de la PR (lo hace Sergio a mano).

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
- Contratos MCP: `uv run python -m headache_mcp.contracts`.
- Workflows: `uvx --from actionlint-py actionlint -ignore 'ubuntu-26.04'`.
- `.github/instructions/`: `uv run python scripts/sync_agent_rules.py` (regenera los punteros de `.claude/rules/`).
- Ficheros con texto en español: escanear las líneas añadidas en busca de tildes rotas (`?` en lugar de tilde, mojibake `Ã`, `Â`), ver `docs/lecciones-aprendidas.md`.
- Tras ediciones múltiples en un fichero, revisar su diff entero: la tool de edición también puede dejar líneas mezcladas.

Si algo falla, arreglar la causa raíz (no silenciar) y volver a verificar.

## 2. Documentación y estado

- Si cambian comandos, variables o convenciones: actualizar la instrucción de su área (`.github/instructions/`) y, si aplica siempre, `AGENTS.md`.
- Si cambian variables: instrucciones exactas para el `.env` de Sergio (clave, valor, bloque, añadir o modificar, para qué).
- Lo aplazado con coste: `docs/pendientes-cloud.md`. Si cambió el alcance de la fase: `docs/plan-fases.md`.
- Informes regenerados (`evals/reports/*`): si solo cambian latencias, proponer restaurarlos (`git restore`, con su confirmación); si cambian campos o resultados, entran en el commit.

## 3. Commit

1. `git status --short` y `git --no-pager diff --stat`: solo entra lo de esta subfase (nunca `.env`, `.secrets/`, `data/`). Un arreglo ajeno a la subfase va en un commit `fix:` aparte.
2. `git add <rutas concretas>`.
3. Mensaje Conventional Commits en español: `feat: fase N.M - <qué>` (`fix:`, `docs:`, `ci:`, `chore:` según el caso), con cuerpo breve si aporta, y el trailer del agente que hizo el trabajo (Copilot: `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`; Claude Code: `Co-authored-by: Claude <noreply@anthropic.com>`). Escribir el mensaje en un fichero temporal fuera del repo con la tool de creación y usar `git commit -F <fichero>` (el here-string por tubería rompe tildes y comillas).
4. El hook `pre-commit` comprueba los términos prohibidos; si falla, quitar el término (nunca `--no-verify`).
5. Comprobar con `git --no-pager log --oneline -1` que el commit existe (el terminal a veces mete un carácter espurio).

## 4. Push, PR y CI

1. Primer push de la rama: `git push -u origin <rama>`; después, `git push`. Comprobar que no hay error.
2. PR: si la rama aún no tiene (`gh pr view --json number,isDraft` falla), abrirla en **draft**
   contra `main`: `gh pr create --draft --base main --title "<fase>: <tema>" --body-file <fichero>`.
   El cuerpo es el **estado de la fase para la próxima sesión** (la lee `retomar-fase`): lista de
   subfases con casilla (`- [x] 2.G1 ...` con su commit, `- [ ] 2.G2 ...` con sus entregables),
   decisiones tomadas, qué se verificó y lo que va a `pendientes-cloud.md`; termina con el trailer
   del agente. Si ya existe, marcar la subfase cerrada con `gh pr edit --body-file <fichero>`.
3. La CI corre por `pull_request` (no por push a la rama): `gh run list --branch <rama> --limit 1` y `gh run watch <id> --exit-status`. Si la API devuelve jobs en `in_progress` con el run ya terminado, fiarse de la conclusión del run (exige `release-gate`).
4. Informar por job (tabla) con el enlace a la ejecución y a la PR. Si falla, diagnosticar con `gh run view <id> --log-failed`, arreglar en un commit nuevo, informar y repetir.

## 5. Siguiente paso

Resumir en 3–5 líneas lo hecho y proponer la siguiente subfase, sin empezarla salvo que Sergio ya lo haya pedido ("dale"). La fase entera se hace en la misma sesión (un commit por subfase en la PR de la fase); proponer una sesión nueva con `retomar-fase` solo si el contexto pasa del ~70 %.
