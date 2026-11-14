# AGENTS.md · GenAI_PoCs

Instrucciones para agentes de IA (GitHub Copilot y Claude Code). Lo que aplica siempre está aquí; el
detalle por área está en `.github/instructions/<area>.instructions.md` (fuente única: Copilot la carga
por `applyTo` y Claude Code por un puntero generado en `.claude/rules/`), y los procedimientos, en las
skills de `.claude/skills/*/SKILL.md` (las leen los dos). Visión general para personas: `README.md`.

## Contexto

- PoCs didácticas del stack completo de **sistemas agénticos GenAI** (RAG, tool calling, MCP, multi-agente, harness/guardrails, evals, observabilidad, seguridad, DevOps/LLMOps) en **Azure y AWS**, y **GCP** en curso (pasada GCP por las fases 0–12, plan en `docs/fase-13-12-checkpoint.md#plan-gcp`).
- **Plan completo de fases** (objetivo, entregables, compromisos heredados e ideas aparcadas): `docs/plan-fases.md`. Es la fuente de verdad: no hace falta que Sergio pegue el plan de una fase.
- Usuario: **Sergio**. Objetivo: preparar entrevistas para **AI Technical Lead** de proyectos GenAI y agénticos en Headache, y saber explicar y defender cada decisión.
- El rol es **50 % liderazgo técnico** (arquitectura, decisiones y trade-offs, calidad, equipo) y **50 % negocio y stakeholders** (impacto, coste/ROI, riesgos, priorización, comunicar a perfiles no técnicos).
- Perfil: 7+ años en IA solo sobre Azure (AI Foundry, AI Search, Container Apps, AKS, Functions, Bicep, Azure DevOps), Microsoft Agent Framework y FastMCP. **El rol pide AWS** (producción de Headache): cada concepto se ancla en lo que conoce de Azure y se traslada a AWS. Aprendiendo también LlamaIndex, LangChain/LangGraph, Strands y Terraform.
- Cliente hipotético: **Headache**, marketplace global de experiencias y eventos (~300 M de usuarios, producción en AWS), con usuarios internos (operaciones, soporte, partners) y una parte pública de alta escala.

## Reglas obligatorias

1. El cliente se llama **siempre "Headache"**: código, comentarios, datos, commits, docs y chat. **Nunca** el nombre de la empresa real en que se inspira ni marcas comerciales de terceros (tampoco en ejemplos ni datos sintéticos). El hook `pre-commit` lo comprueba.
2. **Pedir confirmación** antes de: commit, push, crear recursos cloud, `terraform plan/apply`, cualquier acción con coste, cualquier acción destructiva (borrar ficheros, ramas o volúmenes, `docker compose down -v`, `git restore`/`reset`), **instalar software** en el equipo, **aceptar licencias** (EULA) y regenerar `data/synthetic/`. "Revisado, haz commit y push" autoriza ese commit y ese push **a la rama de trabajo**; "haz commit" autoriza solo el commit.
3. **Nunca secretos** en el repo: `.env` en local (ignorado), `.secrets/` para claves de desarrollo, OIDC en CI, Managed Identity / IAM Roles en la nube.
4. **Solo modelos cloud** (Azure AI Foundry, Amazon Bedrock y Gemini en Gemini Enterprise Agent Platform). Docker en local solo ejecuta infraestructura.
5. **Local y sin coste primero.** Lo que necesite nube o tenga coste se implementa (código e IaC) pero su ejecución se aplaza a `docs/pendientes-cloud.md`. La fase queda `✅ Hecha*`.
6. El LLM nunca toma decisiones de negocio: las toma el dominio (código determinista).
7. **`main` está protegida** (ruleset `proteger-main`, sin bypass): nunca push directo a `main` ni merge de PRs. Se trabaja en una rama por fase y **Sergio revisa y fusiona la PR a mano**.

## Forma de trabajar

