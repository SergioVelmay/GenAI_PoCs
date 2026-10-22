"""Red teaming offline con PyRIT (fase 12.4): genera las mutaciones y las ejecuta.

    uv sync --all-packages --extra redteam
    uv run python -m rag_evals.redteam.pyrit_offline [--flavor native] [--write-dataset]

Cada conversor determinista de PyRIT (base64, ROT13, leetspeak, homoglifos, espaciado, ancho
cero, tildes, mayúsculas, etiquetas Unicode) reescribe el mensaje del cliente de cada escenario
de `attack-scenarios-v1.jsonl`. `--write-dataset` congela el resultado en
`attack-mutations-v1.jsonl` para que la CI lo repita sin instalar PyRIT
(`rag_evals.redteam.mutations --check`). Métricas e informe: ver ese módulo.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from booking_agent.flavors import FLAVORS
from rag_evals.agents.scenarios import ATTACKS_PATH, load_scenarios
from rag_evals.redteam.converters import pyrit_converters
from rag_evals.redteam.mutations import (
    MUTATIONS_PATH,
    build_mutations,
    report_and_check,
    run_mutations,
    write_mutations,
)
from rag_evals.retrieval_eval import REPORTS_DIR


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flavor", choices=sorted(FLAVORS), default="native")
    parser.add_argument("--dataset", type=Path, default=ATTACKS_PATH)
    parser.add_argument("--out", type=Path, default=REPORTS_DIR)
    parser.add_argument(
        "--write-dataset", action="store_true", help=f"Congela las mutaciones en {MUTATIONS_PATH}"
    )
    args = parser.parse_args()
    logging.getLogger("harness").setLevel(logging.ERROR)
    mutations = asyncio.run(build_mutations(load_scenarios(args.dataset), pyrit_converters()))
    if args.write_dataset:
        write_mutations(mutations)
        print(f"{len(mutations)} mutaciones en {MUTATIONS_PATH}")
    outcomes = asyncio.run(run_mutations(mutations, flavor=args.flavor))
    report_and_check(outcomes, out=args.out, flavor=args.flavor, strict=False)


if __name__ == "__main__":
    main()
