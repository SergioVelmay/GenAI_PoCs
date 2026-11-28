"""Carril A de las evals en AWS: Bedrock Evaluations sobre la calibracion (5.P).

El equivalente de `foundry_eval` y `gcp_eval`, con una diferencia de diseño: en AWS la evaluacion
es un **job asincrono**. El dataset se sube a S3, el servicio asume un rol (`bedrock_evals.tf`),
juzga y deja el resultado en S3. Es una RAG evaluation de tipo *retrieve and generate* con
respuestas propias (BYOI, `precomputedRagSourceConfig`): no invoca ninguna Knowledge Base, juzga
las 20 muestras de `calibration-v1` (12 estropeadas a proposito y etiquetadas por un humano).

- Fidelidad: `Builtin.Faithfulness` (0-1). Fiel si llega a `BEDROCK_EVAL__FAITHFULNESS_PASS`,
  un umbral que se calibra como el aprobado de Foundry. Recalibrar no cuesta: `--job-arn` relee
  el resultado de S3 sin lanzar otro job.
- Relevancia: metrica propia `genaipocs_relevance` (prompt versionado `bedrock-eval-relevance`,
  escala 0-3 del juez nativo). La RAG evaluation no tiene metrica de relevancia de la respuesta
  (como en GCP).
- Evaluador: `BEDROCK_EVAL__EVALUATOR_MODEL_ID` (Sonnet 4.6). Sonnet 4.5, el juez nativo, no esta
  entre los evaluadores admitidos.

El resultado se guarda con el formato de `rag_evals.calibration judge` en `evals/reports/aws/`,
asi `calibration agreement --cloud aws` lo compara con el resto de jueces.

Uso (perfil de AWS con BEDROCK__PROFILE; coste solo con `--apply`):
    uv run python -m rag_evals.bedrock_eval                   # dry-run: valida y muestra el job
    uv run python -m rag_evals.bedrock_eval --apply           # sube, lanza, espera y recoge
    uv run python -m rag_evals.bedrock_eval --job-arn <arn>   # recoge un job ya lanzado
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from rag_evals.calibration import CalibrationItem, JudgedItem, load_set, write_judged
from rag_evals.gcp_eval import MetricOutcome
from rag_evals.judges import JudgeScores
from rag_evals.prompts import PROMPTS
from rag_evals.retrieval_eval import cloud_reports_dir

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

    from genaipocs_core.config import BedrockEvalSettings, Settings

JUDGE_NAME = "bedrock_eval"
# Nombre de la fuente RAG: el `knowledgeBaseIdentifier` de cada registro y el `ragSourceIdentifier`
# del job tienen que coincidir.
SOURCE_ID = "genaipocs-calibration-v1"
DATASET_NAME = "calibration-v1"
FAITHFULNESS_METRIC = "Builtin.Faithfulness"
RELEVANCE_METRIC = "genaipocs_relevance"
RELEVANCE_PROMPT = "bedrock-eval-relevance"
# Escala de la metrica propia (valor = posicion), la del juez nativo. Bedrock la añade al prompt
# como salida estructurada: las definiciones (5 palabras como maximo) son las del prompt.
RELEVANCE_SCALE = ("No responde", "Tangencial", "Responde con ruido", "Responde del todo")
RELEVANCE_MAX = len(RELEVANCE_SCALE) - 1
# Obligatorio en la API pero ignorado en las RAG evaluations. Los ejemplos de la documentacion
# usan "General", que no esta en el enum de la API instalada.
TASK_TYPE = "QuestionAndAnswer"
FINAL_STATUSES = frozenset({"Completed", "Failed", "Stopped"})

type SampleKey = tuple[str, str]


class EvaluationJobsClient(Protocol):
    """Lo que se usa del cliente `bedrock` de boto3 (el plano de control no tiene stubs aqui)."""

    def create_evaluation_job(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def get_evaluation_job(self, **kwargs: Any) -> Mapping[str, Any]: ...


def sample_key(question: str, answer: str) -> SampleKey:
    """El servicio devuelve el registro de entrada, no un id: la muestra se reconoce por la
    pregunta y la respuesta (unicas en `calibration-v1`; hay preguntas repetidas)."""
    return (question, answer)


def build_records(items: Sequence[CalibrationItem]) -> list[dict[str, Any]]:
    """Un registro BYOI de *retrieve and generate* por muestra (formato de Bedrock)."""
    keys = [sample_key(item.question, item.answer) for item in items]
    if len(set(keys)) != len(keys):
        raise ValueError("Dos muestras con la misma pregunta y respuesta: no se podrian separar")
    return [
        {
            "conversationTurns": [
                {
                    "prompt": {"content": [{"text": item.question}]},
                    "referenceResponses": [{"content": [{"text": item.reference}]}],
                    "output": {
                        "text": item.answer,
                        "knowledgeBaseIdentifier": SOURCE_ID,
                        "retrievedPassages": {
                            "retrievalResults": [
                                {"name": f"{item.id}-{index}", "content": {"text": context}}
                                for index, context in enumerate(item.contexts)
                            ]
                        },
                    },
                }
            ]
        }
        for item in items
    ]


def to_jsonl(records: Iterable[Mapping[str, Any]]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def relevance_metric() -> dict[str, Any]:
    """Definicion de la metrica propia: prompt versionado y escala 0-3."""
    return {
        "customMetricDefinition": {
            "name": RELEVANCE_METRIC,
            "instructions": PROMPTS.get(RELEVANCE_PROMPT).text,
            "ratingScale": [
                {"definition": definition, "value": {"floatValue": float(value)}}
                for value, definition in enumerate(RELEVANCE_SCALE)
            ],
        }
    }


def job_name(now: datetime) -> str:
    """Unico por region y valido para la API (minusculas, cifras y guiones)."""
    return f"genaipocs-{DATASET_NAME}-{now:%Y%m%d-%H%M%S}"


def build_job_request(
    evals: BedrockEvalSettings, name: str, dataset_uri: str, output_uri: str
) -> dict[str, Any]:
    """Peticion de `CreateEvaluationJob`: RAG evaluation BYOI con una metrica gestionada y una
    propia, las dos con el mismo evaluador."""
    evaluator = {"bedrockEvaluatorModels": [{"modelIdentifier": evals.evaluator_model_id}]}
    return {
        "jobName": name,
        "jobDescription": "Carril A de AWS (5.P): calibration-v1 frente a las etiquetas humanas",
        "roleArn": evals.role_arn,
        "applicationType": "RagEvaluation",
        "evaluationConfig": {
            "automated": {
                "datasetMetricConfigs": [
                    {
                        "taskType": TASK_TYPE,
                        "dataset": {
                            "name": DATASET_NAME,
                            "datasetLocation": {"s3Uri": dataset_uri},
                        },
                        "metricNames": [FAITHFULNESS_METRIC, RELEVANCE_METRIC],
                    }
                ],
                "evaluatorModelConfig": evaluator,
                "customMetricConfig": {
                    "customMetrics": [relevance_metric()],
                    "evaluatorModelConfig": evaluator,
                },
            }
        },
        "inferenceConfig": {
            "ragConfigs": [
                {
                    "precomputedRagSourceConfig": {
                        "retrieveAndGenerateSourceConfig": {"ragSourceIdentifier": SOURCE_ID}
                    }
                }
            ]
        },
        "outputDataConfig": {"s3Uri": output_uri},
    }


def validate_request(request: Mapping[str, Any]) -> None:
    """Valida la peticion contra el modelo de la API instalada, sin red ni credenciales."""
    import botocore.session
    from botocore.validate import validate_parameters

    model = botocore.session.get_session().get_service_model("bedrock")
    shape = model.operation_model("CreateEvaluationJob").input_shape
    if shape is None:
        raise RuntimeError("botocore sin el modelo de CreateEvaluationJob")
    validate_parameters(dict(request), shape)


def _record_key(record: Mapping[str, Any]) -> SampleKey:
    turn = record["conversationTurns"][0]
    return sample_key(turn["prompt"]["content"][0]["text"], turn["output"]["text"])


def _explanation(score: Mapping[str, Any]) -> str | None:
    details = score.get("evaluatorDetails") or []
    if details and isinstance(details[0], Mapping):
        explanation = details[0].get("explanation")
        return str(explanation) if explanation is not None else None
    return None


def parse_results(lines: Iterable[str]) -> dict[SampleKey, dict[str, MetricOutcome]]:
    """Puntuaciones por muestra y metrica a partir de los JSONL de salida (puede haber uno por
    metrica: se fusionan). Un registro que no se entiende es un error: sin el no se puede asignar
    la puntuacion a su muestra."""
    parsed: dict[SampleKey, dict[str, MetricOutcome]] = {}
    for line in lines:
        if not line.strip():
            continue
        record = json.loads(line)
        try:
            key = _record_key(record["inputRecord"])
            scores = record["automatedEvaluationResult"]["scores"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(
                f"Formato de salida desconocido ({exc!r}); claves: {sorted(record)}"
            ) from exc
        by_metric = parsed.setdefault(key, {})
        for score in scores:
            result = score.get("result")
            error = score.get("errorMessage") or score.get("error")
            by_metric[str(score["metricName"])] = MetricOutcome(
                float(result) if isinstance(result, int | float) else None,
                _explanation(score),
                str(error) if error else None,
            )
    return parsed


def outcomes_in_order(
    items: Sequence[CalibrationItem], parsed: Mapping[SampleKey, Mapping[str, MetricOutcome]]
) -> list[Mapping[str, MetricOutcome]]:
    keys = [sample_key(item.question, item.answer) for item in items]
    unknown = set(parsed) - set(keys)
    if unknown:
        raise ValueError(f"{len(unknown)} resultados no son de este dataset")
    return [parsed.get(key, {}) for key in keys]


def _faithfulness(outcome: MetricOutcome | None, passing: float) -> float | str:
    if outcome is None:
        return f"{FAITHFULNESS_METRIC} ausente en el resultado"
    if outcome.error:
        return outcome.error
    if outcome.score is None or not 0 <= outcome.score <= 1:
        return f"{FAITHFULNESS_METRIC} ausente o fuera de 0-1: {outcome.score}"
    # Fiel = 1,0 (lo que `calibration` entiende como "si"); por debajo, la nota del servicio.
    return 1.0 if outcome.score >= passing else round(outcome.score, 4)


def _relevance(outcome: MetricOutcome | None) -> float | str:
    if outcome is None:
        return f"{RELEVANCE_METRIC} ausente en el resultado"
    if outcome.error:
        return outcome.error
    score = outcome.score
    # Se espera el valor de la escala (0, 1, 2 o 3). Una nota decimal seria otra escala (p. ej.,
    # normalizada a 0-1): se rechaza en vez de leerla mal sin avisar.
    if score is None or not score.is_integer() or not 0 <= score <= RELEVANCE_MAX:
        return f"{RELEVANCE_METRIC} fuera de la escala 0-{RELEVANCE_MAX}: {score}"
    return round(score / RELEVANCE_MAX, 4)


def judged_from_outcomes(
    items: Sequence[CalibrationItem],
    outcomes: Sequence[Mapping[str, MetricOutcome]],
    faithfulness_pass: float,
) -> list[JudgedItem]:
    """Fail-closed por muestra, como el juez nativo: no se puntua a ciegas ni se aborta."""
    if len(outcomes) != len(items):
        raise ValueError(f"{len(outcomes)} resultados para {len(items)} muestras")
    judged: list[JudgedItem] = []
    for item, by_metric in zip(items, outcomes, strict=True):
        faithfulness = _faithfulness(by_metric.get(FAITHFULNESS_METRIC), faithfulness_pass)
        relevance = _relevance(by_metric.get(RELEVANCE_METRIC))
        if isinstance(faithfulness, str) or isinstance(relevance, str):
            errors = [value for value in (faithfulness, relevance) if isinstance(value, str)]
            judged.append(JudgedItem(item.id, item.kind, None, "; ".join(errors)))
            continue
        judged.append(
            JudgedItem(item.id, item.kind, JudgeScores(faithfulness, relevance, None, None), None)
        )
    return judged


def split_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"No es una URI de S3: {uri}")
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    return bucket, key


def wait_for_job(
    client: EvaluationJobsClient,
    arn: str,
    *,
    poll_seconds: float,
    timeout_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Mapping[str, Any]:
    """Espera a un estado final. Un job fallido o parado es un error; al agotar la espera, el
    job sigue en AWS y se recoge despues con `--job-arn`."""
    deadline = clock() + timeout_seconds
    while True:
        job = client.get_evaluation_job(jobIdentifier=arn)
        status = str(job["status"])
        if status in FINAL_STATUSES:
            if status != "Completed":
                failures = "; ".join(job.get("failureMessages") or []) or "sin detalle"
                raise RuntimeError(f"Job {status}: {failures}")
            return job
        if clock() >= deadline:
            raise TimeoutError(f"El job sigue {status}; recogelo despues con --job-arn {arn}")
        print(f"{status}...")
        sleep(poll_seconds)


def read_results(s3: S3Client, job: Mapping[str, Any]) -> list[str]:
    """Lineas de los JSONL de resultados bajo `<salida>/<jobName>/` (sin las definiciones de
    `custom_metrics/`)."""
    bucket, prefix = split_s3_uri(str(job["outputDataConfig"]["s3Uri"]))
    prefix = f"{prefix.rstrip('/')}/{job['jobName']}/".lstrip("/")
    keys = [
        obj["Key"]
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
        for obj in page.get("Contents", [])
        if obj["Key"].endswith(".jsonl") and "/custom_metrics/" not in obj["Key"]
    ]
    if not keys:
        raise ValueError(f"Sin resultados en s3://{bucket}/{prefix}")
    lines: list[str] = []
    for key in sorted(keys):
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        lines.extend(body.splitlines())
    return lines


def collect(
    items: Sequence[CalibrationItem], lines: Iterable[str], evals: BedrockEvalSettings, arn: str
) -> tuple[list[JudgedItem], dict[str, object]]:
    judged = judged_from_outcomes(
        items, outcomes_in_order(items, parse_results(lines)), evals.faithfulness_pass
    )
    # El servicio no devuelve tokens: se factura con el evaluador (ver la facturacion).
    cost: dict[str, object] = {
        "cost_usd": None,
        "metric_calls": len(items) * 2,
        "evaluator": evals.evaluator_model_id,
        "faithfulness_pass": evals.faithfulness_pass,
        "job_arn": arn,
    }
    return judged, cost


def _dataset_uri(evals: BedrockEvalSettings, name: str) -> str:
    return f"s3://{evals.bucket}/{evals.prefix.strip('/')}/{name}/input/{DATASET_NAME}.jsonl"


def _output_uri(evals: BedrockEvalSettings) -> str:
    return f"s3://{evals.bucket}/{evals.prefix.strip('/')}/output/"


def _clients(settings: Settings) -> tuple[EvaluationJobsClient, S3Client]:
    import boto3

    region = settings.bedrock_eval.region or settings.bedrock.region
    session = boto3.Session(profile_name=settings.bedrock.profile or None, region_name=region)
    jobs: EvaluationJobsClient = session.client("bedrock")
    return jobs, session.client("s3")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Lanza el job (con coste)")
    mode.add_argument("--job-arn", help="Recoge un job ya lanzado (sin coste nuevo)")
    args = parser.parse_args()

    from genaipocs_core.config import Settings

    settings = Settings()
    evals = settings.bedrock_eval
    items = load_set()
    records = build_records(items)

    if not args.apply and not args.job_arn:
        name = job_name(datetime.now(UTC))
        # Sin las salidas de Terraform, valores de ejemplo: solo para validar la forma.
        preview = evals.model_copy(
            update={
                "bucket": evals.bucket or "sin-terraform",
                "role_arn": evals.role_arn or "arn:aws:iam::000000000000:role/sin-terraform",
            }
        )
        request = build_job_request(
            preview, name, _dataset_uri(preview, name), _output_uri(preview)
        )
        validate_request(request)
        print(json.dumps(request, ensure_ascii=False, indent=2))
        print(f"\n{len(records)} registros; el primero:\n{to_jsonl(records[:1])}")
        print("Dry-run: peticion valida para la API instalada. Con --apply se lanza (coste).")
        return

    jobs, s3 = _clients(settings)
    if args.apply:
        if not evals.role_arn or not evals.bucket:
            raise SystemExit("Faltan BEDROCK_EVAL__BUCKET y BEDROCK_EVAL__ROLE_ARN (terraform)")
        name = job_name(datetime.now(UTC))
        dataset_uri = _dataset_uri(evals, name)
        bucket, key = split_s3_uri(dataset_uri)
        s3.put_object(Bucket=bucket, Key=key, Body=to_jsonl(records).encode("utf-8"))
        request = build_job_request(evals, name, dataset_uri, _output_uri(evals))
        arn = str(jobs.create_evaluation_job(**request)["jobArn"])
        print(f"Job lanzado: {arn}")
    else:
        arn = args.job_arn

    job = wait_for_job(
        jobs, arn, poll_seconds=evals.poll_seconds, timeout_seconds=evals.timeout_minutes * 60
    )
    judged, cost = collect(items, read_results(s3, job), evals, arn)
    path = write_judged(JUDGE_NAME, judged, cost, cloud_reports_dir("aws"))
    print(f"{path}: {sum(i.error is None for i in judged)} puntuadas de {len(judged)}")
    print(json.dumps(asdict(judged[0]), ensure_ascii=False, default=str)[:300])


if __name__ == "__main__":
    main()
