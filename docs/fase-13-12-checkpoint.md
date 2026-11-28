# Checkpoint 12→13 · Repaso de lo aprendido y plan de incorporación de GCP

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
| Modelos actuales: **Gemini 3 Flash** y **Gemini 3.1 Pro** | Barato/potente del router; el nombre exacto y los precios se fijan en la 2.G1. **Corregido en la 2.G1 (14-11-2026):** la gama ya era Gemini 4 y 3.x Flash; se eligen `gemini-4-hydrogen` / `gemini-3.5-flash-lite` ([fase 2](fase-2-modelos.md#10-gcp-pasada-2g)) |
| **Vector Search** se paga por VMs que sirven el índice (mínimo del orden de < 100 $/mes) | Carril B efímero, igual que AI Search: `vector_search_enabled` solo durante las sesiones |
| **RAG Engine** guarda los vectores en un Spanner gestionado que se factura; GA en `europe-west3/4`, lista de acceso en `us-central1` y `europe-west1` | Región `europe-west4`; elegir el tier más barato y apagar al terminar |
| **Agent Gateway** gobierna tráfico usuario↔agente, agente↔MCP y agente↔agente, con Model Armor, políticas de IAM, *semantic governance* y cuotas de tokens (algunas en preview) | Equivalente de AgentCore Gateway / APIM en las fases 6 y 10 |
| **Managed Agents API** está en Pre-GA, con sandbox propio y "no usar datos confidenciales" | Se explora en un *spike* de la 7.G; solo será sabor si admite tools ejecutadas por nuestro harness. **7.G5:** la Gemini API sí las admite (*custom functions*); en Agent Platform, sin confirmar: idea aparcada en el plan |
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
| 3 | Service Bus, AI Search (B), indexer + skillset (A) | SQS, S3 Vectors (B), Bedrock KB (A) | **Pub/Sub** (contrato común + DLQ), embeddings Gemini a 1024, **Agent Retrieval** (antes Vector Search 2.0; B), **RAG Engine** desde Cloud Storage (A), LlamaIndex + pgvector (C) | Emulador, dobles | Ingesta y coste |
| 4 | AI Search híbrida + Semantic Ranker, Foundry IQ | KB Retrieve/RetrieveAndGenerate, Bedrock Rerank | Agent Retrieval híbrida (vector + texto completo con RRF) con filtros JSON como ACL, **Ranking API**, RAG Engine `retrieveContexts`, **Agent Search** (antes Vertex AI Search; confirmado en la 4.G) con respuestas citadas | Dobles | Comparativa A/B/C |
| 5 | Jueces con Azure, Foundry Evaluation | Bedrock Evaluations | Juez Gemini fijo fuera del router, RAGAS/DeepEval con Gemini, **Gen AI Evaluation Service** | Dobles | Calibración (kappa) |
| 6 | APIM, Entra OBO | AgentCore Gateway, Cognito | **Agent Gateway** + Agent Registry + Agent Identity, Apigee (equivalente de APIM), token por servidor con `signJwt` a partir de Identity Platform (sin OBO nativo; corregido en la 6.G), MCP en Cloud Run | Tests del *exchanger* | OBO de extremo a extremo |
| 7 | Sabor `foundry` | Sabor `agentcore` + Memory | Sabores **`adk`** (C) y **`agentplatform`** (A: Agent Runtime + Sessions + **Memory Bank**), emulador local | 14 escenarios, paridad, ataques | Agente real y memoria |
| 8 | Content Safety / Prompt Shields | Bedrock Guardrails | **Model Armor** (`shield: gcp`) y **Sensitive Data Protection** (`pii: gcp`), safety settings de Gemini | Dobles | 24 textos con el servicio |
| 9 | MAF, Container Apps, Durable Functions | Strands, AgentCore A2A, Step Functions | **ADK** como 4.º motor, A2A en Cloud Run / Agent Runtime con ID tokens de Google, Pub/Sub en el flujo asíncrono (*exactly-once*), Workflows como espera humana | Paridad de 4 motores, emulador | Flujo real |
| 10 | Managed Redis, KEDA, Azure Load Testing | ElastiCache, ECS target tracking | **Memorystore (Valkey)**, worker pools de Cloud Run o GKE + KEDA `gcp-pubsub`, métrica de backlog de Pub/Sub, Cloud Armor y cuotas de tokens del gateway, Locust en GKE/Cloud Run jobs | Tests, IaC | Autoescalado y carga |
| 11 | App Insights, KQL | X-Ray, CloudWatch EMF, ADOT | **`collector-gcp.yaml`** (Cloud Trace, Logging, Managed Prometheus), `traceparent` en atributos de Pub/Sub, alertas en Cloud Monitoring (PromQL) | CI valida el collector; emulador | Trazas reales |
| 12 | Evaluadores de Foundry, AI Red Teaming Agent, prompts de Foundry, Azure ML | Bedrock evaluations, Prompt Management, SageMaker MLflow | Evals de trayectoria del Evaluation Service, promptfoo/PyRIT contra Gemini, **gestión de prompts** de Agent Platform, **Vertex AI Experiments** como tracker, drift en Managed Prometheus | Dobles | Calibración, A/B real |

### 3.4 Subfases por fase

Cada subfase termina con verificación (los mismos comandos que la CI), doc de la fase actualizado
(columna GCP en las comparativas y sección "GCP"), pendientes de GCP en `pendientes-cloud.md`,
pregunta de GCP en `preguntas-y-respuestas.md` y "revisado" de Sergio antes del commit.

**0.G · Cimientos multinube** (incluye la fase 1, solo documental)

- 0.G1: dependencias de GCP en el lock (`websockets` 15, `authlib`, `beartype`) con la suite completa y la CI en verde; `GcpSettings` (proyecto, región, `location` de los modelos) y `LLMProvider.GCP` sin implementación.
- 0.G2: `infra/terraform/gcp` (APIs con `google_project_service`, presupuesto con alertas, cuenta de servicio de desarrollo sin claves) y `gcp` en la matriz `terraform` de la CI.
- 0.G3: perfil `pubsub` en `docker-compose.yml` (imagen `google-cloud-cli:emulators`) y arranque en la CI con `docker run`.
- 0.G4: chuleta de `preguntas-y-respuestas.md` a tres columnas (Azure → AWS → GCP), comparativas de las fases 0 y 1, glosario con términos de GCP y `CLOUDSDK_CONFIG` aislado en `AGENTS.md` (sin claves de cuentas de servicio: ADC de usuario e *impersonation*).

**2.G · Modelos y gateway**

- 2.G1: `adapters/gemini.py` con `google-genai` (**2.x**: la 0.G1 lo fijó en `>=2.34,<3` porque ADK 2.14 exige `<3`; verificar las *thought signatures* contra esa versión, no contra la 3.0), sustituyendo el corte `unsupported_provider` de `llm_factory.py`: chat, tools (incluidas las *thought signatures* que Gemini 3 pide devolver en el siguiente turno; comprobarlo al empezar), salida estructurada, `StreamingLLMPort`, uso con tokens cacheados, mapeo de errores (429 → `LLMRateLimitError`, safety → `LLMContentFilterError`, 503, *deadline*). Precios en la tabla versionada. **Al empezar (14-11-2026)** cambió el plan: `ToolCall.signature` en el puerto, modelos `gemini-4-hydrogen` / `gemini-3.5-flash-lite`, `GEMINI__ENTERPRISE` en lugar de `USE_VERTEX`, `thinking_level` por tier y sin temperatura, y `roles/aiplatform.user` en Terraform.
- 2.G2: gateway con tres proveedores (routing barato/potente, fallback en cadena; `fallback_provider` solo admite uno, hay que decidir cómo encadenar tres), LiteLLM con `vertex_ai/` (comprobar cómo pasa las firmas), Langfuse y `llm_gateway_demo.py` con GCP. **Decidido:** `LLM_GATEWAY__FALLBACK_PROVIDERS` (lista ordenada separada por comas) sustituye a `FALLBACK_PROVIDER`; las firmas de LiteLLM se separan del id.
- 2.G3 (nueva al partir la 2.G2): caché implícita frente a explícita (`CachedContent` con TTL y coste de almacenamiento por hora) en `llm_cache_benchmark.py --provider gcp`. Hydrogen no admite la explícita: se mide con el modelo barato.

**3.G · Indexación**

- 3.G1: `adapters/pubsub.py` en el test de contrato de brokers (integración con el emulador): *ack deadline*, reintentos y *dead-letter topic* (mínimo 5 entregas; **el emulador sí lo soporta**, comprobado el 15-11-2026) y `pubsub.tf`.
- 3.G2: embeddings Gemini (`output_dimensionality=1024` y `task_type` distinto para documento y consulta) y `adapters/gcp_vector_search.py` (carril B: mismo orden upsert → borrado) con `vector_search.tf` efímero. **Cambio de plan (15-11-2026):** Vector Search 2.0 es GA y se llama **Agent Retrieval** (*collections* de objetos JSON, filtros `$eq`, texto completo e híbrida con RRF); se usa en lugar de Vector Search 1.0 (índice + endpoint con VMs por hora) con `google-cloud-vectorsearch` en el extra `gcp`.
- 3.G3: carril A con RAG Engine con el cliente `agentplatform` (`vertexai.rag` está obsoleto), `managed_ingestion.py gcp` con MD5 antes de subir a Cloud Storage y `lane_a.tf` (bucket, `rag_engine_config` y corpus efímeros). Carril C: LlamaIndex + pgvector, mismo código. El Terraform se reparte por subfase en vez de ir todo aquí.

**4.G · Retrieval**

- 4.G1: `adapters/gcp_retrieval.py`: Agent Retrieval híbrida (`batch_search_data_objects`: vector + texto completo con RRF) con la ACL como filtro JSON en el servidor y reranker con la Ranking API (`RerankerPort`). **Al empezar (20-11-2026)** cambió el plan: el VertexRanker integrado en el batch degrada a RRF en silencio si falla, así que el reranker por defecto es la Ranking API y el integrado queda como motor `gcp-semantic` de la comparativa; la composición es `RETRIEVAL__BACKEND=gcp_vector_search` + `RETRIEVAL__EMBEDDINGS=gcp` (no `RETRIEVAL__ENGINE`, que elige native/llamaindex) y `retrieval_eval --engine gcp|gcp-semantic`.
- 4.G2: carril A con RAG Engine: `retrieve_contexts` (solo SEMANTIC; filtros por petición rechazados) y respuesta con Gemini + tool de retrieval (`grounding_metadata` → citas validadas), `RETRIEVAL__MANAGED=rag_engine`. **Heredado de la 3.G3:** la ACL de RAG Engine se aplica eligiendo los corpus permitidos (uno por ACL; su filtro por metadatos está en preview) y se vuelve a comprobar en cada resultado.
- 4.G3 (nueva al partir la 4.G2, 20-11-2026): carril A con **Agent Search** (antes Vertex AI Search; API Discovery Engine): ingesta con `metadata.jsonl` (`managed_ingestion.py gcp-search`, dry-run), `agent_search.tf` (data store `eu` y motor Enterprise, `agent_search_enabled`), `search` y `answer_query` con citas, `RETRIEVAL__MANAGED=agent_search`. ACL con filtro del servidor sobre `structData.acl` (GA) y comprobada otra vez en cada referencia (decidido con Sergio).

**5.G · Evals de RAG**

- **Primer commit de la 5.G** (acordado al cerrar la 4.G, 20-11-2026): ficheros temporales dentro del repo. Añadir `.tmp/` a `.gitignore` y actualizar la skill `cerrar-subfase` (mensaje de commit con heredoc `git commit -F - <<'EOF'`; cuerpo de la PR en `.tmp/pr-<fase>.md`), quitar la nota "pendiente" de `AGENTS.md` y una lección en `lecciones-aprendidas.md` (las rutas de `$LOCALAPPDATA` y los escapes de Python desde el shell causaron caracteres BEL en la 4.G).
- ~~5.G1: juez Gemini fijo (distinto del generador), RAGAS y DeepEval con Gemini, `rag_evals.gcp_eval` con el Gen AI Evaluation Service (como `foundry_eval`) y `managed_eval` para RAG Engine y Vertex AI Search.~~ **Al empezar (20-11-2026)** se divide en tres (decidido con Sergio): el juez Gemini ya existía desde la 2.G (`GEMINI__JUDGE_MODEL=gemini-4-argon`), los informes con coste se escribían siempre en `evals/reports/azure/` (una calibración con Gemini habría sobrescrito la de Azure) y el Evaluation Service tiene un cliente nuevo (`client.evals`, Preview, con `pandas`; su extra `[evaluation]` exige `litellm<1.97` y no se instala) sin métrica de relevancia equivalente a la de Foundry.
- 5.G0: el primer commit de arriba (ficheros temporales en `.tmp/`).
- 5.G1: juez Gemini por los carriles B y C: informes con coste separados por nube (`evals/reports/<nube>/`) en `generation_eval`, `calibration` y `managed_eval`; test de que el juez no es ningún modelo del router; esquemas del juez nativo, RAGAS y DeepEval aceptados por el convertidor de `GeminiLLM`.
- 5.G2: carril A `rag_evals.gcp_eval` con el Evaluation Service: `RubricMetric.GROUNDING` (0-1, frase a frase) y una **métrica LLM propia** de relevancia con el criterio de las etiquetas adjudicadas (decidido con Sergio, frente a `QUESTION_ANSWERING_QUALITY` y `GENERAL_QUALITY`), sobre `calibration-v1` y comparable en `calibration agreement`.
- 5.G3: `managed_eval --engine rag_engine|agent_search`, doc de la fase 5 a tres nubes, pendientes y preguntas.

**6.G · Tools y MCP**

- ~~6.G1: bucle de tools y `support_agent` con Gemini (llamadas paralelas y *thought signatures*); `GcpTokenExchanger` (STS / Identity Platform) con tests, como `EntraOboExchanger`.~~ **Al empezar (20-11-2026)** cambió el plan (decidido con Sergio): Google STS solo cambia tokens externos por *access tokens* de Google (federación), no emite un JWT con el audience de un servidor propio, así que **GCP no tiene OBO** como Entra o AgentCore Identity. El token por servidor se firma con **`signJwt`** de IAM Credentials (sin claves; JWKS público de la cuenta de servicio) a partir del token de **Identity Platform** (`customer_id` como *custom claim*), y el 3LO del *auth manager* de Agent Identity queda como contraste. Se divide en tres subfases.
- 6.G1: bucle de tools y `support_agent` con Gemini de punta a punta (lecturas en paralelo, firmas en cada paso, routing de escrituras, errores como datos), con dobles.
- 6.G2: `GcpTokenExchanger` con `signJwt` (`aud` y scope del servidor, `sub` = cliente, `act` = gateway), `MCP__EXCHANGER=dev|gcp` y Terraform de la cuenta de servicio del gateway e Identity Platform (`identity_platform_enabled`).
- 6.G3: Terraform del MCP gateway gestionado: Agent Gateway + Agent Registry (`agent_gateway_enabled`, en `europe-west1`: los gateways de salida a MCP propios solo están en `us-central1` y `europe-west1`) y Apigee de evaluación (`apigee_enabled`, bundles generados desde los contratos con `genaipocs_mcp.apigee`), desactivados por defecto; doc de la fase 6 a tres nubes. **Al hacerla (21-11-2026):** la política de acceso de IAM v3 del agente y el *binding* con un auth provider de Agent Identity necesitan el agente desplegado: pasan a la 7.G2.

**7.G · Agentes** (la más grande: dos sesiones)

**Al empezar (21-11-2026)** cambió el plan (decidido con Sergio; verificación previa en la
[fase 7](fase-7-agentes.md#10-gcp-pasada-7g)): se divide en **cinco** subfases (7.G1–7.G3 en la
primera sesión, 7.G4–7.G5 en la segunda); `prefix_mismatch_behavior` ya no está documentado, así
que al compactar se quitan las firmas del historial conservado; ADK se queda en 2.14 (la 2.15
cambia el orden del prompt por defecto); `agentplatform` aloja el sabor `adk`, como AgentCore
aloja `strands`; y la Managed Agents API no admite *function tools* del cliente, así que no será sabor
(**corregido en la 7.G5**: la Gemini API sí las documenta; en Agent Platform no está confirmado, y
sigue sin ser sabor por ser Pre-GA).

- 7.G1: *thought signatures* en los puentes de LangChain, LlamaIndex, MAF y Strands, test de ida y vuelta por sabor y firmas fuera del historial conservado al compactar.
- 7.G2: puente `bridges/adk.py` y sabor `adk` (14 escenarios, paridad, invariantes, compactación propia frente a `EventsCompactionConfig`).
- 7.G3: sabor `agentplatform` (contrato de Agent Runtime en proceso alojando `adk`, cliente real y emulador que avisa) y `adapters/memory_bank.py` (Sessions y Memory Bank) con dobles.
- 7.G4: `agent_platform.tf` (Agent Runtime *standalone* con Memory Bank y Runtime con contenedor, desactivados), Agent Identity y la política de IAM v3 por tool heredada de la 6.G3.
- 7.G5: *spike* documental de la Managed Agents API, informes y ataques con 9 sabores, doc de la fase 7 a tres nubes.

Plan original (antes del cambio):

- ~~7.G1: puente `bridges/adk.py` (un `BaseLlm` de ADK que llama a nuestro `LLMPort`, tools por `JournaledExecutor`) y sabor `adk`: 14 escenarios, paridad de tools, invariantes y context engineering (compactación propia frente a la de ADK). **Heredado de la 2.G:** que los puentes de los sabores (LangChain, LlamaIndex, MAF, Strands) conserven `ToolCall.signature` al reconstruir el historial, y que la compactación con Gemini 4 envíe `prefix_mismatch_behavior="DROP"` (el SDK 2.34 no lo expone: `extra_body`).~~
- ~~7.G2: sabor `agentplatform` (Agent Runtime con emulador local que **avisa** cuando no toca la nube) y `adapters/memory_bank.py` (`CheckpointStore` con Sessions y `PreferenceStore` con Memory Bank); Terraform `agent_platform.tf`.~~ **Heredado de la 6.G3** (pasa a la 7.G4): política de acceso de IAM v3 (`egressViaIAP` por servidor y tool) para el principal de Agent Identity del agente y, si aplica, `google_agent_registry_binding` con un auth provider.
- ~~7.G3: *spike* de la Managed Agents API; baselines, informes y ataques con **9 sabores**.~~

**8.G · Harness**

- ~~8.G1: `adapters/model_armor.py` (`GUARDRAILS__SHIELD=gcp`), PII con Sensitive Data Protection (`GUARDRAILS__PII=gcp`), plantilla de Model Armor en Terraform, Gemini en `rag_evals.shields` y en los ataques con modelo real (aplazado).~~ **Al empezar (21-11-2026)** se divide en cuatro (decidido con Sergio; verificación previa en la [fase 8](fase-8-harness.md#9-gcp-pasada-8g)): la integración de Model Armor en Gemini (`model_armor_config`) no existe en `global` y falla abierto, así que **no se usa**; el escudo propio falla cerrado y la *floor setting* queda como red de proyecto. Además, `attacks` y `agents --mode llm` escribían siempre en `evals/reports/azure/`.
- 8.G1: `adapters/model_armor.py` (`ModelArmorShield`, endpoint regional, sin `SUCCESS` y sin coincidencias = no disponible), `GUARDRAILS__SHIELD=gcp` y `rag_evals.shields --shield azure|bedrock|gcp` con el informe en la carpeta de cada nube.
- 8.G2: `harness/pii_gcp.py` con Sensitive Data Protection (`GUARDRAILS__PII=gcp`; posiciones en *codepoints*, mismos nombres de entidad que Presidio).
- 8.G3: `model_armor.tf` (`model_armor_enabled`): plantilla de inspección de SDP, plantilla de Model Armor (SDP avanzado solo para datos de pago), *floor setting* de Agent Platform en *inspect only* e IAM.
- 8.G4: `attacks --mode llm` con Gemini y el informe en `evals/reports/<nube>/llm/` (y `fix:` aparte para `agents`), doc de la fase 8 a tres nubes.

**9.G · Multi-agente**

- **Heredado de la 8.G4:** `multiagent --mode llm` escribe siempre en `evals/reports/azure/`: a la carpeta de la nube de `LLM_PROVIDER` (`CloudModels.cloud` / `llm_reports_dir`), como `agents` y `attacks`. Hecho en el `fix:` `ccfcca5`.
- ~~9.G1: ADK como cuarto motor del orquestador (triaje con tabla del dominio, especialistas, revisión humana con confirmación de tools de ADK) con test de paridad frente a LangGraph, MAF y cola.~~
- ~~9.G2: `ORCHESTRATOR__BROKER=pubsub` con el outbox y *exactly-once delivery* como primera capa de deduplicación; A2A con ID tokens de Google (audience por especialista) en `remote/auth.py`; Terraform de Cloud Run y Workflows.~~ **Al empezar (21-11-2026)** se divide en cinco (decidido con Sergio; verificación previa en la [fase 9](fase-9-multiagente.md#7-gcp-pasada-9g)): el motor es un workflow de grafo de ADK (`google.adk.workflow`) y la revisión, un `RequestInput` (la confirmación de tools es para tools de un `LlmAgent`); *exactly-once* solo evita reentregas (no duplicados al publicar) y el emulador lo admite; los ID tokens de Google no llevan `scope`; y Workflows se implementa de verdad (SLA de revisión con escalado), no solo en Terraform.
- 9.G1: `AdkCaseWorkflow` (rutas del dominio con `ctx.route`, `RetryConfig`, revisión con `RequestInput`, estado en la sesión) con paridad frente a LangGraph y MAF.
- 9.G2: motor `adk` en `rag_evals.multiagent` y en el gate de la CI (cuatro motores) y sesiones en PostgreSQL con `DatabaseSessionService` (test `integration`).
- 9.G3: `ORCHESTRATOR__BROKER=pubsub` con *exactly-once* en la topología (emulador y `pubsub.tf`), `ack` fallido tratado como confirmado, test `integration` con el emulador (duplicados, DLQ, CAS) y temas del orquestador en `pubsub.tf`.
- 9.G4: A2A con ID tokens de Google (`A2A__IDENTITY=dev|gcp`: servidor de metadatos o *impersonation*; verificador con el JWKS de Google, `aud` por especialista y `email` en la lista) y `a2a.tf` (Cloud Run con *custom audiences* e *invoker* solo para el orquestador, `a2a_enabled`).
- 9.G5: Workflows como espera humana (`case_review.yaml`: callback, aviso, `await_callback` con SLA y decisión en `case.reviews`), rol `review` del worker y `workflows.tf` (`case_review_workflow_enabled`); doc de la fase 9 a tres nubes.

**10.G · Escala**

- **Heredado de la 8.G4:** `semantic_cache --embeddings azure` escribe en `evals/reports/azure/`; con embeddings de Gemini, a `gcp/`.
- **Heredado de la 9.G3:** ningún adaptador de cola en la nube (SQS, Service Bus, Pub/Sub) renueva el plazo de *ack*/visibilidad mientras el handler trabaja; hoy se cubre con un plazo de 600 s en las suscripciones del orquestador. Con los worker pools, valorar el *streaming pull* de la librería de alto nivel (renueva el *lease*) o un latido propio en el adaptador, y medir `ack_failures`.
- ~~10.G1: métrica de backlog de Pub/Sub para el autoescalador (`queue_metrics`), Memorystore para Valkey (rate limit y caché semántica; comprobar que su búsqueda vectorial cubre lo que usa `RedisSemanticCache`) y umbral de la caché para embeddings de Gemini (pendiente de calibrar). **Heredado de la 2.G3:** valorar compartir el registro de cachés explícitas de Gemini en Valkey (hoy cada réplica crea y paga la suya).~~
- ~~10.G2: Terraform de workers (worker pools de Cloud Run o GKE + KEDA `gcp-pubsub`), Cloud Armor y cuotas de tokens; plan de carga con Locust en la nube.~~ **Al empezar (21-11-2026)** cambió el plan (decidido con Sergio; verificación previa en la [fase 10](fase-10-escala.md#7-gcp-pasada-10g)). Pasa a **cinco** subfases por cuatro hallazgos:
  - La búsqueda vectorial de Memorystore no admite `TEXT`, `SORTBY` ni `AS` en el KNN, así que `RedisSemanticCache` no funciona tal cual.
  - El escalador `gcp-pubsub` de KEDA está obsoleto (MQL); se usa PromQL, la misma consulta en el adaptador, en CREMA y en las alertas.
  - Los worker pools de Cloud Run no autoescalan solos: se usa CREMA, no GKE.
  - Las cuotas de tokens del Agent Gateway están en *early preview* sin Terraform: quedan solo documentales.
- 10.G1: `PubSubBacklogMetrics` (PromQL contra Managed Prometheus, hueco ≠ cola vacía, `value_if_null`) y `queue_autoscaler.py --broker pubsub`; latido de `modifyAckDeadline` en `PubSubMessageBus` (`PUBSUB__MAX_LEASE_S`) con test `integration` de dos workers en el emulador.
- 10.G2: `RedisSemanticCache` portable a Valkey (sin `TEXT` ni `SORTBY`; `__embedding_score`), servicio `valkey-scale` (`valkey/valkey-bundle`) en el perfil `scale` con GCRA y caché probados contra Redis 8 y Valkey, `SCALE_REDIS__AUTH=gcp_iam` con TLS y `memorystore.tf` (`memorystore_enabled`).
- 10.G3: calibración de la caché con `embed_query` (lo que usa en ejecución), `semantic_cache --embeddings gcp|bedrock` con el informe en la carpeta de cada nube y registro compartido de cachés explícitas de Gemini en Valkey (ninguna réplica borra lo que usan las demás).
- 10.G4: `queue_workers.tf` de GCP (worker pool `MANUAL` + CREMA con el escalador `prometheus` desde Parameter Manager, IAM) y `edge_armor.tf` (ALB externo, *serverless NEG*, `throttle` y `rate_based_ban`), desactivados.
- 10.G5: `load_test.tf` (Cloud Run job con Locust `--processes` y CSV en Cloud Storage), comparativa de cuotas de tokens (Agent Gateway / Apigee `LLMTokenQuota`) y doc de la fase 10 a tres nubes.
- **Hecha (21-11-2026)**: las cinco, más el `fix:` `9d82f8b` (validación de Mermaid sin `npx`: herramienta fijada en `tools/mermaid` y paso de la CI). **Heredado para la 11.G:** las alertas de backlog en Cloud Monitoring usan la misma consulta que `pubsub_backlog_promql()`; y para las fases 13/14, el latido del plazo en SQS y Service Bus.

**11.G · Observabilidad**

- **Heredado de la 10.G:** las alertas de backlog de Pub/Sub en Cloud Monitoring con la misma PromQL que `pubsub_backlog_promql()` (fuente única del adaptador y de CREMA), y las del latido (`lease_failures`, `ack_failures`, `expired_ack_deadlines_count`).
- **Replanteada al iniciarla (22-11-2026)**, con lo decidido con Sergio:
  - La propagación de `traceparent` por Pub/Sub ya la cubría el test de contrato de la 3.G1.
  - Las métricas van por `googlemanagedprometheus` y no por la Telemetry API, que conserva los puntos en los nombres.
  - Se portan **todas** las alertas, no 2 de muestra, porque Cloud Monitoring habla PromQL.
  - ADK y Strands emiten el contenido en sus spans por defecto: se corrige aquí; lo de Strands, con un `fix:` aparte.
- 11.G1: `infra/otel/collector-gcp.yaml` (trazas y logs por la Telemetry API, métricas por Managed Prometheus) validado en la CI, `scripts/check_collector_gcp.py` (nombres de las métricas contra un Cloud Monitoring falso) y `observability.tf` (roles del collector, retención y exclusiones de logs, canal de guardia).
- 11.G2: alertas de Cloud Monitoring generadas desde `domain/slo.py` (una política PromQL por alerta, con los SLI expandidos), más las heredadas de la 10.G (backlog con `pubsub_backlog_promql()` y latido como métricas del catálogo) y aviso de ingesta de logs. Mismos tests de `promtool` sobre las alertas expandidas.
- 11.G3: logs JSON con los campos de correlación de Cloud Logging (`TELEMETRY__LOG_FORMAT=gcp`), contenido apagado en los spans de ADK y borrado en el collector (atributos `gcp.vertex.agent.*` y eventos), una traza por el emulador de Pub/Sub de punta a punta, y el `fix:` de Strands.
- 11.G4: doc de la fase 11 a tres nubes, preguntas y respuestas, pendientes y cierre.
- **Hecha (22-11-2026)**: las cuatro, más el `fix:` `812f1f3` (Strands emitía el contenido del turno en eventos del span desde la fase 11). **Heredado para la 12.G:** las reglas de drift ya se generan también como políticas de Cloud Monitoring (`DistributionDrift`, 11.G2): la 12.G2 solo tiene que documentarlas, no portarlas; y si la 12.G añade alertas en `build_rules()`, llegan solas a GCP con su test de `promtool`.

**12.G · Evals de agentes y LLMOps**

- **Heredado de la 8.G4:** los experimentos de LLMOps (`--mode llm`) y `agent_calibration judge` escriben siempre en `evals/reports/azure/`: a la carpeta de la nube del juez.
- ~~12.G1: juez de agentes con Gemini, métricas de trayectoria del Evaluation Service como contraste de la 12.1, promptfoo y PyRIT contra Gemini (aplazado).~~
- ~~12.G2: publicación de prompts en la gestión de prompts de Agent Platform (dry-run), `VertexExperimentTracker` junto a MLflow, reglas de drift en Managed Service for Prometheus.~~
- ~~12.G3: cierre de la pasada: glosario, arquitectura (nivel 1.11 con tres nubes), preguntas, pendientes, `AGENTS.md` y `README.md`.~~
- **Al empezar (22-11-2026)** pasa a **siete** subfases (decidido con Sergio; verificación previa en la [fase 12](fase-12-evals-llmops.md#7-gcp-pasada-12g)):
  - El cliente nuevo del Evaluation Service (`client.evals`) **no tiene las métricas de trayectoria** (`TODO` en el SDK 2.7). Se piden con `evaluate_instances` de `aiplatform_v1beta1` (Preview), no con el `EvalTask` antiguo.
  - Bug encontrado: `with_model` solo forzaba el modelo de Azure, así que un A/B de modelos con Gemini comparaba el mismo modelo. Se arregla en el `fix:` `f98ed1e`.
- 12.G1: juez de agentes con Gemini. Los informes con coste de `agent_calibration` y `experiment --mode llm` van a la carpeta de la nube que juzga. Esquema del juez dentro del subconjunto de Gemini y juez online con solo GCP.
- 12.G2: Evaluation Service como contraste de la 12.1. Trayectoria por cómputo (`v1beta1`) sobre las trayectorias reales de Azure de la fase 7, y rúbricas `hallucination` y `final_response_quality` sobre la calibración de agentes. **Al hacerla:** `final_response_quality` sustituye a `tool_use_quality`, que no distingue las muestras (comparten tools). Además, el contraste destapó un bug en la precisión y el recall de la 12.1 (`fix:` `a523a67`).
- 12.G3: red teaming con Gemini. promptfoo con `vertex:` de generador y juez, sin generación remota. PyRIT con Gemini de atacante. Riesgo residual frente a Model Armor (aplazado). **Al hacerla (decidido con Sergio):**
  - El riesgo residual es de PII, así que se mide con los detectores de PII (regex, Presidio y Sensitive Data Protection) en lugar de con Model Armor.
  - La configuración de promptfoo se genera para las tres nubes y apunta a un objetivo nuevo, el agente con LLM real.
  - El atacante adaptativo de PyRIT pasa a ideas aparcadas en `plan-fases.md`: PyRIT 1.1 reescribió su API y ni Azure ni AWS lo tenían.
- 12.G4: prompts en la gestión de prompts de Agent Platform (`PromptStorePort`, dry-run por defecto).
- 12.G5: `VertexExperimentTracker`, drift en Managed Prometheus (hecho en la 11.G2, solo se documenta) y doc de la fase 12 a tres nubes.
- 12.G6: cierre de la pasada: glosario, arquitectura (nivel 1.11 con tres nubes), preguntas, pendientes, `AGENTS.md` y `README.md`. **Al hacerla (22-11-2026):** la arquitectura por niveles pasa a tres nubes en **todos** los niveles, no solo en el 1.11: todos nombraban solo Azure y AWS, y el agente seguía con 7 sabores. Los 63 pendientes de GCP se agrupan en cinco sesiones de validación ([pendientes](pendientes-cloud.md)).
- 12.G7 (era la 12.G4; añadida el 20-11-2026, documental): **alinear el repo público con las buenas prácticas de GitHub**. Punto de partida (`gh api repos/SergioVelmay/GenAI_PoCs/community/profile`, 20-11-2026): *community profile* al **28 %**, solo con README; sin licencia, `CODE_OF_CONDUCT`, `CONTRIBUTING` ni plantillas de issues y PR; sin *topics*; la descripción no menciona GCP y el enlace de documentación apunta a `master` (la rama es `main`). Entregables: licencia (decidir cuál con Sergio), `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, plantillas de issues y de PR, descripción y *topics*, y medir el resultado (perfil de comunidad y, si aplica, OpenSSF Scorecard) antes y después. Sin el nombre real del cliente ni marcas de terceros en ninguno de esos ficheros. **Al hacerla (22-11-2026):**
  - Antes: perfil de comunidad al **28 %** (solo README); descripción «PoCs de sistemas agenticos GenAI en Azure y AWS», sin *topics*, sin licencia y sin página de inicio (el enlace a `master` ya no estaba).
  - Licencia **MIT**, decidida con Sergio frente a Apache-2.0 (la concesión de patentes no aporta en un repo didáctico de un solo autor) y al copyleft (alejaría a quien se quiere que lo reutilice). Las contribuciones entran con la misma licencia.
  - `CODE_OF_CONDUCT.md` adopta el Contributor Covenant 2.1 por referencia, con un resumen en español. `SECURITY.md` pide el informe privado de GitHub y dice qué interesa (saltarse el harness, secretos en el historial, permisos de la CI). `CONTRIBUTING.md` resume `AGENTS.md` para personas. Hay plantillas de issue (error, mejora) y de PR con las comprobaciones de la CI.
  - Ajustes del repo, con confirmación de Sergio: descripción con las tres nubes, 18 *topics* e informe privado de vulnerabilidades activado (lo dan por hecho `SECURITY.md` y la plantilla de issues).
  - El perfil de comunidad solo mira la rama por defecto, así que el «después» se mide al fusionar la PR.
- **Hecha (22-11-2026)**: las siete, más los `fix:` `f98ed1e` (el A/B de modelos solo forzaba Azure) y `a523a67` (precisión y recall de trayectoria). **Con ella se cierra la pasada GCP** (0.G a 12.G, coste 0, todo con dobles y emuladores); la validación en la nube queda en [pendientes](pendientes-cloud.md), agrupada en cinco sesiones. **Heredado para el checkpoint G→13:** los dobles de GCP se sustituyen por respuestas reales capturadas en la primera validación, y los emuladores oficiales pueden adelantar parte de ella sin coste.

### 3.5 Convenciones de la pasada

- Prefijo de adaptadores: `gemini.py`, `pubsub.py`, `gcp_*.py`, `model_armor.py`, `memory_bank.py`; settings anidados `GCP__PROJECT`, `GCP__REGION`, `GEMINI__MODEL_CHEAP`...
- Región `europe-west4` para los recursos; modelos en el endpoint `global` si la región no los sirve (verificar en la 2.G1).
- `gcloud` aislado como `az`: `$env:CLOUDSDK_CONFIG = "$env:USERPROFILE\.gcloud-genaipocs"` antes de cualquier comando de GCP, Terraform o scripts; nunca claves JSON de cuentas de servicio.
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
el [checkpoint G→13](plan-fases.md#checkpoint-g-13) (emuladores locales oficiales de las tres nubes,
añadido el 21-11-2026) y después la fase 13 (AWS), la 14 (Azure) y la 15 (GCP).

## 4. Cierre

- Hecho: glosario e inventario, arquitectura por niveles y plan de GCP con verificación previa.
- Pendiente para el repaso final (no planificado aún): casos reales contados con el método STAR
  (situación, tarea, acción, resultado), temas no cubiertos (OWASP LLM Top 10, EU AI Act,
  fine-tuning frente a RAG, multimodal, FinOps) y un ejercicio práctico de presentación de la
  arquitectura.
- Pendientes con coste: sin cambios ([pendientes-cloud.md](pendientes-cloud.md)); los de GCP se
  añaden en cada subfase de la pasada.

## Preguntas y respuestas

[Preguntas del checkpoint 12→13](preguntas-y-respuestas.md#checkpoint-12-13).
