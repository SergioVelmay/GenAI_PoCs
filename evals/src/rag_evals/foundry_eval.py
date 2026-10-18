"""Carril A de las evals: evaluadores gestionados de Azure AI Foundry sobre la calibración.

`GroundednessEvaluator` y `RelevanceEvaluator` (SDK `azure-ai-evaluation`) puntúan de 1 a 5 con el
mismo despliegue juez (`gpt-4.1`). Se ejecutan sobre `calibration-v1` (20 muestras, 12 estropeadas
a propósito y etiquetadas por un humano) para compararlos 1:1 con el juez nativo, RAGAS y DeepEval:
el resultado se guarda con el mismo formato que `rag_evals.calibration judge`, así que
`python -m rag_evals.calibration agreement` lo incluye sin cambios.

Uso (con coste; perfil de `az` aislado; extra `evals`):
    uv run python -m rag_evals.foundry_eval [--log-to-foundry]
"""

import argparse
import json
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from headache_core.application.llm.pricing import PriceTable
from headache_core.ports.llm import TokenUsage
from rag_evals.calibration import CalibrationItem, JudgedItem, load_set, write_judged
from rag_evals.judges import JudgeScores

JUDGE_NAME = "foundry"
# Umbral de aprobado por defecto de los evaluadores de Foundry (escala 1-5).
PASS_THRESHOLD = 3


def to_rows(items: Sequence[CalibrationItem]) -> list[dict[str, str]]:
    """Formato de datos de `evaluate()`: una fila por muestra (query, response, context)."""
    return [
        {
            "id": item.id,
            "query": item.question,
            "response": item.answer,
            "context": "\n\n".join(item.contexts),
        }
        for item in items
    ]


def normalize(score: object) -> float | None:
    """Escala 1-5 de Foundry a 0-1 (1 -> 0, 5 -> 1)."""
    if not isinstance(score, int | float) or isinstance(score, bool):
        return None
    value = float(score)
    if not 1 <= value <= 5:
        return None
    return round((value - 1) / 4, 4)


def groundedness_to_faithfulness(score: object, threshold: int = PASS_THRESHOLD) -> float | None:
    """Fiel (1,0) si Foundry la aprueba con su umbral; si no, la escala normalizada (< 1).

    Calibrado contra las etiquetas humanas: Foundry nunca da un 5 a respuestas correctas pero
    escuetas (les da 3-4), así que exigir un 5 marcaba como infiel todo (kappa 0). Con su propio
    umbral de aprobado (3) separa las correctas de las estropeadas.
    """
    value = normalize(score)
    if value is None or not isinstance(score, int | float):
        return None
    return 1.0 if float(score) >= threshold else value


def judged_from_rows(
    items: Sequence[CalibrationItem], rows: Sequence[Mapping[str, Any]]
) -> list[JudgedItem]:
    """Convierte las filas de `evaluate()` (mismo orden que los datos) en `JudgedItem`."""
    if len(rows) != len(items):
        raise ValueError(f"Foundry devolvio {len(rows)} filas para {len(items)} muestras")
    judged: list[JudgedItem] = []
    for item, row in zip(items, rows, strict=True):
        groundedness = groundedness_to_faithfulness(row.get("outputs.groundedness.groundedness"))
        relevance = normalize(row.get("outputs.relevance.relevance"))
        if groundedness is None or relevance is None:
            judged.append(JudgedItem(item.id, item.kind, None, "puntuacion ausente o fuera de 1-5"))
            continue
        judged.append(
            JudgedItem(item.id, item.kind, JudgeScores(groundedness, relevance, None, None), None)
        )
    return judged


def token_cost(rows: Sequence[Mapping[str, Any]], model: str) -> dict[str, str]:
    """Coste aproximado a partir de los tokens que informan los evaluadores (si los informan)."""
    # Según la versión del SDK, los tokens van en columnas `*_prompt_tokens` o anidados en
    # `outputs.<evaluador>.<metrica>_properties` como `prompt_tokens` / `completion_tokens`.
    prompt = completion = 0

    def visit(values: Mapping[str, Any]) -> None:
        nonlocal prompt, completion
        for key, value in values.items():
            if isinstance(value, Mapping):
                visit(value)
            elif isinstance(value, int | float) and not isinstance(value, bool):
                if key.endswith("prompt_tokens"):
                    prompt += int(value)
                elif key.endswith("completion_tokens"):
                    completion += int(value)

    for row in rows:
        visit(row)
    cost = PriceTable().cost(model, TokenUsage(prompt, completion)) or Decimal(0)
    return {
        "input_tokens": str(prompt),
        "output_tokens": str(completion),
        "cost_usd": str(cost.quantize(Decimal("0.000001"))),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log-to-foundry", action="store_true", help="Sube el resultado al proyecto de Foundry"
    )
    args = parser.parse_args()

    from azure.ai.evaluation import (
        AzureOpenAIModelConfiguration,
        GroundednessEvaluator,
        RelevanceEvaluator,
        evaluate,
    )
    from azure.identity import DefaultAzureCredential

    from headache_core.config import Settings

    # Constructores sin tipar en el SDK (y sin el extra `evals` en la CI): se tratan como Any.
    groundedness: Any = GroundednessEvaluator
    relevance: Any = RelevanceEvaluator
    settings = Settings()
    azure = settings.azure_openai
    credential = DefaultAzureCredential()
    model_config = AzureOpenAIModelConfiguration(
        azure_endpoint=azure.endpoint,
        azure_deployment=azure.judge_deployment,
        api_version=azure.api_version,
    )
    evaluators = {
        "groundedness": groundedness(model_config, credential=credential),
        "relevance": relevance(model_config, credential=credential),
    }
    items = load_set()
    with tempfile.TemporaryDirectory() as workdir:
        data = Path(workdir) / "calibration.jsonl"
        data.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in to_rows(items)),
            encoding="utf-8",
        )
        result = evaluate(
            data=data,
            evaluation_name="headache-calibration-v1",
            evaluators=evaluators,
            azure_ai_project=settings.foundry_iq.project_endpoint if args.log_to_foundry else None,
        )
    rows = [dict(row) for row in result["rows"]]
    # El lote corre en paralelo y alguna fila falla por conexión (medido: siempre 1 de 20).
    # Se reintenta esa fila llamando a los evaluadores directamente, en serie.
    for index, (item, row) in enumerate(zip(items, rows, strict=True)):
        if (
            normalize(row.get("outputs.groundedness.groundedness")) is None
            or normalize(row.get("outputs.relevance.relevance")) is None
        ):
            sample = to_rows([item])[0]
            for name, evaluator in evaluators.items():
                output = evaluator(
                    query=sample["query"], response=sample["response"], context=sample["context"]
                )
                rows[index] |= {f"outputs.{name}.{key}": value for key, value in output.items()}
    judged = judged_from_rows(items, rows)
    cost = token_cost(rows, azure.judge_deployment)
    path = write_judged(JUDGE_NAME, judged, cost)
    print(
        f"{path}: {sum(i.error is None for i in judged)} puntuadas, coste ~{cost['cost_usd']} USD"
    )
    if result.get("studio_url"):
        print(f"Foundry: {result['studio_url']}")
    print(json.dumps(asdict(judged[0]), ensure_ascii=False, default=str)[:300])


if __name__ == "__main__":
    main()
