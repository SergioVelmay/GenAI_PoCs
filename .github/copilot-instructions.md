# Instrucciones para Copilot: GenAI_PoCs

## Contexto del proyecto

- PoCs didácticas para dominar el stack completo de **sistemas agénticos GenAI**: RAG, tool calling, MCP, multi-agente, harness/guardrails, evals, observabilidad, seguridad, DevOps/LLMOps/MLOps, en **Azure y AWS**.
- Objetivo del usuario (Sergio): poder explicar y defender cada decisión ante un hiring manager para un puesto de **AI Technical Lead / Senior AI Engineer / Forward Deployed AI Engineer**.
- Perfil: más de 7 años en Azure (AI Foundry, AI Search, Container Apps, AKS, Functions, Bicep, Azure DevOps), Microsoft Agent Framework y FastMCP. Está aprendiendo LlamaIndex, LangChain/LangGraph, Strands, AWS y Terraform.
- Cliente hipotético: **Headache**, marketplace global de experiencias y eventos con unos 300 M de usuarios y producción en **AWS**. Hay usuarios internos (operaciones, soporte, partners) y una parte pública de alta escala.

## Reglas obligatorias

- El cliente se llama **siempre "Headache"**: en código, comentarios, datos, commits y chat. **Nunca** escribas el nombre de la empresa real en la que se inspira ni sus marcas comerciales. Usa nombres genéricos (p. ej., la categoría `candlelit`, no la marca real) y evita también marcas de terceros en los datos sintéticos.
- **Pide confirmación** antes de: commits, push, crear recursos cloud, `terraform apply`, cualquier acción con coste y cualquier acción destructiva (borrar ficheros o volúmenes, `down -v`...).
- Nunca subas secretos. Usa `.env` en local (ignorado por Git), OIDC en CI y Managed Identity / IAM Roles en la nube.

## Forma de trabajar

- Responde en **español** y con enfoque **didáctico**. Para cada fase:
  1. explica el concepto;
  2. da la equivalencia con lo que Sergio ya conoce y la comparativa **Azure ↔ AWS ↔ framework**;
  3. implementa de forma incremental y verificable;
  4. para para que lo revise;
  5. cierra con un **checkpoint de entrevista** (preguntas típicas y respuestas modelo).
- **Tres carriles paralelos** cuando tenga sentido: **A. Gestionado** (Foundry Agent Service, Bedrock Knowledge Bases, AgentCore...), **B. SDK nativo** (`azure-*`, `boto3`) y **C. Framework** (LlamaIndex, LangGraph, MAF, Strands). Siempre se comparan con el mismo dataset y las mismas evals.
- **Solo modelos cloud** (Azure AI Foundry y Amazon Bedrock); no se usan modelos locales. Docker en local solo ejecuta infraestructura.
- **Estrategia de coste** (cuentas gratuitas: 200 $ en Azure y 100 $ en AWS):
  - Modelos baratos por defecto.
  - AI Search en el tier Free.
  - Evitar OpenSearch Serverless; usar S3 Vectors o pgvector.
  - EKS/AKS solo de forma efímera (`apply` → probar → `destroy`).
  - Alertas de presupuesto activas.

## Arquitectura y convenciones

- Monorepo **uv** (Python 3.12). Paquete compartido `packages/headache_core`; los futuros servicios irán en `services/<nombre>`, cada uno con su `pyproject.toml`, añadidos a `[tool.uv.workspace]`.
- **Clean Architecture / Ports & Adapters**:
  - `domain/` es Python puro, con reglas de negocio deterministas. El LLM nunca toma decisiones de negocio: las toma el dominio.
  - `application/` contiene los casos de uso, que dependen solo de puertos.
  - `ports/` define interfaces con `typing.Protocol`.
  - `adapters/` contiene las implementaciones: `memory` para tests y después `azure`, `aws`, `postgres`...
- Configuración con Pydantic Settings y variables anidadas con `__` (p. ej. `BEDROCK__REGION`). Los secretos van como `SecretStr`.
- Calidad (debe pasar antes de cada commit; lo mismo ejecuta la CI):
  `uv run ruff check .` · `uv run ruff format --check .` · `uv run mypy` (strict) · `uv run pytest`.
