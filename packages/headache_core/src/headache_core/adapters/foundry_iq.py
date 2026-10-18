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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

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
    import httpx
    from azure.search.documents.knowledgebases.aio import KnowledgeBaseRetrievalClient
    from openai import AsyncOpenAI
    from openai.types.responses import Response

KB_TOOL = "knowledge_base_retrieve"
# `baseFilter` en la knowledge source solo existe en preview.
PREVIEW_API_VERSION = "2026-08-01-preview"
# El endpoint MCP de la knowledge base solo existe en versiones preview: con la GA
# (2026-04-01, valida para gestionar y para `retrieve`) responde "api-version does not exist"
# y el agente falla al enumerar herramientas (validado en Azure, checkpoint 8→9).
MCP_API_VERSION = PREVIEW_API_VERSION
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


def knowledge_base_definition(
    name: str, *, knowledge_source: str, max_output_documents: int = 5
) -> dict[str, Any]:
    """Cuerpo REST (preview). Sin `models`: sin planificacion ni sintesis con LLM en la KB.

    El esfuerzo `minimal` y la salida extractiva se declaran explicitamente: el endpoint MCP
    (solo preview) aplica por defecto un esfuerzo que exige un modelo en la KB y falla con
    "A Knowledge Base model must be specified" (validado en Azure, checkpoint 8→9).
    """
    return {
        "name": name,
        "knowledgeSources": [{"name": knowledge_source}],
        "retrievalReasoningEffort": {"kind": "minimal"},
        "outputMode": "extractiveData",
        # Sin limite devolvia 30+ documentos por llamada: mas tokens y citas de relleno.
        "retrieveDefaults": {"maxOutputDocuments": max_output_documents},
    }


async def publish_agent(
    http: httpx.AsyncClient, project_endpoint: str, token: str, agent: Mapping[str, Any]
) -> str:
    """Crea el agente o, si ya existe (409), publica una versión nueva. Devuelve la versión.

    Foundry Agent Service versiona los agentes: `POST /agents` no es idempotente.
    """
    base = f"{project_endpoint.rstrip('/')}/agents"
    headers = {"Authorization": f"Bearer {token}"}
    response = await http.post(f"{base}?api-version=v1", headers=headers, json=dict(agent))
    if response.status_code == 409:
        response = await http.post(
            f"{base}/{agent['name']}/versions?api-version=v1",
            headers=headers,
            json={"definition": agent["definition"]},
        )
    response.raise_for_status()
    body = response.json()
    version = body.get("version") or body.get("versions", {}).get("latest", {}).get("version")
    return str(version)


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


def _is_abstention(text: str) -> bool:
    # El modelo a veces anade puntuacion final: "I don't know." sigue siendo abstencion.
    return text.strip().rstrip(".!").casefold() == ABSTENTION.casefold()


def _has_words(text: str) -> bool:
    return any(character.isalnum() for character in text)


@dataclass(frozen=True, slots=True)
class McpDocument:
    """Un resultado de la llamada MCP del agente a la knowledge base."""

    title: str
    text: str
    chunk_id: str = ""
    document_id: str = ""
    acl: str | None = None


def _agent_documents(documents: list[Any]) -> dict[str, McpDocument]:
    """Formato real de Foundry Agent Service: `{"documents": [{"id", "content"}]}`.

    `content` es un JSON con los `sourceDataFields` de la knowledge source (chunk_id, text,
    title, metadata_json). La clave es la del indice: la misma que traen las anotaciones.
    """
    results: dict[str, McpDocument] = {}
    for document in documents:
        try:
            fields = json.loads(document["content"])
            metadata = json.loads(fields.get("metadata_json") or "{}")
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise InvalidCitationError("Documento MCP del agente invalido") from exc
        chunk_id, text = fields.get("chunk_id"), fields.get("text")
        if not isinstance(chunk_id, str) or not chunk_id or not isinstance(text, str) or not text:
            raise InvalidCitationError("Documento MCP del agente sin chunk_id o texto")
        acl = metadata.get("acl")
        results[document_key(chunk_id)] = McpDocument(
            str(fields.get("title", "")),
            text,
            chunk_id,
            str(metadata.get("document_id", "")),
            acl if isinstance(acl, str) else None,
        )
    return results


