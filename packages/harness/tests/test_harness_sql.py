from __future__ import annotations

import pytest

from harness import check_sql
from harness.rules.analytics import ANALYTICS_POLICY

POLICY = ANALYTICS_POLICY


def _codes(sql: str) -> list[tuple[str, bool]]:
    return [(v.code, v.correctable) for v in check_sql(sql, POLICY).violations]


def test_valid_query_is_qualified_with_the_schema_and_limited() -> None:
    check = check_sql("select status, count(*) as n from orders group by status", POLICY)

    assert check.violations == []
    assert check.sql is not None
    assert '"analytics"."orders"' in check.sql and check.sql.endswith("LIMIT 200")


@pytest.mark.parametrize(
    ("sql", "limit"),
    [
        ("select id from analytics.orders limit 10", "LIMIT 10"),
        ("select id from orders limit 100000", "LIMIT 200"),
        ("select id from orders limit all", "LIMIT 200"),
        ("select status from orders union all select status from refunds", "LIMIT 200"),
    ],
)
def test_limit_is_kept_when_small_and_forced_otherwise(sql: str, limit: str) -> None:
    check = check_sql(sql, POLICY)

    assert check.sql is not None and check.sql.endswith(limit)


def test_ctes_joins_and_safe_functions_are_allowed() -> None:
    sql = """
        with monthly as (
            select date_part('month', o.created_at) as month, o.currency,
                   sum(o.total_amount) as revenue
            from orders o join sessions s on s.id = o.session_id
            where o.status = 'paid'
            group by 1, 2
        )
        select * from monthly order by revenue desc
    """
    check = check_sql(sql, POLICY)

    assert check.violations == []
    assert check.sql is not None and '"analytics"."monthly"' not in check.sql


def test_star_expands_only_to_exposed_columns() -> None:
    check = check_sql("select * from customers", POLICY)

    assert check.sql is not None
    assert '"country"' in check.sql and "email" not in check.sql


def test_comments_are_dropped_from_the_executed_sql() -> None:
    check = check_sql("select id from orders -- ; drop table orders", POLICY)

    assert check.sql is not None and "drop" not in check.sql.lower()


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("select email from customers", ("column_not_allowed", True)),
        ("select reason from refunds", ("column_not_allowed", True)),
        ("select id from payments", ("table_not_allowed", True)),
        ("select id from public.customers", ("table_not_allowed", True)),
        ("select id from orders for update", ("locking_clause", True)),
        ("select upper_case(id) from orders", ("function_not_allowed", True)),
        ("with orders as (select 1 as id) select id from orders", ("cte_shadows_table", True)),
        ("selec id frm orders", ("sql_parse", True)),
    ],
)
def test_fixable_mistakes_go_back_to_the_model(sql: str, expected: tuple[str, bool]) -> None:
    assert expected in _codes(sql)


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("select 1; drop table orders", "multiple_statements"),
        ("delete from orders", "read_only"),
        ("update orders set status = 'refunded'", "read_only"),
        ("drop table orders", "read_only"),
        ("set role postgres", "read_only"),
        ("copy orders to stdout", "read_only"),
        ("select * into stolen from orders", "read_only"),
        ("with d as (delete from orders returning id) select * from d", "read_only"),
        ("select usename, passwd from pg_shadow", "system_catalog"),
        ("select rolname from pg_catalog.pg_roles", "system_catalog"),
        ("select table_name from information_schema.tables", "system_catalog"),
        ("select pg_sleep(30)", "function_not_allowed"),
        ("select set_config('role', 'postgres', false)", "function_not_allowed"),
        ("select pg_read_file('/etc/passwd')", "function_not_allowed"),
        (
            "select id from orders where id = (select current_setting('is_superuser'))",
            "function_not_allowed",
        ),
    ],
)
def test_attack_signatures_are_not_correctable(sql: str, code: str) -> None:
    assert (code, False) in _codes(sql)
    assert check_sql(sql, POLICY).sql is None
