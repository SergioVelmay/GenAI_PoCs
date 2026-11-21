from collections.abc import Sequence

import pytest

from genaipocs_core.ports.guardrails import ShieldUnavailableError, ShieldVerdict
from harness.security import HeuristicShield
from rag_evals.shields import load_cases, render, scan, shields_cloud, summarize


def test_dataset_is_balanced_between_attacks_and_lookalikes() -> None:
    cases = load_cases()
    assert sum(c.attack for c in cases) == sum(not c.attack for c in cases) == 12
    assert {c.channel for c in cases} == {"prompt", "document"}


async def test_heuristic_shield_has_no_false_positives_on_lookalikes() -> None:
    results = await scan(HeuristicShield(), load_cases())
    summary = summarize("heuristic", results)
    assert summary.false_positives == 0 and summary.errors == 0
    assert summary.detected >= 7
    assert "heuristic" in render([summary], len(results))


async def test_unavailable_shield_is_counted_as_error_not_as_a_verdict() -> None:
    class Down:
        async def scan_prompt(self, text: str) -> ShieldVerdict:
            raise ShieldUnavailableError("caído")

        async def scan_documents(self, texts: Sequence[str]) -> list[ShieldVerdict]:
            raise ShieldUnavailableError("caído")

        async def scan_output(self, text: str) -> ShieldVerdict:
            raise ShieldUnavailableError("caído")

    cases = load_cases()[:3]
    summary = summarize("down", await scan(Down(), cases))
    assert summary.errors == 3 and summary.recall is None


def test_one_cloud_per_run_and_report_in_its_folder() -> None:
    assert shields_cloud(["heuristic"]) is None
    assert shields_cloud(["heuristic", "gcp"]) == "gcp"
    assert shields_cloud(["heuristic", "bedrock"]) == "aws"
    with pytest.raises(ValueError, match="una sola nube"):
        shields_cloud(["heuristic", "azure", "gcp"])


def test_report_title_names_the_shields() -> None:
    summaries = [summarize("heuristic", []), summarize("gcp", [])]
    assert render(summaries, 0).startswith(
        "# Escudos de contenido: heurístico frente a Model Armor\n"
    )
