# AGENTS.md · GenAI_PoCs

Instrucciones para agentes de IA (GitHub Copilot, Claude Code, Codex...). Lo que aplica siempre está
aquí; el detalle por área se carga solo al tocar esos ficheros (`.github/instructions/*.instructions.md`)
y los procedimientos están en skills (`.github/skills/*/SKILL.md`). Visión general para personas: `README.md`.

## Contexto

- PoCs didácticas del stack completo de **sistemas agénticos GenAI** (RAG, tool calling, MCP, multi-agente, harness/guardrails, evals, observabilidad, seguridad, DevOps/LLMOps) en **Azure y AWS**.
- Usuario: **Sergio**. Objetivo: preparar entrevistas para **AI Technical Lead** de proyectos GenAI y agénticos en Headache, y saber explicar y defender cada decisión.
- El rol es **50 % liderazgo técnico** (arquitectura, decisiones y trade-offs, calidad, equipo) y **50 % negocio y stakeholders** (impacto, coste/ROI, riesgos, priorización, comunicar a perfiles no técnicos).
- Perfil: 7+ años en IA solo sobre Azure (AI Foundry, AI Search, Container Apps, AKS, Functions, Bicep, Azure DevOps), Microsoft Agent Framework y FastMCP. **El rol pide AWS** (producción de Headache): cada concepto se ancla en lo que conoce de Azure y se traslada a AWS. Aprendiendo también LlamaIndex, LangChain/LangGraph, Strands y Terraform.
- Cliente hipotético: **Headache**, marketplace global de experiencias y eventos (~300 M de usuarios, producción en AWS), con usuarios internos (operaciones, soporte, partners) y una parte pública de alta escala.

## Reglas obligatorias

1. El cliente se llama **siempre "Headache"**: código, comentarios, datos, commits, docs y chat. **Nunca** el nombre de la empresa real en que se inspira ni marcas comerciales de terceros (tampoco en ejemplos ni datos sintéticos). El hook `pre-commit` lo comprueba.
2. **Pedir confirmación** antes de: commit, push, crear recursos cloud, `terraform plan/apply`, cualquier acción con coste y cualquier acción destructiva (borrar ficheros o volúmenes, `docker compose down -v`, `git restore`/`reset`). "Revisado, haz commit y push" autoriza ese commit y ese push.
3. **Nunca secretos** en el repo: `.env` en local (ignorado), `.secrets/` para claves de desarrollo, OIDC en CI, Managed Identity / IAM Roles en la nube.
4. **Solo modelos cloud** (Azure AI Foundry y Amazon Bedrock). Docker en local solo ejecuta infraestructura.
5. **Local y sin coste primero.** Lo que necesite nube o tenga coste se implementa (código e IaC) pero su ejecución se aplaza a `docs/pendientes-cloud.md`. La fase queda `✅ Hecha*`.
6. El LLM nunca toma decisiones de negocio: las toma el dominio (código determinista).

## Forma de trabajar

- Responder en **español** y con enfoque **didáctico**. Para cada fase: concepto → equivalencia con lo que Sergio conoce y comparativa **Azure ↔ AWS ↔ framework** → implementación incremental y verificable → **parar para revisión** → **checkpoint de entrevista** con preguntas técnicas **y** de negocio/stakeholders (qué problema de Headache resuelve, KPI, coste, riesgos, cómo explicarlo a un no técnico, qué priorizarías y qué dejarías fuera).
- **Tres carriles** cuando tenga sentido, con el mismo dataset y las mismas evals: **A. Gestionado** (Foundry Agent Service, Bedrock KB, AgentCore...), **B. SDK nativo** (`azure-*`, `boto3`), **C. Framework** (LlamaIndex, LangGraph, MAF, Strands).
- Ritmo: una sesión por fase; subfases pequeñas; tras cada una, verificar y esperar el "revisado". Skills: `iniciar-fase`, `cerrar-subfase`, `cerrar-fase`, `explicar-concepto`.
- Explorar y planificar antes de codificar cambios de varios ficheros. Ante una decisión de diseño con varias opciones razonables, preguntar con la opción recomendada primero.
- Dar evidencia, no afirmaciones: salida de los tests, comando ejecutado, enlace a la ejecución de la CI.
- Tras cada push, **vigilar la CI hasta el final** e informar por job.
- Al terminar de usarlos, detener contenedores y emuladores para ahorrar recursos (`docker compose --profile <p> stop`; sin `-v`).
- Coste: modelos baratos por defecto; AI Search Free; sin OpenSearch Serverless (S3 Vectors o pgvector); EKS/AKS solo efímeros; alertas de presupuesto. Presupuesto total: 200 $ Azure + 100 $ AWS.

## Comandos

```powershell
uv sync --all-packages                                          # dependencias (monorepo uv, Python 3.12)
uv run ruff check . ; uv run ruff format --check . ; uv run mypy ; uv run pytest   # calidad (= CI)
uv run pytest -m integration                                    # requiere docker compose up -d + migraciones
uv run alembic -c packages/headache_core/alembic.ini upgrade head
uv run python scripts/generate_synthetic_data.py --scale small  # datos en data/synthetic/ (no versionados)
docker compose up -d                                            # PostgreSQL+pgvector, RabbitMQ
docker compose --profile observability|sqs|servicebus|mcp up -d # Langfuse, ElasticMQ, Service Bus, MCP
uv run python -m rag_evals.gate ; uv run python -m rag_evals.attacks --check   # gates de evals (coste 0)
```

