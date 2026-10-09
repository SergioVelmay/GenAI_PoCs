"""Carril A en Azure: Foundry IQ (knowledge base de AI Search) + Foundry Agent Service.

Sustituye a Azure OpenAI On Your Data (retirada el 14-10-2026). Clientes inyectados: nada
se crea ni se invoca al importar el modulo ni al arrancar la API local.

- `retrieve`: API GA de la knowledge base. La ACL viaja como `filterAddOn` del servidor y
  se vuelve a comprobar en cada referencia (trae `chunk_id` y metadatos).
- `answer`: el agente invoca la herramienta MCP `knowledge_base_retrieve`. La app no puede
  filtrar esa llamada y la salida MCP solo trae los campos semanticos (titulo y texto),
  sin ACL: el aislamiento lo garantiza una knowledge source por audiencia con `baseFilter`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from azure.search.documents.knowledgebases.models import (
    KnowledgeBaseRetrievalRequest,
    KnowledgeBaseSearchIndexReference,
    KnowledgeRetrievalSemanticIntent,
    SearchIndexKnowledgeSourceParams,
)
from openai.types.responses import ResponseOutputMessage, ResponseOutputText
from openai.types.responses.response_output_item import McpCall

from headache_core.adapters.azure_retrieval import SOURCE_FIELDS, access_filter, to_record
from headache_core.adapters.azure_search import SEMANTIC_CONFIGURATION, document_key
from headache_core.application.retrieval import InvalidCitationError, RetrievalUnavailableError
from headache_core.domain.retrieval import AccessScope
from headache_core.ports.retrieval import (
    ManagedCitation,
    ManagedRagResponse,
    ManagedReference,
    ManagedSearchMode,
)
from headache_core.ports.vector_store import MetadataValue

if TYPE_CHECKING:
    from azure.search.documents.knowledgebases.aio import KnowledgeBaseRetrievalClient
    from openai import AsyncOpenAI
    from openai.types.responses import Response

KB_TOOL = "knowledge_base_retrieve"
# GA: retrieval minimal y extractivo; la knowledge base no invoca ningun LLM.
MCP_API_VERSION = "2026-04-01"
# `baseFilter` en la knowledge source solo existe en preview.
PREVIEW_API_VERSION = "2026-08-01-preview"
ABSTENTION = "I don't know"
CITATION = re.compile(r"【(\d+):(\d+)†([^】]*)】")
AGENT_INSTRUCTIONS = (
    "You must use the knowledge base tool to answer every question and never answer from "
    "your own knowledge. Cite every statement right after it as "
    "【message_idx:search_idx†source_name】. If the knowledge base doesn't contain the "
    f'answer, respond exactly with "{ABSTENTION}".'
)


def knowledge_source_definition(
    name: str, *, index_name: str, audience: AccessScope
) -> dict[str, Any]:
    """Cuerpo REST (preview) de la knowledge source sobre el indice nativo del carril B."""
    if not audience.allowed_acls:
        raise ValueError("Una knowledge source necesita al menos una ACL")
    return {
        "name": name,
        "kind": "searchIndex",
        "searchIndexParameters": {
            "searchIndexName": index_name,
            "semanticConfigurationName": SEMANTIC_CONFIGURATION,
            "sourceDataFields": [{"name": field} for field in SOURCE_FIELDS],
            "baseFilter": access_filter({}, audience),
        },
    }


def knowledge_base_definition(name: str, *, knowledge_source: str) -> dict[str, Any]:
    """Sin `models`: sin planificacion ni sintesis con LLM dentro de la knowledge base."""
    return {"name": name, "knowledgeSources": [{"name": knowledge_source}]}


def mcp_endpoint(search_endpoint: str, knowledge_base: str) -> str:
    return (
        f"{search_endpoint.rstrip('/')}/knowledgebases/{knowledge_base}/mcp"
        f"?api-version={MCP_API_VERSION}"
    )


def mcp_connection_definition(name: str, *, mcp_url: str) -> dict[str, Any]:
    """Conexion `RemoteTool` del proyecto (ARM): el agente usa su identidad gestionada."""
    return {
        "name": name,
        "type": "Microsoft.MachineLearningServices/workspaces/connections",
        "properties": {
            "authType": "ProjectManagedIdentity",
            "category": "RemoteTool",
            "target": mcp_url,
            "isSharedToAll": True,
            "audience": "https://search.azure.com/",
            "metadata": {"ApiType": "Azure"},
        },
    }


def agent_definition(
    name: str, *, model: str, mcp_url: str, connection: str, server_label: str = "knowledge-base"
) -> dict[str, Any]:
    """Cuerpo REST del agente de Foundry Agent Service con la herramienta MCP de la KB."""
    return {
        "name": name,
        "definition": {
            "kind": "prompt",
            "model": model,
            "instructions": AGENT_INSTRUCTIONS,
            "tools": [
                {
                    "type": "mcp",
                    "server_label": server_label,
                    "server_url": mcp_url,
                    "require_approval": "never",
                    "allowed_tools": [KB_TOOL],
                    "project_connection_id": connection,
                }
            ],
        },
    }


def _has_words(text: str) -> bool:
    return any(character.isalnum() for character in text)


def _mcp_results(output: str) -> dict[str, tuple[str, str]]:
    try:
        items = json.loads(output)
    except json.JSONDecodeError as exc:
        raise InvalidCitationError("Salida MCP de la knowledge base no es JSON") from exc
    if not isinstance(items, list):
        raise InvalidCitationError("Salida MCP de la knowledge base inesperada")
    results: dict[str, tuple[str, str]] = {}
    for item in items:
        text = item.get("text") if isinstance(item, dict) else None
        if not isinstance(text, str) or not text or "ref_id" not in item:
            raise InvalidCitationError("Resultado MCP sin ref_id o texto")
        results[str(item["ref_id"])] = (str(item.get("title", "")), text)
    return results


def parse_agent_answer(response: Response, *, server_label: str, origin: str) -> ManagedRagResponse:
    calls: list[dict[str, tuple[str, str]]] = []
    parts: list[str] = []
    for item in response.output:
        if isinstance(item, McpCall):
            if item.server_label != server_label or item.name != KB_TOOL:
                raise InvalidCitationError("El agente invoco una herramienta no permitida")
            if item.error or item.status not in (None, "completed") or item.output is None:
                raise RetrievalUnavailableError("La llamada del agente a la knowledge base fallo")
            calls.append(_mcp_results(item.output))
        elif isinstance(item, ResponseOutputMessage):
            parts.extend(part.text for part in item.content if isinstance(part, ResponseOutputText))
    raw = "".join(parts).strip()
    if not raw or raw == ABSTENTION:
        return ManagedRagResponse("", (), True)
    if not calls:
        raise InvalidCitationError("El agente respondio sin consultar la knowledge base")

    answer = ""
    citations: list[ManagedCitation] = []
    position = 0
    for match in CITATION.finditer(raw):
        segment = raw[position : match.start()]
        position = match.end()
        call, ref_id = int(match.group(1)), match.group(2)
        if call >= len(calls) or ref_id not in calls[call]:
            raise InvalidCitationError("Cita del agente a un resultado inexistente")
        title, text = calls[call][ref_id]
        identity = json.dumps([origin, title, text], ensure_ascii=False).encode()
        # La salida MCP no trae chunk_id ni document_id: identidad estable por contenido.
        reference = ManagedReference(hashlib.sha256(identity).hexdigest(), "", title, text, origin)
        if _has_words(segment):
            stripped = segment.strip()
            start = len(answer) + len(segment) - len(segment.lstrip())
            citations.append(ManagedCitation(stripped, start, start + len(stripped), (reference,)))
        elif citations:
            last = citations[-1]
            if reference not in last.references:
                citations[-1] = ManagedCitation(
                    last.text, last.start, last.end, (*last.references, reference)
                )
        else:
            raise InvalidCitationError("Marca de cita sin texto previo")
        answer += segment
    tail = raw[position:]
    if _has_words(tail):
        raise InvalidCitationError("Respuesta del agente contiene texto sin cita")
    return ManagedRagResponse((answer + tail).rstrip(), tuple(citations), False)


class FoundryIQKnowledge:
    def __init__(
        self,
        retrieval: KnowledgeBaseRetrievalClient,
        *,
        knowledge_source: str,
        responses: AsyncOpenAI | None = None,
        agent_name: str | None = None,
        agent_audience: AccessScope | None = None,
        server_label: str = "knowledge-base",
        supports_hybrid: bool = False,
    ) -> None:
        self._retrieval = retrieval
        self._source = knowledge_source
        self._responses = responses
        self._agent = agent_name
        self._audience = agent_audience
        self._label = server_label
        self._supports_hybrid = supports_hybrid

    def _check(self, query: str, top_k: int, mode: ManagedSearchMode) -> None:
        if not query.strip() or not 1 <= top_k <= 100:
            raise ValueError("Consulta o top_k invalido")
        if mode == "HYBRID" and not self._supports_hybrid:
            raise RetrievalUnavailableError("La knowledge source no declara vectorizador (HYBRID)")

    def _reference(
        self,
        item: object,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
    ) -> ManagedReference:
        if not isinstance(item, KnowledgeBaseSearchIndexReference) or not item.source_data:
            raise InvalidCitationError("Referencia Foundry IQ sin datos del indice")
        try:
            record = to_record(item.source_data)
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidCitationError("Referencia Foundry IQ con datos invalidos") from exc
        if item.doc_key != document_key(record.id):
            raise InvalidCitationError("docKey de Foundry IQ no corresponde al chunk")
        if not scope.permits(record.metadata) or any(
            record.metadata.get(key) != value for key, value in filters.items()
        ):
            raise InvalidCitationError("Referencia Foundry IQ fuera del alcance autorizado")
        document_id = record.metadata.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            raise InvalidCitationError("Referencia Foundry IQ sin document_id")
        title = str(record.metadata.get("title", document_id))
        uri = f"foundry-iq://{self._source}/{record.id}"
        return ManagedReference(record.id, document_id, title, record.text, uri)

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
        self._check(query, top_k, mode)
        request = KnowledgeBaseRetrievalRequest(
            intents=[KnowledgeRetrievalSemanticIntent(search=query)],
            include_activity=True,
            knowledge_source_params=[
                SearchIndexKnowledgeSourceParams(
                    knowledge_source_name=self._source,
                    include_references=True,
                    include_reference_source_data=True,
                    filter_add_on=access_filter(filters, scope),
                )
            ],
        )
        response = await self._retrieval.retrieve(request)
        for activity in response.activity or []:
            if activity.error is not None:
                raise RetrievalUnavailableError("Foundry IQ devolvio un resultado parcial (206)")
        references: dict[str, ManagedReference] = {}
        for item in response.references or []:
            reference = self._reference(item, filters, scope)
            references.setdefault(reference.source_id, reference)
        return list(references.values())[:top_k]

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
        if self._responses is None or self._agent is None or self._audience is None:
            raise RetrievalUnavailableError("Foundry Agent Service no esta configurado")
        self._check(query, top_k, mode)
        if set(scope.allowed_acls) != set(self._audience.allowed_acls):
            raise RetrievalUnavailableError("El agente esta vinculado a otra audiencia")
        if filters:
            raise RetrievalUnavailableError(
                "El agente no admite filtros por peticion: usa una knowledge source dedicada"
            )
        response = await self._responses.responses.create(
            input=query,
            extra_body={"agent_reference": {"name": self._agent, "type": "agent_reference"}},
        )
        return parse_agent_answer(
            response, server_label=self._label, origin=f"foundry-iq://{self._source}"
        )
