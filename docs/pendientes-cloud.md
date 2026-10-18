# Pendientes con coste o nube

Todo lo que se aplazó al cerrar cada fase: la implementación local está hecha y probada, y falta
la validación en la nube. **Todo tiene coste o crea recursos: pedir confirmación antes de cada paso.**
Al completar uno, se marca `[x]` y se documenta el resultado en el doc de su fase.

Orden recomendado: primero la fase 2 (credenciales y modelos), que desbloquea el resto.

**Decisión de Azure (checkpoint 8→9):** región `swedencentral`, tiers gratuitos donde no recorten
capacidades (Content Safety F0, plan gratuito del Semantic Ranker) y **AI Search Basic** (~70 €/mes
de coste fijo) para tener identidad gestionada, Semantic Ranker y Foundry IQ. Presupuesto del grupo
de recursos: 100 €/mes con alertas al 50, 75 y 100 %. AWS, más adelante.

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

## Fase 2 · Capa de modelos ([doc](fase-2-modelos.md))

- [x] **Azure CLI** autenticada en un perfil aislado (`AZURE_CONFIG_DIR=%USERPROFILE%\.azure-headache`), suscripción *MSDN - Sergio Velasco* (17-10-2026).
- [ ] **AWS CLI** (`aws configure sso`, perfil en `BEDROCK__PROFILE`).
- [x] **Azure AI Foundry** como código (`foundry.tf`): cuenta AI Services, proyecto `headache` y despliegues `gpt-4.1-mini`, `gpt-4.1-nano` y `text-embedding-3-small` en `swedencentral`, **DataZoneStandard** (chat 200 K TPM desde el 18-10-2026; embeddings 50 K), con *Cognitive Services OpenAI User* y *Foundry User* para el usuario. `terraform apply` del grupo `rg-headache-dev` (17 recursos; AI Search apagado) el 17-10-2026. Prueba de humo: `gpt-4.1-nano` responde con Entra ID (16 + 2 tokens, 0,0000024 $) y el embedding devuelve 1024 dimensiones. Hallazgos: el proyecto debe crearse en serie con los despliegues (409) y el rol "Azure AI User" se llama ahora "Foundry User". El 18-10-2026 los recursos se recrearon con nombres CAF (`aif-headache-dev-swc-001`...), sin sufijo aleatorio.
- [ ] **Bedrock** (`eu-west-1`): habilitar el acceso a Claude Haiku 4.5 (formulario de Anthropic) y a Nova Lite.
- [x] Azure (18-10-2026): `llm_cache_benchmark.py` en los dos tiers (caché −58 % de coste, 98 % de tokens cacheados desde la 2.ª llamada) y `llm_gateway_demo.py` con `native`, `litellm` y router LLM (20/20; el router LLM duplica llamadas sin mejorar el enrutado). Trazas en Langfuse. Capacidad subida a 200 K TPM tras un 429 con 50 K. Detalle en [fase 2](fase-2-modelos.md).
- [ ] AWS: repetir ambos scripts con `--provider aws` y probar el fallback entre proveedores.
- [ ] Langfuse local sigue en la imagen `langfuse:3` (OSS v3, parches de seguridad hasta 31-01-2027). El SDK Python v4 es compatible (servidor ≥ 3.63.0). Valorar migrar a la imagen v4 en la fase 11.

## Fase 3 · Indexación ([doc](fase-3-comparativa.md))

