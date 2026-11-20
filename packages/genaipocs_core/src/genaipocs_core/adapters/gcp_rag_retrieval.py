"""Carril A de GCP con RAG Engine (fase 4.G2): `ManagedKnowledgePort`, clientes inyectados.

Equivale a Bedrock KB (`Retrieve` / `RetrieveAndGenerate`) sobre los corpus que llena la 3.G3:

- `retrieve`: `retrieve_contexts` de `agentplatform` (cliente `Client.aio.rag`). RAG Engine
  embebe la consulta y devuelve fragmentos con su URI de Cloud Storage.
- `answer`: Gemini (`google-genai`) con la herramienta `Retrieval(vertex_rag_store=...)`. El
  modelo recupera y responde en una sola llamada y devuelve `grounding_metadata`: qué segmentos
  de la respuesta se apoyan en qué fragmentos. Como en `RetrieveAndGenerate`, esa generación no
  pasa por nuestro `LLMPort`: es la diferencia deliberada entre el carril A y los B/C.

La ACL es **por corpus** (uno por nivel, 3.G3): se pregunta solo a los corpus que permite el
scope y cada fragmento se vuelve a comprobar por el prefijo de su URI (`gs://<bucket>/<acl>/`).
Lo que el adaptador no puede garantizar, lo rechaza en lugar de ignorarlo:

- Filtros por petición: el filtro por metadatos de RAG Engine está en preview (y no hay
  metadatos en el corpus), así que un filtro de idioma o de categoría no se cumpliría.
- `HYBRID`: `RagManagedDb` (KNN) solo hace búsqueda vectorial.
- Generar con más de un corpus: cada petición admite **un** corpus. `retrieve` hace una por
  corpus y fusiona por posición (RRF), porque la escala del score depende del almacén.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

from google.genai import types

from genaipocs_core.adapters.gemini import SDK_ERRORS, GeminiModels, to_llm_error
from genaipocs_core.application.retrieval import InvalidCitationError, RetrievalUnavailableError
from genaipocs_core.domain.retrieval import AccessScope
from genaipocs_core.ports.retrieval import (
    ManagedCitation,
    ManagedRagResponse,
    ManagedReference,
    ManagedSearchMode,
)
from genaipocs_core.ports.vector_store import MetadataValue
from genaipocs_core.prompts import PROMPTS

ANSWER_INSTRUCTIONS = PROMPTS.get("rag-engine-answer").text
RRF_K = 60
# Respuestas que Gemini cortó por seguridad: abstención, como un guardrail de Bedrock KB.
_BLOCKED = frozenset(
    {
        types.FinishReason.SAFETY,
        types.FinishReason.PROHIBITED_CONTENT,
        types.FinishReason.BLOCKLIST,
        types.FinishReason.SPII,
        types.FinishReason.RECITATION,
    }
)


class RagContextsClient(Protocol):
    """Lo que el adaptador usa de `Client.aio.rag` (permite un doble en los tests)."""

    async def retrieve_contexts(
        self, *, vertex_rag_store: types.VertexRagStore, query: Any, config: Any = None
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class _Source:
    acl: str
    document_id: str


class RagEngineKnowledge:
    def __init__(
        self,
        rag: RagContextsClient,
        *,
        bucket: str,
        corpora: Mapping[str, str],
        models: GeminiModels | None = None,
        generation_model: str = "",
        rerank_model: str = "",
    ) -> None:
        if not bucket or not all(corpora.values()):
            raise ValueError("Faltan el bucket o los corpus de RAG Engine (salidas de lane_a.tf)")
        self._rag = rag
        self._bucket = bucket
        self._corpora = dict(corpora)
        self._models = models
        self._generation_model = generation_model
        self._rerank_model = rerank_model

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
        mode: ManagedSearchMode = "SEMANTIC",
    ) -> list[ManagedReference]:
        acls = self._allowed(query, top_k, filters, scope, mode)
        rankings: list[list[ManagedReference]] = []
        for acl in acls:
            response = await self._rag.retrieve_contexts(
                vertex_rag_store=self._store(acl),
                query=self._query(query, top_k),
            )
            contexts = response.contexts.contexts if response.contexts else None
            rankings.append(
                [
                    self._reference(acl, context.source_uri, context.text, scope)
                    for context in contexts or []
                ]
            )
        return _fuse(rankings, top_k)

    async def answer(
        self,
        query: str,
        *,
        top_k: int,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
        mode: ManagedSearchMode = "SEMANTIC",
    ) -> ManagedRagResponse:
        if self._models is None or not self._generation_model:
            raise RetrievalUnavailableError(
                "La generacion de RAG Engine requiere GCP_RAG_ENGINE__GENERATION_MODEL"
            )
        acls = self._allowed(query, top_k, filters, scope, mode)
        if not acls:
            return ManagedRagResponse("", (), True)
        if len(acls) > 1:
            raise RetrievalUnavailableError(
                "RAG Engine genera con un solo corpus por peticion: el scope abarca varios"
            )
        acl = acls[0]
        tool = types.Tool(
            retrieval=types.Retrieval(
                vertex_rag_store=types.VertexRagStore(
                    rag_resources=self._store(acl).rag_resources,
                    rag_retrieval_config=self._config(top_k),
                )
            )
        )
        try:
            response = await self._models.generate_content(
                model=self._generation_model,
                contents=[types.Content(role="user", parts=[types.Part(text=query)])],
                config=types.GenerateContentConfig(
                    system_instruction=ANSWER_INSTRUCTIONS, tools=[tool]
                ),
            )
        except SDK_ERRORS as exc:
            raise to_llm_error(exc) from exc
        return self._grounded(response, acl, scope)

    def _allowed(
        self,
        query: str,
        top_k: int,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
        mode: ManagedSearchMode,
    ) -> list[str]:
        if not query.strip() or not 1 <= top_k <= 100:
            raise ValueError("Consulta o top_k invalido")
        if filters:
            raise RetrievalUnavailableError(
                "RAG Engine no garantiza filtros por peticion (su filtro esta en preview)"
            )
        if mode == "HYBRID":
            raise RetrievalUnavailableError("RagManagedDb solo admite busqueda SEMANTIC")
        # Solo corpus conocidos: una ACL del scope sin corpus no amplía nada.
        return [acl for acl in scope.allowed_acls if acl in self._corpora]

    def _store(self, acl: str) -> types.VertexRagStore:
        return types.VertexRagStore(
            rag_resources=[types.VertexRagStoreRagResource(rag_corpus=self._corpora[acl])]
        )

    def _config(self, top_k: int) -> types.RagRetrievalConfig:
        config = types.RagRetrievalConfig(top_k=top_k)
        if self._rerank_model:
            # Reordena dentro de RAG Engine con la Ranking API (la misma que el carril B).
            config.ranking = types.RagRetrievalConfigRanking(
                rank_service=types.RagRetrievalConfigRankingRankService(
                    model_name=self._rerank_model
                )
            )
        return config

    def _query(self, query: str, top_k: int) -> Any:
        from agentplatform import types as rag_types

        return rag_types.RagQuery(text=query, rag_retrieval_config=self._config(top_k))

    def _source(self, acl: str, uri: str | None) -> _Source:
        """`gs://<bucket>/<acl>/<tipo>/<id>.md` (3.G3): la ACL sale del corpus y de la ruta."""
        prefix = f"gs://{self._bucket}/"
        if not uri or not uri.startswith(prefix):
            raise InvalidCitationError("Fragmento de RAG Engine fuera del bucket del corpus")
        path = PurePosixPath(uri.removeprefix(prefix))
        if len(path.parts) != 3 or path.parts[0] != acl or path.suffix != ".md" or not path.stem:
            raise InvalidCitationError("Fragmento de RAG Engine con una ruta no reconocida")
        return _Source(acl, path.stem)

    def _reference(
        self, acl: str, uri: str | None, text: str | None, scope: AccessScope
    ) -> ManagedReference:
        source = self._source(acl, uri)
        if not scope.permits({"acl": source.acl}):
            raise InvalidCitationError("Fragmento de RAG Engine fuera del alcance autorizado")
        if not text:
            raise InvalidCitationError("Fragmento de RAG Engine sin texto")
        identity = [self._corpora[acl], source.document_id, uri, text]
        return ManagedReference(
            hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest(),
            source.document_id,
            # Sin metadatos en el corpus: el título es el documento, como el fallback de Bedrock.
            source.document_id,
            text,
            str(uri),
        )

    def _grounded(
        self, response: types.GenerateContentResponse, acl: str, scope: AccessScope
    ) -> ManagedRagResponse:
        feedback = response.prompt_feedback
        candidate = response.candidates[0] if response.candidates else None
        if (
            (feedback is not None and feedback.block_reason is not None)
            or candidate is None
            or candidate.finish_reason in _BLOCKED
        ):
            return ManagedRagResponse("", (), True)
        metadata = candidate.grounding_metadata
        supports = (metadata.grounding_supports if metadata else None) or []
        parts = (candidate.content.parts if candidate.content else None) or []
        # Las partes `thought` son razonamiento, no respuesta (y sin `include_thoughts` no
        # llegan). `part_index` se interpreta sobre las partes de respuesta: a confirmar en la
        # nube con un modelo que piense (pendientes-cloud.md).
        texts = [str(part.text) for part in parts if part.text and not part.thought]
        answer = "".join(texts)
        if not supports or not answer.strip():
            # Sin soporte no hay respuesta: se descarta el texto del modelo (como Bedrock KB).
            return ManagedRagResponse("", (), True)
        chunks = (metadata.grounding_chunks if metadata else None) or []
        citations: list[ManagedCitation] = []
        covered: set[int] = set()
        for support in supports:
            text, start, end = _segment(support.segment, texts)
            references = tuple(
                self._chunk_reference(chunks, index, acl, scope)
                for index in support.grounding_chunk_indices or []
            )
            if not references:
                raise InvalidCitationError("Segmento de Gemini sin fragmentos")
            citations.append(ManagedCitation(text, start, end, references))
            covered.update(range(start, end))
        if any(i not in covered and not c.isspace() for i, c in enumerate(answer)):
            raise InvalidCitationError("Respuesta de Gemini con texto sin cita")
        return ManagedRagResponse(answer, tuple(citations), False)

    def _chunk_reference(
        self,
        chunks: Sequence[types.GroundingChunk],
        index: int,
        acl: str,
        scope: AccessScope,
    ) -> ManagedReference:
        context = chunks[index].retrieved_context if 0 <= index < len(chunks) else None
        if context is None:
            raise InvalidCitationError("Cita de Gemini a un fragmento inexistente")
        return self._reference(acl, context.uri, context.text, scope)


def _segment(segment: types.Segment | None, texts: Sequence[str]) -> tuple[str, int, int]:
    """Segmento de Gemini → (texto, inicio, fin) en caracteres de la respuesta completa.

    Los índices van en **bytes UTF-8** dentro de su parte (`start_index` ausente = 0): cortar el
    `str` con ellos desplaza la cita en cuanto hay una tilde.
    """
    part_index = (segment.part_index if segment else None) or 0
    if segment is None or not 0 <= part_index < len(texts) or segment.end_index is None:
        raise InvalidCitationError("Segmento de Gemini sin parte de texto o sin fin")
    raw = texts[part_index].encode()
    start, end = segment.start_index or 0, segment.end_index
    if not 0 <= start < end <= len(raw):
        raise InvalidCitationError("Segmento de Gemini fuera del texto generado")
    try:
        prefix, text = raw[:start].decode(), raw[start:end].decode()
    except UnicodeDecodeError as exc:
        raise InvalidCitationError("Segmento de Gemini corta un caracter") from exc
    if not text.strip() or (segment.text is not None and segment.text != text):
        raise InvalidCitationError("Segmento de Gemini no corresponde al texto generado")
    begin = sum(len(previous) for previous in texts[:part_index]) + len(prefix)
    return text, begin, begin + len(text)


def _fuse(rankings: Sequence[Sequence[ManagedReference]], top_k: int) -> list[ManagedReference]:
    """Un corpus: su orden. Varios: RRF por posición (no se comparan scores entre peticiones)."""
    scores: dict[str, float] = {}
    first: dict[str, ManagedReference] = {}
    for ranking in rankings:
        for position, reference in enumerate(ranking, start=1):
            scores[reference.source_id] = scores.get(reference.source_id, 0.0) + 1 / (
                RRF_K + position
            )
            first.setdefault(reference.source_id, reference)
    ordered = sorted(first, key=lambda source_id: (-scores[source_id], source_id))
    return [first[source_id] for source_id in ordered[:top_k]]