- Tests con adaptadores en memoria: deterministas y sin red ni coste. `pytest-asyncio` en modo `auto`.
- Tests de integración (marcador `integration`, excluidos por defecto): `uv run pytest -m integration`. Requieren PostgreSQL migrado.
- Migraciones: `uv run alembic -c packages/headache_core/alembic.ini upgrade head` (0001 esquema, 0002 índices `CONCURRENTLY`, 0003 pgvector `knowledge_chunks` + `ingestion_ledger`, vector(1024)). Carga masiva: `scripts/load_seed.py --truncate`; benchmark de índices: `scripts/sql_benchmark.py`.
- Indexer (fase 3, `services/indexer`): `uv run python -m indexer.worker` (N procesos = competing consumers), `uv run python -m indexer.producer [--limit N | --delete ID | --poison]`, `uv run python -m indexer.search "consulta" --filter language=en`. Cola RabbitMQ quorum `knowledge.documents` + DLQ `knowledge.documents.dlq` (`x-delivery-limit`). Config `INDEXER__*`.
- Carriles de ingesta con el mismo `KnowledgeIndexer`: `INDEXER__ENGINE=native` (carril B, tabla `knowledge_chunks` + ledger) o `llamaindex` (carril C, `IngestionPipeline`; tablas propias en el esquema `llamaindex`, fuera de Alembic). Estrategias: `fixed`, `sentence` y, solo en LlamaIndex, `semantic` y `hierarchical`. Comparativa sin coste: `uv run python scripts/chunking_report.py [--doc-type handbook]`.
- En Windows usa `127.0.0.1` y no `localhost` para conectar desde el código (PostgreSQL, RabbitMQ, Langfuse): el intento IPv6 añade ~2 s por conexión.
- Datos sintéticos: `uv run python scripts/generate_synthetic_data.py --scale small|medium|large`. Son deterministas por semilla y se generan en `data/synthetic/`, que no se versiona.
- LLM: todo pasa por `LLMPort`. El gateway se monta en `adapters/llm_factory.build_gateway` (routing → fallback → trazas/coste → rate limit → adaptador). `LLM_GATEWAY__ENGINE=native|litellm`. Scripts con coste: `scripts/llm_cache_benchmark.py` y `scripts/llm_gateway_demo.py`.
- Entorno local: `docker compose up -d` (PostgreSQL + pgvector, RabbitMQ). Añade `--profile observability` para Langfuse v3, `--profile sqs` para ElasticMQ (SQS, puerto 9324) y `--profile servicebus` para el emulador de Service Bus (AMQP en 5673, requiere `SERVICEBUS_ACCEPT_EULA=Y`). Todos los puertos están ligados a `127.0.0.1`.
- Brokers del indexer: `INDEXER__BROKER=rabbitmq|sqs|servicebus` (mismo `MessageBusPort`). Garantías comunes verificadas en `tests/integration/test_message_bus_contract.py` (cada broker se salta si su emulador no está arrancado). En SQS y Service Bus las colas y DLQ las declara la infraestructura (`infra/local/elasticmq/elasticmq.conf`, `infra/local/servicebus/config.json`; Terraform/Bicep en la nube).
- IaC con **Terraform** como hilo principal (más ejemplos puntuales de CDK/CloudFormation y Bicep). CI/CD con **GitHub Actions**.
- Terraform en `infra/terraform/{aws,azure}` (estado local, `terraform.tfvars` sin versionar). Validación sin coste: `terraform fmt -check`, `init -backend=false`, `validate` (también en CI). `plan`/`apply` requieren credenciales y confirmación. El índice de AI Search no lo gestiona azurerm: `scripts/provision_search_index.py [--apply]`.
- Carril B en la nube del indexer: `INDEXER__EMBEDDINGS=hashing|azure|bedrock` (1024 dims) y `INDEXER__VECTOR_STORE=pgvector|azure_search|s3_vectors`; el ledger sigue en PostgreSQL con `index_name@store`.
- Retrieval (fase 4, `services/retrieval`): `uv run uvicorn retrieval.app:create_app --factory --host 127.0.0.1 --port 8010`; comparativa B/C: `uv run python -m retrieval.compare "consulta"`. Por defecto 100 % local y sin coste; la nube se compone solo de forma explícita con `RETRIEVAL__BACKEND|EMBEDDINGS|SEMANTIC_RANKER|RERANKER|LLM|MANAGED` (`retrieval/cloud.py`). Carril A en rutas `/managed/*` (`bedrock_kb` o `foundry_iq`). Aprovisionamiento de Foundry IQ: `scripts/provision_foundry_iq.py [--apply]`.
- Commits con Conventional Commits en español, p. ej. `feat: fase 1 - esquema SQL de ticketing`.

