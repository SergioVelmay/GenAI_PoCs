"""Carril A de AWS (5.P): Bedrock Evaluations con Stubber de botocore, sin red ni coste.

El Stubber valida cada peticion y cada respuesta contra el modelo de la API instalada, asi que
los dobles no pueden inventarse campos de `CreateEvaluationJob` ni de `GetEvaluationJob`.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from genaipocs_core.config import BedrockEvalSettings
from rag_evals.bedrock_eval import (
    FAITHFULNESS_METRIC,
    JUDGE_NAME,
    RELEVANCE_METRIC,
    RELEVANCE_PROMPT,
    RELEVANCE_SCALE,
    SOURCE_ID,
    build_job_request,
    build_records,
    collect,
    job_name,
    judged_from_outcomes,
    outcomes_in_order,
    parse_results,
    read_results,
    validate_request,
    wait_for_job,
)
from rag_evals.calibration import (
    ADJUDICATED_PATH,
    agreement,
    load_judged,
    load_labels,
    load_set,
    write_judged,
)
from rag_evals.gcp_eval import MetricOutcome
from rag_evals.prompts import PROMPTS

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

ITEMS = load_set()
ROLE = "arn:aws:iam::123456789012:role/genaipocs-dev-bedrock-evals"
BUCKET = "genaipocs-dev-evals-123456789012"
EVALS = BedrockEvalSettings(bucket=BUCKET, role_arn=ROLE)
NAME = "genaipocs-calibration-v1-20261128-120000"
DATASET_URI = f"s3://{BUCKET}/evals/{NAME}/input/calibration-v1.jsonl"
OUTPUT_URI = f"s3://{BUCKET}/evals/output/"
ARN = "arn:aws:bedrock:eu-west-1:123456789012:evaluation-job/abc123def456"
# Un evaluador perfecto: fiel solo en las buenas y relevante salvo fuera de tema.
FAITHFUL = {"good": 1.0, "fabricated": 0.0, "unsupported": 0.25, "off_topic": 0.0}


def _output_line(record: dict[str, Any], scores: list[dict[str, Any]]) -> str:
    """Una linea de resultado como la documentada: el registro de entrada y sus puntuaciones."""
    return json.dumps(
        {"automatedEvaluationResult": {"scores": scores}, "inputRecord": record},
        ensure_ascii=False,
    )


def _perfect_lines() -> list[str]:
    """Una linea por muestra y metrica, como si el servicio escribiera un fichero por metrica."""
    lines = []
    for item, record in zip(ITEMS, build_records(ITEMS), strict=True):
        faithful = FAITHFUL.get(item.kind, 0.0)
        relevance = 0.0 if item.kind == "off_topic" else 3.0
        faithfulness = [{"metricName": FAITHFULNESS_METRIC, "result": faithful}]
        lines.append(_output_line(record, faithfulness))
        lines.append(
            _output_line(
                record,
                [
                    {
                        "metricName": RELEVANCE_METRIC,
                        "result": relevance,
                        "evaluatorDetails": [{"explanation": "responde"}],
                    }
                ],
            )
        )
    return lines


def test_records_follow_the_byoi_retrieve_and_generate_format() -> None:
    records = build_records(ITEMS)

    assert len(records) == len(ITEMS)
    turn, item = records[0]["conversationTurns"][0], ITEMS[0]
    assert turn["prompt"]["content"][0]["text"] == item.question
    assert turn["referenceResponses"][0]["content"][0]["text"] == item.reference
    assert turn["output"]["text"] == item.answer
    # Tiene que coincidir con el `ragSourceIdentifier` del job.
    assert turn["output"]["knowledgeBaseIdentifier"] == SOURCE_ID
    passages = turn["output"]["retrievedPassages"]["retrievalResults"]
    assert [passage["content"]["text"] for passage in passages] == item.contexts


def test_two_samples_with_the_same_question_and_answer_are_rejected() -> None:
    with pytest.raises(ValueError, match="misma pregunta y respuesta"):
        build_records([ITEMS[0], ITEMS[0]])


def test_request_is_valid_for_the_installed_api() -> None:
    request = build_job_request(EVALS, NAME, DATASET_URI, OUTPUT_URI)

    validate_request(request)

    automated = request["evaluationConfig"]["automated"]
    assert automated["datasetMetricConfigs"][0]["metricNames"] == [
        FAITHFULNESS_METRIC,
        RELEVANCE_METRIC,
    ]
    # El mismo evaluador para la metrica gestionada y la propia (Sonnet 4.6, no el juez nativo).
    evaluator = automated["evaluatorModelConfig"]
    assert automated["customMetricConfig"]["evaluatorModelConfig"] == evaluator
    assert evaluator["bedrockEvaluatorModels"][0]["modelIdentifier"] == EVALS.evaluator_model_id
    rag = request["inferenceConfig"]["ragConfigs"][0]["precomputedRagSourceConfig"]
    assert rag["retrieveAndGenerateSourceConfig"]["ragSourceIdentifier"] == SOURCE_ID
    assert request["applicationType"] == "RagEvaluation"


def test_a_bad_request_is_caught_offline() -> None:
    """La validacion del dry-run detecta lo que la API rechazaria (p. ej., un nombre con
    mayusculas), sin red ni credenciales."""
    from botocore.exceptions import ParamValidationError

    request = build_job_request(EVALS, NAME, DATASET_URI, OUTPUT_URI)
    request["evaluationConfig"]["automated"]["datasetMetricConfigs"][0]["metricNames"] = []

    with pytest.raises(ParamValidationError):
        validate_request(request)


def test_job_name_is_valid_for_the_api() -> None:
    name = job_name(datetime(2026, 11, 28, 12, 0, tzinfo=UTC))

    assert name == NAME
    assert len(name) <= 63 and re.fullmatch(r"[a-z0-9](-*[a-z0-9]){0,62}", name)


def test_relevance_prompt_follows_bedrock_custom_metric_rules() -> None:
    """Reglas de las metricas propias: hasta 5000 caracteres, `{{prediction}}` obligatoria, las
    variables al final y la escala fuera del prompt con definiciones de 5 palabras como maximo
    que coinciden con las del texto."""
    text = PROMPTS.get(RELEVANCE_PROMPT).text

    assert len(text) <= 5000
    assert set(re.findall(r"\{\{(\w+)\}\}", text)) == {"prompt", "prediction"}
    assert text.rstrip().endswith("{{prediction}}</respuesta>")
    for definition in RELEVANCE_SCALE:
        assert f"- {definition}:" in text
        assert len(definition.split()) <= 5 and len(definition) <= 100
        assert not {"el", "la", "los", "las", "un", "una"} & set(definition.lower().split())


def test_full_flow_with_stubbed_clients(tmp_path: Path) -> None:
    """Lanzar, esperar y recoger con los clientes de boto3 sustituidos por el Stubber."""
    bedrock = _bedrock()
    s3 = _s3()
    request = build_job_request(EVALS, NAME, DATASET_URI, OUTPUT_URI)
    body = "\n".join(_perfect_lines()).encode("utf-8")
    prefix = f"evals/output/{NAME}/"
    keys = [
        f"{prefix}abc123def456/custom_metrics/{RELEVANCE_METRIC}.json",
        f"{prefix}abc123def456/datasets/calibration-v1/x_output.jsonl",
    ]

    with Stubber(bedrock) as jobs, Stubber(s3) as storage:
        jobs.add_response("create_evaluation_job", {"jobArn": ARN}, request)
        jobs.add_response("get_evaluation_job", _job(request, "InProgress"), {"jobIdentifier": ARN})
        jobs.add_response("get_evaluation_job", _job(request, "Completed"), {"jobIdentifier": ARN})
        storage.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": key} for key in keys], "IsTruncated": False},
            {"Bucket": BUCKET, "Prefix": prefix},
        )
        storage.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(body), len(body))},
            {"Bucket": BUCKET, "Key": keys[1]},
        )

        arn = bedrock.create_evaluation_job(**request)["jobArn"]
        sleeps: list[float] = []
        job = wait_for_job(bedrock, arn, poll_seconds=30, timeout_seconds=600, sleep=sleeps.append)
        judged, cost = collect(ITEMS, read_results(s3, job), EVALS, arn)

    assert sleeps == [30]
    assert cost["job_arn"] == ARN and cost["cost_usd"] is None
    path = write_judged(JUDGE_NAME, judged, cost, tmp_path)
    name, loaded = load_judged(path)
    result = agreement(name, loaded, load_labels(ADJUDICATED_PATH))
    assert (name, result.judge_errors) == ("bedrock_eval", 0)
    assert {kind: result.detected[kind] for kind in FAITHFUL} == {
        "good": "8/8",
        "fabricated": "4/4",
        "unsupported": "4/4",
        "off_topic": "2/2",
    }


def test_scores_from_several_files_are_merged_in_dataset_order() -> None:
    lines = list(reversed(_perfect_lines()))

    outcomes = outcomes_in_order(ITEMS, parse_results(lines))

    assert set(outcomes[0]) == {FAITHFULNESS_METRIC, RELEVANCE_METRIC}
    assert outcomes[0][RELEVANCE_METRIC].explanation == "responde"


def test_results_that_are_not_from_this_dataset_are_rejected() -> None:
    foreign = build_records(ITEMS[:1])[0]
    foreign["conversationTurns"][0]["output"]["text"] = "otra respuesta"
    line = _output_line(foreign, [{"metricName": FAITHFULNESS_METRIC, "result": 1.0}])

    with pytest.raises(ValueError, match="no son de este dataset"):
        outcomes_in_order(ITEMS, parse_results([line]))
    with pytest.raises(ValueError, match="Formato de salida desconocido"):
        parse_results([json.dumps({"scores": []})])


def test_the_pass_threshold_decides_what_is_faithful() -> None:
    """El umbral se calibra como el aprobado de Foundry (exigir el maximo le dio kappa 0)."""
    outcomes = [
        {
            FAITHFULNESS_METRIC: MetricOutcome(0.75, None, None),
            RELEVANCE_METRIC: MetricOutcome(2.0, None, None),
        }
    ]

    strict = judged_from_outcomes(ITEMS[:1], outcomes, faithfulness_pass=1.0)[0].scores
    lenient = judged_from_outcomes(ITEMS[:1], outcomes, faithfulness_pass=0.75)[0].scores

    assert strict is not None and lenient is not None
    assert (strict.faithfulness, lenient.faithfulness) == (0.75, 1.0)
    assert strict.answer_relevancy == 0.6667


@pytest.mark.parametrize(
    ("metrics", "error"),
    [
        ({RELEVANCE_METRIC: MetricOutcome(3.0, None, None)}, "ausente"),
        (
            {
                FAITHFULNESS_METRIC: MetricOutcome(None, None, "ThrottlingException"),
                RELEVANCE_METRIC: MetricOutcome(3.0, None, None),
            },
            "ThrottlingException",
        ),
        (
            {
                FAITHFULNESS_METRIC: MetricOutcome(1.5, None, None),
                RELEVANCE_METRIC: MetricOutcome(3.0, None, None),
            },
            "fuera de 0-1",
        ),
        (
            # Una relevancia normalizada (2/3) no se confunde con la escala 0-3.
            {
                FAITHFULNESS_METRIC: MetricOutcome(1.0, None, None),
                RELEVANCE_METRIC: MetricOutcome(0.6667, None, None),
            },
            "fuera de la escala 0-3",
        ),
    ],
)
def test_service_errors_are_recorded_not_scored(
    metrics: dict[str, MetricOutcome], error: str
) -> None:
    judged = judged_from_outcomes(ITEMS[:1], [metrics], faithfulness_pass=1.0)

    assert judged[0].scores is None
    assert judged[0].error is not None and error in judged[0].error


def test_a_failed_job_raises_with_its_messages() -> None:
    bedrock = _bedrock()
    request = build_job_request(EVALS, NAME, DATASET_URI, OUTPUT_URI)
    failed = _job(request, "Failed") | {"failureMessages": ["AccessDenied en s3://..."]}

    with Stubber(bedrock) as jobs:
        jobs.add_response("get_evaluation_job", failed, {"jobIdentifier": ARN})
        with pytest.raises(RuntimeError, match="Failed: AccessDenied"):
            wait_for_job(bedrock, ARN, poll_seconds=30, timeout_seconds=600)


def test_waiting_too_long_says_how_to_collect_later() -> None:
    bedrock = _bedrock()
    request = build_job_request(EVALS, NAME, DATASET_URI, OUTPUT_URI)
    ticks: Iterator[float] = iter([0.0, 601.0])

    with Stubber(bedrock) as jobs:
        jobs.add_response("get_evaluation_job", _job(request, "InProgress"), {"jobIdentifier": ARN})
        with pytest.raises(TimeoutError, match="--job-arn"):
            wait_for_job(
                bedrock,
                ARN,
                poll_seconds=30,
                timeout_seconds=600,
                sleep=lambda _: None,
                clock=lambda: next(ticks),
            )


def test_a_job_without_result_files_is_an_error() -> None:
    s3 = _s3()
    job = _job(build_job_request(EVALS, NAME, DATASET_URI, OUTPUT_URI), "Completed")

    with Stubber(s3) as storage:
        storage.add_response(
            "list_objects_v2",
            {"IsTruncated": False},
            {"Bucket": BUCKET, "Prefix": f"evals/output/{NAME}/"},
        )
        with pytest.raises(ValueError, match="Sin resultados"):
            read_results(s3, job)


# Claves falsas explicitas: el test no lee las credenciales del shell.
def _bedrock() -> Any:
    return boto3.client(
        "bedrock", region_name="eu-west-1", aws_access_key_id="t", aws_secret_access_key="t"
    )


def _s3() -> S3Client:
    return boto3.client(
        "s3", region_name="eu-west-1", aws_access_key_id="t", aws_secret_access_key="t"
    )


def _job(request: dict[str, Any], status: str) -> dict[str, Any]:
    """Respuesta de `GetEvaluationJob` con los campos obligatorios del modelo de la API."""
    return {
        "jobName": request["jobName"],
        "status": status,
        "jobArn": ARN,
        "roleArn": request["roleArn"],
        "jobType": "Automated",
        "applicationType": request["applicationType"],
        "evaluationConfig": request["evaluationConfig"],
        "inferenceConfig": request["inferenceConfig"],
        "outputDataConfig": request["outputDataConfig"],
        "creationTime": datetime(2026, 11, 28, 12, 0, tzinfo=UTC),
    }
