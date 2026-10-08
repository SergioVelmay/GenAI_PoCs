"""Bedrock Rerank (B) y Knowledge Bases (A), siempre con cliente SDK inyectado."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from pydantic import TypeAdapter

from headache_core.application.retrieval import (
    FILTER_KEYS,
    InvalidCitationError,
    RetrievalUnavailableError,
)
from headache_core.domain.retrieval import AccessScope
from headache_core.ports.retrieval import (
    ManagedCitation,
    ManagedRagResponse,
    ManagedReference,
    ManagedSearchMode,
)
from headache_core.ports.vector_store import MetadataValue, SearchResult

if TYPE_CHECKING:
    from mypy_boto3_bedrock_agent_runtime import AgentsforBedrockRuntimeClient
    from mypy_boto3_bedrock_agent_runtime.type_defs import (
        KnowledgeBaseRetrievalConfigurationTypeDef,
        RetrievalFilterTypeDef,
    )

METADATA = TypeAdapter(dict[str, MetadataValue])


class BedrockReranker:
    """Solo recibe candidatos previamente autorizados por RetrievalService."""

    def __init__(self, client: AgentsforBedrockRuntimeClient, *, model_arn: str) -> None:
        self._client = client
        self._model_arn = model_arn

    async def rerank(
        self,
        query: str,
        candidates: Sequence[SearchResult],
        *,
        top_k: int,
    ) -> list[SearchResult]:
        if not query.strip() or top_k < 1 or len(candidates) > 1000:
            raise ValueError("Consulta, top_k o numero de candidatos invalido")
        if not candidates:
            return []
        response = await asyncio.to_thread(
            self._client.rerank,
            queries=[{"type": "TEXT", "textQuery": {"text": query}}],
            sources=[
                {
                    "type": "INLINE",
                    "inlineDocumentSource": {
                        "type": "TEXT",
                        "textDocument": {"text": item.record.text},
                    },
                }
                for item in candidates
            ],
            rerankingConfiguration={
                "type": "BEDROCK_RERANKING_MODEL",
                "bedrockRerankingConfiguration": {
                    "modelConfiguration": {"modelArn": self._model_arn},
                    "numberOfResults": min(top_k, len(candidates)),
                },
            },
        )
        if response.get("nextToken"):
            raise RetrievalUnavailableError(
                "Respuesta Rerank paginada: no se admite ranking incompleto"
            )
        results: list[SearchResult] = []
        seen: set[int] = set()
        for result in response.get("results", []):
            index, score = result["index"], result["relevanceScore"]
            if not 0 <= index < len(candidates) or index in seen or not math.isfinite(score):
                raise ValueError("Indice o score invalido en Bedrock Rerank")
            seen.add(index)
            results.append(SearchResult(candidates[index].record, score))
        return results[:top_k]


def knowledge_filter(
    filters: Mapping[str, MetadataValue],
    scope: AccessScope,
) -> RetrievalFilterTypeDef:
    if filters.keys() - FILTER_KEYS or not scope.allowed_acls:
        raise ValueError("Filtros o ACL invalidos")
    acl: dict[str, Any] = {"equals": {"key": "acl", "value": scope.allowed_acls[0]}}
    if len(scope.allowed_acls) > 1:
        acl = {
            "orAll": [{"equals": {"key": "acl", "value": value}} for value in scope.allowed_acls]
        }
    clauses: list[dict[str, Any]] = [acl]
    clauses.extend({"equals": {"key": key, "value": value}} for key, value in filters.items())
    return cast("RetrievalFilterTypeDef", {"andAll": clauses} if len(clauses) > 1 else acl)


class BedrockKnowledgeBase:
    def __init__(
        self,
        client: AgentsforBedrockRuntimeClient,
        *,
        knowledge_base_id: str,
        model_arn: str | None = None,
        supports_hybrid: bool = False,
    ) -> None:
        self._client = client
        self._kb = knowledge_base_id
        self._model_arn = model_arn
        self._supports_hybrid = supports_hybrid

    def _configuration(
        self,
        query: str,
        top_k: int,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
        mode: ManagedSearchMode,
    ) -> KnowledgeBaseRetrievalConfigurationTypeDef:
        if not query.strip() or not 1 <= top_k <= 100:
            raise ValueError("Consulta o top_k invalido")
        if mode == "HYBRID" and not self._supports_hybrid:
            raise RetrievalUnavailableError("El store de esta KB no declara soporte HYBRID")
        return {
            "vectorSearchConfiguration": {
                "numberOfResults": top_k,
                "overrideSearchType": mode,
                "filter": knowledge_filter(filters, scope),
            }
        }

    def _reference(
        self,
        reference: Mapping[str, Any],
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
    ) -> ManagedReference:
        metadata = METADATA.validate_python(
            {
                key: value
                for key, value in reference.get("metadata", {}).items()
                if key in FILTER_KEYS | {"acl", "title"}
            }
        )
        if not scope.permits(metadata) or any(
            metadata.get(key) != value for key, value in filters.items()
        ):
            raise InvalidCitationError("Referencia Bedrock fuera del alcance autorizado")
        text = reference.get("content", {}).get("text")
        location = reference.get("location", {})
        uri = location.get("s3Location", {}).get("uri")
        document_id = metadata.get("document_id")
        if (
            not isinstance(text, str)
            or not text
            or not isinstance(document_id, str)
            or not document_id
        ):
            raise InvalidCitationError("Referencia Bedrock sin texto o document_id")
        if location.get("type") != "S3" or not isinstance(uri, str) or not uri.startswith("s3://"):
            raise InvalidCitationError("Esta KB solo admite fuentes S3 verificables")
        identity = json.dumps([self._kb, document_id, uri, text], ensure_ascii=False).encode()
        return ManagedReference(
            hashlib.sha256(identity).hexdigest(),
            document_id,
            str(metadata.get("title", document_id)),
            text,
            uri,
        )

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
        mode: ManagedSearchMode = "SEMANTIC",
    ) -> list[ManagedReference]:
        if not scope.allowed_acls:
            return []
        configuration = self._configuration(query, top_k, filters, scope, mode)
        response = await asyncio.to_thread(
            self._client.retrieve,
            knowledgeBaseId=self._kb,
            retrievalQuery={"text": query},
            retrievalConfiguration=configuration,
        )
        if response.get("guardrailAction") == "INTERVENED":
            return []
        if response.get("nextToken"):
            raise RetrievalUnavailableError("Retrieve paginado: no se admite snapshot incompleto")
        references = [
            self._reference(item, filters, scope) for item in response.get("retrievalResults", [])
        ]
        unique = {reference.source_id: reference for reference in references}
        return list(unique.values())[:top_k]

    async def answer(
        self,
        query: str,
        *,
        top_k: int,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
        mode: ManagedSearchMode = "SEMANTIC",
    ) -> ManagedRagResponse:
        if not scope.allowed_acls:
            return ManagedRagResponse("", (), True)
        if self._model_arn is None:
            raise RetrievalUnavailableError(
                "La generacion gestionada requiere un model ARN explicito"
            )
        configuration = self._configuration(query, top_k, filters, scope, mode)
        response = await asyncio.to_thread(
            self._client.retrieve_and_generate,
            input={"text": query},
            retrieveAndGenerateConfiguration={
                "type": "KNOWLEDGE_BASE",
                "knowledgeBaseConfiguration": {
                    "knowledgeBaseId": self._kb,
                    "modelArn": self._model_arn,
                    "retrievalConfiguration": configuration,
                },
            },
        )
        if response.get("guardrailAction") == "INTERVENED" or not response.get("citations"):
            return ManagedRagResponse("", (), True)
        answer = response["output"]["text"]
        citations: list[ManagedCitation] = []
        covered: set[int] = set()
        for citation in response["citations"]:
            part = citation.get("generatedResponsePart", {}).get("textResponsePart", {})
            text = part.get("text", "")
            span = part.get("span", {})
            start, provider_end = span.get("start", -1), span.get("end", -1)
            end = start + len(text)
            if (
                not text
                or start < 0
                or end > len(answer)
                or provider_end not in (end, end - 1)
                or answer[start:end] != text
            ):
                raise InvalidCitationError("Span Bedrock no corresponde al texto generado")
            references = tuple(
                self._reference(item, filters, scope)
                for item in citation.get("retrievedReferences", [])
            )
            if not references:
                raise InvalidCitationError("Cita Bedrock sin referencias")
            citations.append(ManagedCitation(text, start, end, references))
            covered.update(range(start, end))
        if any(
            index not in covered and not character.isspace()
            for index, character in enumerate(answer)
        ):
            raise InvalidCitationError("Respuesta Bedrock contiene texto sin cita")
        return ManagedRagResponse(answer, tuple(citations), False)