## Plan de fases (estado)

| Fase | Tema | Estado |
|---|---|---|
| 0 | Cimientos: monorepo, Clean Architecture, docker compose, datos sintéticos, CI | ✅ Hecha |
| 1 | SQL avanzado (PostgreSQL, Alembic, SQLAlchemy async, índices, `EXPLAIN`) + `ticketing_api` (FastAPI) | ✅ Hecha |
| 2 | Capa de modelos: Azure OpenAI vs Bedrock Converse, LLM Gateway (LiteLLM), model routing, prompt caching, coste por tokens | ✅ Hecha* (validación en cloud pendiente, ver abajo) |
| 3 | Indexación event-driven (RabbitMQ/SQS/Service Bus) en 3 carriles × 2 nubes (AI Search, Bedrock KB, LlamaIndex) | ✅ Hecha* (validación en cloud pendiente; comparativa en `docs/fase-3-comparativa.md`) |
| 4 | Retrieval avanzado: híbrido, filtros, reranking, query rewriting, servicio RAG | ✅ Hecha* (validación en cloud pendiente; carril A Azure = Foundry IQ + Agent Service por retirada de On Your Data el 14-10-2026; `docs/fase-4-retrieval.md`) |
| 5 | Evals de RAG: golden dataset, RAGAS/DeepEval, hit rate/MRR, gate en CI | |
| 6 | Tool calling avanzado (bucle manual) + servidores MCP con FastMCP + MCP gateway | |
| 7 | Un agente en 5 sabores: LangGraph, MAF, Strands, LlamaIndex, Foundry Agent Service / AgentCore; context engineering | |
| 8 | Agentic harness y guardrails: validación, autocorrección, SQL seguro, PII, prompt injection, human-in-the-loop | |
| 9 | Orquestación multi-agente (Triaje → Reembolsos / Creativo / Conocimiento), A2A, asíncrono | |
| 10 | Escala: backpressure, rate limiting, caché semántica, autoescalado por colas, pruebas de carga | |
| 11 | Observabilidad: OpenTelemetry GenAI, Langfuse, CloudWatch/X-Ray vs App Insights, alertas | |
| 12 | Evals de agentes, red teaming (PyRIT/promptfoo), LLMOps (prompts versionados, A/B) | |
| 13 | Despliegue en AWS con Terraform: ECS Fargate/EKS, Lambda, Bedrock, SQS, Aurora, OIDC | |
| 14 | Despliegue espejo en Azure: ACA/AKS, Functions, Foundry, Service Bus, AI Search | |
| 15+ | Extras: MLOps (fine-tuning de embeddings), recomendador, multi-región, FinOps, vLLM | |

Requisitos de las ofertas que deben quedar cubiertos: orquestación multi-agente con grafos, tool calling avanzado, harness y guardrails, RAG con búsqueda híbrida, context engineering + prompt caching + model routing, evals con métricas, Clean Architecture/DDD, patrones asíncronos y colas, SQL avanzado, CI/CD + observabilidad y equivalencias Azure ↔ AWS.

Actualiza la tabla de estado al cerrar cada fase.

## Pendientes "para más adelante" (Sergio los aplazó; recuérdaselos)

Desde la **fase 2** (implementación cerrada; falta la validación en cloud):

