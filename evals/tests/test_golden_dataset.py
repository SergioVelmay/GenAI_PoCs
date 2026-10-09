from collections import Counter

import pytest

from rag_evals.golden import (
    CORPUS_PATH,
    DATASET_PATH,
    HANDBOOKS,
    build_golden,
    check_golden,
    load_corpus,
    load_golden,
)


def test_versioned_dataset_matches_corpus_and_templates() -> None:
    assert check_golden(DATASET_PATH, CORPUS_PATH) == []


def test_dataset_covers_every_question_type() -> None:
    meta, items = load_golden(DATASET_PATH)
    types = Counter(item.question_type for item in items)
    assert 130 <= meta.items <= 170
    assert set(types) == {"factual", "cross_lingual", "filtered", "unanswerable", "acl_adversarial"}
    assert {item.language for item in items} == {"es", "en"}
    assert all(item.filters for item in items if item.question_type == "filtered")


def test_gold_is_public_and_adversarial_items_target_internal_handbook() -> None:
    _, items = load_golden(DATASET_PATH)
    for item in items:
        assert not set(item.relevance) & set(HANDBOOKS)
        if item.question_type == "acl_adversarial":
            assert item.forbidden_document_ids == list(HANDBOOKS)
            assert item.expected_answer is None


def test_builder_detects_corpus_drift() -> None:
    documents = load_corpus(CORPUS_PATH)
    drifted = [
        document.model_copy(update={"content": document.content.replace("Capacity:", "Seats:")})
        for document in documents
    ]
    with pytest.raises(ValueError, match="Hecho no encontrado"):
        build_golden(drifted)


def test_builder_rejects_unanswerable_question_that_gained_an_answer() -> None:
    documents = load_corpus(CORPUS_PATH)
    leaked = documents[0].model_copy(
        update={"content": documents[0].content + "\n\nFree parking is available."}
    )
    with pytest.raises(ValueError, match="parking"):
        build_golden([leaked, *documents[1:]])
