"""Embeddings en la nube: Azure OpenAI (text-embedding-3-small) y Bedrock (Titan Text v2).

Ambos se piden a 1024 dimensiones: la misma tabla/índice sirve para las dos nubes y se pueden
comparar sin reindexar el esquema. Los vectores llegan normalizados (similitud coseno).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Self

import openai
from openai import AsyncOpenAI

from headache_core.adapters.azure_openai import create_client, to_llm_error
from headache_core.config import AzureOpenAISettings, BedrockSettings

if TYPE_CHECKING:
    from mypy_boto3_bedrock_runtime import BedrockRuntimeClient

DIMENSIONS = 1024


class AzureOpenAIEmbedding:
    # Límite de la API por petición: 2048 entradas.
    MAX_BATCH = 2048

    def __init__(self, client: AsyncOpenAI, deployment: str, dimensions: int = DIMENSIONS) -> None:
        self._client = client
        self._deployment = deployment
        self._dimensions = dimensions

    @classmethod
    def from_settings(cls, settings: AzureOpenAISettings, dimensions: int = DIMENSIONS) -> Self:
        return cls(create_client(settings), settings.embedding_deployment, dimensions)

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.MAX_BATCH):
            try:
                response = await self._client.embeddings.create(
                    model=self._deployment,
                    input=list(texts[start : start + self.MAX_BATCH]),
                    # text-embedding-3-* admite recortar dimensiones (Matryoshka).
                    dimensions=self._dimensions,
                )
            except openai.OpenAIError as exc:
                raise to_llm_error(exc) from exc
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_texts([text]))[0]


class BedrockTitanEmbedding:
    """Titan v2 no admite lotes en InvokeModel: una llamada por texto, en paralelo acotado."""

    def __init__(
        self,
        client: BedrockRuntimeClient,
        model_id: str,
        dimensions: int = DIMENSIONS,
        *,
        concurrency: int = 8,
    ) -> None:
        self._client = client
        self._model_id = model_id
        self._dimensions = dimensions
        self._semaphore = asyncio.Semaphore(concurrency)

    @classmethod
    def from_settings(cls, settings: BedrockSettings, dimensions: int = DIMENSIONS) -> Self:
        import boto3

        session = boto3.Session(profile_name=settings.profile or None, region_name=settings.region)
        return cls(session.client("bedrock-runtime"), settings.embedding_model_id, dimensions)

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return list(await asyncio.gather(*(self._embed(text) for text in texts)))

    async def embed_query(self, text: str) -> list[float]:
        return await self._embed(text)

    async def _embed(self, text: str) -> list[float]:
        body = json.dumps({"inputText": text, "dimensions": self._dimensions, "normalize": True})
        async with self._semaphore:
            response = await asyncio.to_thread(
                self._client.invoke_model,
                modelId=self._model_id,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
        payload = json.loads(response["body"].read())
        return [float(v) for v in payload["embedding"]]
