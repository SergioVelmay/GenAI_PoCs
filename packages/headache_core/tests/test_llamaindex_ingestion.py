from __future__ import annotations

from dataclasses import replace

import pytest
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import MetadataMode
from llama_index.core.storage.docstore import SimpleDocumentStore
from llama_index.core.vector_stores import SimpleVectorStore

from headache_core.adapters.llamaindex import (
    ChunkStrategy,
    LlamaIndexIndexer,
    MarkdownSectionExtractor,
    PortEmbedding,
    to_llama_document,
)
from headache_core.adapters.memory import HashingEmbedding
from headache_core.application.ingestion import IndexingOutcome
from headache_core.domain import DocumentType, KnowledgeDocument

SECTION = (
    "Las entradas son digitales y aparecen en la app tras la compra. "
    "No hace falta imprimirlas porque se escanea el código QR. "
    "Puedes enviarlas a tus acompañantes desde la propia app. "
)


@pytest.fixture
def document() -> KnowledgeDocument:
    content = "\n\n".join(
        ["# Guía del centro de ayuda"] + [f"## Sección {i}\n\n{SECTION * 3}" for i in range(1, 5)]
    )
    return KnowledgeDocument(
        id="handbook-help-center-es",
        title="Guía del centro de ayuda",
        doc_type=DocumentType.HANDBOOK,
        language="es",
        content=content,
    )


@pytest.fixture
def embed() -> PortEmbedding:
    return PortEmbedding(HashingEmbedding(64), model_name="hashing-64")


def _indexer(
    strategy: ChunkStrategy,
    embed: PortEmbedding,
) -> tuple[LlamaIndexIndexer, SimpleVectorStore, SimpleDocumentStore]:
    vectors = SimpleVectorStore()
    docstore = SimpleDocumentStore()
    indexer = LlamaIndexIndexer.create(
        vector_store=vectors,
        docstore=docstore,
        embed_model=embed,
        strategy=strategy,
        chunk_size=64,
        chunk_overlap=10,
    )
    return indexer, vectors, docstore


@pytest.mark.parametrize("strategy", ["fixed", "sentence", "semantic", "hierarchical"])
async def test_every_strategy_indexes_several_chunks(
    strategy: ChunkStrategy, document: KnowledgeDocument, embed: PortEmbedding
) -> None:
    indexer, vectors, _ = _indexer(strategy, embed)

    result = await indexer.index(document)

    assert result.outcome is IndexingOutcome.INDEXED
    assert result.chunks_upserted > 1
    assert len(vectors.data.embedding_dict) == result.chunks_upserted


async def test_unchanged_document_is_skipped(
    document: KnowledgeDocument, embed: PortEmbedding
) -> None:
    indexer, _, _ = _indexer("sentence", embed)
    await indexer.index(document)

    result = await indexer.index(document)

    assert result.outcome is IndexingOutcome.UNCHANGED


async def test_changed_document_replaces_its_vectors(
    document: KnowledgeDocument, embed: PortEmbedding
) -> None:
    indexer, vectors, _ = _indexer("sentence", embed)
    await indexer.index(document)

    result = await indexer.index(replace(document, content="# Guía\n\nAhora es muy corta."))

    assert result.chunks_upserted == 1
    assert len(vectors.data.embedding_dict) == 1


async def test_remove_cleans_vectors_and_docstore(
    document: KnowledgeDocument, embed: PortEmbedding
) -> None:
    indexer, vectors, docstore = _indexer("sentence", embed)
    await indexer.index(document)

    removed = await indexer.remove(document.id)
    again = await indexer.remove(document.id)

    assert removed.outcome is IndexingOutcome.DELETED
    assert again.outcome is IndexingOutcome.NOT_FOUND
    assert vectors.data.embedding_dict == {}
    assert await docstore.aget_document_hash(document.id) is None


async def test_hierarchical_keeps_parents_in_docstore_and_leaves_in_vectors(
    document: KnowledgeDocument, embed: PortEmbedding
) -> None:
    indexer, vectors, docstore = _indexer("hierarchical", embed)

    await indexer.index(document)

    # Jerarquía completa + el propio documento en el docstore; solo hojas en el vector store.
    assert len(docstore.docs) > len(vectors.data.embedding_dict) + 1


def test_only_title_and_section_reach_the_embedding(document: KnowledgeDocument) -> None:
    nodes = SentenceSplitter(chunk_size=64, chunk_overlap=0).get_nodes_from_documents(
        [to_llama_document(document)]
    )
    MarkdownSectionExtractor()(nodes)

    embedded = nodes[-1].get_content(metadata_mode=MetadataMode.EMBED)
    assert "title: Guía del centro de ayuda" in embedded
    assert "section: Sección 4" in embedded
    assert "document_id" not in embedded
    assert "chunk_words" not in embedded
    assert nodes[-1].metadata["chunk_words"] > 0


async def test_port_embedding_uses_our_port(embed: PortEmbedding) -> None:
    expected = await HashingEmbedding(64).embed_texts(["reembolso candlelit"])

    assert await embed.aget_text_embedding_batch(["reembolso candlelit"]) == expected
