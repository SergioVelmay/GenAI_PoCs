"""`scripts/restore_latency_reports.py`: solo restaura si las latencias son lo único que cambia."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "restore_latency_reports.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("restore_latency_reports", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


restore = _load()
SUMMARY: dict[str, object] = {"contained": 10, "latency_p95_ms": 1.2}
HEAD: dict[str, object] = {"summary": SUMMARY, "runs": [{"id": "a", "latency_ms": 3}]}


def _changed(**summary: object) -> str:
    return json.dumps(HEAD | {"summary": SUMMARY | summary})


def test_latencies_alone_are_noise() -> None:
    noisy = HEAD | {"runs": [{"id": "a", "latency_ms": 9.9}]}
    assert restore.only_latencies_changed(json.dumps(HEAD), json.dumps(noisy))
    assert restore.only_latencies_changed(json.dumps(HEAD), _changed(latency_p95_ms=7.0))


def test_any_other_change_blocks_the_restore() -> None:
    assert not restore.only_latencies_changed(json.dumps(HEAD), _changed(contained=9))
    assert not restore.only_latencies_changed(json.dumps(HEAD), _changed(new_field=1))


MARKDOWN = """# Informe

| Sabor | Llamadas LLM | p50 ms | p95 ms | Coste USD |
|---|---:|---:|---:|---:|
| native | 50 | 2.3 | 4.0 | 0 |

| Escenario | Tools |
|---|---|
| refund | 3 |
"""


def test_markdown_latency_columns_are_noise() -> None:
    noisy = MARKDOWN.replace("| 2.3 | 4.0 |", "| 2.6 | 6.1 |")
    assert restore.only_latencies_changed(MARKDOWN, noisy, markdown=True)


def test_markdown_changes_outside_latency_columns_block_the_restore() -> None:
    other_cell = MARKDOWN.replace("| native | 50 |", "| native | 51 |")
    # La columna 2 de la segunda tabla no es de latencia aunque lo sea en la primera.
    second_table = MARKDOWN.replace("| refund | 3 |", "| refund | 4 |")
    prose = MARKDOWN.replace("# Informe", "# Otro informe")
    for changed in (other_cell, second_table, prose):
        assert not restore.only_latencies_changed(MARKDOWN, changed, markdown=True)


def test_only_reports_of_the_reports_dir_are_touched() -> None:
    assert "no se toca" in restore.restore("docs/fase-5-evals.md")
    assert "no se toca" in restore.restore("evals/reports/load/plan.txt")
