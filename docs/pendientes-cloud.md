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
# ... reindexar (indexer con INDEXER__VECTOR_STORE=azure_search), y si se usan:
#     scripts/managed_ingestion.py azure --apply (carril A) y scripts/provision_foundry_iq.py --apply
# Al terminar: search_enabled = false y
terraform -chdir=infra/terraform/azure apply
```

## Fase 2 · Capa de modelos ([doc](fase-2-modelos.md))

- [x] **Azure CLI** autenticada en un perfil aislado (`AZURE_CONFIG_DIR=%USERPROFILE%\.azure-headache`), suscripción *MSDN - Sergio Velasco* (17-10-2026).
- [ ] **AWS CLI** (`aws configure sso`, perfil en `BEDROCK__PROFILE`).
- [x] **Azure AI Foundry** como código (`foundry.tf`): cuenta AI Services, proyecto `headache` y despliegues `gpt-4.1-mini`, `gpt-4.1-nano` y `text-embedding-3-small` en `swedencentral`, **DataZoneStandard** (50 K TPM), con *Cognitive Services OpenAI User* y *Foundry User* para el usuario. `terraform apply` del grupo `rg-headache-dev` (17 recursos; AI Search apagado) el 17-10-2026. Prueba de humo: `gpt-4.1-nano` responde con Entra ID (16 + 2 tokens, 0,0000024 $) y el embedding devuelve 1024 dimensiones. Hallazgos: el proyecto debe crearse en serie con los despliegues (409) y el rol "Azure AI User" se llama ahora "Foundry User". El 18-10-2026 los recursos se recrearon con nombres CAF (`aif-headache-dev-swc-001`...), sin sufijo aleatorio.
- [ ] **Bedrock** (`eu-west-1`): habilitar el acceso a Claude Haiku 4.5 (formulario de Anthropic) y a Nova Lite.
- [ ] Ejecutar `scripts/llm_cache_benchmark.py --provider azure|aws` y `scripts/llm_gateway_demo.py` (motores `native` y `litellm`) y revisar las trazas en Langfuse.
- [ ] Langfuse local sigue en la imagen `langfuse:3` (OSS v3, parches de seguridad hasta 31-01-2027). El SDK Python v4 es compatible (servidor ≥ 3.63.0). Valorar migrar a la imagen v4 en la fase 11.

## Fase 3 · Indexación ([doc](fase-3-comparativa.md))

- [ ] `terraform plan`/`apply` en `infra/terraform/aws` y `infra/terraform/azure` (crear `terraform.tfvars` con `budget_email` y, en Azure, `subscription_id`). Decidir `search_sku` (free con claves o basic ~70 €/mes con identidad gestionada).
- [ ] Copiar los outputs de Terraform al `.env` (`AZURE_SEARCH__*`, `S3_VECTORS__*`, `AZURE_MANAGED__*`, `BEDROCK_KB__*`, colas SQS/Service Bus).
- [ ] Carril B: `scripts/provision_search_index.py --apply` e indexar con `INDEXER__EMBEDDINGS=azure|bedrock` y `INDEXER__VECTOR_STORE=azure_search|s3_vectors` (también con `INDEXER__BROKER=sqs|servicebus`).
- [ ] Carril A: `scripts/managed_ingestion.py aws --apply` y `azure --apply` (Azure: con `search_sku = "basic"`, el indexer usa identidad gestionada); comprobar que el vectorizador de Azure usa 1024 dimensiones.
- [ ] Medir por carril (ingesta completa, incremental, coste real, latencia) y completar `docs/fase-3-comparativa.md`.

## Fase 4 · Retrieval ([doc](fase-4-retrieval.md))

Requiere antes los pendientes de las fases 2 y 3.

- [ ] Clasificar el corpus con `acl` explícita y reingerir: `provision_search_index.py --apply` (el esquema añade `title`, `acl` y configuración semántica) y resincronizar Bedrock KB con los sidecars nuevos. Sin `acl`, los adaptadores cloud deniegan.
- [ ] AI Search en tier `basic` para Semantic Ranker y Foundry IQ (plan semántico gratuito, ya en Terraform). Revisar el coste antes del `apply`.
- [ ] Foundry IQ: con `foundry_enabled = true` el proyecto y su rol *Search Index Data Reader* ya los crea Terraform; rellenar `FOUNDRY_IQ__*` con los outputs y ejecutar `scripts/provision_foundry_iq.py --apply` (verifica el `baseFilter` de la audiencia).
- [ ] Bedrock: habilitar Cohere Rerank y rellenar `BEDROCK__RERANK_MODEL_ARN` y `BEDROCK_KB__GENERATION_MODEL_ARN`.
- [ ] Ejecutar el servicio con `RETRIEVAL__BACKEND=azure_search`, `RETRIEVAL__LLM=gateway`, `RETRIEVAL__RERANKER=bedrock` y `RETRIEVAL__MANAGED=bedrock_kb|foundry_iq`; comparar A/B/C con el mismo corpus y las mismas preguntas (coste, latencia, citas y abstención) y documentarlo.

## Fase 5 · Evals ([doc](fase-5-evals.md))

- [ ] `generation_eval --mode llm --judge native --limit 20 --budget-usd 0.50` (gpt-4.1-nano/mini); después `--judge ragas` y `--judge deepeval` con la misma muestra para comparar coste, latencia y acuerdo entre jueces.
- [ ] Calibrar los jueces: Sergio etiqueta ~20 respuestas a mano y se mide el acuerdo humano-juez antes de usar el juez como gate.
- [ ] Juez con modelo distinto del generador (sesgo de autopreferencia) y embeddings reales para `AnswerRelevancy` de RAGAS (en local son hashing, léxicos).
- [ ] Carril A: Azure AI Foundry Evaluation y Amazon Bedrock Evaluations con el mismo golden dataset.
- [ ] Repetir las evals de retrieval con embeddings reales y reranker de nube, fijar un baseline nuevo y completar el doc.
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
