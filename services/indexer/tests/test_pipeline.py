"""Flujo completo productor -> cola -> worker con adaptadores en memoria (sin red ni coste)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from llama_index.core.storage.docstore import SimpleDocumentStore
from llama_index.core.vector_stores import SimpleVectorStore

from headache_core.adapters.llamaindex import LlamaIndexIndexer, PortEmbedding
from headache_core.adapters.memory import (
    HashingEmbedding,
    InMemoryIngestionLedger,
    InMemoryMessageBus,
    InMemoryVectorStore,
)
from headache_core.application.ingestion import DocumentIndexer, SentenceChunker
from headache_core.domain import DocumentType
from headache_core.ports import LedgerEntry
from indexer.handler import IndexingHandler
from indexer.messages import DocumentPayload, IndexingCommand

TOPIC = "knowledge.documents"
INDEX = "knowledge"


class FlakyLedger(InMemoryIngestionLedger):
    """Falla en el primer `save`, como si se cayera la conexión justo antes de confirmar."""

    def __init__(self) -> None:
        super().__init__()
        self.failures_left = 1

    async def save(self, entry: LedgerEntry) -> None:
        if self.failures_left:
            self.failures_left -= 1
            raise ConnectionError("BD no disponible")
        await super().save(entry)


@dataclass
class Harness:
    bus: InMemoryMessageBus
    store: InMemoryVectorStore
    ledger: InMemoryIngestionLedger


async def _harness(ledger: InMemoryIngestionLedger | None = None) -> Harness:
    bus = InMemoryMessageBus(max_deliveries=3)
    store = InMemoryVectorStore()
    ledger = ledger or InMemoryIngestionLedger()
    indexer = DocumentIndexer(
        index_name=INDEX,
        chunker=SentenceChunker(max_words=20, overlap_words=0),
        embeddings=HashingEmbedding(64),
        store=store,
        ledger=ledger,
        pipeline_version="test",
    )
    await bus.subscribe(TOPIC, IndexingHandler(indexer))
    return Harness(bus, store, ledger)


@pytest.fixture
def document() -> DocumentPayload:
    return DocumentPayload(
        id="faq-madrid-es",
        title="Preguntas frecuentes: Madrid",
        doc_type=DocumentType.FAQ,
        language="es",
        content=(
            "**¿Cuándo abren las puertas?** 45 minutos antes del inicio.\n\n"
            "**¿Dónde están mis entradas?** En la app de Headache, sección «Mis entradas».\n\n"
            "**¿Necesito imprimir la entrada?** No, basta con el código QR en el móvil."
        ),
        metadata={"city": "Madrid"},
    )


async def test_upsert_then_duplicate_then_delete(document: DocumentPayload) -> None:
    h = await _harness()

    await h.bus.publish(TOPIC, IndexingCommand.upsert(document).model_dump(mode="json"))
    indexed = len(h.store)
    await h.bus.publish(TOPIC, IndexingCommand.upsert(document).model_dump(mode="json"))

    assert indexed > 1
    assert len(h.store) == indexed

    await h.bus.publish(TOPIC, IndexingCommand.delete(document.id).model_dump(mode="json"))

    assert len(h.store) == 0
    assert h.bus.dead_letters == []


async def test_invalid_message_goes_to_dlq_without_retries() -> None:
    h = await _harness()

    await h.bus.publish(TOPIC, {"action": "upsert", "document_id": "sin-documento"})

    assert len(h.bus.dead_letters) == 1
    assert len(h.store) == 0


async def test_crash_before_ledger_is_recovered_by_redelivery(document: DocumentPayload) -> None:
    ledger = FlakyLedger()
    h = await _harness(ledger)

    await h.bus.publish(TOPIC, IndexingCommand.upsert(document).model_dump(mode="json"))

    entry = await ledger.get(INDEX, document.id)
    assert entry is not None
    assert len(h.store) == len(entry.chunk_ids)
    assert h.bus.dead_letters == []


def test_command_rejects_mismatched_ids(document: DocumentPayload) -> None:
    with pytest.raises(ValueError, match="mismo id"):
        IndexingCommand(action="upsert", document_id="otro", document=document)


async def test_same_messages_drive_the_llamaindex_engine(document: DocumentPayload) -> None:
    vectors = SimpleVectorStore()
    indexer = LlamaIndexIndexer.create(
        vector_store=vectors,
        docstore=SimpleDocumentStore(),
        embed_model=PortEmbedding(HashingEmbedding(64), model_name="hashing-64"),
        strategy="sentence",
        chunk_size=32,
        chunk_overlap=0,
    )
    bus = InMemoryMessageBus(max_deliveries=3)
    await bus.subscribe(TOPIC, IndexingHandler(indexer))

    await bus.publish(TOPIC, IndexingCommand.upsert(document).model_dump(mode="json"))
    indexed = len(vectors.data.embedding_dict)
    await bus.publish(TOPIC, IndexingCommand.upsert(document).model_dump(mode="json"))

    assert indexed > 0
    assert len(vectors.data.embedding_dict) == indexed

    await bus.publish(TOPIC, IndexingCommand.delete(document.id).model_dump(mode="json"))

    assert vectors.data.embedding_dict == {}
    assert bus.dead_letters == []
