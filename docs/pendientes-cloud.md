# Pendientes con coste o nube

Todo lo que se aplazó al cerrar cada fase: la implementación local está hecha y probada, y falta
la validación en la nube. **Todo tiene coste o crea recursos: pedir confirmación antes de cada paso.**
Al completar uno, se marca `[x]` y se documenta el resultado en el doc de su fase.

Orden recomendado: primero la fase 2 (credenciales y modelos), que desbloquea el resto. En GCP,
antes la [fase 0](#fase-0) (proyecto, APIs y presupuesto).

**Decisión de Azure (checkpoint 8→9):** región `swedencentral`, tiers gratuitos donde no recorten
capacidades (Content Safety F0, plan gratuito del Semantic Ranker) y **AI Search Basic** (~70 €/mes
de coste fijo) para tener identidad gestionada, Semantic Ranker y Foundry IQ. Presupuesto del grupo
de recursos: 100 €/mes con alertas al 50, 75 y 100 %. AWS, más adelante.

**Decisión de GCP (checkpoint 12→13):** región `europe-west4`, presupuesto de 100 $ con alertas al
50, 75 y 100 % (intentando usar créditos de prueba) y `gcloud` aislado con `CLOUDSDK_CONFIG`. Los
pendientes de GCP se añaden a la sección de cada fase durante la pasada GCP
([plan](fase-13-12-checkpoint.md#plan-gcp)); Vector Search y RAG Engine serán efímeros, como AI Search.

**AI Search es efímero** (`search_enabled`): se cobra por hora aunque no se use, así que solo existe
durante las sesiones de trabajo (p. ej., de viernes a domingo). El nombre y el endpoint no cambian.

```powershell
$env:AZURE_CONFIG_DIR = "$env:USERPROFILE\.azure-headache"; az account show -o table
# Al empezar: search_enabled = true en terraform.tfvars y después
terraform -chdir=infra/terraform/azure apply
uv run python scripts/provision_search_index.py --apply   # índice del carril B
uv run python scripts/classify_corpus.py                  # corpus de evals con ACL explícita
# Carril B (worker en otra terminal con INDEXER__BROKER=servicebus, INDEXER__EMBEDDINGS=azure,
# INDEXER__VECTOR_STORE=azure_search; PostgreSQL arrancado para el ledger):
uv run python -m indexer.producer --source data/synthetic/knowledge-classified.jsonl
# Si se usan: carril A y Foundry IQ
uv run python scripts/managed_ingestion.py azure --source data/synthetic/knowledge-classified.jsonl --apply
uv run python scripts/provision_foundry_iq.py --apply
# Al terminar: search_enabled = false y
terraform -chdir=infra/terraform/azure apply
```

<a id="fase-0"></a>

## Fase 0 · Cimientos: GCP ([doc](fase-0-cimientos.md#7-gcp-pasada-0g))

Desbloquea todos los pendientes de GCP de las fases siguientes. Coste 0: APIs, presupuesto,
cuentas de servicio y federación no se facturan.

```powershell
$env:CLOUDSDK_CONFIG = "$env:USERPROFILE\.gcloud-headache"; gcloud config list
gcloud auth login ; gcloud auth application-default login     # ADC de usuario, sin claves JSON
terraform -chdir=infra/terraform/gcp init
terraform -chdir=infra/terraform/gcp plan     # revisar antes del apply
```

- [ ] **Cuenta y proyecto**: cuenta nueva con créditos de prueba, proyecto `headache-dev-<sufijo>` vinculado a la cuenta de facturación y `gcloud` aislado con `CLOUDSDK_CONFIG`. Anotar la moneda de la cuenta (el presupuesto la hereda).
- [ ] **`terraform apply`** de `infra/terraform/gcp` con `terraform.tfvars` (copia de la plantilla): 15 APIs, presupuesto de 100 con alertas al 50/75/100 % sobre el gasto **bruto**, cuenta de servicio `headache-dev-app` con *impersonation* para el usuario y federación de GitHub Actions. Comprobar que llega el correo de prueba del canal de notificación.
- [ ] **Suplantación desde local**: `GCP__IMPERSONATE_SERVICE_ACCOUNT` en el `.env` y `gcloud auth print-access-token --impersonate-service-account=<cuenta>` responde.
- [ ] **Federación de la CI** (fase 15): workflow en `main` con `google-github-actions/auth` (`workload_identity_provider` y `service_account` de los outputs) y comprobar que un workflow de una PR **no** obtiene credenciales.
- [ ] **Pub/Sub real frente al emulador** (con la 3.G1): el smoke test contra un tema real y anotar diferencias (p. ej., *dead-letter topic*, que el emulador puede no soportar).

## Fase 2 · Capa de modelos ([doc](fase-2-modelos.md))

- [x] **Azure CLI** autenticada en un perfil aislado (`AZURE_CONFIG_DIR=%USERPROFILE%\.azure-headache`), suscripción *MSDN - Sergio Velasco* (17-10-2026).
- [ ] **AWS CLI** (`aws configure sso`, perfil en `BEDROCK__PROFILE`).
- [x] **Azure AI Foundry** como código (`foundry.tf`): cuenta AI Services, proyecto `headache` y despliegues `gpt-4.1-mini`, `gpt-4.1-nano` y `text-embedding-3-small` en `swedencentral`, **DataZoneStandard** (chat 200 K TPM desde el 18-10-2026; embeddings 50 K), con *Cognitive Services OpenAI User* y *Foundry User* para el usuario. `terraform apply` del grupo `rg-headache-dev` (17 recursos; AI Search apagado) el 17-10-2026. Prueba de humo: `gpt-4.1-nano` responde con Entra ID (16 + 2 tokens, 0,0000024 $) y el embedding devuelve 1024 dimensiones. Hallazgos: el proyecto debe crearse en serie con los despliegues (409) y el rol "Azure AI User" se llama ahora "Foundry User". El 18-10-2026 los recursos se recrearon con nombres CAF (`aif-headache-dev-swc-001`...), sin sufijo aleatorio.
- [ ] **Bedrock** (`eu-west-1`): habilitar el acceso a Claude Haiku 4.5 (formulario de Anthropic) y a Nova Lite.
- [x] Azure (18-10-2026): `llm_cache_benchmark.py` en los dos tiers (caché −58 % de coste, 98 % de tokens cacheados desde la 2.ª llamada) y `llm_gateway_demo.py` con `native`, `litellm` y router LLM (20/20; el router LLM duplica llamadas sin mejorar el enrutado). Trazas en Langfuse. Capacidad subida a 200 K TPM tras un 429 con 50 K. Detalle en [fase 2](fase-2-modelos.md).
- [ ] AWS: repetir ambos scripts con `--provider aws` y probar el fallback entre proveedores.
- [ ] Langfuse local sigue en la imagen `langfuse:3` (OSS v3, parches de seguridad hasta 31-01-2027). El SDK Python v4 es compatible (servidor ≥ 3.63.0). Valorar migrar a la imagen v4 en la fase 11. Ojo: las APIs de lectura nuevas del SDK v4 (`api.observations`, `api.metrics`) necesitan servidor v4; contra el v3 se usa `api.legacy.observations_v1`.

<a id="fase-2-gcp"></a>

### GCP (pasada 2.G)

Requiere la [fase 0 de GCP](#fase-0) (proyecto, `apply` y suplantación). Gemini se paga por token:
la prueba de humo cuesta céntimos.

```powershell
$env:CLOUDSDK_CONFIG = "$env:USERPROFILE\.gcloud-headache"; gcloud config list
terraform -chdir=infra/terraform/gcp plan     # solo debe añadir roles/aiplatform.user
```

- [ ] **`apply` del rol** `roles/aiplatform.user` para `headache-dev-app` (2.G1) y comprobar la suplantación con una llamada real (un 403 justo después suele ser propagación).
- [ ] **Prueba de humo** con `LLM_PROVIDER=gcp`: `gemini-4-hydrogen` y `gemini-3.5-flash-lite` responden en `global`; anotar el `model_version` real, los tokens de razonamiento con `thinking_level=low` y si `eu` sirve los dos modelos.
- [ ] **Tools con firma**: un bucle de dos pasos con llamadas en paralelo (`scripts/tool_loop_demo.py`) y otro con historial de Azure (fallback a mitad de conversación) para ver el valor ficticio aceptado. Capturar las respuestas reales y sustituir los dobles de `test_gemini_adapter.py`.
- [ ] **Precios en Agent Platform**: confirmar los de `pricing.py` (se tomaron de la Developer API) y el recargo de `eu`.
- [ ] **Gateway con tres nubes** (2.G2): `llm_gateway_demo.py --limit 20` con `LLM_PROVIDER=gcp` en los dos motores (`native` y `litellm`) y comparar acierto, enrutado y coste con los de Azure; después, la cadena `LLM_GATEWAY__FALLBACK_PROVIDERS=aws,gcp` forzando un fallo del principal (p. ej., un despliegue inexistente) y ver en Langfuse el salto.
- [ ] **Caché implícita frente a explícita** (2.G3): `llm_cache_benchmark.py --provider gcp --tier cheap` con `--gemini-cache implicit` y `explicit` (5 llamadas por modo), y `--tier strong` para ver que Hydrogen degrada a la implícita. Anotar el acierto real de la implícita, el coste de escritura y almacenamiento de la explícita y confirmar en `gcloud`/consola que la caché se borra al terminar. Comprobar el mínimo de tokens real de Flash-Lite y si `global` admite cachés explícitas.
- [ ] **LiteLLM con firmas reales**: un bucle de tools con `LLM_GATEWAY__ENGINE=litellm` y Gemini, para confirmar que la firma separada del id (`provider_specific_fields`) es la que LiteLLM devuelve a Gemini, y el aviso de temperatura en el log.

## Fase 3 · Indexación ([doc](fase-3-comparativa.md))

- [x] Azure (18-10-2026): Terraform aplicado (AI Search Basic en `francecentral`), outputs en el `.env`, corpus clasificado con ACL (`scripts/classify_corpus.py`), carril B con Service Bus + Foundry + AI Search y carril A con indexer + skillset (vectorizador verificado). Medidas y hallazgos en [fase 3](fase-3-comparativa.md#6-resultados-en-azure-18-10-2026).
- [x] Indexer del carril A que reprocesaba todos los blobs (18-10-2026): era `managed_ingestion.py`, que los resubía siempre. Ahora solo sube si cambia el MD5 o los metadatos; sin cambios = 0 procesados.
- [ ] AWS: `terraform apply` en `infra/terraform/aws`, carril B con `INDEXER__EMBEDDINGS=bedrock`, `INDEXER__VECTOR_STORE=s3_vectors` e `INDEXER__BROKER=sqs`, y carril A con `scripts/managed_ingestion.py aws --apply`.
- [x] Coste real con Cost Management (20-10-2026): indexar por cualquiera de los dos carriles cuesta céntimos (embeddings 0,005 €, Service Bus ~0); lo que pesa es AI Search por horas (0,44 € en ≈ 3,7 h). Detalle en el [checkpoint](fase-9-8-checkpoint.md#6-coste-real-cost-management-1720-de-octubre). AWS (Cost Explorer), pendiente.

## Fase 4 · Retrieval ([doc](fase-4-retrieval.md))

Requiere antes los pendientes de las fases 2 y 3.

- [x] Azure (18-10-2026): corpus clasificado con ACL explícita e ingerido en el índice `knowledge`; AI Search Basic con Semantic Ranker; Foundry IQ aprovisionado (KB en preview con esfuerzo `minimal`, salida extractiva y 5 documentos) y comparativa A/B/C con el golden dataset completo. Resultados y hallazgos en [fase 4](fase-4-retrieval.md#resultados-en-azure-18-10-2026).
- [ ] AWS: resincronizar Bedrock KB con los sidecars con ACL, habilitar Cohere Rerank (`BEDROCK__RERANK_MODEL_ARN`, `BEDROCK_KB__GENERATION_MODEL_ARN`) y repetir la comparativa con `RETRIEVAL__RERANKER=bedrock` y `RETRIEVAL__MANAGED=bedrock_kb`.
- [x] Estrategias con LLM (rewrite, HyDE, subpreguntas, router, auto) sobre AI Search con el golden dataset (18-10-2026, 0,10 $): ninguna mejora a la híbrida + Semantic Ranker y todas triplican la latencia. Informe en `evals/reports/azure/retrieval-llm.md`.

## Fase 5 · Evals ([doc](fase-5-evals.md))

- [x] `generation_eval --mode llm --judge native|ragas|deepeval --limit 20 --budget-usd 0.50` con la misma muestra (18-10-2026, ~0,39 $ en total): informes en `evals/reports/azure/`.
- [x] Calibrar los jueces (18-10-2026, ~0,96 $): conjunto `evals/calibration/calibration-v1`, etiquetas a ciegas y adjudicadas; juez nativo kappa 0,82 (fidelidad) y 1,0 (relevancia). Informe en `evals/reports/azure/calibration.md`.
- [x] Juez con modelo distinto del generador (`gpt-4.1`, despliegue `judge` en Terraform) y embeddings reales (`text-embedding-3-small`) para `AnswerRelevancy` de RAGAS.
- [x] Carril A en Azure: Foundry Evaluation (`rag_evals.foundry_eval`, 18-10-2026, 0,17 $) sobre el conjunto de calibración; kappa 0,82 en fidelidad con su umbral de aprobado. Ejecución registrada en el proyecto de Foundry.
- [ ] Carril A en AWS: Amazon Bedrock Evaluations con el mismo conjunto.
- [x] Evals de retrieval con embeddings reales y Semantic Ranker en Azure (`--engine azure --engine azure-semantic`, 18-10-2026): informe en `evals/reports/azure/`. El baseline de la CI sigue siendo el local (coste 0); el de nube se compara a mano.
- [x] Juez de fidelidad sobre el `answer` de Foundry IQ (18-10-2026, `rag_evals.managed_eval`): 19/21 fieles; detecta la sobre-respuesta y una respuesta que añade condiciones no citadas. Informe en `evals/reports/azure/managed-foundry_iq.md`.
- [x] Job `evals` de la CI en `--mode enforce` (18-10-2026), tras 15 ejecuciones seguidas con 0 regresiones.

## Fase 6 · Tools y MCP ([doc](fase-6-tools-mcp.md))

- [x] Azure (18-10-2026): `tool_loop_demo.py` y `support_agent` (con y sin `--gateway`, `MCP__AUTH=jwt`) con Azure OpenAI; trazas y coste en Langfuse. Tres fallos de diseño de tools corregidos y regla de routing para escrituras.
- [ ] AWS: los mismos agentes con Bedrock.
- [ ] Registrar los servidores MCP en Entra ID (App ID URI y scopes; revisar el formato de `scp`) y en Cognito (resource server + scopes); probar OBO de extremo a extremo. **Aplazado a la fase 14** (decisión del 18-10-2026: las App Registrations van como código con `azuread`, no a mano en el tenant corporativo). Ya está `EntraOboExchanger` (`headache_mcp.entra`) con tests y el gateway pasa el token de entrada al exchanger; falta el mapeo `oid` → `customer_id`.
- [ ] Con los servidores MCP en HTTPS (fases 13/14): `mcp_gateway_enabled=true` (AgentCore Gateway) y `apim_enabled=true` (APIM); comparar latencia y coste con el gateway propio.
- [ ] Tool de compra con `Idempotency-Key` estable y aprobación humana.

## Fase 7 · Agentes ([doc](fase-7-agentes.md))

- [x] Suite de agentes con LLM real en Azure (18-10-2026, ~0,08 $): 4-6 de 12 por sabor, pero la mayoría de fallos son del comprobador por literales; fallos reales: idioma, bucle en una pregunta informativa y una escritura de memoria no pedida. Criterios de modo `llm` basados en el resultado; juez de respuestas para la fase 12.
- [x] Foundry: agente `headache-booking-agent` publicado (v2) y sabor `foundry` contra **Foundry Agent Service real** (`--foundry-service`, `create_cloud`); el primer recorrido de extremo a extremo usó el emulador local sin saberlo.
- [x] Conversación larga: compactación propia frente a Foundry (`scripts/context_growth_demo.py`, 18-10-2026): Foundry guarda las salidas de las tools y gasta un 33 % más a los 14 turnos; ninguno trunca.
- [ ] AgentCore: imagen ARM64 del Runtime en ECR (entrypoint con `create_runtime_app`, fase 13), `agentcore_enabled=true` + `agentcore_container_uri`, cablear `AgentCoreCheckpointStore`/`AgentCorePreferenceStore` con `AGENTCORE__MEMORY_ID` e invocar con `BotoRuntimeInvoker` (rol con `InvokeAgentRuntimeForUser`).
- [ ] Valorar las estrategias de extracción de AgentCore Memory (`USER_PREFERENCE`) y la memoria de Foundry frente a la tool explícita `remember_preference`. **Pasa a la fase 9** (memoria compartida entre agentes); la suite con LLM real ya mostró que el modelo guarda planes puntuales como preferencias.

## Fase 8 · Harness ([doc](fase-8-harness.md))

- [x] Azure (18-10-2026): `analytics_sql_demo.py` con 5 preguntas: 1 respondida, 1 escritura rechazada, 3 canceladas por el timeout de 3 s sobre 3 M de pedidos (escaladas). Bedrock pendiente.
- [x] Azure: Content Safety (F0) frente al escudo heurístico con `rag_evals.shields` (24 textos): 7/12 cada uno, 9/12 juntos, 0 falsos positivos; Content Safety a ~2 s por llamada. Bedrock Guardrails pendiente.
- [x] Tests de ataque con modelo real (`rag_evals.attacks --mode llm`): con harness, 0 invariantes en los 7 sabores. El harness ahora convierte el filtro de contenido del proveedor en un bloqueo.
- [ ] Red teaming automatizado (PyRIT / promptfoo) y juez de respuestas para agentes y ataques (fase 12).

## Fase 9 · Multi-agente ([doc](fase-9-multiagente.md))

- [ ] Eval multi-agente con modelo real: `uv run python -m rag_evals.multiagent --mode llm --budget-usd 0.20` (precisión del triaje del modelo, que en modo guionizado no se mide). Ampliar el dataset con emails sintéticos reales (`data/synthetic/support_emails.jsonl` trae `expected_intent`).
- [ ] Flujo de punta a punta con LLM real (Azure OpenAI): triaje + especialistas en proceso y con los contenedores A2A (`docker compose --profile agents`). En contenedor, Azure OpenAI necesita `AZURE_OPENAI__API_KEY` o una identidad de servicio (no hay `az login`).
- [ ] A2A con identidad de nube: tokens de servicio de Entra ID (app roles / Managed Identity) y Cognito client credentials en lugar del emisor de desarrollo (fases 13/14).
- [ ] Carril A: especialistas en AgentCore Runtime con protocolo A2A y workflow multi-agente de Foundry Agent Service con el mismo dataset.
- [ ] Flujo asíncrono en la nube: colas `support-emails`, `case-triaged-*` y `case-outcomes` con su DLQ en SQS (Terraform, fase 13) y Service Bus (fase 14), y workers con `ORCHESTRATOR__BROKER=sqs|servicebus`. Valorar la deduplicación del broker (SQS FIFO `MessageDeduplicationId`, *duplicate detection* de Service Bus) como primera capa antes del `CaseStore`.

## Fase 10 · Escala ([doc](fase-10-escala.md))

- [ ] Calibrar el umbral de la caché semántica con embeddings reales: `uv run python -m rag_evals.semantic_cache --embeddings azure --budget-usd 0.01` (~0,00003 $). Añadir el umbral resultante a `CALIBRATED_THRESHOLDS` (`azure`); hasta entonces, con embeddings de Azure la caché no se activa. Repetir con Titan (`bedrock`).
- [ ] Redis gestionado para rate limiting y caché: Azure Managed Redis (con el módulo de búsqueda) y ElastiCache (Valkey con búsqueda vectorial), por Terraform y desactivado por defecto.
- [ ] Streaming real con Azure OpenAI y Bedrock: `EDGE__ANSWER_MODE=generative` y `POST /ask/stream`; medir el tiempo hasta el primer trozo frente a la respuesta completa, comprobar que el uso del último chunk llega (Azure `include_usage`, Bedrock `metadata`) y que el filtro de contenido corta con `LLMContentFilterError`.
- [ ] Autoescalado por cola en la nube (con la imagen del orquestador publicada en las fases 13/14): `queue_workers_enabled = true` en Azure (Container Apps + regla KEDA `azure-servicebus` con identidad) y en AWS (ECS Fargate + *target tracking* de mensajes por tarea). Repetir la demo de dos ráfagas y comparar con `evals/reports/autoscaling.md`: arranque en frío de una réplica, retardo de la métrica (SQS en CloudWatch ~1 min frente al *polling* de KEDA) y coste.
- [ ] Comparar con el carril A: políticas `llm-token-limit` y `azure-openai-semantic-cache-lookup/store` de APIM y *usage plans* de API Gateway frente a `edge_api`.
- [ ] Carga desde la nube con el mismo `loadtests/locustfile.py`: Azure Load Testing (admite Locust) contra el edge desplegado con varias réplicas, para separar el límite del edge del de un generador en la misma máquina. Repetir los tres perfiles y comparar con `evals/reports/load/README.md`. En AWS, *Distributed Load Testing on AWS* o Locust en ECS.
- [ ] Prueba pequeña contra Azure OpenAI real (`LLM_PROVIDER=azure`, `LOAD_PEAK=20`, despliegue con poca cuota): ver los 429 reales, su `Retry-After` y que el breaker y la degradación se comportan como con el simulado. Presupuesto acordado antes (~0,50 $).

## Fase 11 · Observabilidad ([doc](fase-11-observabilidad.md))

- [ ] Azure: `observability_enabled = true` (Log Analytics con tope de 0,5 GB/día, Application Insights sin claves locales, action group y 2 alertas). Collector `infra/otel/collector-azure.yaml` con la identidad (rol *Monitoring Metrics Publisher*) y los servicios con `TELEMETRY__EXPORTER=otlp`: ver la traza asíncrona de 14 spans en *transaction search* y el mapa de aplicación, y forzar la alerta de coste con un presupuesto ficticio bajo.
- [ ] Conectar Application Insights al proyecto de Foundry (Foundry Tracing) y comparar sus trazas de agentes con los spans `invoke_agent`/`execute_tool` propios.
- [ ] AWS (con la cuenta de la fase 13): activar X-Ray Transaction Search (`aws xray update-trace-segment-destination --destination CloudWatchLogs`), `observability_enabled = true` y el collector `infra/otel/collector-aws.yaml` (imagen ADOT) con el rol de la tarea y la política `otel_collector`. Comprobar que los `gen_ai.*` llegan indexados y que las métricas EMF solo crean las dimensiones declaradas.
- [ ] AgentCore Observability con los agentes del carril A (fase 7) frente a la instrumentación propia.
- [ ] Evaluación online con juez real: `EDGE__ONLINE_EVAL_RATE=0.05` y `EDGE__ONLINE_EVAL_PER_MINUTE=10` con Azure OpenAI; medir el coste por 1.000 respuestas evaluadas y la correlación con el faithfulness offline de la fase 5.
- [ ] Medir los GB/mes de ingesta con tráfico de la demo (`telemetry_demo.py`) en App Insights y CloudWatch, y fijar el muestreo (`TELEMETRY__SAMPLE_RATIO` o *tail sampling* en el collector) a partir del dato.
- [ ] Portar el resto de alertas de SLO a la nube (KQL / Metrics Insights) o usar Managed Prometheus / Amazon Managed Prometheus con las mismas reglas generadas.
- [ ] Langfuse v4 (o Langfuse Cloud) y comparar con Arize Phoenix sobre la misma traza OTLP.

## Fase 12 · Evals de agentes, red teaming y LLMOps ([doc](fase-12-evals-llmops.md))

- [ ] Etiquetar a ciegas `evals/calibration/agents-labels-v1.csv` (Sergio, coste 0) leyendo `agents-v1.md`.
- [ ] Calibrar el juez de agentes: `uv run python -m rag_evals.agent_calibration judge --budget-usd 0.30` (32 muestras con `gpt-4.1`, ~0,25 $) y `agreement`. Es un gate si el kappa de `acceptable` es ≥ 0,6.
- [ ] Suite con modelo real y juez: `uv run python -m rag_evals.agents --mode llm --flavor native --judge native --budget-usd 0.20` y lo mismo con `rag_evals.attacks` (~0,10 $ cada uno): ¿los falsos fallos de la fase 7 eran respuestas aceptables?
- [ ] Red teaming con atacante LLM (PyRIT `RedTeamingAttack` / `CrescendoAttack` con Azure OpenAI como atacante y el agente con harness como objetivo) y el carril A de Azure: AI Red Teaming Agent de Foundry (`azure-ai-evaluation[redteam]`, construido sobre PyRIT). Con `--budget-usd`.
- [ ] promptfoo con LLM (fase 12.5): plugins que generan ataques (`pii`, `bola`, `rbac`, `excessive-agency`, `hijacking`) y estrategias iterativas (`jailbreak`, `crescendo`) con Azure OpenAI como generador y juez (`redteam.provider`), nunca con la generación remota de promptfoo. Con presupuesto acordado antes.
- [ ] Riesgo residual de la 12.4 y la 12.5 (PII en base64, hexadecimal, morse o con homoglifos): probarlo contra Bedrock Guardrails (filtro de información sensible) y Azure Content Safety; si lo paran, documentar la combinación escudo local + guardrail del proveedor.
- [ ] Prompts en Langfuse (fase 12.6): ya publicados en el Langfuse local (`rag_evals.llmops.prompts publish --apply`, 19 en v1). Falta ver en una traza real con Azure OpenAI que cada generación queda enlazada con su versión de prompt. Repetir en Langfuse Cloud o v4 si se migra. Equivalentes gestionados: Bedrock Prompt Management (versiones inmutables y variantes) y los prompts de Foundry.
- [ ] A/B de prompt con modelo real (fase 12.7): `uv run python -m rag_evals.llmops.experiment run evals/experiments/booking-agent-prompt-v2.yaml --mode llm --budget-usd 0.40 --tracker mlflow` (métrica `acceptable` del juez; requiere calibrarlo antes). Repetir con un experimento de modelo (`model: gpt-4.1-nano` frente a `gpt-4.1-mini`) para medir calidad frente a coste. Con 12 escenarios casi nunca habrá significancia: ampliar el dataset o repetir con temperatura > 0.
- [ ] MLflow gestionado: SageMaker (MLflow tracking server) y Azure ML (workspace con URI de MLflow) con el mismo `MlflowExperimentTracker` (solo cambia la URI) y A/B online del candidato con `assignment` (10 % por cliente) y el juez online por variante.
- [ ] Juez online de los especialistas con Azure OpenAI: `ORCHESTRATOR__ONLINE_EVAL_RATE=0.2` y el flujo asíncrono con LLM real (pendiente de la fase 9); ver los scores `agent_*` en la traza del caso en Langfuse y el panel «Agentes: respuestas aceptables». Medir el coste por 1.000 turnos evaluados. Alertar sobre la tasa solo cuando el juez esté calibrado.
- [ ] Drift con tráfico real (fase 12.8): llevar las reglas `drift:psi` y `DistributionDrift` a Amazon Managed Prometheus / Azure Monitor managed Prometheus (son las mismas reglas generadas) y compararlas con la detección de anomalías gestionada (CloudWatch Anomaly Detection, alertas dinámicas de Azure Monitor). Fijar `min_samples` con el volumen real tras 2 semanas de observación.
- [ ] Drift de entrada por embeddings: distancia entre el centroide de las preguntas de la semana y el de referencia con embeddings reales (`text-embedding-3-small` / Titan), y comparar con Bedrock Model Monitor / Azure ML data drift. Con los embeddings `hashing` locales solo se valida el arnés.
