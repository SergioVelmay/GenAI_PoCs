"""Registros de experimentos (fase 12.7): JSONL local (por defecto, la CI) y MLflow (extra).

- `JsonlExperimentTracker`: una línea por run en `<dir>/<experimento>.jsonl`. Sin servidor ni
  dependencias; se compara con `jq` o pandas.
- `MlflowExperimentTracker`: el mismo run en MLflow (extra `llmops`), con `sqlite` local y la UI
  con `mlflow ui --backend-store-uri sqlite:///.mlflow/mlflow.db`. En la nube, el mismo
  adaptador contra MLflow gestionado de SageMaker o de Azure ML (cambia solo la URI).
- `VertexExperimentTracker` (12.G5): el mismo run en Vertex AI Experiments, el registro de
  experimentos de GCP, que no habla MLflow. Ver la clase.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from genaipocs_core.ports.experiments import ExperimentRun

if TYPE_CHECKING:
    from genaipocs_core.config import Settings


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


def vertex_id(name: str) -> str:
    """Vertex exige ids `[a-z0-9][a-z0-9-]*`: minúsculas y el resto, guiones."""
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValueError(f"{name!r} no deja nada válido como id de Vertex")
    return slug


def _repo_path(path: Path) -> str:
    """Ruta relativa al directorio de trabajo: la absoluta enseñaría el equipo de quien lanza."""
    try:
        return path.resolve().relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.name


class VertexExperimentTracker:
    """Vertex AI Experiments (12.G5) con la API de objetos de `google.cloud.aiplatform`.

    - `Experiment.get_or_create` y `ExperimentRun.create` reciben proyecto, región y
      credenciales en cada llamada: sin `aiplatform.init` ni el run «activo» global del SDK, que
      se pisarían con otro uso de `aiplatform` en el mismo proceso.
    - El id del run es `<experimento>-<run>` y debe ser único: el nombre de la variante
      (`control`, `tratamiento`) se repite en cada ejecución, así que lleva un sufijo aleatorio.
    - Sin *tags*: van como parámetros `tag.<clave>`. Los artefactos, como parámetro con sus rutas
      relativas (subirlos a GCS tendría coste, igual que el MLflow gestionado guarda los suyos).
    """

    def __init__(
        self,
        api: Any,
        *,
        project: str,
        location: str,
        credentials: Any = None,
    ) -> None:
        # `api` es el módulo `google.cloud.aiplatform` (o el doble de los tests): solo se usan
        # `Experiment.get_or_create` y `ExperimentRun.create`.
        self._api = api
        self._scope: dict[str, Any] = {
            "project": project,
            "location": location,
            "credentials": credentials,
        }

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        gcp = settings.gcp
        if not gcp.project:
            raise ValueError("Falta GCP__PROJECT")
        from google.cloud import aiplatform

        from genaipocs_core.adapters.gcp_auth import gcp_credentials

        return cls(
            aiplatform, project=gcp.project, location=gcp.region, credentials=gcp_credentials(gcp)
        )

    def log_run(self, run: ExperimentRun) -> str:
        experiment = self._api.Experiment.get_or_create(
            vertex_id(run.experiment),
            description=f"A/B de Genaipocs: {run.experiment}",
            **self._scope,
        )
        name = f"{vertex_id(run.name)}-{uuid.uuid4().hex[:8]}"
        active = self._api.ExperimentRun.create(name, experiment=experiment, **self._scope)
        params: dict[str, str] = dict(run.params)
        params |= {f"tag.{key}": value for key, value in run.tags.items()}
        if run.artifacts:
            params["artifacts"] = ", ".join(_repo_path(path) for path in run.artifacts)
        active.log_params(params)
        active.log_metrics(dict(run.metrics))
        # Sin esto, el run se queda RUNNING en la consola.
        active.end_run()
        return name
