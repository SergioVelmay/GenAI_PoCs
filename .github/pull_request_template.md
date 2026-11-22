## Qué cambia y por qué

<!-- Fase o subfase (p. ej. 12.G7), problema que resuelve y decisiones tomadas. -->

## Verificación

<!-- Comandos ejecutados y su resultado. Evidencia, no afirmaciones. -->

- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` y `uv run pytest`
- [ ] Gates afectados (`rag_evals.gate`, `rag_evals.attacks --check`, `llmops.prompts check`...)
- [ ] Si arranca un servicio: probado de verdad (HTTP o E2E sin LLM), no solo con tests

## Reglas

- [ ] Sin secretos ni `.env`; sin nombres de empresas reales ni marcas de terceros
- [ ] Lo que tiene coste va en dry-run por defecto o con `--budget-usd`; lo aplazado, en `docs/pendientes-cloud.md`
- [ ] Docs de la fase, `.env.example` y prompts (versión y candado) al día si aplica
