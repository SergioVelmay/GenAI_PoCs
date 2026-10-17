---
applyTo: "services/indexer/**,services/retrieval/**,packages/headache_core/src/headache_core/application/ingestion/**,packages/headache_core/src/headache_core/ports/vector_store.py,packages/headache_core/src/headache_core/ports/messaging.py,scripts/chunking_report.py,scripts/provision_search_index.py,scripts/provision_foundry_iq.py,scripts/managed_ingestion.py,infra/local/**"
description: "Indexación event-driven y retrieval (fases 3 y 4)."
---

# Indexación y retrieval (fases 3 y 4)

Docs: `docs/fase-3-comparativa.md`, `docs/fase-4-retrieval.md`.

## Indexer (`services/indexer`)

- `uv run python -m indexer.worker` (N procesos = competing consumers) · `uv run python -m indexer.producer [--limit N | --delete ID | --poison]` · `uv run python -m indexer.search "consulta" --filter language=en`.
- Brokers con el mismo `MessageBusPort`: `INDEXER__BROKER=rabbitmq|sqs|servicebus`. RabbitMQ: cola quorum `knowledge.documents` + DLQ `knowledge.documents.dlq` (`x-delivery-limit`). En SQS y Service Bus las colas y DLQ las declara la infraestructura (`infra/local/elasticmq/elasticmq.conf`, `infra/local/servicebus/config.json`; Terraform en la nube).
- Garantías comunes en `packages/headache_core/tests/integration/test_message_bus_contract.py` (cada broker se salta si su emulador no está arrancado). Un broker nuevo debe pasar ese contrato.
- Carriles con el mismo `KnowledgeIndexer`: `INDEXER__ENGINE=native` (B: `knowledge_chunks` + ledger) o `llamaindex` (C: `IngestionPipeline`, esquema `llamaindex`). Chunking `fixed`, `sentence` y, solo en LlamaIndex, `semantic` y `hierarchical`. Comparativa sin coste: `uv run python scripts/chunking_report.py [--doc-type handbook]`.
- Nube (carril B): `INDEXER__EMBEDDINGS=hashing|azure|bedrock` (1024 dims) y `INDEXER__VECTOR_STORE=pgvector|azure_search|s3_vectors`; el ledger sigue en PostgreSQL con `index_name@store`. El índice de AI Search no lo gestiona azurerm: `scripts/provision_search_index.py [--apply]`.
- Idempotencia por hash de contenido en el ledger; los mensajes envenenados acaban en la DLQ, no en bucle.

## Retrieval (`services/retrieval`)

- `uv run uvicorn retrieval.app:create_app --factory --host 127.0.0.1 --port 8010`; comparativa B/C: `uv run python -m retrieval.compare "consulta"`.
- 100 % local por defecto; la nube se compone solo de forma explícita con `RETRIEVAL__BACKEND|EMBEDDINGS|SEMANTIC_RANKER|RERANKER|LLM|MANAGED` (`retrieval/cloud.py`).
- Carril A en `/managed/*`: `bedrock_kb` o `foundry_iq` (On Your Data se retiró el 14-10-2026). Aprovisionamiento: `scripts/provision_foundry_iq.py [--apply]`.
- La ACL se aplica como filtro del servidor **y** se vuelve a comprobar en cada resultado; sin `acl` explícita, los adaptadores cloud deniegan. Citas obligatorias y verificables; abstenerse antes que inventar.
