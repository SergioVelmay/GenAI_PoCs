from __future__ import annotations

import pytest

from headache_core.application.ingestion import FixedSizeChunker, SentenceChunker

POLICY = (
    "# Refund policy: Candlelit concerts\n\n"
    "Applies to the 'Candlelit concerts' category in every city.\n\n"
    "- Refundable: yes, up to 48 hours before the start.\n"
    "- Money is returned to the original payment method within 5-10 business days.\n"
    "- Booking fees are non-refundable.\n"
    "- If the organiser cancels the session, a full refund is issued automatically."
)


def test_fixed_size_windows_overlap() -> None:
    chunks = FixedSizeChunker(max_words=4, overlap_words=1).split("a b c d e f g h i j")

    assert chunks == ["a b c d", "d e f g", "g h i j"]


def test_fixed_size_rejects_overlap_not_smaller_than_size() -> None:
    with pytest.raises(ValueError, match="solape"):
        FixedSizeChunker(max_words=10, overlap_words=10)


@pytest.mark.parametrize("chunker", [FixedSizeChunker(), SentenceChunker()])
def test_empty_text_has_no_chunks(chunker: FixedSizeChunker | SentenceChunker) -> None:
    assert chunker.split("   \n\n ") == []


def test_sentence_chunker_respects_limit_and_never_cuts_sentences() -> None:
    chunks = SentenceChunker(max_words=25, overlap_words=0).split(POLICY)

    assert len(chunks) > 1
    assert all(len(c.split()) <= 25 for c in chunks)
    assert any(c.endswith("48 hours before the start.") for c in chunks)
    assert " ".join(chunks).split() == POLICY.split()


def test_sentence_chunker_overlaps_whole_sentences() -> None:
    chunks = SentenceChunker(max_words=25, overlap_words=12).split(POLICY)

    assert chunks[1].splitlines()[0] == chunks[0].splitlines()[-1]
    lines = set(POLICY.splitlines())
    assert all(line in lines for chunk in chunks for line in chunk.splitlines())


def test_sentence_chunker_splits_oversized_sentence() -> None:
    text = " ".join(f"w{i}" for i in range(50))

    chunks = SentenceChunker(max_words=20, overlap_words=0).split(text)

    assert [len(c.split()) for c in chunks] == [20, 20, 10]


def test_short_document_is_a_single_chunk() -> None:
    assert len(SentenceChunker(max_words=200).split(POLICY)) == 1
