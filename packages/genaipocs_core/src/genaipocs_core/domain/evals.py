"""Metricas de retrieval puras y deterministas: el gold se define por document_id."""

import math
from collections.abc import Collection, Hashable, Iterable, Mapping, Sequence


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


def ratio(flags: Sequence[bool]) -> float | None:
    """Fraccion de verdaderos; None si no hay nada que medir (no es un 0 ni un 1)."""
    return sum(flags) / len(flags) if flags else None


def average_precision(relevance: Sequence[bool]) -> float | None:
    """Precision media de un ranking de contextos (Context Precision de RAGAS).

    Premia que los contextos relevantes esten arriba: media de precision@i en cada
    posicion relevante. 0 si ningun contexto es relevante; None si no hay contextos.
    """
    if not relevance:
        return None
    hits = 0
    precisions: list[float] = []
    for position, relevant in enumerate(relevance, start=1):
        if relevant:
            hits += 1
            precisions.append(hits / position)
    return sum(precisions) / len(precisions) if precisions else 0.0


def cohen_kappa(first: Sequence[Hashable], second: Sequence[Hashable]) -> float | None:
    """Acuerdo entre dos anotadores corregido por azar: 1 total, 0 como el azar, <0 peor.

    None si no hay pares o si el acuerdo por azar es 1 (ambos usan siempre la misma etiqueta):
    ahi el acuerdo observado no dice nada.
    """
    if len(first) != len(second):
        raise ValueError("Las dos series de etiquetas deben tener la misma longitud")
    if not first:
        return None
    total = len(first)
    observed = sum(a == b for a, b in zip(first, second, strict=True)) / total
    labels = set(first) | set(second)
    expected = sum((first.count(label) / total) * (second.count(label) / total) for label in labels)
    if expected == 1:
        return None
    return (observed - expected) / (1 - expected)


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
