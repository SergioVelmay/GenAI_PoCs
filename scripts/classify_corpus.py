"""Clasifica el corpus con una ACL explícita antes de ingerirlo en la nube.

Los adaptadores cloud deniegan los documentos sin `acl` (no hay acceso público por defecto).
Este paso escribe una copia del corpus con `metadata.acl` en cada documento, según la política
del dominio (`classify_acl`), y conserva cualquier ACL que ya viniera explícita. El corpus de
entrada (p. ej. el congelado de evals) no se modifica.

    uv run python scripts/classify_corpus.py                       # corpus de evals
    uv run python scripts/classify_corpus.py --source <jsonl> --out <jsonl>

La salida es la que se ingiere en la nube (indexer `--source`, managed_ingestion `--source`).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from headache_core.domain.retrieval import classify_acl

DEFAULT_SOURCE = Path("evals/corpus/knowledge.jsonl")
DEFAULT_OUT = Path("data/synthetic/knowledge-classified.jsonl")

type Row = dict[str, Any]  # Una línea JSON del corpus.


def classify(rows: list[Row]) -> tuple[list[Row], Counter[str]]:
    classified: list[Row] = []
    origins: Counter[str] = Counter()
    for row in rows:
        metadata = dict(row.get("metadata") or {})
        if metadata.get("acl"):
            origins["explícita"] += 1
        else:
            metadata["acl"] = classify_acl(str(row["doc_type"]))
            origins["por política"] += 1
        classified.append({**row, "metadata": metadata})
    return classified, origins


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.source.read_text(encoding="utf-8").splitlines()]
    classified, origins = classify(rows)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as fh:
        for row in classified:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    by_type = Counter((str(r["doc_type"]), str(r["metadata"]["acl"])) for r in classified)
    print(f"{len(classified)} documentos de {args.source} -> {args.out}")
    print(f"Origen de la ACL: {dict(origins)}")
    for (doc_type, acl), count in sorted(by_type.items()):
        print(f"  {doc_type:<12} {acl:<9} {count:>4}")
    internal = [str(r["id"]) for r in classified if r["metadata"]["acl"] != "public"]
    print(f"No públicos ({len(internal)}): {', '.join(internal) or '-'}")


if __name__ == "__main__":
    main()
