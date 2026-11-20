"""Composicion cloud explicita del servicio de retrieval; ninguna pieza se crea por defecto.

Construir un cliente no llama a la red: el coste aparece al consultar. Cada pieza exige su
configuracion y falla al arrancar si falta, en lugar de degradar en silencio a local.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from genaipocs_core.adapters.postgres import EMBEDDING_DIMENSIONS
from genaipocs_core.config import Settings
from genaipocs_core.domain.retrieval import AccessScope
from genaipocs_core.ports.llm import LLMPort
from genaipocs_core.ports.retrieval import ManagedKnowledgePort, RerankerPort, RetrievalPort
from genaipocs_core.ports.vector_store import EmbeddingPort
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
        from genaipocs_core.adapters.cloud_embeddings import AzureOpenAIEmbedding

        _require(settings.azure_openai.endpoint, "AZURE_OPENAI__ENDPOINT")
        return AzureOpenAIEmbedding.from_settings(settings.azure_openai, EMBEDDING_DIMENSIONS)
    if config.embeddings == "gcp":
        from genaipocs_core.adapters.gemini_embeddings import GeminiEmbedding

        if settings.gemini.enterprise:
            _require(settings.gcp.project, "GCP__PROJECT")
        # `embed_query` usa `RETRIEVAL_QUERY`; la ingesta, `RETRIEVAL_DOCUMENT` (3.G2).
        return GeminiEmbedding.from_settings(settings.gcp, settings.gemini, EMBEDDING_DIMENSIONS)
    from genaipocs_core.adapters.cloud_embeddings import BedrockTitanEmbedding

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

    from genaipocs_core.adapters.foundry_iq import FoundryIQKnowledge

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


def _agent_retrieval(
    config: RetrievalSettings, settings: Settings, parts: CloudParts
) -> RetrievalPort:
    from google.cloud.vectorsearch_v1 import (
        DataObjectSearchServiceAsyncClient,
        DataObjectServiceAsyncClient,
    )

    from genaipocs_core.adapters.gcp_auth import gcp_credentials
    from genaipocs_core.adapters.gcp_retrieval import AgentRetrievalSearch

    gcp, vector_search = settings.gcp, settings.gcp_vector_search
    project = _require(gcp.project, "GCP__PROJECT")
    embeddings = build_embeddings(config, settings)
    location = vector_search.location or gcp.region
    credentials = gcp_credentials(gcp)
    backend = AgentRetrievalSearch(
        DataObjectSearchServiceAsyncClient(credentials=credentials),
        DataObjectServiceAsyncClient(credentials=credentials),
        collection=(
            f"projects/{project}/locations/{location}/collections/{vector_search.collection}"
        ),
        embeddings=embeddings,
        semantic=config.semantic_ranker,
    )
    parts.closers.append(backend.close)
    return backend


def _rag_engine(settings: Settings) -> ManagedKnowledgePort:
    import agentplatform

    from genaipocs_core.adapters.gcp_auth import gcp_credentials
    from genaipocs_core.adapters.gcp_rag_retrieval import RagEngineKnowledge
    from genaipocs_core.adapters.gemini import gemini_client

    gcp, rag = settings.gcp, settings.gcp_rag_engine
    project = _require(gcp.project, "GCP__PROJECT")
    _require(rag.bucket, "GCP_RAG_ENGINE__BUCKET")
    _require(rag.public_corpus, "GCP_RAG_ENGINE__PUBLIC_CORPUS")
    _require(rag.internal_corpus, "GCP_RAG_ENGINE__INTERNAL_CORPUS")
    client = agentplatform.Client(
        project=project, location=gcp.region, credentials=gcp_credentials(gcp)
    )
    models = None
    if rag.generation_model:
        # La generación con la herramienta de RAG Engine va a la región del corpus, no al
        # endpoint `global` del chat.
        location = rag.generation_location or gcp.region
        gemini = settings.gemini.model_copy(update={"enterprise": True, "location": location})
        models = gemini_client(gcp, gemini).aio.models
    return RagEngineKnowledge(
        client.aio.rag,
        bucket=rag.bucket,
        corpora=rag.corpora(),
        models=models,
        generation_model=rag.generation_model,
        rerank_model=rag.rerank_model,
    )


def _agent_search(settings: Settings, parts: CloudParts) -> ManagedKnowledgePort:
    from google.cloud.discoveryengine_v1 import (
        ConversationalSearchServiceAsyncClient,
        SearchServiceAsyncClient,
    )

    from genaipocs_core.adapters.gcp_agent_search import (
        AgentSearchKnowledge,
        discovery_engine_options,
    )
    from genaipocs_core.adapters.gcp_auth import gcp_credentials

    gcp, search = settings.gcp, settings.gcp_agent_search
    project = _require(gcp.project, "GCP__PROJECT")
    bucket = _require(
        search.bucket or settings.gcp_rag_engine.bucket,
        "GCP_AGENT_SEARCH__BUCKET o GCP_RAG_ENGINE__BUCKET",
    )
    credentials = gcp_credentials(gcp)
    options = discovery_engine_options(search.location)
    search_client = SearchServiceAsyncClient(credentials=credentials, client_options=options)
    parts.closers.append(search_client.transport.close)
    answer_client = ConversationalSearchServiceAsyncClient(
        credentials=credentials, client_options=options
    )
    parts.closers.append(answer_client.transport.close)
    return AgentSearchKnowledge(
        search=search_client,
        answers=answer_client,
        serving_config=(
            f"projects/{project}/locations/{search.location}/collections/default_collection"
            f"/engines/{search.engine}/servingConfigs/default_search"
        ),
        bucket=bucket,
        prefix=search.prefix,
        answer_model=search.answer_model,
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

        from genaipocs_core.adapters.azure_retrieval import AzureSearchRetrieval

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
    elif config.backend == "gcp_vector_search":
        parts.backend = _agent_retrieval(config, settings, parts)
        ranker = "+vertex-ranker" if config.semantic_ranker else ""
        parts.components["backend"] = f"gcp_vector_search:{config.embeddings}{ranker}"
    if config.reranker == "gcp":
        from google.cloud.discoveryengine_v1 import RankServiceAsyncClient

        from genaipocs_core.adapters.gcp_auth import gcp_credentials
        from genaipocs_core.adapters.gcp_retrieval import VertexRankingReranker

        ranking = settings.gcp_ranking
        ranking_client = RankServiceAsyncClient(credentials=gcp_credentials(settings.gcp))
        reranker = VertexRankingReranker(
            ranking_client,
            ranking_config=RankServiceAsyncClient.ranking_config_path(
                _require(settings.gcp.project, "GCP__PROJECT"),
                ranking.location,
                ranking.ranking_config,
            ),
            model=ranking.model,
        )
        parts.closers.append(reranker.close)
        parts.reranker = reranker
        parts.components["reranker"] = f"gcp-ranking:{ranking.model}"
    elif config.reranker == "bedrock":
        from genaipocs_core.adapters.bedrock_retrieval import BedrockReranker

        arn = _require(settings.bedrock.rerank_model_arn, "BEDROCK__RERANK_MODEL_ARN")
        client = _bedrock_runtime(settings.bedrock.region, settings.bedrock.profile)
        parts.reranker = BedrockReranker(client, model_arn=arn)
        parts.components["reranker"] = "bedrock-rerank"
    if config.llm == "gateway":
        from genaipocs_core.adapters.llm_factory import build_gateway

        parts.llm = build_gateway(settings)
        parts.components["llm"] = f"gateway:{settings.llm_gateway.engine}:{settings.llm_provider}"
    if config.managed == "bedrock_kb":
        from genaipocs_core.adapters.bedrock_retrieval import BedrockKnowledgeBase

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
    elif config.managed == "rag_engine":
        parts.managed = _rag_engine(settings)
        model = settings.gcp_rag_engine.generation_model
        parts.components["managed"] = f"rag_engine:{model}" if model else "rag_engine"
    elif config.managed == "agent_search":
        parts.managed = _agent_search(settings, parts)
        parts.components["managed"] = f"agent_search:{settings.gcp_agent_search.answer_model}"
