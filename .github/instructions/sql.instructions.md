---
applyTo: "packages/headache_core/migrations/**,packages/headache_core/src/headache_core/adapters/postgres/**,services/ticketing_api/**,infra/sql/**,scripts/load_seed.py,scripts/sql_benchmark.py"
description: "PostgreSQL, Alembic, SQLAlchemy async y ticketing_api (fase 1)."
---

# PostgreSQL y ticketing (fase 1, ampliado en 3, 8)

Doc: `docs/fase-1-sql.md`.

## Migraciones (Alembic)

- `uv run alembic -c packages/headache_core/alembic.ini upgrade head`.
- Historial: 0001 esquema (sin índices secundarios) · 0002 índices `CONCURRENTLY` (`autocommit_block`) · 0003 pgvector `knowledge_chunks` vector(1024) + `ingestion_ledger` · 0004 vistas `analytics` sin PII + rol `headache_analyst` NOLOGIN · 0005 `approval_requests` · 0006 `support_cases` (fase 9.6: estado de los casos, único por email) · 0007 `outbox_events` (identity, índice parcial `WHERE published_at IS NULL`) + `case_submissions` (PK cliente + `Idempotency-Key`) (fase 10.5). Los checkpoints de LangGraph van en el esquema `langgraph`, fuera de Alembic.
- Cada migración con `downgrade` real: la CI hace arriba → abajo → arriba y `alembic check` (el ORM de `adapters/postgres/tables.py` debe coincidir).
- Restricciones con nombre (`pk_`, `fk_`, `ck_`, `uq_`, `ix_`); enumerados como `CHECK ... IN (...)`, no `ENUM` nativo.
- Índices en tablas grandes siempre `CONCURRENTLY`. Las tablas de LlamaIndex (esquema `llamaindex`) quedan fuera de Alembic.

## Concurrencia y consistencia

- Inventario: `UPDATE ... WHERE available >= :q RETURNING` atómico (`try_reserve`) + `CHECK` como red de seguridad. Nunca leer-calcular-escribir.
- Decisiones que leen varias filas (reembolsos): `SELECT ... FOR UPDATE`.
- Idempotencia: `Idempotency-Key` con índice único parcial; misma clave y otro cuerpo ⇒ `ConflictError` (409).
- Unit of Work: una transacción por caso de uso; `expire_on_commit=False`.

## ticketing_api

- `services/ticketing_api` (FastAPI, `create_app(uow_factory, clock)` inyectables). La consumen `ticketing_http`, las tools y los servidores MCP: cambiar un contrato rompe las fases 6–8.
- Errores de dominio → HTTP en un único sitio (`NotFoundError` 404, `PurchaseNotAllowedError`/`ConflictError` 409).

## Datos y rendimiento

- Datos: `uv run python scripts/generate_synthetic_data.py --scale small|medium|large` → `scripts/load_seed.py --truncate` (COPY por lotes + `ANALYZE`).
- Benchmark: `scripts/sql_benchmark.py --label X [--compare Y] [--plan 01]` sobre `infra/sql/queries/*.sql` con `EXPLAIN (ANALYZE, BUFFERS)`. Un índice nuevo se justifica con su plan antes/después.

## SQL generado por LLM (fase 8.2)

- Solo a través de `harness/sql.py` `check_sql` y `PostgresReadOnlySql` (transacción READ ONLY, `SET LOCAL statement_timeout/ROLE/search_path`, tope de filas). Nunca ejecutar SQL del modelo con otro rol.