- Responder en **español** y con enfoque **didáctico**. Para cada fase: concepto → equivalencia con lo que Sergio conoce y comparativa **Azure ↔ AWS ↔ GCP ↔ framework** → implementación incremental y verificable → **parar para revisión** → **checkpoint de entrevista** con preguntas técnicas **y** de negocio/stakeholders (qué problema de Headache resuelve, KPI, coste, riesgos, cómo explicarlo a un no técnico, qué priorizarías y qué dejarías fuera).
- **Tres carriles** cuando tenga sentido, con el mismo dataset y las mismas evals: **A. Gestionado** (Foundry Agent Service, Bedrock KB, AgentCore, RAG Engine, Agent Runtime...), **B. SDK nativo** (`azure-*`, `boto3`, `google-genai`/`google-cloud-*`), **C. Framework** (LlamaIndex, LangGraph, MAF, Strands, ADK).
- Ritmo: subfases pequeñas; tras cada una, verificar y esperar el "revisado". Skills: `iniciar-fase`, `retomar-fase`, `cerrar-subfase`, `cerrar-fase`, `explicar-concepto`.
- **Vocabulario de Sergio:** "revisado" (o "revisado, haz commit y push") = commit + push + PR de la subfase entregada; "haz commit" = solo commit; "dale" = empieza la subfase que acabas de proponer.
- **Preguntas:** pocas, solo las que bloquean el paso actual, con la opción recomendada primero; decir explícitamente si una propuesta **cambia el plan** original; aparcar las dudas que no bloquean.
- Explorar y planificar antes de codificar cambios de varios ficheros. Antes de escribir contra una librería, leer **la API instalada** (firmas, código fuente; en Terraform, `terraform providers schema -json`): las versiones van por delante de lo que conoce el modelo.
- Dar evidencia, no afirmaciones: salida de los tests, comando ejecutado, enlace a la ejecución de la CI. Si la subfase arranca un servicio, probarlo de verdad (HTTP o E2E sin LLM), no solo los tests.
- **Variables de entorno:** si un cambio toca variables, dar a Sergio las instrucciones exactas para su `.env` (clave, valor, bloque, si se añade o modifica y para qué). Su `.env` sigue el mismo orden, grupos y comentarios que `.env.example`.
- **Tareas humanas** (etiquetar, revisar datos) que no bloquean: commit, apuntarlas en `docs/pendientes-cloud.md` y seguir.
- **Ramas y PR** (desde la 0.G): una rama por fase desde `main` actualizada (`fase/<N>` o `fase/<N>-g`, p. ej. `fase/0-g`), un commit por subfase tras el "revisado" y una **PR en draft** abierta con el primer push (la CI corre en `pull_request`). Al cerrar la fase, la PR pasa a lista para revisión. `main` exige PR, el check `release-gate` y **merge commit** (squash y rebase desactivados: los docs citan hashes de commits que no deben reescribirse). Arreglos ajenos a la subfase, en un commit `fix:` aparte; un arreglo para devolver a verde la CI de la propia subfase entra en el flujo de `cerrar-subfase` y se informa.
- Tras cada push, **vigilar la CI hasta el final** e informar por job.
- Al terminar de usarlos, detener contenedores y emuladores para ahorrar recursos (`docker compose --profile <p> stop` para también el núcleo y las dependencias del perfil; sin `-v`).
- Coste: modelos baratos por defecto; AI Search **Basic y efímero** (Free no admite identidad gestionada y solo hay uno por suscripción); sin OpenSearch Serverless (S3 Vectors o pgvector); EKS/AKS/GKE solo efímeros; Vector Search y RAG Engine efímeros; alertas de presupuesto. Presupuesto total: 200 $ Azure + 100 $ AWS + 100 $ GCP.

## Sesiones y contexto

