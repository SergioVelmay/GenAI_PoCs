"""Contrato de los mensajes de indexación. Se valida al entrar: lo inválido va a la DLQ."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from headache_core.domain import DocumentType, KnowledgeDocument


class DocumentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=200, pattern=r"^[\w.-]+$")
    title: str = Field(min_length=1, max_length=500)
    doc_type: DocumentType
    language: str = Field(min_length=2, max_length=5)
    # Documentos grandes no viajan en el mensaje (SQS y Service Bus Standard: 256 KB);
    # se usa claim-check: el mensaje lleva la ruta en S3/Blob y el worker lo descarga.
    content: str = Field(max_length=100_000)
    metadata: dict[str, str] = Field(default_factory=dict)

    def to_domain(self) -> KnowledgeDocument:
        return KnowledgeDocument(
            id=self.id,
            title=self.title,
            doc_type=self.doc_type,
            language=self.language,
            content=self.content,
            metadata=dict(self.metadata),
        )


class IndexingCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["upsert", "delete"]
    document_id: str = Field(min_length=1, max_length=200)
    document: DocumentPayload | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.action == "upsert" and (
            self.document is None or self.document.id != self.document_id
        ):
            raise ValueError("Un upsert debe incluir el documento con el mismo id.")
        if self.action == "delete" and self.document is not None:
            raise ValueError("Un delete no lleva documento.")
        return self

    @classmethod
    def upsert(cls, document: DocumentPayload) -> Self:
        return cls(action="upsert", document_id=document.id, document=document)

    @classmethod
    def delete(cls, document_id: str) -> Self:
        return cls(action="delete", document_id=document_id)
