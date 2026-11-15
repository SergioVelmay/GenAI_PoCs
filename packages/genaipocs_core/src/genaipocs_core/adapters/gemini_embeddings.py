"""Embeddings de Gemini (`gemini-embedding-001`) con `google-genai` (fase 3.G2).

Mismo `EmbeddingPort` y mismas 1024 dimensiones que Azure OpenAI y Titan v2 (Matryoshka: el modelo
da 3072 y se recorta). Dos diferencias que el adaptador absorbe:

- **`task_type` asimétrico**: Gemini optimiza el vector según el uso. `embed_texts` embebe
  *documentos* (`RETRIEVAL_DOCUMENT`) y `embed_query`, *consultas* (`RETRIEVAL_QUERY`). Quien
  compare textos del mismo tipo (p. ej., consultas entre sí en la caché semántica) usa
  `embed_texts` para los dos lados y la comparación sigue siendo simétrica.
- **Normalización propia**: la documentación solo garantiza vectores normalizados a 3072; al
  recortar a 1024 se normalizan aquí (L2), para que coseno, producto escalar y euclídea ordenen
  igual en cualquier almacén.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from typing import Protocol, Self

from google.genai import types

from genaipocs_core.adapters.gemini import SDK_ERRORS, gemini_client, to_llm_error
from genaipocs_core.config import GcpSettings, GeminiSettings
from genaipocs_core.ports import LLMError

DIMENSIONS = 1024
DOCUMENT_TASK = "RETRIEVAL_DOCUMENT"
QUERY_TASK = "RETRIEVAL_QUERY"


class GeminiEmbedModels(Protocol):
    """Lo que el adaptador usa de `Client.aio.models` (permite un doble en los tests)."""

    async def embed_content(
        self, *, model: str, contents: list[types.Content], config: types.EmbedContentConfig
    ) -> types.EmbedContentResponse: ...


def normalize(values: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    return [float(v) / norm for v in values] if norm else [float(v) for v in values]


class GeminiEmbedding:
    def __init__(
        self,
        models: GeminiEmbedModels,
        model: str,
        dimensions: int = DIMENSIONS,
        *,
        batch_size: int = 100,
        concurrency: int = 4,
    ) -> None:
        self._models = models
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size
        self._semaphore = asyncio.Semaphore(concurrency)

    @classmethod
    def from_settings(
        cls, gcp: GcpSettings, gemini: GeminiSettings, dimensions: int = DIMENSIONS
    ) -> Self:
        return cls(
            gemini_client(gcp, gemini).aio.models,
            gemini.embedding_model,
            dimensions,
            batch_size=gemini.embedding_batch_size,
        )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        batches = [
            list(texts[start : start + self._batch_size])
            for start in range(0, len(texts), self._batch_size)
        ]
        results = await asyncio.gather(*(self._embed(batch, DOCUMENT_TASK) for batch in batches))
        return [vector for batch in results for vector in batch]

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([text], QUERY_TASK))[0]

    async def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        # Sin `auto_truncate=False`: solo existe en Agent Platform (no en la Developer API) y los
        # chunks (~140 tokens) quedan muy lejos del límite de 2048 por texto.
        config = types.EmbedContentConfig(
            task_type=task_type, output_dimensionality=self._dimensions
        )
        async with self._semaphore:
            try:
                response = await self._models.embed_content(
                    model=self._model,
                    # Un `Content` por texto = un embedding por texto, en el mismo orden.
                    contents=[types.Content(parts=[types.Part(text=text)]) for text in texts],
                    config=config,
                )
            except SDK_ERRORS as exc:
                raise to_llm_error(exc) from exc
        embeddings = response.embeddings or []
        if len(embeddings) != len(texts):
            raise LLMError(
                f"Gemini devolvió {len(embeddings)} embeddings para {len(texts)} textos."
            )
        vectors = []
        for embedding in embeddings:
            values = embedding.values or []
            if len(values) != self._dimensions:
                raise LLMError(
                    f"Embedding de {len(values)} dimensiones (se pidieron {self._dimensions})."
                )
            vectors.append(normalize(values))
        return vectors
