# Conectores candidatos y pasada de paridad

Origen: investigación de Sergio del 23-11-2026 sobre conectores (servicios gestionados, librerías y
herramientas) que el repo aún no tiene. En el checkpoint G→13 B (27-11-2026) se contrastó cada uno
con el repo y se decidió una **pasada de paridad (P)** antes de los despliegues: las fases 13, 14 y
15 deben partir de las **tres nubes iguales**.

Leyenda: **Puerto** = puerto del core donde entraría como adaptador. **Esfuerzo**: S (una tarde),
M (una subfase), L (una fase).

## 1. Cómo se comprobó

Búsqueda de cada servicio en el código, la IaC y los docs (`rg` sin `docs/` ni `*.md` para el
código). **Ninguno de los servicios de nube de este documento está implementado.** Donde aparecen en
el código es solo en comentarios de equivalencias (`infra/terraform/gcp/workflows.tf`,
`infra/terraform/gcp/edge_armor.tf`, `infra/terraform/gcp/model_armor.tf`,
`adapters/redis_rate_limit.py`, `adapters/experiment_trackers.py`...). Matices:

- **APIM** existe como **MCP gateway** (fase 6.4, `apim_enabled`), no como AI gateway delante de
  Azure OpenAI.
- **AWS** detecta PII dentro del escudo (Bedrock Guardrails, `sensitive_information_policy_config`
  en `infra/terraform/aws/guardrails.tf`), pero no como detector de `GUARDRAILS__PII`, que hoy
  admite `regex`, `presidio` y `gcp` (Sensitive Data Protection).
- **Eventarc** solo se usa como disparador del workflow de revisión humana (9.G5).
- **GCP** ya usa el *layout parser* dentro de Agent Search (4.G), pero no un parser gestionado
  antes del troceo propio.

<a id="pasada-p"></a>

## 2. Pasada de paridad (P)

Misma mecánica que la pasada GCP: subfases `N.P1`, `N.P2`... por fase (`feat: fase 8.P1 - ...`),
una rama y una PR por fase (`fase/<N>-p`), código y Terraform con dobles en local y coste 0 (regla
5), sección "Paridad (pasada N.P)" en el doc de la fase, preguntas en `preguntas-entrevista.md` y lo
real en `pendientes-cloud.md`. Al iniciar cada `N.P` se comprueban la API instalada y el estado del
servicio (GA o preview) antes de proponer las subfases.

### 2.1 Huecos de paridad (compromiso)

Una nube lo tiene implementado y las otras no.

| Pasada | Ya implementado | Falta | Puerto | Esfuerzo |
|---|---|---|---|---|
| 5.P | Carril A de evals en Azure (Foundry Evaluation) y GCP (Gen AI Evaluation Service) | **Bedrock Evaluations** (AWS). Antes asignado a la fase 13: se adelanta aquí | Juez y evals de `rag_evals` | M |
| 6.P | Registro de servidores MCP en GCP (Agent Registry, 6.G) | **API Center** (Azure); el equivalente de AWS se comprueba al iniciar la 6.P | Terraform | S |
| 7.P | Memoria gestionada en AWS (AgentCore Memory) y GCP (Memory Bank) | **Memoria de Foundry** (Azure, preview) | Memoria del agente (`adapters/agentcore_memory.py`, `adapters/memory_bank.py`) | S–M |
| 8.P | PII gestionado en GCP (Sensitive Data Protection) | **Azure AI Language (PII)** y **Amazon Comprehend (PII)** en `GUARDRAILS__PII` | Detector de PII (`harness/pii.py`) | S c/u |
| 9.P | Espera humana con plazo en GCP (Workflows con `await_callback`, 9.G5) | **Step Functions** (`.waitForTaskToken` + `TimeoutSeconds`) y **Durable Functions** (`WaitForExternalEvent` + temporizador). Estaba en "Ideas aparcadas" | `ReviewHandler`, `CaseReviewService` (ya comunes) | M c/u |
| 12.P | Prompts versionados en Langfuse y Agent Platform (12.G4) | **Prompts de Foundry** y **Bedrock Prompt Management** | `PromptStorePort` | S c/u |
| 12.P | Experimentos en MLflow local y Vertex AI Experiments | **MLflow gestionado de SageMaker y de Azure ML** (hoy solo se menciona que cambia la URI) | `ExperimentTrackerPort` | S–M |

### 2.2 Ampliaciones de las tres nubes (se deciden al iniciar cada `N.P`)

Ninguna nube las tiene. Entran en la pasada de su fase, después de los huecos de 2.1, con su
equivalente en las otras dos nubes (o la constatación de que no existe).