## Arquitectura

- **Clean Architecture / Ports & Adapters** en `packages/headache_core`: `domain/` (Python puro), `application/` (casos de uso), `ports/` (`typing.Protocol`), `adapters/` (`memory` para tests; `postgres`, `azure_*`, `bedrock*`...).
- Paquetes: `packages/headache_core`, `packages/harness` (depende de core, nunca al revés), `evals/` (`rag_evals`). Servicios en `services/`: `ticketing_api`, `indexer`, `retrieval`, `mcp_servers`, `support_agent`, `booking_agent`.
- Todo LLM pasa por `LLMPort` y el gateway (`adapters/llm_factory.build_gateway`). Toda tool, por `JournaledExecutor` + `GuardedExecutor`. Todo agente cumple `AgentRuntime`.
- Configuración con Pydantic Settings, variables anidadas con `__` (`BEDROCK__REGION`), secretos `SecretStr`. Por defecto, todo local.
- IaC: **Terraform** en `infra/terraform/{aws,azure}` (recursos con coste desactivados por defecto). CI/CD: **GitHub Actions**.

## Convenciones

- Commits: Conventional Commits en español (`feat: fase 8.2 - ...`), con trailer `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.
- Tests deterministas, sin red ni coste (adaptadores en memoria). Lo que tiene coste exige `--budget-usd` o dry-run por defecto (`--apply`).
- Docs de fase en `docs/fase-N-*.md` con la misma estructura. Las preguntas de entrevista viven solo en `docs/preguntas-entrevista.md` (una sección por fase; el doc enlaza a ella).
- `✅ Hecha*` = implementación cerrada, falta validación en cloud. No escribir "asterisco".

## Entorno y problemas conocidos

- Windows + **PowerShell 5.1** (sin `&&`, `||`, heredocs), Docker Desktop (WSL2), `gh` autenticado como `SergioVelmay`. Repo privado `SergioVelmay/GenAI_PoCs`.
- El terminal integrado a veces mete un **carácter espurio** al inicio de un comando: no encadenar acciones destructivas detrás de pasos que puedan fallar y verificar el resultado (`git log -1`, `git status`).
- Desde el código, `127.0.0.1` y no `localhost` (IPv6 añade ~2 s por conexión).
- Verificar con **los mismos comandos que la CI** (p. ej. `terraform fmt -check -recursive`).
- Si aparecen ficheros con cambios que no son de esta sesión: no guardarlos ni descartarlos sin analizarlos con Sergio.
- Más casos y su porqué: `docs/lecciones-aprendidas.md`.

## Estado de las fases

| Fase | Tema | Estado | Doc |
|---|---|---|---|
| 0 | Cimientos: monorepo, Clean Architecture, docker compose, datos sintéticos, CI | ✅ Hecha | `docs/fase-0-cimientos.md` |
| 1 | SQL avanzado + `ticketing_api` | ✅ Hecha | `docs/fase-1-sql.md` |
| 2 | Capa de modelos, LLM gateway, routing, prompt caching, coste | ✅ Hecha* | `docs/fase-2-modelos.md` |
| 3 | Indexación event-driven, 3 carriles × 2 nubes | ✅ Hecha* | `docs/fase-3-comparativa.md` |
| 4 | Retrieval avanzado y servicio RAG | ✅ Hecha* | `docs/fase-4-retrieval.md` |
| 5 | Evals de RAG y gate en CI | ✅ Hecha* | `docs/fase-5-evals.md` |
| 6 | Tool calling manual, servidores MCP, OAuth y MCP gateway | ✅ Hecha* | `docs/fase-6-tools-mcp.md` |
| 7 | Un agente en 7 sabores y context engineering | ✅ Hecha* | `docs/fase-7-agentes.md` |
| 8 | Agentic harness y guardrails | ✅ Hecha* | `docs/fase-8-harness.md` |
| 8→9 | Checkpoint: CI, docs 0–2, README, AGENTS.md | 🚧 En curso | — |
| 9 | Multi-agente (Triaje → Reembolsos / Creativo / Conocimiento), A2A, asíncrono | | |
| 10 | Escala: backpressure, rate limiting, caché semántica, autoescalado por colas, carga | | |
| 11 | Observabilidad: OpenTelemetry GenAI, Langfuse, CloudWatch/X-Ray vs App Insights | | |
| 12 | Evals de agentes, red teaming, LLMOps (prompts versionados, A/B) | | |
| 13 | Despliegue en AWS con Terraform (ECS/EKS, Lambda, Bedrock, SQS, Aurora, OIDC) | | |
| 14 | Despliegue espejo en Azure (ACA/AKS, Functions, Foundry, Service Bus, AI Search) | | |
| 15+ | Extras: MLOps, recomendador, multi-región, FinOps, vLLM | | |

Pendientes con coste de cada fase: `docs/pendientes-cloud.md` (recordárselos a Sergio cuando toque).
Requisitos de las ofertas a cubrir: multi-agente con grafos, tool calling avanzado, harness y guardrails, RAG híbrido, context engineering + prompt caching + model routing, evals con métricas, Clean Architecture/DDD, asíncrono y colas, SQL avanzado, CI/CD + observabilidad y equivalencias Azure ↔ AWS.
