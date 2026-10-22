"""Registros de experimentos (fase 12.7): JSONL local (por defecto, la CI) y MLflow (extra).

- `JsonlExperimentTracker`: una línea por run en `<dir>/<experimento>.jsonl`. Sin servidor ni
  dependencias; se compara con `jq` o pandas.
- `MlflowExperimentTracker`: el mismo run en MLflow (extra `llmops`), con `sqlite` local y la UI
  con `mlflow ui --backend-store-uri sqlite:///.mlflow/mlflow.db`. En la nube, el mismo
  adaptador contra MLflow gestionado de SageMaker o de Azure ML (cambia solo la URI).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from headache_core.ports.experiments import ExperimentRun


class JsonlExperimentTracker:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def log_run(self, run: ExperimentRun) -> str:
        self._directory.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        record = {
            "run_id": run_id,
            "experiment": run.experiment,
            "name": run.name,
            "logged_at": datetime.now(UTC).isoformat(),
            "params": dict(run.params),
            "metrics": dict(run.metrics),
            "tags": dict(run.tags),
            "artifacts": [str(p) for p in run.artifacts],
        }
        path = self._directory / f"{run.experiment}.jsonl"
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return run_id


class MlflowExperimentTracker:
    def __init__(self, tracking_uri: str, *, artifact_location: str | None = None) -> None:
        import mlflow

        self._mlflow = mlflow
        self._artifact_location = artifact_location
        mlflow.set_tracking_uri(tracking_uri)

    def log_run(self, run: ExperimentRun) -> str:
        mlflow = self._mlflow
        if mlflow.get_experiment_by_name(run.experiment) is None:
            mlflow.create_experiment(run.experiment, artifact_location=self._artifact_location)
        mlflow.set_experiment(run.experiment)
        with mlflow.start_run(run_name=run.name) as active:
            mlflow.log_params(dict(run.params))
            mlflow.log_metrics(dict(run.metrics))
            mlflow.set_tags(dict(run.tags))
            for artifact in run.artifacts:
                mlflow.log_artifact(str(artifact))
            return str(active.info.run_id)
