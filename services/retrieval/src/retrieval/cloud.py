"""Composicion cloud explicita del servicio de retrieval; ninguna pieza se crea por defecto.

Construir un cliente no llama a la red: el coste aparece al consultar. Cada pieza exige su
configuracion y falla al arrancar si falta, en lugar de degradar en silencio a local.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from headache_core.adapters.postgres import EMBEDDING_DIMENSIONS
from headache_core.config import Settings
from headache_core.domain.retrieval import AccessScope
from headache_core.ports.llm import LLMPort
from headache_core.ports.retrieval import ManagedKnowledgePort, RerankerPort, RetrievalPort
from headache_core.ports.vector_store import EmbeddingPort
from retrieval.settings import RetrievalSettings

if TYPE_CHECKING:
    from azure.core.credentials import AzureKeyCredential
    from azure.core.credentials_async import AsyncTokenCredential

AI_FOUNDRY_SCOPE = "https://ai.azure.com/.default"


@dataclass
class CloudParts:
    backend: RetrievalPort | None = None
    reranker: RerankerPort | None = None
    llm: LLMPort | None = None
    managed: ManagedKnowledgePort | None = None
    components: dict[str, str] = field(default_factory=dict)
    closers: list[Callable[[], Awaitable[object]]] = field(default_factory=list)

    async def aclose(self) -> None:
        for close in reversed(self.closers):
            await close()


def _require(value: str, variable: str) -> str:
    if not value:
        raise ValueError(f"Falta {variable}")
    return value


def build_embeddings(config: RetrievalSettings, settings: Settings) -> EmbeddingPort:
    if config.embeddings == "azure":
        from headache_core.adapters.cloud_embeddings import AzureOpenAIEmbedding

        _require(settings.azure_openai.endpoint, "AZURE_OPENAI__ENDPOINT")
        return AzureOpenAIEmbedding.from_settings(settings.azure_openai, EMBEDDING_DIMENSIONS)
    from headache_core.adapters.cloud_embeddings import BedrockTitanEmbedding

    return BedrockTitanEmbedding.from_settings(settings.bedrock, EMBEDDING_DIMENSIONS)


def _search_credential(
    settings: Settings, parts: CloudParts
) -> AzureKeyCredential | AsyncTokenCredential:
    _require(settings.azure_search.endpoint, "AZURE_SEARCH__ENDPOINT")
    if settings.azure_search.api_key is not None:
        from azure.core.credentials import AzureKeyCredential

        return AzureKeyCredential(settings.azure_search.api_key.get_secret_value())
    from azure.identity.aio import DefaultAzureCredential

    credential = DefaultAzureCredential()
    parts.closers.append(credential.close)
    return credential


def _bedrock_runtime(region: str, profile: str | None) -> Any:
    import boto3

    session = boto3.Session(profile_name=profile or None, region_name=region)
    return session.client("bedrock-agent-runtime")


def _foundry_iq(settings: Settings, parts: CloudParts) -> ManagedKnowledgePort:
    from azure.search.documents.knowledgebases.aio import KnowledgeBaseRetrievalClient

    from headache_core.adapters.foundry_iq import FoundryIQKnowledge

    foundry = settings.foundry_iq
    retrieval = KnowledgeBaseRetrievalClient(
        settings.azure_search.endpoint,
        _search_credential(settings, parts),
        knowledge_base_name=foundry.knowledge_base,
    )
    parts.closers.append(retrieval.close)
    responses = None
    if foundry.project_endpoint:
        from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
        from openai import AsyncOpenAI

        credential = DefaultAzureCredential()
        parts.closers.append(credential.close)
        responses = AsyncOpenAI(
            base_url=f"{foundry.project_endpoint.rstrip('/')}/openai/v1",
            api_key=get_bearer_token_provider(credential, AI_FOUNDRY_SCOPE),
        )
        parts.closers.append(responses.close)
    return FoundryIQKnowledge(
        retrieval,
        knowledge_source=foundry.knowledge_source,
        responses=responses,
        agent_name=foundry.agent_name if responses else None,
        agent_audience=AccessScope(foundry.audience) if responses else None,
    )


async def build_cloud_parts(config: RetrievalSettings, settings: Settings) -> CloudParts:
    parts = CloudParts()
    try:
        _compose(config, settings, parts)
    except BaseException:
        await parts.aclose()
        raise
    return parts


def _compose(config: RetrievalSettings, settings: Settings, parts: CloudParts) -> None:
    if config.backend == "azure_search":
        from azure.search.documents.aio import SearchClient

        from headache_core.adapters.azure_retrieval import AzureSearchRetrieval

        client = SearchClient(
            settings.azure_search.endpoint,
            settings.azure_search.index_name,
            _search_credential(settings, parts),
        )
        parts.closers.append(client.close)
        parts.backend = AzureSearchRetrieval(
            client, embeddings=build_embeddings(config, settings), semantic=config.semantic_ranker
        )
        ranker = "+semantic-ranker" if config.semantic_ranker else ""
        parts.components["backend"] = f"azure_search:{config.embeddings}{ranker}"
    if config.reranker == "bedrock":
        from headache_core.adapters.bedrock_retrieval import BedrockReranker

        arn = _require(settings.bedrock.rerank_model_arn, "BEDROCK__RERANK_MODEL_ARN")
        client = _bedrock_runtime(settings.bedrock.region, settings.bedrock.profile)
        parts.reranker = BedrockReranker(client, model_arn=arn)
        parts.components["reranker"] = "bedrock-rerank"
    if config.llm == "gateway":
        from headache_core.adapters.llm_factory import build_gateway

        parts.llm = build_gateway(settings)
        parts.components["llm"] = f"gateway:{settings.llm_gateway.engine}:{settings.llm_provider}"
    if config.managed == "bedrock_kb":
        from headache_core.adapters.bedrock_retrieval import BedrockKnowledgeBase

        kb = settings.bedrock_kb
        parts.managed = BedrockKnowledgeBase(
            _bedrock_runtime(kb.region, kb.profile),
            knowledge_base_id=_require(kb.knowledge_base_id, "BEDROCK_KB__KNOWLEDGE_BASE_ID"),
            model_arn=kb.generation_model_arn or None,
        )
        parts.components["managed"] = "bedrock_kb"
    elif config.managed == "foundry_iq":
        parts.managed = _foundry_iq(settings, parts)
        parts.components["managed"] = "foundry_iq"
