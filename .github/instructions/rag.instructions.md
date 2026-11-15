---
applyTo: "services/indexer/**,services/retrieval/**,packages/genaipocs_core/src/genaipocs_core/application/ingestion/**,packages/genaipocs_core/src/genaipocs_core/ports/vector_store.py,packages/genaipocs_core/src/genaipocs_core/ports/messaging.py,scripts/chunking_report.py,scripts/provision_search_index.py,scripts/provision_foundry_iq.py,scripts/managed_ingestion.py,infra/local/**"
description: "Indexación event-driven y retrieval (fases 3 y 4)."
---

# Indexación y retrieval (fases 3 y 4)

Docs: `docs/fase-3-comparativa.md`, `docs/fase-4-retrieval.md`.

## Indexer (`services/indexer`)

- `uv run python -m indexer.worker` (N procesos = competing consumers) · `uv run python -m indexer.producer [--limit N | --delete ID | --poison]` · `uv run python -m indexer.search "consulta" --filter language=en`.
- Brokers con el mismo `MessageBusPort`: `INDEXER__BROKER=rabbitmq|sqs|servicebus|pubsub`. RabbitMQ: cola quorum `knowledge.documents` + DLQ `knowledge.documents.dlq` (`x-delivery-limit`). En SQS y Service Bus las colas y DLQ las declara la infraestructura (`infra/local/elasticmq/elasticmq.conf`, `infra/local/servicebus/config.json`; Terraform en la nube).
- Pub/Sub (3.G1): tema `knowledge.documents`, suscripción de los workers `knowledge.documents.worker` y DLQ `knowledge.documents.dlq` (tema **con** suscripción). El adaptador exige dead-letter policy (mínimo 5 entregas) y el backoff es la `retry_policy` de la suscripción. Con `PUBSUB__EMULATOR_HOST`, el adaptador declara la topología (`EmulatorTopology`); en la nube, `infra/terraform/gcp/pubsub.tf` (con permisos del agente de servicio de Pub/Sub sobre la DLQ).
- Garantías comunes en `packages/genaipocs_core/tests/integration/test_message_bus_contract.py` (cada broker se salta si su emulador no está arrancado). Un broker nuevo debe pasar ese contrato.
- Carriles con el mismo `KnowledgeIndexer`: `INDEXER__ENGINE=native` (B: `knowledge_chunks` + ledger) o `llamaindex` (C: `IngestionPipeline`, esquema `llamaindex`). Chunking `fixed`, `sentence` y, solo en LlamaIndex, `semantic` y `hierarchical`. Comparativa sin coste: `uv run python scripts/chunking_report.py [--doc-type handbook]`.
- Nube (carril B): `INDEXER__EMBEDDINGS=hashing|azure|bedrock|gcp` (1024 dims) y `INDEXER__VECTOR_STORE=pgvector|azure_search|s3_vectors|gcp_vector_search`; el ledger sigue en PostgreSQL con `index_name@store`. El índice de AI Search no lo gestiona azurerm: `scripts/provision_search_index.py [--apply]`.
- GCP (3.G2): `GeminiEmbedding` (`adapters/gemini_embeddings.py`) usa `RETRIEVAL_DOCUMENT` en `embed_texts` y `RETRIEVAL_QUERY` en `embed_query`, y normaliza al recortar. `AgentRetrievalVectorStore` (`adapters/gcp_vector_search.py`, Agent Retrieval, antes Vector Search 2.0): IDs RFC1035 con `object_id` (hash), sin upsert (crear o reemplazar), borrado idempotente, `metadata_json` para los tipos y filtros solo sobre los campos del `data_schema` de `vector_search.tf` (efímera, `vector_search_enabled`). Tras recrear un destino vacío: `uv run python -m indexer.forget_ledger <almacén>`.
- Idempotencia por hash de contenido en el ledger; los mensajes envenenados acaban en la DLQ, no en bucle. Si el destino se recrea vacío (AI Search efímero), se olvida su ledger (`forget_index`; lo hace `provision_search_index --apply` al crear el índice).
- Carril A: `managed_ingestion.py` solo sube un blob si cambia su MD5 o sus metadatos (`needs_upload`); resubir cambia la fecha de modificación y el indexer reprocesa el documento.
- Carril A en GCP (3.G3): `managed_ingestion.py gcp` con `adapters/gcp_rag_engine.py` (cliente `agentplatform`, no `vertexai.rag`). **Un corpus por ACL** (`gs://<bucket>/<acl>/`, `GCP_RAG_ENGINE__PUBLIC_CORPUS`/`INTERNAL_CORPUS`): el filtro por metadatos de RAG Engine está en preview y no se usa para la ACL. Orden: subir (MD5) → `sync` → `prune` (borra los `RagFile` y objetos que ya no son de esa ACL). RAG Engine es efímero (`rag_engine_enabled`; `false` = `Unprovisioned`, borra los datos).
- Estrategias con LLM: una sola consulta con `SingleQueryPlan` (solo `subquestions` admite varias) y filtros con enumeraciones del dominio. Medidas en Azure: no mejoran a la híbrida + Semantic Ranker.

## Retrieval (`services/retrieval`)

- `uv run uvicorn retrieval.app:create_app --factory --host 127.0.0.1 --port 8010`; comparativa B/C: `uv run python -m retrieval.compare "consulta"`.
- 100 % local por defecto; la nube se compone solo de forma explícita con `RETRIEVAL__BACKEND|EMBEDDINGS|SEMANTIC_RANKER|RERANKER|LLM|MANAGED` (`retrieval/cloud.py`).
- Carril A en `/managed/*`: `bedrock_kb` o `foundry_iq` (On Your Data se retiró el 14-10-2026). Aprovisionamiento idempotente: `scripts/provision_foundry_iq.py [--apply]` (knowledge source y knowledge base en **preview**: `baseFilter`, esfuerzo `minimal`, salida extractiva y 5 documentos; el endpoint MCP solo existe en preview; agentes con `publish_agent`).
- Foundry IQ `answer`: el parser mapea cada cita por su anotación `url_citation` a un documento devuelto por la KB y comprueba su ACL. Los dobles de test reproducen el formato real capturado en Azure (`{"documents": [...]}`).
- Nube: ingerir siempre el corpus **clasificado con ACL explícita** (`scripts/classify_corpus.py` → `--source data/synthetic/knowledge-classified.jsonl`); la regla está en `domain/retrieval.classify_acl`. AI Search vive en `francecentral` (`search_location`) y es efímero (`search_enabled`).
- Carril A de Azure (indexer + skillset): con `parsingMode=markdown/oneToOne` el texto está en `/document/document_content`. Comprobar el número de chunks del índice, no solo el estado del indexer.
- La ACL se aplica como filtro del servidor **y** se vuelve a comprobar en cada resultado; sin `acl` explícita, los adaptadores cloud deniegan. Citas obligatorias y verificables; abstenerse antes que inventar.
