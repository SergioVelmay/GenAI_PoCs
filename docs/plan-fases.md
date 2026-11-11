# Plan de fases (fuente de verdad)

Plan acordado al arrancar el proyecto (03-10-2026) con los ajustes posteriores. Antes, Sergio pegaba
el bloque original de cada fase al abrir sesión. Ahora **este fichero es el plan**: la skill
`iniciar-fase` lo lee y nadie tiene que pegarlo. Si una fase cambia de alcance, se actualiza aquí,
en el mismo commit que el cambio.

- Estado de cada fase: tabla de `AGENTS.md`. Lo hecho y medido: `docs/fase-N-*.md`.
- Pasada GCP por las fases 0–12: [plan de GCP](fase-13-12-checkpoint.md#plan-gcp) (subfases `N.G1`...).
- Lo aplazado por coste o nube: [pendientes-cloud.md](pendientes-cloud.md).

## Cómo se trabaja cada fase

1. **Explicación**: concepto, equivalencia con lo que Sergio ya conoce y comparativa
   Azure ↔ AWS ↔ GCP ↔ framework.
2. **Implementación** pequeña, incremental y ejecutable en local (`docker compose`).
3. **Revisión de Sergio** antes de cada commit.
4. **Checkpoint de entrevista**: preguntas técnicas y de negocio en `preguntas-entrevista.md`.

Los carriles (A gestionado, B SDK nativo, C framework) se aplican solo donde tienen sentido. AWS es
la nube de "producción" de Headache; Azure y GCP son espejos.

## Requisitos de las ofertas y dónde se cubren

| Requisito | Fases principales | Refuerzo |
|---|---|---|
| Orquestación multi-agente con grafos | **9** | 7, 10 |
| Tool calling avanzado e integración de APIs | **6** | 1, 7 |
| Agentic harness y guardrails de ejecución | **8** | 9, 12 |
| RAG y búsqueda semántica (híbrida) | **3, 4** | 5, 7 |
| Context engineering, prompt caching y model routing | **2, 7** | 10 |
| Evals con métricas | **5, 12** | 0 (CI), 13 |
| Clean Architecture y DDD | **0** | todas (puertos y adaptadores) |
| Asíncrono y colas | **3, 10** | 1, 9 |
| SQL avanzado | **1** | 8 (SQL seguro) |
| Ownership en producción y observabilidad | **11, 13** | 0, 2 |
| Equivalencias entre nubes (Azure → AWS → GCP) | **13, 14, 15** | todas (carriles paralelos) |

## Fases 0–12 (hechas)

El objetivo original de cada una sigue en su doc; aquí solo el resumen y los cambios frente al plan.

| Fase | Objetivo original | Cambios frente al plan original |
|---|---|---|
| 0 · Cimientos | Esqueleto profesional donde el negocio no sabe qué nube ni qué modelo hay debajo | Sin Ollama: solo modelos cloud (regla 4) |
| 1 · SQL | El "backend corporativo" de Headache que leen y modifican los agentes | — |
| 2 · Modelos | Llamar a cualquier modelo de forma barata, rápida y trazada (routing, caché, gateway) | — |
| 3 · Indexación | Ingesta event-driven en tres carriles × dos nubes | OpenSearch Serverless (~175 $/mes fijo) sustituido por **S3 Vectors** y pgvector |
| 4 · Retrieval | Recuperar el fragmento correcto y responder sin inventar, con citas | *On Your Data* retirado (14-10-2026): carril A de Azure con **Foundry IQ** |
| 5 · Evals de RAG | Demostrar con números qué estrategia es mejor; gate en CI | — |
| 6 · Tools y MCP | El LLM decide cuándo llamar a la API y el backend procesa la respuesta con seguridad | La tool de **compra** (Idempotency-Key + aprobación humana) quedó sin hacer: ver "Ideas aparcadas" |
| 7 · Agentes | El mismo agente en varios sabores con la misma suite | 5 sabores previstos → **7** (y 9 con GCP) |
| 8 · Harness | Ninguna acción del agente llega a producción sin validar | — |
| 9 · Multi-agente | Triaje → Reembolsos / Creativo / Conocimiento, A2A y asíncrono | — |
| 10 · Escala | 10.000 usuarios simultáneos sin tirar el sistema ni agotar la cuota | — |
| 11 · Observabilidad | Detectar fallos de los agentes antes que el usuario | — |
| 12 · Evals de agentes y LLMOps | Ciclo de calidad continuo: trayectoria, red teaming, prompts, A/B, drift | — |

Azure AI Search: el plan decía tier Free; se pasó a **Basic efímero** (Free no admite identidad
gestionada ni Semantic Ranker, y solo hay uno gratuito por suscripción).

## Fase 13 · Despliegue en AWS (producción principal)

**Objetivo:** llevar todo a la nube de Headache con IaC y CI/CD.

- **Aprenderás:**
  - Terraform en AWS (los módulos ya existen con lo caro en `*_enabled = false`).
  - **Cómputo:** ECS Fargate primero y **EKS** después (efímero: `apply` → probar → `destroy`), para
    comparar; **Lambda** para los *triggers* de indexación.
  - **Modelos:** Bedrock.
  - **Datos y mensajería:** SQS y SNS / EventBridge, Aurora PostgreSQL, S3 Vectors.
  - **Seguridad y red:** Secrets Manager, IAM Roles (el equivalente a Managed Identity), VPC y
    *private endpoints*, API Gateway + WAF.
  - **CI/CD:** GitHub Actions con **OIDC** (sin claves), entornos dev y pro, despliegue
    *blue/green* o *canary*, con `release-gate` como condición.
  - **Ecosistema nativo de AWS (compromiso del plan):** un ejemplo pequeño con **CDK** y otro con
    **CodeBuild `buildspec.yml`**, para comparar con Bicep y Azure Pipelines.
- **Entregables:** infraestructura reproducible y pipeline de despliegue con gates de evals.
- **Cubre:** CI/CD, equivalencia de nubes (AWS) y producción.
- **Heredado de fases anteriores** (detalle en [pendientes-cloud.md](pendientes-cloud.md)):
  - Imagen ARM64 del Runtime de AgentCore en ECR (fase 7).
  - Colas del flujo asíncrono con DLQ en SQS y workers con `ORCHESTRATOR__BROKER=sqs` (fase 9).
  - ECS + *target tracking* por mensajes en cola (fase 10).
  - X-Ray Transaction Search, `observability_enabled` y collector ADOT (fase 11).
  - Servidores MCP en HTTPS y `mcp_gateway_enabled` (fase 6); confirmar en el `plan` que
    `grant_type = "TOKEN_EXCHANGE"` de `infra/terraform/aws/mcp_gateway.tf` es válido (el provider
    no lo documenta).
  - A2A con Cognito *client credentials* (fase 9).
  - `edge_api` detrás de un balanceador: fijar `--forwarded-allow-ips` de uvicorn (por defecto confía
    en `X-Forwarded-For` desde 127.0.0.1; ver `services/edge_api/src/edge_api/identity.py`).
  - Workflow de CI para los jueces LLM (manual o programado, con OIDC y `--budget-usd`).

## Fase 14 · Despliegue espejo en Azure

**Objetivo:** el mismo sistema en el terreno de Sergio, para dominar la traducción en ambos sentidos.

- **Aprenderás:**
  - **Cómputo:** Container Apps frente a **AKS** (efímero), Functions.
  - **Modelos y agentes:** Foundry.
  - **Datos y mensajería:** Service Bus / Event Grid, PostgreSQL Flexible Server, AI Search.
  - **Seguridad y red:** Key Vault + Managed Identity, APIM + Front Door / WAF.
  - **IaC:** Terraform (estado en **backend remoto**: hasta aquí es local y no se borra), y Bicep
    opcional para comparar.
- **Entregables:** despliegue espejo y **chuleta de equivalencias** construida con lo desplegado.
- **Cubre:** equivalencia de nubes en ambos sentidos.
- **Heredado:** App Registrations como código con `azuread` y OBO de extremo a extremo (fase 6,
  decisión del 18-10-2026: nada a mano en el tenant), `apim_enabled`, colas en Service Bus con
  `ORCHESTRATOR__BROKER=servicebus` (fase 9), Container Apps + KEDA `azure-servicebus` (fase 10),
  tokens de servicio de Entra ID para A2A (fase 9), `--forwarded-allow-ips` como en la 13.

## Fase 15 · Despliegue espejo en GCP

**Objetivo:** cerrar el triángulo con la tercera nube, con las mismas evals.

- **Aprenderás:** Cloud Run / GKE (efímero), Agent Runtime, Pub/Sub, Cloud SQL / AlloyDB con
  autenticación IAM, Vector Search (efímero), Memorystore, Cloud Armor, Workload Identity Federation
  en la CI (ya en `infra/terraform/gcp/identity.tf`).
- **Entregables:** despliegue espejo y columna GCP de la chuleta validada con lo desplegado.
- **Heredado:** los pendientes de GCP de cada subfase `N.G` en
  [pendientes-cloud.md](pendientes-cloud.md).

## Fase 16+ · Extras

- **MLOps:** *fine-tuning* de embeddings o de un reranker con datos de Headache (SageMaker / Azure ML /
  Agent Platform) y medición con las evals de la fase 5.
- **Recomendador de eventos** (ML clásico + LLM).
- Multi-región y residencia de datos (UE / EE. UU.).
- **FinOps:** coste por conversación y presupuestos.
- Modelos *self-hosted* (vLLM en EKS/AKS/GKE con GPU) frente a la API gestionada.
- **Text-to-SQL analítico** sobre un *data warehouse* (Redshift / Athena frente a Fabric / Synapse
  frente a BigQuery). La fase 8 cubre solo el text-to-SQL operativo.

## Ideas aparcadas (sin fase asignada)

- **Tool de compra** con `Idempotency-Key` y aprobación humana: propuesta en la 6, aplazada a la 8 y
  luego a la 9, que no la hizo. Es trabajo local, no de nube.
- **Golden dataset más difícil** (fase 5): Hit@5 está saturado; faltan preguntas sintetizadas con LLM
  y paráfrasis.
- **Job de CI con el extra `evals`** (RAGAS y DeepEval): hoy la CI nunca los ejecuta, así que una
  subida de esas librerías puede romper los adaptadores sin que nadie lo vea.
- **Clasificador de seguridad** basado en un modelo razonador sin capa de respuesta (Sergio lo
  mencionó al principio; sin referencia concreta). Encajaría en el routing (fase 2) o como guardrail
  (fase 8). Alternativas conocidas: modelos de política como Llama Guard o gpt-oss-safeguard.
- **Repaso de entrevista** (del checkpoint 12→13): historias STAR, OWASP LLM Top 10, EU AI Act,
  fine-tuning frente a RAG, multimodal, FinOps y un simulacro.
- Fijar la versión de `uv` en la CI (`version:` de `setup-uv` o `required-version`), igual que los
  Dockerfiles fijan `uv:0.12.17`.