- [x] Azure (18-10-2026): Terraform aplicado (AI Search Basic en `francecentral`), outputs en el `.env`, corpus clasificado con ACL (`scripts/classify_corpus.py`), carril B con Service Bus + Foundry + AI Search y carril A con indexer + skillset (vectorizador verificado). Medidas y hallazgos en [fase 3](fase-3-comparativa.md#6-resultados-en-azure-18-10-2026).
- [ ] Investigar por qué el indexer del carril A reprocesa todos los blobs en cada ejecución (hallazgo abierto).
- [ ] AWS: `terraform apply` en `infra/terraform/aws`, carril B con `INDEXER__EMBEDDINGS=bedrock`, `INDEXER__VECTOR_STORE=s3_vectors` e `INDEXER__BROKER=sqs`, y carril A con `scripts/managed_ingestion.py aws --apply`.
- [ ] Coste real por carril con Cost Management / Cost Explorer tras unos días de uso.

## Fase 4 · Retrieval ([doc](fase-4-retrieval.md))

Requiere antes los pendientes de las fases 2 y 3.

- [x] Azure (18-10-2026): corpus clasificado con ACL explícita e ingerido en el índice `knowledge`; AI Search Basic con Semantic Ranker; Foundry IQ aprovisionado (KB en preview con esfuerzo `minimal`, salida extractiva y 5 documentos) y comparativa A/B/C con el golden dataset completo. Resultados y hallazgos en [fase 4](fase-4-retrieval.md#resultados-en-azure-18-10-2026).
- [ ] AWS: resincronizar Bedrock KB con los sidecars con ACL, habilitar Cohere Rerank (`BEDROCK__RERANK_MODEL_ARN`, `BEDROCK_KB__GENERATION_MODEL_ARN`) y repetir la comparativa con `RETRIEVAL__RERANKER=bedrock` y `RETRIEVAL__MANAGED=bedrock_kb`.
- [ ] Estrategias con LLM (rewrite, HyDE, subpreguntas, router, auto) con `RETRIEVAL__LLM=gateway` y modelos reales, medidas con el golden dataset.

## Fase 5 · Evals ([doc](fase-5-evals.md))

- [x] `generation_eval --mode llm --judge native|ragas|deepeval --limit 20 --budget-usd 0.50` con la misma muestra (18-10-2026, ~0,39 $ en total): informes en `evals/reports/azure/`.
- [x] Calibrar los jueces (18-10-2026, ~0,96 $): conjunto `evals/calibration/calibration-v1`, etiquetas a ciegas y adjudicadas; juez nativo kappa 0,82 (fidelidad) y 1,0 (relevancia). Informe en `evals/reports/azure/calibration.md`.
- [x] Juez con modelo distinto del generador (`gpt-4.1`, despliegue `judge` en Terraform) y embeddings reales (`text-embedding-3-small`) para `AnswerRelevancy` de RAGAS.
- [ ] Carril A: Azure AI Foundry Evaluation y Amazon Bedrock Evaluations con el mismo golden dataset.
- [x] Evals de retrieval con embeddings reales y Semantic Ranker en Azure (`--engine azure --engine azure-semantic`, 18-10-2026): informe en `evals/reports/azure/`. El baseline de la CI sigue siendo el local (coste 0); el de nube se compara a mano.
- [x] Juez de fidelidad sobre el `answer` de Foundry IQ (18-10-2026, `rag_evals.managed_eval`): 19/21 fieles; detecta la sobre-respuesta y una respuesta que añade condiciones no citadas. Informe en `evals/reports/azure/managed-foundry_iq.md`.
- [ ] Pasar el job `evals` de CI a `--mode enforce` tras varias ejecuciones estables.

## Fase 6 · Tools y MCP ([doc](fase-6-tools-mcp.md))

- [ ] Ejecutar `scripts/tool_loop_demo.py` y `python -m support_agent` (con y sin `--gateway`) con Azure OpenAI y Bedrock; revisar trazas y coste en Langfuse.
- [ ] Registrar los servidores MCP en Entra ID (App ID URI y scopes; revisar el formato de `scp`) y en Cognito (resource server + scopes); probar OBO de extremo a extremo y sustituir `DevTokenExchanger`.
- [ ] Con los servidores MCP en HTTPS (fases 13/14): `mcp_gateway_enabled=true` (AgentCore Gateway) y `apim_enabled=true` (APIM); comparar latencia y coste con el gateway propio.
- [ ] Tool de compra con `Idempotency-Key` estable y aprobación humana.

## Fase 7 · Agentes ([doc](fase-7-agentes.md))

- [ ] Suite de agentes con LLM real: `python -m rag_evals.agents --mode llm --budget-usd ...` (sabores de framework y carril A); comparar coste, latencia y aciertos.
- [ ] Foundry: `scripts/provision_foundry_agent.py --apply`, ejecutar el sabor `foundry` con `FoundryAgentService` (`AsyncOpenAI` sobre `{proyecto}/openai/v1` con token de Entra ID) y medir el truncado del servicio frente a nuestra compactación.
- [ ] AgentCore: imagen ARM64 del Runtime en ECR (entrypoint con `create_runtime_app`, fase 13), `agentcore_enabled=true` + `agentcore_container_uri`, cablear `AgentCoreCheckpointStore`/`AgentCorePreferenceStore` con `AGENTCORE__MEMORY_ID` e invocar con `BotoRuntimeInvoker` (rol con `InvokeAgentRuntimeForUser`).
- [ ] Valorar las estrategias de extracción de AgentCore Memory (`USER_PREFERENCE`) y la memoria de Foundry frente a la tool explícita `remember_preference`.

## Fase 8 · Harness ([doc](fase-8-harness.md))

- [ ] `scripts/analytics_sql_demo.py "pregunta"` con Azure OpenAI y Bedrock: intentos de autocorrección, aciertos y coste con un conjunto de preguntas de operaciones.
- [ ] `content_safety_enabled=true` (F0) y `bedrock_guardrail_enabled=true`; ejecutar el agente con `GUARDRAILS__SHIELD=azure|bedrock` y comparar con `HeuristicShield` (detección, falsos positivos, latencia y coste) sobre los mismos ataques.
- [ ] Tests de ataque con LLM real (hoy `rag_evals.attacks` solo usa el modelo comprometido guionizado; añadir un modo `llm` con presupuesto) y red teaming automatizado (fase 12).
