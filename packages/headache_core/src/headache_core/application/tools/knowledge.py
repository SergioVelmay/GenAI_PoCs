"""Tool de conocimiento (RAG): el agente consulta políticas, FAQ y guías con citas.

La ACL no es un argumento del modelo: el canal de cliente siempre busca con el ámbito público.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from headache_core.application.retrieval import RetrievalRequest, RetrievalService
from headache_core.application.tools.registry import Tool, ToolContext
from headache_core.domain.retrieval import AccessScope
from headache_core.ports.vector_store import MetadataValue

CUSTOMER_SCOPE = AccessScope(("public",))


class SearchKnowledgeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=3, max_length=300, description="Pregunta autocontenida.")
    language: Literal["es", "en"] | None = Field(
        default=None, description="Idioma de los documentos; normalmente el del cliente."
    )
    city: str | None = Field(
        default=None, max_length=60, description="Ciudad en inglés si la pregunta es local."
    )
    doc_type: Literal["policy", "faq", "venue_guide", "event_sheet"] | None = Field(
        default=None, description="policy = reembolsos por categoría; faq = dudas por ciudad."
    )


class KnowledgeHit(BaseModel):
    source_id: str
    document_id: str
    title: str
    text: str


def build_knowledge_tools(
    service: RetrievalService, *, top_k: int = 3, max_chars: int = 500
) -> list[Tool[SearchKnowledgeArgs]]:
    async def search_knowledge(args: SearchKnowledgeArgs, _: ToolContext) -> list[KnowledgeHit]:
        filters: dict[str, MetadataValue] = {
            key: value
            for key, value in (
                ("language", args.language),
                ("city", args.city),
                ("doc_type", args.doc_type),
            )
            if value is not None
        }
        response = await service.retrieve(
            RetrievalRequest(
                args.query, strategy="hybrid", top_k=top_k, filters=filters, scope=CUSTOMER_SCOPE
            )
        )
        return [
            KnowledgeHit(
                source_id=r.record.id,
                document_id=str(r.record.metadata.get("document_id", r.record.id)),
                title=str(r.record.metadata.get("title", "")),
                text=r.record.text[:max_chars],
            )
            for r in response.results
            # Defensa en profundidad: el servicio ya filtra, la tool lo vuelve a comprobar.
            if CUSTOMER_SCOPE.permits(r.record.metadata)
        ]

    return [
        Tool(
            "search_knowledge",
            "Busca en la base de conocimiento de Headache (políticas de reembolso por categoría, "
            "FAQ por ciudad, guías de recintos, fichas de eventos). Cita el source_id de lo que "
            "uses. Si no hay resultados relevantes, dilo: no inventes.",
            SearchKnowledgeArgs,
            search_knowledge,
        )
    ]
