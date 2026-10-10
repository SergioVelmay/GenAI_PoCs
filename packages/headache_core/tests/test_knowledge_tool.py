from __future__ import annotations

import json

from headache_core.adapters.local_retrieval import LocalRetrieval
from headache_core.adapters.memory import HashingEmbedding
from headache_core.application.retrieval import RetrievalService
from headache_core.application.tools import ToolContext, ToolExecutor, ToolRegistry
from headache_core.application.tools.knowledge import build_knowledge_tools
from headache_core.ports import ToolCall, VectorRecord

DOCS = [
    ("policy-candlelit-es", "public", "policy", "Reembolsos: hasta 48 horas antes del inicio."),
    ("handbook-es", "internal", "handbook", "Reembolsos manuales: procedimiento interno."),
]


async def _registry() -> ToolRegistry:
    embeddings = HashingEmbedding(64)
    vectors = await embeddings.embed_texts([text for *_, text in DOCS])
    records = [
        VectorRecord(
            f"{doc_id}::chunk-0",
            text,
            vector,
            {
                "document_id": doc_id,
                "title": doc_id,
                "acl": acl,
                "doc_type": doc_type,
                "language": "es",
            },
        )
        for (doc_id, acl, doc_type, text), vector in zip(DOCS, vectors, strict=True)
    ]
    service = RetrievalService(LocalRetrieval(records, embeddings=embeddings))
    return ToolRegistry(build_knowledge_tools(service))


async def test_knowledge_tool_only_returns_public_documents() -> None:
    executor = ToolExecutor(await _registry())

    execution = await executor.execute(
        ToolCall("a", "search_knowledge", '{"query": "reembolsos manuales internos"}'),
        ToolContext(customer_id="cus_1"),
    )

    hits = json.loads(execution.output)["items"]
    assert [h["document_id"] for h in hits] == ["policy-candlelit-es"]
    assert set(hits[0]) == {"source_id", "document_id", "title", "text"}


async def test_knowledge_tool_rejects_acl_as_an_argument() -> None:
    executor = ToolExecutor(await _registry())

    execution = await executor.execute(
        ToolCall("a", "search_knowledge", '{"query": "reembolsos", "acl": "internal"}'),
        ToolContext(customer_id="cus_1"),
    )

    assert execution.error == "invalid_arguments"