| Pasada | Candidato | Por qué | Puerto | Esfuerzo |
|---|---|---|---|---|
| 2.P | **Bedrock Intelligent Prompt Routing** y su equivalente en Azure y GCP | Router gestionado frente al routing propio del gateway | `LLMPort` / gateway | M |
| 2.P | **Mismo modelo por otra nube**: Claude en Agent Platform (Model Garden), Mistral en Bedrock | Fallback "mismo modelo, otra nube" en vez de "otro modelo" | `LLMPort` (nuevos IDs) | S |
| 3.P | **Parsing gestionado**: Document Intelligence / Content Understanding, Textract / Bedrock Data Automation, Document AI / Layout Parser de RAG Engine | PDF, Office y tablas antes del troceo | Indexador | M |
| 3.P | **Event Grid · EventBridge · Eventarc** para "documento publicado" | Eventos sin cola propia frente al contrato de brokers | Indexador / brokers | M |
| 3.P | **Event Hubs** (tiene emulador oficial, inventariado en el G→13) | Log tipo Kafka en vez de cola | Contrato de brokers | M |
| 4.P | **Cosmos DB for NoSQL con vectores** (su emulador admite índices vectoriales) | Vectores de Azure en local | `VectorStorePort` | M |
| 4.P | **pgvector gestionado con DiskANN** (Azure Flexible Server) / **AlloyDB ScaNN** / Aurora | El mismo `PgVectorStore` en las versiones gestionadas | `VectorStorePort` | S–M |
| 4.P | **GraphRAG** con Neptune Analytics (Bedrock KB) y Spanner Graph | RAG sobre grafo para preguntas de relación | `RetrievalPort` | L |
| 6.P | **AgentCore Policy (Cedar)** y su equivalente en Azure y GCP | Política por tool y por argumento (p. ej., reembolso < 200) en el gateway | `mcp_gateway.tf` + tests | M |
| 6.P | **AgentCore Identity con OAuth saliente** y su equivalente | Tools que llaman a APIs de terceros en nombre del usuario | MCP gateway | M |
| 7.P | **Bedrock Agents (clásico)** | El agente gestionado anterior a AgentCore; contraste con Foundry Agent Service | `AgentRuntime` | M |
| 12.P | **AgentCore Evaluations** y su equivalente | Evaluación online por muestreo de trazas | Evals de agentes | M |
| 12.P | **AI Red Teaming Agent** de Foundry y su equivalente | Red teaming gestionado frente a promptfoo y PyRIT (hoy en pendientes de la fase 12) | `rag_evals.redteam` | M |

### 2.3 Se quedan en las fases de despliegue

Son configuración de la infraestructura desplegada, sin código de dominio nuevo: se hacen al
desplegar y no rompen la paridad.

| Fase | Servicios |
|---|---|
| 13 (AWS) | *Usage plans* de API Gateway, AgentCore Observability, KEDA en EKS, CDK, SAM local |
| 14 (Azure) | APIM como AI gateway (`llm-token-limit`, caché semántica), Front Door + WAF, Foundry Tracing, KEDA en AKS, Bicep, Functions Core Tools, Microsoft MCP Gateway (proxy MCP en AKS) |
| 15 (GCP) | AlloyDB, KEDA en GKE, Infrastructure Manager (Terraform gestionado con el mismo módulo) |

Sigue aparcado: el sabor `managedagents` (Managed Agents API de GCP, Pre-GA; ver
[plan](plan-fases.md#ideas-aparcadas-sin-fase-asignada)).

## 3. Backlog sin compromiso (OSS, SaaS y locales)

No entra en la pasada. Sirve para elegir contrastes cuando una fase lo pida.

> **Regla 4** ("solo modelos cloud; Docker en local solo ejecuta infraestructura"): los candidatos
> con modelos locales (Ollama, Docker Model Runner, Foundry Local, clasificadores de seguridad y
> rerankers en local) chocan con ella. Si se quieren, antes hay que decidirlo como cambio de plan
> (p. ej., un perfil `local-models` opcional y fuera de la CI).

| Capa | Candidatos |
|---|---|
| LLM gateway (`LLMPort`) | Proveedores por API directa (OpenAI, Anthropic, Mistral, xAI, DeepSeek), OpenRouter, gateways de terceros (Envoy AI Gateway, agentgateway, Kong AI Gateway, Portkey OSS); vLLM ya está en la fase 16+ |
| MCP gateway e identidad | Keycloak como IdP OAuth real en local, Docker MCP Gateway, agentgateway, IBM ContextForge |
| Agentes (`AgentRuntime`) | OpenAI Agents SDK, Claude Agent SDK, Pydantic AI, CrewAI, smolagents |
| Multi-agente y asíncrono | Temporal, Dapr Agents / Workflows, Kafka / Redpanda como cuarto broker |
| Indexación | Docling, MarkItDown, Unstructured; embeddings de Cohere o Voyage |
| Retrieval y vectores | Qdrant, Weaviate, Milvus, OpenSearch en local, LightRAG; rerank de Voyage o Cohere por API |
| Harness y guardrails | Llama Guard / Prompt Guard, gpt-oss-safeguard / Qwen3Guard (encajan en el "clasificador razonador" aparcado), NeMo Guardrails, Guardrails AI, Lakera Guard |
| Edge y escala | Garnet y Dragonfly como backends compatibles con Redis, k6 frente a Locust |
| Observabilidad (`TelemetryPort`) | Arize Phoenix, Comet Opik, Grafana Tempo + Loki, Aspire Dashboard, OpenLIT / OpenLLMetry, Datadog LLM Observability, LangSmith |
| Evals y LLMOps | garak, Inspect AI, Evidently |
| Despliegue e IaC | OpenTofu, Pulumi (Python), kind / k3d, Argo CD / Flux |
| Otras nubes | OCI Generative AI, watsonx.ai, Databricks Mosaic AI, Snowflake Cortex, Workers AI: otro adaptador de `LLMPort` (y a veces de `VectorStorePort`), sin prioridad |

Las afirmaciones sobre terceros (licencias, compras de empresas, fechas de GA) no se han verificado
en este checkpoint: se comprueban con fuentes oficiales al elegir un candidato.