def _mcp_results(output: str) -> dict[str, McpDocument]:
    if not output.strip():
        return {}  # Sin resultados: el agente debe abstenerse.
    try:
        items = json.loads(output)
    except json.JSONDecodeError as exc:
        raise InvalidCitationError("Salida MCP de la knowledge base no es JSON") from exc
    if isinstance(items, dict) and isinstance(items.get("documents"), list):
        return _agent_documents(items["documents"])
    if not isinstance(items, list):
        raise InvalidCitationError("Salida MCP de la knowledge base inesperada")
    results: dict[str, McpDocument] = {}
    for item in items:
        text = item.get("text") if isinstance(item, dict) else None
        if not isinstance(text, str) or not text or "ref_id" not in item:
            raise InvalidCitationError("Resultado MCP sin ref_id o texto")
        results[str(item["ref_id"])] = McpDocument(str(item.get("title", "")), text)
    return results


def _citation_key(url: str) -> str:
    """Clave del documento en el indice a partir de la URL de una anotacion `url_citation`."""
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


def _reference(
    document: McpDocument, *, origin: str, audience: AccessScope | None
) -> ManagedReference:
    if not document.chunk_id:
        identity = json.dumps([origin, document.title, document.text], ensure_ascii=False)
        # Formato sin chunk_id ni document_id: identidad estable por contenido.
        source_id = hashlib.sha256(identity.encode()).hexdigest()
        return ManagedReference(source_id, "", document.title, document.text, origin)
    # La salida del agente trae la ACL: se vuelve a comprobar (sin ACL se deniega).
    if audience is not None and not audience.permits({"acl": document.acl}):
        raise InvalidCitationError("El agente cito un documento fuera de su audiencia")
    return ManagedReference(
        document.chunk_id, document.document_id, document.title, document.text, origin
    )


def parse_agent_answer(
    response: Response,
    *,
    server_label: str,
    origin: str,
    audience: AccessScope | None = None,
) -> ManagedRagResponse:
    calls: list[dict[str, McpDocument]] = []
    parts: list[str] = []
    # Anotaciones url_citation: posicion de la marca en el texto -> clave del documento.
    annotated: dict[int, str] = {}
    for item in response.output:
        if isinstance(item, McpCall):
            if item.server_label != server_label or item.name != KB_TOOL:
                raise InvalidCitationError("El agente invoco una herramienta no permitida")
            if item.error or item.status not in (None, "completed") or item.output is None:
                raise RetrievalUnavailableError("La llamada del agente a la knowledge base fallo")
            calls.append(_mcp_results(item.output))
        elif isinstance(item, ResponseOutputMessage):
            for part in item.content:
                if isinstance(part, ResponseOutputText):
                    offset = sum(len(p) for p in parts)
                    for note in part.annotations or []:
                        url = getattr(note, "url", None)
                        if note.type == "url_citation" and isinstance(url, str):
                            annotated[offset + note.start_index] = _citation_key(url)
                    parts.append(part.text)
    joined = "".join(parts)
    raw = joined.strip()
    lead = len(joined) - len(joined.lstrip())
    if not raw or _is_abstention(raw):
        return ManagedRagResponse("", (), True)
    if not calls:
        raise InvalidCitationError("El agente respondio sin consultar la knowledge base")
    if not any(calls) and not CITATION.search(raw):
        # La KB no devolvio nada: no hay respuesta fundamentada posible. Se descarta el texto
        # del modelo (p. ej. "No dispongo de informacion..." en vez de la frase pactada).
        return ManagedRagResponse("", (), True)
    by_key = {key: doc for call in calls for key, doc in call.items() if doc.chunk_id}

    answer = ""
    citations: list[ManagedCitation] = []
    position = 0
    for match in CITATION.finditer(raw):
        segment = raw[position : match.start()]
        position = match.end()
        key = annotated.get(match.start() + lead)
        if key is not None:
            # Formato real: la anotacion identifica el documento en el indice.
            if key not in by_key:
                raise InvalidCitationError("Cita del agente a un documento que no devolvio la KB")
            document = by_key[key]
        else:
            call, ref_id = int(match.group(1)), match.group(2)
            if call >= len(calls) or ref_id not in calls[call]:
                raise InvalidCitationError("Cita del agente a un resultado inexistente")
            document = calls[call][ref_id]
        reference = _reference(document, origin=origin, audience=audience)
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
            response,
            server_label=self._label,
            origin=f"foundry-iq://{self._source}",
            audience=self._audience,
        )