- **El contexto vive en el repo, nunca en el chat ni en la memoria del agente**: plan (`docs/plan-fases.md`), estado (tabla de abajo), subfases de la fase en curso (cuerpo de su PR draft), lo hecho (`docs/fase-N-*.md`), el porqué de las reglas (`docs/lecciones-aprendidas.md`). Lo que aprendas y deba sobrevivir a la sesión se escribe ahí, no en una memoria privada.
- **Una sesión por fase**, con todas sus subfases: arranca con `iniciar-fase` (fase nueva), sigue subfase a subfase en la misma sesión (un commit por subfase como mínimo, todos en la **PR de la fase**) y termina con `cerrar-fase`. Solo se abre una sesión nueva con `retomar-fase` si el contexto pasa del ~70 % o la fase se retoma otro día. Mensajes de arranque en `README.md` ("Sesiones de trabajo").
- Modelo: el Claude Opus más reciente. Si la sesión arranca en automático o cambia sola de modelo, parar antes de implementar y avisar.
- Exportaciones de chats antiguos: **nunca** dentro del repo (contienen el nombre real).

## Comandos

```powershell
uv sync --all-packages --locked                                 # dependencias (= CI; desinstala los extras: evals, pii, redteam, llmops)
uv run ruff check . ; uv run ruff format --check . ; uv run mypy ; uv run pytest   # calidad (= CI)
uv run pytest -m integration                                    # docker compose up -d + migraciones; Redis (scale), sqs, servicebus y pubsub o se saltan
uv run alembic -c packages/headache_core/alembic.ini upgrade head
uv run python scripts/generate_synthetic_data.py --scale small  # data/synthetic/ está en escala large: no regenerar sin preguntar
docker compose up -d                                            # PostgreSQL+pgvector, RabbitMQ
docker compose --profile observability|sqs|servicebus|pubsub|mcp|agents|scale up -d # Langfuse, colas, MCP, A2A, Redis
docker compose --profile telemetry up -d                        # collector, Jaeger :16686, Prometheus :9095, Grafana :3001
uv run python -m rag_evals.gate ; uv run python -m rag_evals.agents --check ; uv run python -m rag_evals.attacks --check ; uv run python -m rag_evals.multiagent --check   # gates de evals (coste 0)
uv run python -m rag_evals.redteam.mutations --check ; uv run python -m rag_evals.llmops.prompts check ; uv run python -m rag_evals.llmops.drift --check   # fase 12 (coste 0)
uv run python scripts/sync_agent_rules.py [--check]             # regenera .claude/rules desde .github/instructions
```

Con un extra instalado (`uv sync --all-packages --extra pii`...), `uv run --no-sync` evita que el siguiente `uv run` lo desinstale.

## Arquitectura

- **Clean Architecture / Ports & Adapters** en `packages/headache_core`: `domain/` (Python puro), `application/` (casos de uso), `ports/` (`typing.Protocol`), `adapters/` (`memory` para tests; `postgres`, `azure_*`, `bedrock*`...).
- Paquetes: `packages/headache_core`, `packages/harness` (depende de core, nunca al revés), `evals/` (`rag_evals`). Servicios en `services/`: `ticketing_api`, `indexer`, `retrieval`, `mcp_servers`, `support_agent`, `booking_agent`, `orchestrator` (multi-agente, fase 9), `edge_api` (puerta pública, fase 10).
- Todo prompt vive en `<paquete>/prompts/*.md` con versión y candado (`prompts.lock.json`, fase 12.6): cambiar el texto exige subir la versión. Todo LLM pasa por `LLMPort` y el gateway (`adapters/llm_factory.build_gateway`). Toda tool, por `JournaledExecutor` + `GuardedExecutor`. Todo agente cumple `AgentRuntime`. Toda traza, por `TelemetryPort` (OpenTelemetry, fase 11).
- Configuración con Pydantic Settings, variables anidadas con `__` (`BEDROCK__REGION`), secretos `SecretStr`. Por defecto, todo local.
- IaC: **Terraform** en `infra/terraform/{aws,azure,gcp}` (recursos con coste desactivados por defecto; estado local, no borrarlo). CI/CD: **GitHub Actions**.

## Convenciones

