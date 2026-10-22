"""Fase 12.7: experimentos A/B offline sobre la suite de agentes (coste 0)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from headache_core.domain.experiments import Decision
from headache_core.ports.experiments import ExperimentRun
from rag_evals.llmops.experiment import (
    EXPERIMENTS_DIR,
    candidate_prompt,
    compare,
    load_spec,
    log_runs,
    render,
    run_variants,
)

needs_mlflow = pytest.mark.skipif(
    importlib.util.find_spec("mlflow") is None, reason="extra llmops no instalado"
)


class Recorder:
    def __init__(self) -> None:
        self.runs: list[ExperimentRun] = []

    def log_run(self, run: ExperimentRun) -> str:
        self.runs.append(run)
        return f"run-{len(self.runs)}"


@pytest.mark.parametrize(
    ("experiment", "decision", "discordant"),
    [
        ("harness-on-vs-off", Decision.SHIP, (0, 7)),
        ("flavor-native-vs-foundry", Decision.INCONCLUSIVE, (1, 0)),
        # Con el modelo guionizado un prompt no cambia nada: hay que medirlo con --mode llm.
        ("booking-agent-prompt-v2", Decision.KEEP, (0, 0)),
    ],
)
async def test_committed_experiments_reach_their_documented_decision(
    experiment: str, decision: Decision, discordant: tuple[int, int]
) -> None:
    spec = load_spec(EXPERIMENTS_DIR / f"{experiment}.yaml")
    results, meter = await run_variants(spec)

    control, treatment = results
    comparison = compare(control, treatment, spec.metric, spec.alpha)

    assert meter is None
    assert comparison.verdict.decision is decision
    assert (comparison.test.only_control, comparison.test.only_treatment) == discordant
    assert f"Decisión: `{decision.value}`" in render(spec, results, comparison)


async def test_the_candidate_prompt_reaches_the_agent_and_is_logged(tmp_path: Path) -> None:
    spec = load_spec(EXPERIMENTS_DIR / "booking-agent-prompt-v2.yaml")
    candidate = candidate_prompt("candidates/booking-agent.md")
    assert candidate.version == 2 and "SIEMPRE en el idioma" in candidate.text
    results, _ = await run_variants(spec)
    control, treatment = results
    comparison = compare(control, treatment, spec.metric, spec.alpha)
    tracker = Recorder()

    ids = log_runs(tracker, spec, results, comparison, tmp_path / "r.md", None)

    assert ids == ["run-1", "run-2"]
    control_run, treatment_run = tracker.runs
    assert control_run.params["prompt"] == "producción"
    assert treatment_run.params["prompt"] == "booking-agent v2"
    assert treatment_run.tags["decision"] == Decision.KEEP.value
    assert "p_value" in treatment_run.metrics and "p_value" not in control_run.metrics


def test_spec_validation_and_online_assignment() -> None:
    spec = load_spec(EXPERIMENTS_DIR / "booking-agent-prompt-v2.yaml")
    assert spec.treatment() == "tratamiento"
    assigner = spec.assigner()
    assert assigner.variant("cus_0000001") == assigner.variant("cus_0000001")
    with pytest.raises(ValueError):
        load_spec(EXPERIMENTS_DIR / "harness-on-vs-off.yaml").assigner()


@needs_mlflow
def test_mlflow_tracker_logs_params_metrics_and_artifacts(tmp_path: Path) -> None:
    from headache_core.adapters.experiment_trackers import MlflowExperimentTracker

    report = tmp_path / "informe.md"
    report.write_text("# informe\n", encoding="utf-8")
    uri = f"sqlite:///{(tmp_path / 'mlflow.db').as_posix()}"
    tracker = MlflowExperimentTracker(uri, artifact_location=(tmp_path / "art").as_uri())

    run_id = tracker.log_run(
        ExperimentRun("exp-test", "tratamiento", {"flavor": "native"}, {"p": 0.5}, {}, (report,))
    )

    import mlflow

    client = mlflow.MlflowClient(tracking_uri=uri)
    run = client.get_run(run_id)
    assert run.data.params["flavor"] == "native" and run.data.metrics["p"] == 0.5
    assert [a.path for a in client.list_artifacts(run_id)] == ["informe.md"]
