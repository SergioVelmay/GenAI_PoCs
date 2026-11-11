# Checkpoint 12→13 · Repaso para la entrevista y plan de incorporación de GCP

**Objetivo:** consolidar lo aprendido en las fases 0–12 (glosario, inventario de librerías y
arquitectura por niveles) y dejar **atado y documentado** el plan para añadir **Google Cloud (GCP)**
como tercera nube en todo lo construido, con paridad total con Azure y AWS. La implementación de GCP
empieza en otra sesión.

| Subfase | Commit | Qué |
|---|---|---|
| 12→13.1 | `a0edbea` | [Glosario](glosario.md): ~170 términos en 13 bloques e inventario de librerías e imágenes |
| 12→13.2 | `b00743d` | [Arquitectura por niveles](arquitectura.md): vista general, 11 zooms y el recorrido de un caso |
| 12→13.3 | (este cierre) | Decisión de incorporar GCP, verificación previa y plan por fases ([sección 3](#plan-gcp)) |

## 1. Glosario e inventario (12→13.1)

- Cada término tiene "qué es" (una frase para alguien de negocio) y "en este repo" (dónde se usa y
  con qué números): fundamentos, modelos y coste, RAG, evals, MCP e identidad, agentes, harness,
  multi-agente, escala, observabilidad, red teaming y LLMOps, arquitectura y negocio.
- Inventario: todas las dependencias de los `pyproject.toml`, las imágenes de `docker-compose.yml` y
  las herramientas externas (promptfoo, Terraform), con su alternativa o equivalente.

## 2. Arquitectura por niveles (12→13.2)

- Nivel 0: el sistema en una pantalla. Nivel 1: un zoom por bloque con "qué contar en 30
  segundos". Nivel 2: un email de reembolso de punta a punta para la pizarra.
- Los 13 diagramas se validaron renderizándolos con `mermaid-cli` (13/13).

<a id="plan-gcp"></a>

## 3. Plan de incorporación de GCP (12→13.3)

### 3.1 Decisiones (09-11-2026)

| Decisión | Elegido | Por qué |
|---|---|---|
| Encaje | **Pasada GCP** por las fases 0–12 como bloque propio, con subfases `N.G1`, `N.G2`... y commits `feat: fase 3.G1 - ...` | Cada fase queda completa en su doc (tres nubes) y se vuelve "a donde estábamos" con GCP integrado |
| Profundidad | **Paridad total**: todo lo que existe para Azure o AWS (adaptadores, carriles, sabores, motores, IaC, scripts, evals, docs) existe también para GCP, aunque GCP "no aporte nada nuevo" | El repo es para aprender; probar cada servicio es el objetivo |
| Sabores y motores | `adk` (carril C), `agentplatform` (carril A) en el agente y **ADK como cuarto motor** del orquestador | Paridad con LangGraph/MAF/Strands y con Foundry/AgentCore |
| Carriles de datos | Todos los que ofrezca GCP: RAG Engine y Vertex AI Search (A), Vector Search (B), LlamaIndex + pgvector en Cloud SQL/AlloyDB (C) | Paridad con AI Search + Foundry IQ (Azure) y Bedrock KB + S3 Vectors (AWS) |
| Fases de despliegue | 13 AWS, 14 Azure, **15 GCP**; extras a 16+ | Mismo patrón espejo |
| Presupuesto | **100 $** (como AWS), intentando usar los créditos de prueba de una cuenta nueva | Alertas al 50, 75 y 100 % |
| Ejecución en la nube | Ninguna por ahora: todo local y sin coste; lo de la nube, a `pendientes-cloud.md` | Regla 5 de `AGENTS.md` |

### 3.2 Verificación previa (hecha en este cierre)

Antes de diseñar sobre GCP se comprobó su estado en la documentación oficial (lección de *On Your
Data*) y que sus librerías conviven con las nuestras:

| Hallazgo | Consecuencia en el plan |
|---|---|
| Vertex AI se presenta ahora como **Gemini Enterprise Agent Platform**: Agent Engine pasa a **Agent Runtime**, con Sessions, Memory Bank, Example Store, Evaluation Service, Agent Gateway, Agent Registry y Agent Identity | Usar los nombres nuevos en docs y código; los recursos de Terraform y las rutas del SDK pueden conservar el nombre antiguo (verificar en cada subfase) |
| El módulo generativo del SDK de Vertex (`vertexai.generative_models`, `caching`...) **se retiró el 24-06-2026** | Todo el acceso a Gemini con **`google-genai`**; `google-cloud-aiplatform` solo para RAG Engine, Agent Runtime, evaluación y experimentos |
| Modelos actuales: **Gemini 3 Flash** y **Gemini 3.1 Pro** | Barato/potente del router; el nombre exacto y los precios se fijan en la 2.G1 |
| **Vector Search** se paga por VMs que sirven el índice (mínimo del orden de < 100 $/mes) | Carril B efímero, igual que AI Search: `vector_search_enabled` solo durante las sesiones |
| **RAG Engine** guarda los vectores en un Spanner gestionado que se factura; GA en `europe-west3/4`, lista de acceso en `us-central1` y `europe-west1` | Región `europe-west4`; elegir el tier más barato y apagar al terminar |
| **Agent Gateway** gobierna tráfico usuario↔agente, agente↔MCP y agente↔agente, con Model Armor, políticas de IAM, *semantic governance* y cuotas de tokens (algunas en preview) | Equivalente de AgentCore Gateway / APIM en las fases 6 y 10 |
| **Managed Agents API** está en Pre-GA, con sandbox propio y "no usar datos confidenciales" | Se explora en un *spike* de la 7.G; solo será sabor si admite tools ejecutadas por nuestro harness |
| El emulador de Pub/Sub escucha por defecto en el `localhost` **del contenedor** (no en IPv6, como se anotó al principio: corregido en la 0.G3 tras comprobarlo) y no admite `gcloud pubsub` | Arrancarlo con `--host-port=0.0.0.0:8085` y conectar a `127.0.0.1` |
| Los *services* de GitHub Actions no admiten `command` | El emulador de Pub/Sub se arranca en la CI con un paso `docker run` |
| Prueba de resolución en una copia del workspace (`uv add` de `google-adk`, `google-genai`, `google-cloud-{pubsub,aiplatform,discoveryengine,dlp,modelarmor,monitoring,storage}`): **resuelve** con ADK 2.14 | Cambian `websockets` 16.1 → 15.0 (ADK exige `<16`), `authlib` 1.8 → 1.9 y `beartype` (de rc a estable). Se valida con la CI completa en la 0.G1 |

### 3.3 Mapa de equivalencias por fase

La columna GCP es lo que se construye en la pasada. **Local** = verificable sin coste ahora;
**Nube** = va a `pendientes-cloud.md`.

| Fase | Azure (hecho) | AWS (hecho) | GCP (a construir) | Local | Nube |
|---|---|---|---|---|---|
| 0 | Bicep→Terraform, Entra ID, emulador de Service Bus | Terraform, IAM, ElasticMQ | `infra/terraform/gcp` (proveedores `google` y `google-beta`), presupuesto de Cloud Billing, Workload Identity Federation, emulador de Pub/Sub | `terraform validate`, perfil `pubsub` | `apply` |
| 1 | PostgreSQL Flexible | RDS/Aurora | Cloud SQL para PostgreSQL / AlloyDB, BigQuery como almacén analítico | Docs (el código no cambia) | Fase 15 |
| 2 | Azure OpenAI, caché automática | Bedrock Converse, `cachePoint` | `GeminiLLM` (`google-genai`): tools, salida estructurada, streaming, caché **implícita y explícita**, errores 429/safety; LiteLLM `vertex_ai/`; fallback entre 3 nubes | Tests con cliente falso | Benchmarks de caché y gateway |
| 3 | Service Bus, AI Search (B), indexer + skillset (A) | SQS, S3 Vectors (B), Bedrock KB (A) | **Pub/Sub** (contrato común + DLQ), embeddings Gemini a 1024, **Vector Search** (B), **RAG Engine** desde Cloud Storage (A), LlamaIndex + pgvector (C) | Emulador, dobles | Ingesta y coste |
| 4 | AI Search híbrida + Semantic Ranker, Foundry IQ | KB Retrieve/RetrieveAndGenerate, Bedrock Rerank | Vector Search híbrida (denso + disperso) con `restricts` como ACL, **Ranking API**, RAG Engine `retrieveContexts`, **Vertex AI Search** con respuestas citadas | Dobles | Comparativa A/B/C |
| 5 | Jueces con Azure, Foundry Evaluation | Bedrock Evaluations | Juez Gemini fijo fuera del router, RAGAS/DeepEval con Gemini, **Gen AI Evaluation Service** | Dobles | Calibración (kappa) |
| 6 | APIM, Entra OBO | AgentCore Gateway, Cognito | **Agent Gateway** + Agent Registry + Agent Identity, Apigee (equivalente de APIM), intercambio de tokens con STS / Identity Platform, MCP en Cloud Run | Tests del *exchanger* | OBO de extremo a extremo |
| 7 | Sabor `foundry` | Sabor `agentcore` + Memory | Sabores **`adk`** (C) y **`agentplatform`** (A: Agent Runtime + Sessions + **Memory Bank**), emulador local | 14 escenarios, paridad, ataques | Agente real y memoria |
| 8 | Content Safety / Prompt Shields | Bedrock Guardrails | **Model Armor** (`shield: gcp`) y **Sensitive Data Protection** (`pii: gcp`), safety settings de Gemini | Dobles | 24 textos con el servicio |
| 9 | MAF, Container Apps, Durable Functions | Strands, AgentCore A2A, Step Functions | **ADK** como 4.º motor, A2A en Cloud Run / Agent Runtime con ID tokens de Google, Pub/Sub en el flujo asíncrono (*exactly-once*), Workflows como espera humana | Paridad de 4 motores, emulador | Flujo real |
| 10 | Managed Redis, KEDA, Azure Load Testing | ElastiCache, ECS target tracking | **Memorystore (Valkey)**, worker pools de Cloud Run o GKE + KEDA `gcp-pubsub`, métrica de backlog de Pub/Sub, Cloud Armor y cuotas de tokens del gateway, Locust en GKE/Cloud Run jobs | Tests, IaC | Autoescalado y carga |
| 11 | App Insights, KQL | X-Ray, CloudWatch EMF, ADOT | **`collector-gcp.yaml`** (Cloud Trace, Logging, Managed Prometheus), `traceparent` en atributos de Pub/Sub, alertas en Cloud Monitoring (PromQL) | CI valida el collector; emulador | Trazas reales |
| 12 | Evaluadores de Foundry, AI Red Teaming Agent, prompts de Foundry, Azure ML | Bedrock evaluations, Prompt Management, SageMaker MLflow | Evals de trayectoria del Evaluation Service, promptfoo/PyRIT contra Gemini, **gestión de prompts** de Agent Platform, **Vertex AI Experiments** como tracker, drift en Managed Prometheus | Dobles | Calibración, A/B real |

### 3.4 Subfases por fase

Cada subfase termina con verificación (los mismos comandos que la CI), doc de la fase actualizado
(columna GCP en las comparativas y sección "GCP"), pendientes de GCP en `pendientes-cloud.md`,
pregunta de GCP en `preguntas-entrevista.md` y "revisado" de Sergio antes del commit.

**0.G · Cimientos multinube** (incluye la fase 1, solo documental)

- 0.G1: dependencias de GCP en el lock (`websockets` 15, `authlib`, `beartype`) con la suite completa y la CI en verde; `GcpSettings` (proyecto, región, `location` de los modelos) y `LLMProvider.GCP` sin implementación.
- 0.G2: `infra/terraform/gcp` (APIs con `google_project_service`, presupuesto con alertas, cuenta de servicio de desarrollo sin claves) y `gcp` en la matriz `terraform` de la CI.
- 0.G3: perfil `pubsub` en `docker-compose.yml` (imagen `google-cloud-cli:emulators`) y arranque en la CI con `docker run`.
- 0.G4: chuleta de `preguntas-entrevista.md` a tres columnas (Azure → AWS → GCP), comparativas de las fases 0 y 1, glosario con términos de GCP y `CLOUDSDK_CONFIG` aislado en `AGENTS.md` (sin claves de cuentas de servicio: ADC de usuario e *impersonation*).

**2.G · Modelos y gateway**

- 2.G1: `adapters/gemini.py` con `google-genai`: chat, tools (incluidas las *thought signatures* que Gemini 3 pide devolver en el siguiente turno; comprobarlo al empezar), salida estructurada, `StreamingLLMPort`, uso con tokens cacheados, mapeo de errores (429 → `LLMRateLimitError`, safety → `LLMContentFilterError`, 503, *deadline*). Precios en la tabla versionada.
- 2.G2: gateway con tres proveedores (routing barato/potente, fallback en cadena), LiteLLM con `vertex_ai/`, Langfuse; caché implícita frente a explícita (`CachedContent` con TTL y coste de almacenamiento) en `llm_cache_benchmark.py --provider gcp`.

**3.G · Indexación**

- 3.G1: `adapters/pubsub.py` en el test de contrato de brokers (integración con el emulador): *ack deadline*, reintentos y *dead-letter topic* (mínimo 5 entregas; si el emulador no lo soporta, doble y pendiente en la nube).
- 3.G2: embeddings Gemini (`output_dimensionality=1024` y `task_type` distinto para documento y consulta) y `adapters/gcp_vector_search.py` (carril B: upsert y borrado en streaming, mismo orden upsert → borrado).
- 3.G3: carril A con RAG Engine (`managed_ingestion.py gcp` con MD5 antes de subir a Cloud Storage) y Terraform (`lane_a.tf`, `main.tf`: temas, suscripciones con DLQ, bucket, índice y endpoint efímeros). Carril C: LlamaIndex + pgvector, mismo código.

**4.G · Retrieval**

- 4.G1: `adapters/gcp_retrieval.py`: Vector Search híbrida con ACL como `restricts` (prefiltro en el servidor) y reranker con la Ranking API (`RerankerPort`).
- 4.G2: carril A: RAG Engine `retrieveContexts` y Vertex AI Search (respuesta con citas, equivalente de Foundry IQ); el adaptador rechaza filtros que no pueda garantizar. Scripts de aprovisionamiento en dry-run y composición explícita (`RETRIEVAL__ENGINE=gcp`, `RETRIEVAL__MANAGED=rag_engine|vertex_search`).

**5.G · Evals de RAG**

- 5.G1: juez Gemini fijo (distinto del generador), RAGAS y DeepEval con Gemini, `rag_evals.gcp_eval` con el Gen AI Evaluation Service (como `foundry_eval`) y `managed_eval` para RAG Engine y Vertex AI Search.

**6.G · Tools y MCP**

- 6.G1: bucle de tools y `support_agent` con Gemini (llamadas paralelas y *thought signatures*); `GcpTokenExchanger` (STS / Identity Platform) con tests, como `EntraOboExchanger`.
- 6.G2: Terraform del MCP gateway gestionado: Agent Gateway + Agent Registry + Agent Identity (`agent_gateway_enabled`) y Apigee (`apigee_enabled`), desactivados por defecto.

**7.G · Agentes** (la más grande: dos sesiones)

- 7.G1: puente `bridges/adk.py` (un `BaseLlm` de ADK que llama a nuestro `LLMPort`, tools por `JournaledExecutor`) y sabor `adk`: 14 escenarios, paridad de tools, invariantes y context engineering (compactación propia frente a la de ADK).
- 7.G2: sabor `agentplatform` (Agent Runtime con emulador local que **avisa** cuando no toca la nube) y `adapters/memory_bank.py` (`CheckpointStore` con Sessions y `PreferenceStore` con Memory Bank); Terraform `agent_platform.tf`.
- 7.G3: *spike* de la Managed Agents API; baselines, informes y ataques con **9 sabores**.

**8.G · Harness**

- 8.G1: `adapters/model_armor.py` (`GUARDRAILS__SHIELD=gcp`), PII con Sensitive Data Protection (`GUARDRAILS__PII=gcp`), plantilla de Model Armor en Terraform, Gemini en `rag_evals.shields` y en los ataques con modelo real (aplazado).

**9.G · Multi-agente**

- 9.G1: ADK como cuarto motor del orquestador (triaje con tabla del dominio, especialistas, revisión humana con confirmación de tools de ADK) con test de paridad frente a LangGraph, MAF y cola.
- 9.G2: `ORCHESTRATOR__BROKER=pubsub` con el outbox y *exactly-once delivery* como primera capa de deduplicación; A2A con ID tokens de Google (audience por especialista) en `remote/auth.py`; Terraform de Cloud Run y Workflows.

**10.G · Escala**

- 10.G1: métrica de backlog de Pub/Sub para el autoescalador (`queue_metrics`), Memorystore para Valkey (rate limit y caché semántica; comprobar que su búsqueda vectorial cubre lo que usa `RedisSemanticCache`) y umbral de la caché para embeddings de Gemini (pendiente de calibrar).
- 10.G2: Terraform de workers (worker pools de Cloud Run o GKE + KEDA `gcp-pubsub`), Cloud Armor y cuotas de tokens; plan de carga con Locust en la nube.

**11.G · Observabilidad**

- 11.G1: `infra/otel/collector-gcp.yaml` validado en la CI, propagación de `traceparent` por atributos de Pub/Sub (test con el emulador) y `observability.tf` (políticas de alerta en PromQL, canal de notificación, retención y exclusiones de logs).

**12.G · Evals de agentes y LLMOps**

- 12.G1: juez de agentes con Gemini, métricas de trayectoria del Evaluation Service como contraste de la 12.1, promptfoo y PyRIT contra Gemini (aplazado).
- 12.G2: publicación de prompts en la gestión de prompts de Agent Platform (dry-run), `VertexExperimentTracker` junto a MLflow, reglas de drift en Managed Service for Prometheus.
- 12.G3: cierre de la pasada: glosario, arquitectura (nivel 1.11 con tres nubes), preguntas, pendientes, `AGENTS.md` y `README.md`.

### 3.5 Convenciones de la pasada

- Prefijo de adaptadores: `gemini.py`, `pubsub.py`, `gcp_*.py`, `model_armor.py`, `memory_bank.py`; settings anidados `GCP__PROJECT`, `GCP__REGION`, `GEMINI__MODEL_CHEAP`...
- Región `europe-west4` para los recursos; modelos en el endpoint `global` si la región no los sirve (verificar en la 2.G1).
- `gcloud` aislado como `az`: `$env:CLOUDSDK_CONFIG = "$env:USERPROFILE\.gcloud-headache"` antes de cualquier comando de GCP, Terraform o scripts; nunca claves JSON de cuentas de servicio.
- Recursos con coste por hora (Vector Search, RAG Engine, Memorystore, GKE) detrás de una variable `*_enabled = false` y efímeros, como AI Search.
- Los dobles de test se construyen desde la documentación ahora y se reemplazan por **respuestas reales capturadas** en la primera validación en la nube (lección del checkpoint 8→9).
- En la tabla de estado de `AGENTS.md`, la columna **GCP** marca el avance por fase.

### 3.6 Riesgos

| Riesgo | Mitigación |
|---|---|
| Cambios de nombre y de API (el *rebrand* ya ocurrió) | Comprobar la documentación al empezar cada subfase; todo detrás de puertos |
| Funciones en preview (Agent Gateway, Managed Agents API, cuotas de tokens) | Marcarlas en el doc; ninguna en el camino crítico de los tests |
| Más dependencias y CI más lenta (9 sabores, 4 motores) | Vigilar la duración de la CI por job; extras opcionales si hace falta |
| Emulador de Pub/Sub incompleto | Test de contrato con *skip* explícito y documentado de lo que no soporta |
| Coste por hora de Vector Search y RAG Engine | Efímeros, presupuesto con alertas, validar en una sola sesión |

### 3.7 Orden de sesiones

Una sesión por fase con la skill `iniciar-fase` ("vamos a por la 0.G"): 0.G → 2.G → 3.G → 4.G → 5.G →
6.G → 7.G (dos sesiones) → 8.G → 9.G → 10.G → 11.G → 12.G. Unas 13 sesiones, coste 0. Al terminar,
se retoma la fase 13 (AWS), después la 14 (Azure) y la 15 (GCP).

## 4. Cierre

- Hecho: glosario e inventario, arquitectura por niveles y plan de GCP con verificación previa.
- Pendiente para el repaso de entrevista (no planificado aún): historias STAR, temas no cubiertos
  (OWASP LLM Top 10, EU AI Act, fine-tuning frente a RAG, multimodal, FinOps) y un simulacro.
- Pendientes con coste: sin cambios ([pendientes-cloud.md](pendientes-cloud.md)); los de GCP se
  añaden en cada subfase de la pasada.

## Checkpoint de entrevista

[Preguntas del checkpoint 12→13](preguntas-entrevista.md#checkpoint-12-13).