- [ ] Instalar y autenticar **Azure CLI** (`az login`) y **AWS CLI** (`aws configure sso`, perfil en `BEDROCK__PROFILE`).
- [ ] **Azure AI Foundry**: desplegar `gpt-4.1-mini` (potente) y `gpt-4.1-nano` (barato); rellenar `AZURE_OPENAI__ENDPOINT` y dar el rol *Cognitive Services OpenAI User* a su usuario (Entra ID, sin clave).
- [ ] **Bedrock** (`eu-west-1`): habilitar el acceso a Claude Haiku 4.5 (formulario de Anthropic) y a Nova Lite.
- [ ] Ejecutar con coste (pedir confirmación): `scripts/llm_cache_benchmark.py --provider azure|aws` y `scripts/llm_gateway_demo.py` (motores `native` y `litellm`), y revisar las trazas en Langfuse.
- [ ] Langfuse local sigue en la imagen `langfuse:3` (OSS v3, parches de seguridad hasta 31-01-2027). El SDK Python v4 es compatible (requiere servidor ≥ 3.63.0). Valorar migrar a la imagen v4 en la fase 11.

Desde la **fase 3** (código, IaC y comparativa cerrados; falta la validación en cloud, todo con coste y confirmación):

- [ ] `terraform plan`/`apply` en `infra/terraform/aws` y `infra/terraform/azure` (crear `terraform.tfvars` con `budget_email` y, en Azure, `subscription_id`). Decidir `search_sku` (free con claves o basic ~70 €/mes con identidad gestionada).
- [ ] Copiar los outputs de Terraform al `.env` (`AZURE_SEARCH__*`, `S3_VECTORS__*`, `AZURE_MANAGED__*`, `BEDROCK_KB__*`, colas SQS/Service Bus).
- [ ] Carril B: `scripts/provision_search_index.py --apply` e indexar con `INDEXER__EMBEDDINGS=azure|bedrock` y `INDEXER__VECTOR_STORE=azure_search|s3_vectors` (también con `INDEXER__BROKER=sqs|servicebus`).
- [ ] Carril A: `scripts/managed_ingestion.py aws --apply` y `azure --apply`; comprobar que el vectorizador de Azure usa 1024 dimensiones.
- [ ] Medir por carril (ingesta completa, incremental, coste real, latencia) y completar `docs/fase-3-comparativa.md`.

Desde la **fase 4** (código, composición, IaC y aprovisionamiento cerrados y probados en local; falta la validación en cloud, todo con coste y confirmación). Requiere antes los pendientes de las fases 2 y 3:

- [ ] Clasificar el corpus con `acl` explícita y reingerir: `provision_search_index.py --apply` (el esquema añade `title`, `acl` y configuración semántica) y resincronizar Bedrock KB con los sidecars nuevos. Sin `acl`, los adaptadores cloud deniegan.
- [ ] AI Search en tier `basic` para Semantic Ranker y Foundry IQ (plan semántico gratuito, ya en Terraform). Revisar el coste antes del `apply`.
- [ ] Foundry IQ: crear el proyecto de Foundry en el portal, rellenar `FOUNDRY_IQ__*`, pasar `foundry_project_principal_id` a Terraform (rol *Search Index Data Reader*) y ejecutar `scripts/provision_foundry_iq.py --apply` (verifica el `baseFilter` de la audiencia).
- [ ] Bedrock: habilitar Cohere Rerank y rellenar `BEDROCK__RERANK_MODEL_ARN` y `BEDROCK_KB__GENERATION_MODEL_ARN`.
- [ ] Ejecutar el servicio con `RETRIEVAL__BACKEND=azure_search`, `RETRIEVAL__LLM=gateway`, `RETRIEVAL__RERANKER=bedrock` y `RETRIEVAL__MANAGED=bedrock_kb|foundry_iq`; comparar A/B/C con el mismo corpus y las mismas preguntas (coste, latencia, citas y abstención) y documentarlo en `docs/fase-4-retrieval.md`.

## Entorno del usuario

- Windows + PowerShell 5.1, Docker Desktop (WSL2), `gh` autenticado como `SergioVelmay`. Repo privado: `SergioVelmay/GenAI_PoCs`.
- El terminal integrado a veces introduce un carácter espurio al inicio del comando. No encadenes acciones destructivas detrás de pasos que puedan fallar, y verifica el resultado antes de continuar.
