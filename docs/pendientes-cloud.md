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

## Fase 9 · Multi-agente (en curso)

- [ ] Flujo de punta a punta con LLM real (Azure OpenAI): triaje + especialistas en proceso y con los contenedores A2A (`docker compose --profile agents`). En contenedor, Azure OpenAI necesita `AZURE_OPENAI__API_KEY` o una identidad de servicio (no hay `az login`).
- [ ] A2A con identidad de nube: tokens de servicio de Entra ID (app roles / Managed Identity) y Cognito client credentials en lugar del emisor de desarrollo (fases 13/14).
- [ ] Carril A: especialistas en AgentCore Runtime con protocolo A2A y workflow multi-agente de Foundry Agent Service con el mismo dataset.
