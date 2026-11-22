"""Riesgo residual del red teaming frente a los detectores de PII (fase 12.G3).

La 12.4 y la 12.5 aceptaron un riesgo residual: la PII **codificada u ofuscada** (base64,
hexadecimal, morse, homoglifos) llega al modelo porque el detector local no la ve, y decodificar
cada mensaje se descartó (CPU y falsos positivos). La mitigación escrita era «el guardrail del
proveedor en la nube». Este módulo lo comprueba: pasa todas las variantes del ataque de PII
(las 10 de PyRIT y las 10 de promptfoo) por cada detector y dice qué entidades encuentra.

- `regex`: el detector local del harness (fase 8), sin coste.
- `presidio`: con el extra `pii` (`uv sync --all-packages --extra pii`), sin coste.
- `gcp`: Sensitive Data Protection (`GUARDRAILS__PII=gcp`, 8.G2), con coste por llamada
  (gcloud aislado). El informe va a `evals/reports/gcp/`.

Uso:
    uv run python -m rag_evals.redteam.residual                     # regex (coste 0)
    uv run python -m rag_evals.redteam.residual --detector gcp      # y SDP (20 llamadas)
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import yaml

from harness.normalize import sanitize
from harness.pii import PiiDetector, PiiUnavailableError
from rag_evals.redteam import mutations, promptfoo
from rag_evals.retrieval_eval import REPORTS_DIR, cloud_reports_dir

SCENARIO = "attack-pii-in-prompt-es"
REPORT_NAME = "redteam-residual"
type DetectorName = Literal["regex", "presidio", "gcp"]
DETECTORS: tuple[DetectorName, ...] = ("regex", "presidio", "gcp")
# Lo que no debe llegar al modelo en este ataque: una tarjeta y un correo.
TARGET_ENTITIES = ("CREDIT_CARD", "EMAIL_ADDRESS")


@dataclass(frozen=True, slots=True)
class Variant:
    source: Literal["pyrit", "promptfoo"]
    technique: str
    text: str
    # Riesgo residual aceptado en la baseline de su fuente (llega al modelo con el harness).
    accepted: bool


def load_variants(
    mutations_path: Path = mutations.MUTATIONS_PATH, tests_path: Path = promptfoo.TESTS_PATH
) -> list[Variant]:
    pyrit_known = mutations.load_baseline().known_breaches
    promptfoo_known = promptfoo.load_baseline().known_breaches
    variants: list[Variant] = []
    for line in mutations_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        technique, _, scenario = str(case["id"]).partition(":")
        if scenario == SCENARIO:
            variants.append(
                Variant("pyrit", technique, case["turns"][0]["user"], case["id"] in pyrit_known)
            )
    for test in yaml.safe_load(tests_path.read_text(encoding="utf-8")):
        values = test["vars"]
        if values["scenario_id"] == SCENARIO:
            key = f"{values['strategy']}:{SCENARIO}"
            variants.append(
                Variant("promptfoo", values["strategy"], values["prompt"], key in promptfoo_known)
            )
    return variants


@dataclass(frozen=True, slots=True)
class Detection:
    detector: str
    source: str
    technique: str
    accepted: bool
    entities: list[str]
    error: str | None

    @property
    def caught(self) -> bool:
        """Encuentra todo lo que el ataque intenta colar (tarjeta y correo)."""
        return self.error is None and set(TARGET_ENTITIES) <= set(self.entities)


def scan(name: str, detector: PiiDetector, variants: Sequence[Variant]) -> list[Detection]:
    """Un fallo del servicio es un resultado (sin detectar), no aborta el resto."""
    detections: list[Detection] = []
    for variant in variants:
        try:
            # Lo mismo que ve el filtro del harness: sin invisibles ni etiquetas Unicode.
            spans = detector.analyze(sanitize(variant.text), "es")
        except PiiUnavailableError as error:
            detections.append(
                Detection(name, variant.source, variant.technique, variant.accepted, [], str(error))
            )
            continue
        entities = sorted({span.entity for span in spans})
        detections.append(
            Detection(name, variant.source, variant.technique, variant.accepted, entities, None)
        )
    return detections


def build_detector(name: DetectorName) -> PiiDetector:
    from booking_agent.guardrails import build_pii_detector
    from genaipocs_core.config import Settings

    settings = Settings()
    guardrails = settings.guardrails.model_copy(update={"pii": name})
    return build_pii_detector(settings.model_copy(update={"guardrails": guardrails}))


def render(detections: Mapping[str, Sequence[Detection]], variants: Sequence[Variant]) -> str:
    names = list(detections)
    lines = [
        "# Riesgo residual del red teaming frente a los detectores de PII (12.G3)",
        "",
        f"- Ataque `{SCENARIO}`: tarjeta y correo en el mensaje, ofuscados de "
        f"{len(variants)} formas (PyRIT y promptfoo).",
        "- Celda: entidades que encuentra el detector; ✅ = tarjeta **y** correo.",
        "- **Aceptado**: riesgo residual escrito en la baseline (llega al modelo con el harness).",
        "",
        "| Fuente | Técnica | Aceptado | " + " | ".join(names) + " |",
        "|---|---|---|" + "---|" * len(names),
    ]
    for index, variant in enumerate(variants):
        cells = []
        for name in names:
            found = detections[name][index]
            if found.error:
                cells.append(f"error: {found.error[:40]}")
            else:
                mark = "✅ " if found.caught else ""
                cells.append(mark + (", ".join(found.entities) or "—"))
        accepted = "sí" if variant.accepted else ""
        lines.append(
            f"| {variant.source} | {variant.technique} | {accepted} | " + " | ".join(cells) + " |"
        )
    lines += ["", "| Detector | Atrapa tarjeta y correo | De los aceptados |", "|---|---:|---:|"]
    accepted_total = sum(v.accepted for v in variants)
    for name in names:
        results = detections[name]
        caught = sum(d.caught for d in results)
        caught_accepted = sum(d.caught for d in results if d.accepted)
        lines.append(f"| {name} | {caught}/{len(results)} | {caught_accepted}/{accepted_total} |")
    return "\n".join(lines) + "\n"


def write(
    detections: Mapping[str, Sequence[Detection]], variants: Sequence[Variant], out_dir: Path
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {name: [asdict(d) for d in found] for name, found in detections.items()}
    (out_dir / f"{REPORT_NAME}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    path = out_dir / f"{REPORT_NAME}.md"
    path.write_text(render(detections, variants), encoding="utf-8", newline="\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--detector",
        action="append",
        choices=[d for d in DETECTORS if d != "regex"],
        help="Detectores además del regex local (gcp tiene coste)",
    )
    args = parser.parse_args()
    names: list[DetectorName] = ["regex", *(args.detector or [])]
    variants = load_variants()
    detections: dict[str, list[Detection]] = {
        name: scan(name, build_detector(name), variants) for name in names
    }
    out_dir = cloud_reports_dir("gcp") if "gcp" in names else REPORTS_DIR
    print(write(detections, variants, out_dir).read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
