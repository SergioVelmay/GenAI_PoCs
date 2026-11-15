"""Embeddings de Gemini (3.G2) sin red: `task_type`, dimensiones, normalización, lotes y errores."""

from __future__ import annotations

import math

import pytest
from google.genai import errors, types

from headache_core.adapters.gemini_embeddings import (
    DOCUMENT_TASK,
    QUERY_TASK,
    GeminiEmbedding,
    normalize,
)
from headache_core.ports import LLMError, LLMRateLimitError


class FakeModels:
    def __init__(self, *, dimensions: int = 4, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, list[str], types.EmbedContentConfig]] = []
        self._dimensions = dimensions
        self._fail = fail

    async def embed_content(
        self, *, model: str, contents: list[types.Content], config: types.EmbedContentConfig
    ) -> types.EmbedContentResponse:
        texts = [str((content.parts or [types.Part()])[0].text) for content in contents]
        self.calls.append((model, texts, config))
        if self._fail is not None:
            raise self._fail
        # Vector sin normalizar y distinto por texto, para comprobar orden y normalización.
        return types.EmbedContentResponse(
            embeddings=[
                types.ContentEmbedding(values=[float(len(text))] + [1.0] * (self._dimensions - 1))
                for text in texts
            ]
        )


async def test_documents_and_queries_use_different_task_types() -> None:
    models = FakeModels()
    embedding = GeminiEmbedding(models, "gemini-embedding-001", dimensions=4)

    await embedding.embed_texts(["documento"])
    await embedding.embed_query("consulta")

    (_, _, doc_config), (_, _, query_config) = models.calls
    assert (doc_config.task_type, query_config.task_type) == (DOCUMENT_TASK, QUERY_TASK)
    assert doc_config.output_dimensionality == 4


async def test_vectors_are_normalized_and_keep_the_input_order_across_batches() -> None:
    models = FakeModels()
    embedding = GeminiEmbedding(models, "m", dimensions=4, batch_size=2)

    vectors = await embedding.embed_texts(["a", "bb", "ccc"])

    assert [len(contents) for _, contents, _ in models.calls] == [2, 1]
    assert all(math.isclose(math.hypot(*v), 1.0) for v in vectors)
    # El primer componente crece con la longitud del texto: el orden se conserva.
    assert vectors[0][0] < vectors[1][0] < vectors[2][0]


async def test_wrong_dimensions_fail_instead_of_corrupting_the_index() -> None:
    embedding = GeminiEmbedding(FakeModels(dimensions=3), "m", dimensions=4)

    with pytest.raises(LLMError, match="3 dimensiones"):
        await embedding.embed_texts(["a"])


async def test_quota_errors_are_typed_for_the_gateway() -> None:
    quota = errors.ClientError(429, {"error": {"code": 429, "message": "quota"}})
    embedding = GeminiEmbedding(FakeModels(fail=quota), "m", dimensions=4)

    with pytest.raises(LLMRateLimitError):
        await embedding.embed_query("a")


def test_normalize_leaves_a_zero_vector_alone() -> None:
    assert normalize([0.0, 0.0]) == [0.0, 0.0]
    assert normalize([3.0, 4.0]) == [0.6, 0.8]
