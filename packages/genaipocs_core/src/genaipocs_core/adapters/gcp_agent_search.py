"""Carril A de GCP con Agent Search (antes Vertex AI Search; API Discovery Engine), fase 4.G3.

Equivale a Foundry IQ (Azure) y a Bedrock KB (AWS): un servicio gestionado que trocea, indexa,
busca y responde con citas. Dos piezas con clientes inyectados:

- `AgentSearchIngestion`: sube cada documento como HTML a `gs://<bucket>/agent-search/<acl>/...`,
  escribe un `metadata.jsonl` (id, `structData` con la ACL y los filtros, URI del contenido) y
  lo importa con reconciliación **FULL**: lo que ya no está en el corpus se borra del data store,
  sin poda propia (en RAG Engine hizo falta `prune`).
- `AgentSearchKnowledge` (`ManagedKnowledgePort`): `retrieve` con `search` en modo `CHUNKS` y
  `answer` con `answer_query` (respuesta generada con citas).

Por qué HTML: el troceo de Agent Search usa el *layout parser*, que admite HTML, PDF y Office pero
**no** TXT ni Markdown (el TXT va al parser digital, sin garantía de troceo).

La ACL, en dos capas (decisión de la 4.G): filtro del servidor sobre `structData.acl` (GA, a
diferencia del filtro de RAG Engine) y comprobación de cada fragmento por el prefijo de su URI y,
si el servicio lo devuelve, por su `structData`. Los filtros por petición viajan al servidor y se
comprueban en el `structData` devuelto; si no viene, no se pueden comprobar y se rechaza.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

from google.api_core.client_options import ClientOptions
from google.cloud.discoveryengine_v1 import (
    Answer,
    AnswerQueryRequest,
    AnswerQueryResponse,
    GcsSource,
    ImportDocumentsRequest,
    Query,
    SearchRequest,
)

from genaipocs_core.adapters.gcp_rag_engine import ACL_LEVELS, Bucket, document_acl
from genaipocs_core.adapters.gcp_rag_retrieval import utf8_span
from genaipocs_core.application.retrieval import (
    FILTER_KEYS,
    InvalidCitationError,
    RetrievalUnavailableError,
)
from genaipocs_core.config import Settings
from genaipocs_core.domain import KnowledgeDocument
from genaipocs_core.domain.retrieval import AccessScope
from genaipocs_core.ports.retrieval import (
    ManagedCitation,
    ManagedRagResponse,
    ManagedReference,
    ManagedSearchMode,
)
from genaipocs_core.ports.vector_store import MetadataValue

# IDs de documento de Discovery Engine: letras, dígitos, `-` y `_`, hasta 63 caracteres.
DOCUMENT_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}")
# Campos de `structData`: la ACL, los filtros del dominio y el título (el esquema de
# `agent_search.tf` los declara `indexable` para poder filtrar).
STRUCT_FIELDS = ("acl", *sorted(FILTER_KEYS), "title")
METADATA_FILE = "metadata.jsonl"


def discovery_engine_options(location: str) -> ClientOptions | None:
    """Agent Search vive en `global`, `us` o `eu`: fuera de `global`, su endpoint regional
    (sin él, la petición a un data store de `eu` falla)."""
    if location == "global":
        return None
    return ClientOptions(api_endpoint=f"{location}-discoveryengine.googleapis.com")


# ---------- Ingesta ----------


def render_html(document: KnowledgeDocument) -> str:
    """Markdown del corpus → HTML mínimo (títulos, listas y párrafos), escapado."""
    body: list[str] = []
    in_list = False
    for line in document.content.splitlines():
        text = line.strip()
        item = text.startswith(("- ", "* "))
        if in_list and not item:
            body.append("</ul>")
            in_list = False
        if not text:
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", text)
        if heading:
            level = len(heading.group(1))
            body.append(f"<h{level}>{html.escape(heading.group(2))}</h{level}>")
        elif item:
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{html.escape(text[2:])}</li>")
        else:
            body.append(f"<p>{html.escape(text)}</p>")
    if in_list:
        body.append("</ul>")
    lang, title = html.escape(document.language), html.escape(document.title)
    return (
        f'<!DOCTYPE html>\n<html lang="{lang}"><head><meta charset="utf-8">'
        f"<title>{title}</title></head>\n<body>\n" + "\n".join(body) + "\n</body></html>\n"
    )


def struct_data(document: KnowledgeDocument) -> dict[str, str]:
    """Metadatos del documento en el data store, siempre como texto (como en AI Search)."""
    values: dict[str, str] = {
        "acl": document_acl(document),
        "document_id": document.id,
        "doc_type": document.doc_type.value,
        "language": document.language,
        "title": document.title,
    }
    for key in FILTER_KEYS - values.keys():
        if document.metadata.get(key):
            values[key] = str(document.metadata[key])
    return dict(sorted(values.items()))


def object_name(document: KnowledgeDocument, prefix: str) -> str:
    """`<prefijo>/<acl>/<tipo>/<id>.html`: la ACL también queda en la ruta."""
    if not DOCUMENT_ID.fullmatch(document.id):
        raise ValueError(f"Id no admitido por Agent Search: {document.id}")
    acl = document_acl(document)
    return f"{prefix}/{acl}/{document.doc_type.value}/{document.id}.html"


def manifest(documents: Sequence[KnowledgeDocument], bucket: str, prefix: str) -> str:
    """`metadata.jsonl`: una línea por documento con su `structData` y la URI del HTML."""
    lines = [
        json.dumps(
            {
                "id": doc.id,
                "structData": struct_data(doc),
                "content": {
                    "mimeType": "text/html",
                    "uri": f"gs://{bucket}/{object_name(doc, prefix)}",
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for doc in sorted(documents, key=lambda doc: doc.id)
    ]
    return "\n".join(lines) + "\n"


def html_md5(document: KnowledgeDocument) -> str:
    return base64.b64encode(hashlib.md5(render_html(document).encode()).digest()).decode()


@dataclass(frozen=True, slots=True)
class ImportResult:
    uploaded: int
    succeeded: int
    failed: int
    pruned_objects: int
    errors: tuple[str, ...] = ()


class DocumentClient(Protocol):
    """Lo que la ingesta usa de `DocumentServiceAsyncClient` (permite un doble en los tests)."""

    async def import_documents(self, *, request: ImportDocumentsRequest) -> Any: ...


class AgentSearchIngestion:
    def __init__(
        self,
        *,
        documents: DocumentClient,
        bucket: Bucket,
        branch: str,
        prefix: str = "agent-search",
    ) -> None:
        self._documents = documents
        self._bucket = bucket
        # projects/{p}/locations/{l}/collections/default_collection/dataStores/{d}/branches/...
        self._branch = branch
        self._prefix = prefix.strip("/")

    async def upload(self, document: KnowledgeDocument) -> bool:
        """Sube el HTML solo si cambió (MD5, como en los otros carriles A)."""
        name = object_name(document, self._prefix)
        current = await asyncio.to_thread(self._bucket.get_blob, name)
        if current is not None and current.md5_hash == html_md5(document):
            return False
        blob = self._bucket.blob(name)
        await asyncio.to_thread(
            blob.upload_from_string,
            render_html(document).encode(),
            content_type="text/html; charset=utf-8",
        )
        return True

    async def sync(self, documents: Sequence[KnowledgeDocument]) -> ImportResult:
        """Sube lo cambiado, escribe el manifiesto, importa con FULL y poda objetos huérfanos."""
        if len({doc.id for doc in documents}) != len(documents):
            raise ValueError("Ids de documento repetidos: el manifiesto los fusionaría")
        uploaded = sum([await self.upload(doc) for doc in documents])
        manifest_name = f"{self._prefix}/{METADATA_FILE}"
        blob = self._bucket.blob(manifest_name)
        await asyncio.to_thread(
            blob.upload_from_string,
            manifest(documents, self._bucket.name, self._prefix).encode(),
            content_type="application/x-ndjson",
        )
        operation = await self._documents.import_documents(
            request=ImportDocumentsRequest(
                parent=self._branch,
                gcs_source=GcsSource(
                    input_uris=[f"gs://{self._bucket.name}/{manifest_name}"],
                    data_schema="document",
                ),
                # FULL: lo que no esté en el manifiesto se borra del data store. Un documento
                # que cambia de ACL conserva su id y solo cambia su `structData` y su URI.
                reconciliation_mode=ImportDocumentsRequest.ReconciliationMode.FULL,
            )
        )
        response = await operation.result()
        metadata = operation.metadata
        # Primero importar y después borrar los HTML huérfanos (nunca al revés).
        expected = {object_name(doc, self._prefix) for doc in documents}
        expected.add(manifest_name)
        blobs = await asyncio.to_thread(
            lambda: list(self._bucket.list_blobs(prefix=f"{self._prefix}/"))
        )
        pruned = 0
        for orphan in blobs:
            if orphan.name not in expected:
                await asyncio.to_thread(orphan.delete)
                pruned += 1
        return ImportResult(
            uploaded=uploaded,
            succeeded=int(getattr(metadata, "success_count", 0) or 0),
            failed=int(getattr(metadata, "failure_count", 0) or 0),
            pruned_objects=pruned,
            errors=tuple(str(error.message) for error in response.error_samples),
        )


def build_ingestion(settings: Settings) -> AgentSearchIngestion:
    """Composición con la nube real: cliente de documentos regional + bucket del carril A."""
    from google.cloud import storage
    from google.cloud.discoveryengine_v1 import DocumentServiceAsyncClient

    from genaipocs_core.adapters.gcp_auth import gcp_credentials

    gcp, search = settings.gcp, settings.gcp_agent_search
    bucket_name = search.bucket or settings.gcp_rag_engine.bucket
    if not (gcp.project and bucket_name):
        raise ValueError("Faltan GCP__PROJECT y el bucket (GCP_AGENT_SEARCH__BUCKET o RAG Engine).")
    credentials = gcp_credentials(gcp)
    documents = DocumentServiceAsyncClient(
        credentials=credentials, client_options=discovery_engine_options(search.location)
    )
    # `google-cloud-storage` no anota el constructor ni `bucket`.
    storage_client = storage.Client(project=gcp.project, credentials=credentials)  # type: ignore[no-untyped-call]
    return AgentSearchIngestion(
        documents=documents,
        bucket=storage_client.bucket(bucket_name),  # type: ignore[no-untyped-call]
        branch=DocumentServiceAsyncClient.branch_path(
            gcp.project, search.location, search.data_store, "default_branch"
        ),
        prefix=search.prefix,
    )


# ---------- Consulta ----------


class SearchClient(Protocol):
    async def search(self, *, request: SearchRequest) -> Any: ...


class AnswerClient(Protocol):
    async def answer_query(self, *, request: AnswerQueryRequest) -> AnswerQueryResponse: ...


def search_filter(filters: Mapping[str, MetadataValue], scope: AccessScope) -> str:
    """Expresión de filtro de Discovery Engine: `acl: ANY(...)` y un `ANY` por filtro.

    Los valores vienen de la petición: con comillas o barras podrían cerrar la cadena e
    inyectar condiciones (p. ej. `" OR acl: ANY("internal`), así que se rechazan.
    """
    if filters.keys() - FILTER_KEYS:
        raise ValueError("Filtros no permitidos: la ACL pertenece al servidor")
    clauses = [("acl", list(scope.allowed_acls))]
    clauses.extend((key, [str(value)]) for key, value in sorted(filters.items()))
    parts: list[str] = []
    for key, values in clauses:
        if any('"' in value or "\\" in value for value in values):
            raise ValueError(f"Valor de filtro no admitido en '{key}'")
        parts.append(f"{key}: ANY({', '.join(json.dumps(value) for value in values)})")
    return " AND ".join(parts)


class AgentSearchKnowledge:
    def __init__(
        self,
        *,
        search: SearchClient,
        answers: AnswerClient,
        serving_config: str,
        bucket: str,
        prefix: str = "agent-search",
        answer_model: str = "stable",
    ) -> None:
        if not bucket:
            raise ValueError("Falta el bucket de Agent Search (GCP_AGENT_SEARCH__BUCKET)")
        self._search = search
        self._answers = answers
        # projects/{p}/locations/{l}/collections/default_collection/engines/{e}/servingConfigs/...
        self._serving_config = serving_config
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._answer_model = answer_model

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
        mode: ManagedSearchMode = "SEMANTIC",
    ) -> list[ManagedReference]:
        # Agent Search decide su propia mezcla léxico-semántica: `mode` no cambia la petición.
        predicate = self._validate(query, top_k, filters, scope)
        if predicate is None:
            return []
        pager = await self._search.search(
            request=SearchRequest(
                serving_config=self._serving_config,
                query=query,
                page_size=top_k,
                filter=predicate,
                content_search_spec=SearchRequest.ContentSearchSpec(
                    search_result_mode=SearchRequest.ContentSearchSpec.SearchResultMode.CHUNKS
                ),
            )
        )
        references: dict[str, ManagedReference] = {}
        # Solo la primera página (ya acotada por `page_size`): iterar el pager pediría más.
        for result in pager.results:
            chunk = result.chunk
            if not chunk:
                raise InvalidCitationError("Agent Search devolvio un resultado sin fragmento")
            metadata = chunk.document_metadata
            reference = self._reference(
                metadata.uri if metadata else None,
                chunk.content,
                dict(metadata.struct_data) if metadata and metadata.struct_data else None,
                filters,
                scope,
            )
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
        predicate = self._validate(query, top_k, filters, scope)
        if predicate is None:
            return ManagedRagResponse("", (), True)
        response = await self._answers.answer_query(
            request=AnswerQueryRequest(
                serving_config=self._serving_config,
                query=Query(text=query),
                search_spec=AnswerQueryRequest.SearchSpec(
                    search_params=AnswerQueryRequest.SearchSpec.SearchParams(
                        max_return_results=top_k, filter=predicate
                    )
                ),
                answer_generation_spec=AnswerQueryRequest.AnswerGenerationSpec(
                    model_spec=AnswerQueryRequest.AnswerGenerationSpec.ModelSpec(
                        model_version=self._answer_model
                    ),
                    include_citations=True,
                    # Que el servicio se abstenga ante ataques y preguntas sin respuesta.
                    ignore_adversarial_query=True,
                    ignore_jail_breaking_query=True,
                    ignore_non_answer_seeking_query=True,
                    ignore_low_relevant_content=True,
                ),
            )
        )
        return self._grounded(response.answer, filters, scope)

    def _validate(
        self,
        query: str,
        top_k: int,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
    ) -> str | None:
        if not query.strip() or not 1 <= top_k <= 100:
            raise ValueError("Consulta o top_k invalido")
        predicate = search_filter(filters, scope)
        return predicate if scope.allowed_acls else None

    def _reference(
        self,
        uri: str | None,
        text: str | None,
        data: Mapping[str, Any] | None,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
    ) -> ManagedReference:
        prefix = f"gs://{self._bucket}/{self._prefix}/"
        if not uri or not uri.startswith(prefix):
            raise InvalidCitationError("Fragmento de Agent Search fuera de su prefijo")
        path = PurePosixPath(uri.removeprefix(prefix))
        if len(path.parts) != 3 or path.suffix != ".html" or not path.stem:
            raise InvalidCitationError("Fragmento de Agent Search con una ruta no reconocida")
        acl, document_id = path.parts[0], path.stem
        if acl not in ACL_LEVELS or not scope.permits({"acl": acl}):
            raise InvalidCitationError("Fragmento de Agent Search fuera del alcance autorizado")
        if data is not None and (data.get("acl") != acl or data.get("document_id") != document_id):
            raise InvalidCitationError("structData de Agent Search no coincide con su ruta")
        if filters and data is None:
            raise InvalidCitationError("Agent Search no devolvio structData: filtros sin comprobar")
        if any(str((data or {}).get(key)) != str(value) for key, value in filters.items()):
            raise InvalidCitationError("Fragmento de Agent Search que no cumple los filtros")
        if not text:
            raise InvalidCitationError("Fragmento de Agent Search sin texto")
        identity = json.dumps([self._serving_config, document_id, uri, text], ensure_ascii=False)
        title = str((data or {}).get("title") or document_id)
        return ManagedReference(
            hashlib.sha256(identity.encode()).hexdigest(), document_id, title, text, uri
        )

    def _grounded(
        self, answer: Answer, filters: Mapping[str, MetadataValue], scope: AccessScope
    ) -> ManagedRagResponse:
        if answer.answer_skipped_reasons:
            # Pregunta adversaria, sin contenido relevante, respuesta poco fundamentada...: el
            # servicio se abstiene y se descarta cualquier texto.
            return ManagedRagResponse("", (), True)
        if answer.state != Answer.State.SUCCEEDED:
            raise RetrievalUnavailableError(
                f"Agent Search no completo la respuesta: {answer.state.name}"
            )
        if not answer.citations or not answer.answer_text.strip():
            # Sin citas no hay respuesta (como en Bedrock KB y RAG Engine).
            return ManagedRagResponse("", (), True)
        text = answer.answer_text
        references = [self._answer_reference(item, filters, scope) for item in answer.references]
        citations: list[ManagedCitation] = []
        covered: set[int] = set()
        for citation in answer.citations:
            fragment, start, end = utf8_span(text, citation.start_index, citation.end_index)
            cited: list[ManagedReference] = []
            for source in citation.sources:
                index = int(source.reference_id) if source.reference_id.isdigit() else -1
                if not 0 <= index < len(references):
                    raise InvalidCitationError("Cita de Agent Search a una referencia inexistente")
                cited.append(references[index])
            if not cited:
                raise InvalidCitationError("Cita de Agent Search sin referencias")
            citations.append(ManagedCitation(fragment, start, end, tuple(cited)))
            covered.update(range(start, end))
        if any(i not in covered and not c.isspace() for i, c in enumerate(text)):
            raise InvalidCitationError("Respuesta de Agent Search con texto sin cita")
        return ManagedRagResponse(text, tuple(citations), False)

    def _answer_reference(
        self,
        reference: Answer.Reference,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
    ) -> ManagedReference:
        if "chunk_info" in reference:
            info = reference.chunk_info
            metadata = info.document_metadata
            data = dict(metadata.struct_data) if metadata.struct_data else None
            return self._reference(metadata.uri, info.content, data, filters, scope)
        if "unstructured_document_info" in reference:
            document = reference.unstructured_document_info
            text = "\n".join(chunk.content for chunk in document.chunk_contents)
            data = dict(document.struct_data) if document.struct_data else None
            return self._reference(document.uri, text, data, filters, scope)
        raise InvalidCitationError("Referencia de Agent Search de un tipo no admitido")
