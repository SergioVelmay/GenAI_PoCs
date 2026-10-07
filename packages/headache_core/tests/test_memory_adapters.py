from __future__ import annotations

from headache_core.adapters.memory import (
    FakeLLM,
    HashingEmbedding,
    InMemoryMessageBus,
    InMemoryVectorStore,
)
from headache_core.ports import (
    ChatMessage,
    EmbeddingPort,
    LLMPort,
    Message,
    MessageBusPort,
    PermanentMessageError,
    Role,
    VectorRecord,
    VectorStorePort,
)


async def test_fake_llm_returns_scripted_responses() -> None:
    llm: LLMPort = FakeLLM(responses=["Hola desde Headache"])

    response = await llm.complete([ChatMessage(Role.USER, "hola")])

    assert response.content == "Hola desde Headache"
    assert response.usage.output_tokens == 3


async def test_vector_store_ranks_by_similarity_and_filters() -> None:
    embedder: EmbeddingPort = HashingEmbedding()
    store: VectorStorePort = InMemoryVectorStore()
    texts = {
        "refund-es": ("politica de reembolso candlelit", "es"),
        "access-es": ("horario de apertura de puertas", "es"),
        "refund-en": ("candlelit refund policy", "en"),
    }
    vectors = await embedder.embed_texts([text for text, _ in texts.values()])
    await store.upsert(
        [
            VectorRecord(id=key, text=text, embedding=vector, metadata={"language": lang})
            for (key, (text, lang)), vector in zip(texts.items(), vectors, strict=True)
        ]
    )

    query = await embedder.embed_query("reembolso candlelit")
    results = await store.search(query, top_k=2, filters={"language": "es"})

    assert [r.record.id for r in results] == ["refund-es", "access-es"]


async def test_message_bus_delivers_to_subscribers() -> None:
    bus: MessageBusPort = InMemoryMessageBus()
    received: list[Message] = []

    async def handler(message: Message) -> None:
        received.append(message)

    await bus.subscribe("documents.uploaded", handler)
    await bus.publish("documents.uploaded", {"path": "policies/refunds.md"})

    assert received[0].payload["path"] == "policies/refunds.md"


async def test_message_bus_retries_transient_failures() -> None:
    bus = InMemoryMessageBus(max_deliveries=3)
    attempts: list[int] = []

    async def flaky(message: Message) -> None:
        attempts.append(message.delivery_count)
        if message.delivery_count < 3:
            raise ConnectionError("pasajero")

    await bus.subscribe("t", flaky)
    await bus.publish("t", {})

    assert attempts == [1, 2, 3]
    assert bus.dead_letters == []


async def test_message_bus_dead_letters_after_max_deliveries() -> None:
    bus = InMemoryMessageBus(max_deliveries=2)
    attempts: list[int] = []

    async def broken(message: Message) -> None:
        attempts.append(message.delivery_count)
        raise ConnectionError("siempre falla")

    await bus.subscribe("t", broken)
    message = await bus.publish("t", {"k": 1})

    assert attempts == [1, 2]
    assert bus.dead_letters == [message]


async def test_message_bus_sends_permanent_errors_straight_to_dlq() -> None:
    bus = InMemoryMessageBus(max_deliveries=5)
    attempts: list[int] = []

    async def rejecting(message: Message) -> None:
        attempts.append(message.delivery_count)
        raise PermanentMessageError("mal formado")

    await bus.subscribe("t", rejecting)
    await bus.publish("t", {})

    assert attempts == [1]
    assert len(bus.dead_letters) == 1