- Commits: Conventional Commits en español (`feat: fase 8.2 - ...`), mensaje desde fichero (`git commit -F <fichero>`), con el trailer **del agente que ha hecho el trabajo**: Copilot → `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`; Claude Code → `Co-authored-by: Claude <noreply@anthropic.com>`.
- Tests deterministas, sin red ni coste (adaptadores en memoria) y sin depender de variables del shell de quien los ejecuta. Lo que tiene coste exige `--budget-usd` o dry-run por defecto (`--apply`).
- Docs de fase en `docs/fase-N-*.md` con la misma estructura. Las preguntas de entrevista viven solo en `docs/preguntas-entrevista.md` (una sección por fase; el doc enlaza a ella).
- `✅ Hecha*` = implementación cerrada, falta validación en cloud (desde el checkpoint 8→9, en las fases 2–8 falta AWS; y GCP en todas). No escribir "asterisco".
- Pasada GCP: subfases `N.G1`, `N.G2`... por fase (`feat: fase 3.G1 - ...`), con paridad total con Azure y AWS; cada una actualiza el doc de su fase y la columna GCP de la tabla de estado.
- Informes de evals que se regeneran al pasar los gates (`evals/reports/*`): si solo cambian latencias (p50/p95), se restauran (con confirmación); si cambian campos o resultados, entran en el commit.

## Entorno y problemas conocidos

- Windows, Docker Desktop (WSL2; si está parado, arrancarlo: no tiene coste), `gh` autenticado como `SergioVelmay`. Repo **público** `SergioVelmay/GenAI_PoCs` (desde el 09-11-2026): todo lo que se sube es visible, también el historial.
- **Shell según el agente.** Copilot: **PowerShell 5.1** (sin `&&`, `||`, heredocs; `$env:VAR = "..."`). Claude Code: **Git Bash** (`&&`, `export VAR=...`, `$USERPROFILE`). En los dos, editar ficheros con la tool de edición, nunca reescribiéndolos desde el shell (tildes: ver lecciones).
- El terminal integrado a veces mete un **carácter espurio** al inicio de un comando o se queda bloqueado y ejecuta después lo acumulado: no encadenar acciones destructivas detrás de pasos que puedan fallar, verificar el resultado (`git log -1`, `git status`) y no relanzar sin comprobar si ya se ejecutó.
- Git: la identidad del repo es **local** (`git config --local user.email`, la cuenta personal de los commits de `main`); la global es corporativa y no se toca. Hook `pre-commit` activo con `git config core.hooksPath .githooks`; necesita `.secrets/forbidden_terms.txt` (no versionado; sin él avisa y no bloquea).
- Desde el código, `127.0.0.1` y no `localhost` (IPv6 añade ~2 s por conexión). Excepción: URLs que abre el navegador (`NEXTAUTH_URL` y `LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT` de Langfuse se quedan en `localhost`).
- **Azure CLI aislada**: el perfil por defecto de `az` tiene sesiones de otra organización. En este repo, cada comando de Azure, Terraform o scripts con Entra ID se ejecuta con `AZURE_CONFIG_DIR` = `%USERPROFILE%\.azure-headache` (Claude Code lo recibe de `.claude/settings.local.json`), y antes se comprueba `az account show`. Nunca `az login`/`az account set` sin esa variable.
- **gcloud aislado** (desde la 0.G): `CLOUDSDK_CONFIG` = `%USERPROFILE%\.gcloud-headache` antes de cualquier comando de GCP, Terraform o scripts, y comprobar `gcloud config list`. Sin claves JSON de cuentas de servicio (ADC de usuario e *impersonation*).
- Verificar con **los mismos comandos que la CI** (p. ej. `terraform fmt -check -recursive`).
- Si aparecen ficheros con cambios que no son de esta sesión: no guardarlos ni descartarlos sin analizarlos con Sergio.
- Más casos y su porqué: `docs/lecciones-aprendidas.md`.

## Estado de las fases

