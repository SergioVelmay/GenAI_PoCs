"""Metricas de retrieval puras y deterministas: el gold se define por document_id."""

import math
from collections.abc import Collection, Iterable, Mapping, Sequence


def unique_in_order(ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(ids))


def _top(ranking: Sequence[str], k: int) -> Sequence[str]:
    if k < 1:
        raise ValueError("k debe ser positivo")
    if len(set(ranking)) != len(ranking):
        raise ValueError("El ranking debe estar deduplicado por documento")
    return ranking[:k]


def hit_at_k(ranking: Sequence[str], relevant: Collection[str], k: int) -> float:
    return 1.0 if any(doc in relevant for doc in _top(ranking, k)) else 0.0


def recall_at_k(ranking: Sequence[str], relevant: Collection[str], k: int) -> float:
    if not relevant:
        raise ValueError("Recall no definido sin documentos relevantes")
    return len(set(_top(ranking, k)).intersection(relevant)) / len(set(relevant))


def reciprocal_rank(ranking: Sequence[str], relevant: Collection[str], k: int) -> float:
    for position, doc in enumerate(_top(ranking, k), start=1):
        if doc in relevant:
            return 1.0 / position
    return 0.0


def ndcg_at_k(ranking: Sequence[str], grades: Mapping[str, int], k: int) -> float:
    if any(grade < 0 for grade in grades.values()):
        raise ValueError("Las relevancias graduadas no pueden ser negativas")
    ideal = sorted((grade for grade in grades.values() if grade > 0), reverse=True)[:k]
    if not ideal:
        raise ValueError("nDCG no definido sin documentos relevantes")
    dcg = sum(
        (2.0 ** grades.get(doc, 0) - 1) / math.log2(position + 1)
        for position, doc in enumerate(_top(ranking, k), start=1)
    )
    idcg = sum(
        (2.0**grade - 1) / math.log2(position + 1) for position, grade in enumerate(ideal, 1)
    )
    return dcg / idcg


def percentile(values: Sequence[float], q: float) -> float:
    """Interpolacion lineal entre rangos (equivale al metodo por defecto de numpy)."""
    if not values:
        raise ValueError("Percentil no definido sin valores")
    if not 0 <= q <= 100:
        raise ValueError("q debe estar entre 0 y 100")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100
    lower = math.floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
