"""`VertexExperimentTracker` (12.G5) contra un doble de `google.cloud.aiplatform`: sin red."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from genaipocs_core.adapters.experiment_trackers import VertexExperimentTracker, vertex_id
from genaipocs_core.config import GcpSettings, Settings
from genaipocs_core.ports.experiments import ExperimentRun


@dataclass
class FakeRun:
    name: str
    experiment: str
    scope: dict[str, Any]
    params: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    ended: bool = False

    def log_params(self, params: dict[str, Any]) -> None:
        assert not self.ended
        self.params |= params

    def log_metrics(self, metrics: dict[str, Any]) -> None:
        assert not self.ended
        self.metrics |= metrics

    def end_run(self) -> None:
        self.ended = True


class FakeAiplatform:
    def __init__(self) -> None:
        self.experiments: dict[str, dict[str, Any]] = {}
        self.runs: list[FakeRun] = []
        api = self

        class Experiment:
            @staticmethod
            def get_or_create(name: str, *, description: str, **scope: Any) -> str:
                api.experiments.setdefault(name, {"description": description, **scope})
                return name

        class ExperimentRun:
            @staticmethod
            def create(name: str, *, experiment: str, **scope: Any) -> FakeRun:
                assert all(run.name != name for run in api.runs), "id de run repetido"
                api.runs.append(FakeRun(name, experiment, scope))
                return api.runs[-1]

        self.Experiment = Experiment
        self.ExperimentRun = ExperimentRun


RUN = ExperimentRun(
    "harness-on-vs-off",
    "tratamiento",
    {"flavor": "native", "harness": "false"},
    {"pass_rate": 0.5, "p_value": 0.03},
    {"git_commit": "926e824", "role": "treatment"},
    (Path("evals/reports/experiments/harness-on-vs-off.md"),),
)


def test_logs_params_metrics_and_tags_as_params_and_ends_the_run() -> None:
    api = FakeAiplatform()
    tracker = VertexExperimentTracker(api, project="genaipocs-dev", location="europe-west1")

    run_id = tracker.log_run(RUN)

    [run] = api.runs
    assert run_id == run.name and run_id.startswith("tratamiento-")
    assert run.experiment == "harness-on-vs-off" and run.ended
    assert run.scope == {"project": "genaipocs-dev", "location": "europe-west1"} | {
        "credentials": None
    }
    assert run.params == {
        "flavor": "native",
        "harness": "false",
        "tag.git_commit": "926e824",
        "tag.role": "treatment",
        "artifacts": "evals/reports/experiments/harness-on-vs-off.md",
    }
    assert run.metrics == {"pass_rate": 0.5, "p_value": 0.03}


def test_each_execution_creates_a_new_run_in_the_same_experiment() -> None:
    """El nombre de la variante se repite entre ejecuciones; el id de Vertex no puede."""
    api = FakeAiplatform()
    tracker = VertexExperimentTracker(api, project="p", location="europe-west1")

    first, second = tracker.log_run(RUN), tracker.log_run(RUN)

    assert first != second
    assert list(api.experiments) == ["harness-on-vs-off"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [("harness-on-vs-off", "harness-on-vs-off"), ("Booking_Agent v2", "booking-agent-v2")],
)
def test_names_become_valid_vertex_ids(name: str, expected: str) -> None:
    assert vertex_id(name) == expected


def test_a_name_without_valid_characters_is_rejected() -> None:
    with pytest.raises(ValueError, match="id de Vertex"):
        vertex_id("__")


def test_without_project_it_does_not_reach_the_cloud() -> None:
    with pytest.raises(ValueError, match="GCP__PROJECT"):
        VertexExperimentTracker.from_settings(Settings(gcp=GcpSettings(project="")))


def test_the_double_matches_the_installed_sdk() -> None:
    """El doble no puede inventarse la API: las llamadas del adaptador encajan en las firmas de
    `google.cloud.aiplatform` instalado (sin llamarlas)."""
    import inspect

    from google.cloud import aiplatform

    scope = {"project": "p", "location": "europe-west1", "credentials": None}
    inspect.signature(aiplatform.Experiment.get_or_create).bind("exp", description="d", **scope)
    inspect.signature(aiplatform.ExperimentRun.create).bind("run", experiment="exp", **scope)
    for method in ("log_params", "log_metrics", "end_run"):
        assert callable(getattr(aiplatform.ExperimentRun, method))
