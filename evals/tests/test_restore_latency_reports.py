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


def test_only_json_reports_of_the_reports_dir_are_touched() -> None:
    assert "no se toca" in restore.restore("docs/fase-5-evals.md")
    assert "no se toca" in restore.restore("evals/reports/attacks.md")
