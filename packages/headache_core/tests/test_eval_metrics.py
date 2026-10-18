import math

import pytest

from headache_core.domain.evals import (
    average_precision,
    cohen_kappa,
    hit_at_k,
    ndcg_at_k,
    percentile,
    ratio,
    recall_at_k,
    reciprocal_rank,
    unique_in_order,
)


def test_unique_in_order_keeps_first_occurrence() -> None:
    assert unique_in_order(["b", "a", "b", "c", "a"]) == ("b", "a", "c")


def test_hit_and_reciprocal_rank_respect_cutoff() -> None:
    ranking = ["x", "y", "gold"]
    assert hit_at_k(ranking, {"gold"}, 3) == 1.0
    assert hit_at_k(ranking, {"gold"}, 2) == 0.0
    assert reciprocal_rank(ranking, {"gold"}, 3) == pytest.approx(1 / 3)
    assert reciprocal_rank(ranking, {"gold"}, 2) == 0.0
    assert reciprocal_rank(["gold", "other"], {"gold", "other"}, 5) == 1.0


def test_recall_counts_every_relevant_document() -> None:
    assert recall_at_k(["a", "x", "b"], {"a", "b"}, 2) == 0.5
    assert recall_at_k(["a", "x", "b"], {"a", "b"}, 3) == 1.0
    with pytest.raises(ValueError):
        recall_at_k(["a"], set(), 1)


def test_ndcg_rewards_graded_order() -> None:
    grades = {"primary": 2, "translation": 1}
    assert ndcg_at_k(["primary", "translation"], grades, 5) == pytest.approx(1.0)
    swapped = ndcg_at_k(["translation", "primary"], grades, 5)
    expected = (1 + 3 / math.log2(3)) / (3 + 1 / math.log2(3))
    assert swapped == pytest.approx(expected)
    assert ndcg_at_k(["noise"], grades, 5) == 0.0
    with pytest.raises(ValueError):
        ndcg_at_k(["a"], {}, 5)


def test_metrics_reject_duplicated_rankings_and_invalid_k() -> None:
    with pytest.raises(ValueError):
        hit_at_k(["a", "a"], {"a"}, 2)
    with pytest.raises(ValueError):
        reciprocal_rank(["a"], {"a"}, 0)


def test_ratio_distinguishes_nothing_to_measure_from_zero() -> None:
    assert ratio([True, False, True, True]) == 0.75
    assert ratio([False]) == 0.0
    assert ratio([]) is None


def test_cohen_kappa_corrects_agreement_for_chance() -> None:
    assert cohen_kappa(["si", "no", "si", "no"], ["si", "no", "si", "no"]) == 1.0
    assert cohen_kappa(["si", "si", "no", "no"], ["si", "no", "si", "no"]) == 0.0
    assert cohen_kappa(["si", "no"], ["no", "si"]) == -1.0
    # Ejemplo clasico: 70 % de acuerdo observado, 50 % por azar -> 0,4.
    first = ["si"] * 25 + ["no"] * 25
    second = ["si"] * 20 + ["no"] * 5 + ["si"] * 10 + ["no"] * 15
    assert cohen_kappa(first, second) == pytest.approx(0.4)
    assert cohen_kappa([], []) is None
    assert cohen_kappa(["si", "si"], ["si", "si"]) is None
    with pytest.raises(ValueError):
        cohen_kappa(["si"], [])


def test_average_precision_rewards_relevant_contexts_on_top() -> None:
    assert average_precision([True, True, False]) == 1.0
    assert average_precision([False, True]) == 0.5
    assert average_precision([True, False, True]) == pytest.approx((1 + 2 / 3) / 2)
    assert average_precision([False, False]) == 0.0
    assert average_precision([]) is None


def test_percentile_interpolates_like_numpy() -> None:
    values = [10.0, 20.0, 30.0, 40.0]
    assert percentile(values, 50) == 25.0
    assert percentile(values, 95) == pytest.approx(38.5)
    assert percentile([7.0], 95) == 7.0
    with pytest.raises(ValueError):
        percentile([], 50)