| Fase | Tema | Estado | GCP | Doc |
|---|---|---|---|---|
| 0 | Cimientos: monorepo, Clean Architecture, docker compose, datos sintéticos, CI | ✅ Hecha | ✅* | `docs/fase-0-cimientos.md` |
| 1 | SQL avanzado + `ticketing_api` | ✅ Hecha | ✅* | `docs/fase-1-sql.md` |
| 2 | Capa de modelos, LLM gateway, routing, prompt caching, coste | ✅ Hecha* | ✅* | `docs/fase-2-modelos.md` |
| 3 | Indexación event-driven, 3 carriles × 2 nubes | ✅ Hecha* | 3.G | `docs/fase-3-comparativa.md` |
| 4 | Retrieval avanzado y servicio RAG | ✅ Hecha* | 4.G | `docs/fase-4-retrieval.md` |
| 5 | Evals de RAG y gate en CI | ✅ Hecha* | 5.G | `docs/fase-5-evals.md` |
| 6 | Tool calling manual, servidores MCP, OAuth y MCP gateway | ✅ Hecha* | 6.G | `docs/fase-6-tools-mcp.md` |
| 7 | Un agente en 7 sabores y context engineering | ✅ Hecha* | 7.G | `docs/fase-7-agentes.md` |
| 8 | Agentic harness y guardrails | ✅ Hecha* | 8.G | `docs/fase-8-harness.md` |
| 8→9 | Checkpoint: CI, docs 0–2, README, AGENTS.md, conexión con Azure y pendientes de Azure de las fases 2–8 | ✅ Hecha | — | `docs/fase-9-8-checkpoint.md` |
| 9 | Multi-agente (Triaje → Reembolsos / Creativo / Conocimiento), A2A, asíncrono | ✅ Hecha* | 9.G | `docs/fase-9-multiagente.md` |
| 10 | Escala: backpressure, rate limiting, caché semántica, autoescalado por colas, carga | ✅ Hecha* | 10.G | `docs/fase-10-escala.md` |
| 11 | Observabilidad: OpenTelemetry GenAI, Langfuse, CloudWatch/X-Ray vs App Insights | ✅ Hecha* | 11.G | `docs/fase-11-observabilidad.md` |
| 12 | Evals de agentes, red teaming, LLMOps (prompts versionados, A/B, drift) | ✅ Hecha* | 12.G | `docs/fase-12-evals-llmops.md` |
| 12→13 | Checkpoint: glosario, arquitectura por niveles y plan de GCP | ✅ Hecha | — | `docs/fase-13-12-checkpoint.md` |
| G | **Pasada GCP** por las fases 0–12 (hechas: 0.G y 2.G; siguiente: **3.G**, rama `fase/3-g`) | | | `docs/fase-13-12-checkpoint.md#plan-gcp` |
| 13 | Despliegue en AWS con Terraform (ECS/EKS, Lambda, Bedrock, SQS, Aurora, OIDC) | | | `docs/plan-fases.md` |
| 14 | Despliegue espejo en Azure (ACA/AKS, Functions, Foundry, Service Bus, AI Search) | | | `docs/plan-fases.md` |
| 15 | Despliegue espejo en GCP (Cloud Run/GKE, Agent Runtime, Pub/Sub, Cloud SQL/AlloyDB, Vector Search) | | | `docs/plan-fases.md` |
| 16+ | Extras: MLOps, recomendador, multi-región, FinOps, vLLM, text-to-SQL analítico | | | `docs/plan-fases.md` |

En la columna GCP, el código de la subfase de la pasada; pasa a `✅` (o `✅*`) al cerrarla.

Pendientes con coste de cada fase: `docs/pendientes-cloud.md` (recordárselos a Sergio cuando toque).
Requisitos de las ofertas a cubrir: multi-agente con grafos, tool calling avanzado, harness y guardrails, RAG híbrido, context engineering + prompt caching + model routing, evals con métricas, Clean Architecture/DDD, asíncrono y colas, SQL avanzado, CI/CD + observabilidad y equivalencias Azure ↔ AWS ↔ GCP.
