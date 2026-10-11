from __future__ import annotations

import json
from datetime import date

from harness.rules.analytics import answer_analytics_question
from headache_core.adapters.memory import FakeLLM
from headache_core.ports.sql import QueryResult, SqlExecutionError

TODAY = date(2026, 10, 11)
QUESTION = "¿Cuántos pedidos hay por estado?"
GOOD = "select status, count(*) as n from orders group by status"


class FakeRunner:
    def __init__(self, explain_errors: list[str] | None = None) -> None:
        self._explain_errors = list(explain_errors or [])
        self.explained: list[str] = []
        self.ran: list[str] = []

    async def explain(self, sql: str) -> None:
        self.explained.append(sql)
        if self._explain_errors:
            raise SqlExecutionError(self._explain_errors.pop(0))

    async def run(self, sql: str) -> QueryResult:
        self.ran.append(sql)
        return QueryResult(("status", "n"), (("paid", 10), ("refunded", 2)))


def _query(sql: str) -> str:
    return json.dumps({"sql": sql, "assumptions": "Todos los pedidos."})


async def test_forbidden_column_is_corrected_and_the_safe_sql_is_run() -> None:
    llm = FakeLLM(
        [
            _query("select email, status from customers c join orders o on o.customer_id = c.id"),
            _query(GOOD),
        ]
    )
    runner = FakeRunner()

    answer = await answer_analytics_question(llm, runner, QUESTION, today=TODAY)

    assert not answer.escalated and answer.result is not None
    assert answer.result.rows[0] == ("paid", 10)
    assert answer.correction.attempts[0].violations[0].code == "column_not_allowed"
    assert "[column_not_allowed]" in llm.calls[1][-1].content
    assert runner.ran == [answer.sql] and runner.explained == [answer.sql]
    assert answer.sql is not None and answer.sql.endswith("LIMIT 200")


async def test_database_errors_from_explain_become_feedback() -> None:
    llm = FakeLLM([_query("select sum(status) from orders"), _query(GOOD)])
    runner = FakeRunner(["function sum(character varying) does not exist"])

    answer = await answer_analytics_question(llm, runner, QUESTION, today=TODAY)

    assert not answer.escalated
    [violation] = answer.correction.attempts[0].violations
    assert violation.code == "sql_execution" and "does not exist" in violation.message


async def test_write_attempt_escalates_without_touching_the_database() -> None:
    llm = FakeLLM([_query("drop table orders")] * 3)
    runner = FakeRunner()

    answer = await answer_analytics_question(llm, runner, "Borra la tabla de pedidos", today=TODAY)

    assert answer.escalated and len(llm.calls) == 1
    assert runner.explained == [] and runner.ran == []


async def test_prompt_lists_only_exposed_columns_and_wraps_the_question() -> None:
    llm = FakeLLM([_query(GOOD)])

    await answer_analytics_question(llm, FakeRunner(), QUESTION, today=TODAY)

    system, user = llm.calls[0]
    assert "analytics.customers(id text, country varchar, language varchar)" in system.content
    assert "email" not in system.content and "2026-10-11" in system.content
    assert user.content == f"<pregunta>\n{QUESTION}\n</pregunta>"
