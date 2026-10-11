"""Text-to-SQL de operaciones con el harness (fase 8.2). TIENE COSTE (llama al LLM).

docker compose up -d
uv run alembic -c packages/headache_core/alembic.ini upgrade head
uv run python scripts/analytics_sql_demo.py "¿Cuántos reembolsos rechazados hubo por ciudad?"
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from harness.rules.analytics import answer_analytics_question
from headache_core.adapters.llm_factory import build_gateway, build_tracer
from headache_core.adapters.postgres import create_engine
from headache_core.adapters.postgres.readonly_sql import PostgresReadOnlySql
from headache_core.config import get_settings


async def main() -> None:
    parser = argparse.ArgumentParser(description="Text-to-SQL validado por el harness")
    parser.add_argument("question")
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args()

    settings = get_settings()
    llm = build_gateway(settings, tracer=build_tracer(settings))
    engine = create_engine(settings.postgres, application_name="analytics-sql")
    try:
        answer = await answer_analytics_question(
            llm,
            PostgresReadOnlySql(engine),
            args.question,
            today=datetime.now(UTC).date(),
            max_attempts=args.max_attempts,
        )
    finally:
        await engine.dispose()

    for number, attempt in enumerate(answer.correction.attempts, start=1):
        codes = ", ".join(v.code for v in attempt.violations) or "ok"
        print(f"intento {number}: {codes}")
    if answer.result is None:
        reasons = answer.error or "; ".join(v.message for v in answer.correction.violations)
        print(f"\nEscalado a un analista: {reasons}")
        return
    print(f"\nSQL ejecutada:\n{answer.sql}\n")
    print(" | ".join(answer.result.columns))
    for row in answer.result.rows:
        print(" | ".join(str(value) for value in row))
    if answer.result.truncated:
        print("… (resultado truncado)")


if __name__ == "__main__":
    asyncio.run(main())
