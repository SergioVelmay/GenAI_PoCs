# Instrucciones para Copilot: GenAI_PoCs

## Contexto del proyecto

- PoCs didácticas para dominar el stack completo de **sistemas agénticos GenAI**: RAG, tool calling, MCP, multi-agente, harness/guardrails, evals, observabilidad, seguridad, DevOps/LLMOps/MLOps, en **Azure y AWS**.
- Objetivo del usuario (Sergio): poder explicar y defender cada decisión ante un hiring manager para un puesto de **AI Technical Lead / Senior AI Engineer / Forward Deployed AI Engineer**.
- Perfil: más de 7 años en Azure (AI Foundry, AI Search, Container Apps, AKS, Functions, Bicep, Azure DevOps), Microsoft Agent Framework y FastMCP. Está aprendiendo LlamaIndex, LangChain/LangGraph, Strands, AWS y Terraform.
- Cliente hipotético: **Headache**, marketplace global de experiencias y eventos con unos 300 M de usuarios y producción en **AWS**. Hay usuarios internos (operaciones, soporte, partners) y una parte pública de alta escala.

## Reglas obligatorias

- El cliente se llama **siempre "Headache"**: en código, comentarios, datos, commits y chat. **Nunca** escribas el nombre de la empresa real en la que se inspira ni sus marcas comerciales. Usa nombres genéricos (p. ej., la categoría `candlelit`, no la marca real) y evita también marcas de terceros en los datos sintéticos.
- **Pide confirmación** antes de: commits, push, crear recursos cloud, `terraform apply`, cualquier acción con coste y cualquier acción destructiva (borrar ficheros o volúmenes, `down -v`...).
- Nunca subas secretos. Usa `.env` en local (ignorado por Git), OIDC en CI y Managed Identity / IAM Roles en la nube.

## Forma de trabajar

- Responde en **español** y con enfoque **didáctico**. Para cada fase:
  1. explica el concepto;
  2. da la equivalencia con lo que Sergio ya conoce y la comparativa **Azure ↔ AWS ↔ framework**;
  3. implementa de forma incremental y verificable;
  4. para para que lo revise;
  5. cierra con un **checkpoint de entrevista** (preguntas típicas y respuestas modelo).
- **Tres carriles paralelos** cuando tenga sentido: **A. Gestionado** (Foundry Agent Service, Bedrock Knowledge Bases, AgentCore...), **B. SDK nativo** (`azure-*`, `boto3`) y **C. Framework** (LlamaIndex, LangGraph, MAF, Strands). Siempre se comparan con el mismo dataset y las mismas evals.
- **Solo modelos cloud** (Azure AI Foundry y Amazon Bedrock); no se usan modelos locales. Docker en local solo ejecuta infraestructura.
- **Estrategia de coste** (cuentas gratuitas: 200 $ en Azure y 100 $ en AWS):
  - Modelos baratos por defecto.
  - AI Search en el tier Free.
  - Evitar OpenSearch Serverless; usar S3 Vectors o pgvector.
  - EKS/AKS solo de forma efímera (`apply` → probar → `destroy`).
  - Alertas de presupuesto activas.

## Arquitectura y convenciones

- Monorepo **uv** (Python 3.12). Paquete compartido `packages/headache_core`; los futuros servicios irán en `services/<nombre>`, cada uno con su `pyproject.toml`, añadidos a `[tool.uv.workspace]`.
- **Clean Architecture / Ports & Adapters**:
  - `domain/` es Python puro, con reglas de negocio deterministas. El LLM nunca toma decisiones de negocio: las toma el dominio.
  - `application/` contiene los casos de uso, que dependen solo de puertos.
  - `ports/` define interfaces con `typing.Protocol`.
  - `adapters/` contiene las implementaciones: `memory` para tests y después `azure`, `aws`, `postgres`...
- Configuración con Pydantic Settings y variables anidadas con `__` (p. ej. `BEDROCK__REGION`). Los secretos van como `SecretStr`.
- Calidad (debe pasar antes de cada commit; lo mismo ejecuta la CI):
  `uv run ruff check .` · `uv run ruff format --check .` · `uv run mypy` (strict) · `uv run pytest`.
- Tests con adaptadores en memoria: deterministas y sin red ni coste. `pytest-asyncio` en modo `auto`.
- Tests de integración (marcador `integration`, excluidos por defecto): `uv run pytest -m integration`. Requieren PostgreSQL migrado.
- Migraciones: `uv run alembic -c packages/headache_core/alembic.ini upgrade head` (0001 esquema, 0002 índices `CONCURRENTLY`). Carga masiva: `scripts/load_seed.py --truncate`; benchmark de índices: `scripts/sql_benchmark.py`.
- En Windows usa `127.0.0.1` y no `localhost` para PostgreSQL (el intento IPv6 añade ~2 s por conexión).
- Datos sintéticos: `uv run python scripts/generate_synthetic_data.py --scale small|medium|large`. Son deterministas por semilla y se generan en `data/synthetic/`, que no se versiona.
- LLM: todo pasa por `LLMPort`. El gateway se monta en `adapters/llm_factory.build_gateway` (routing → fallback → trazas/coste → rate limit → adaptador). `LLM_GATEWAY__ENGINE=native|litellm`. Scripts con coste: `scripts/llm_cache_benchmark.py` y `scripts/llm_gateway_demo.py`.
- Entorno local: `docker compose up -d` (PostgreSQL + pgvector, RabbitMQ). Añade `--profile observability` para Langfuse v3. Todos los puertos están ligados a `127.0.0.1`.
- IaC con **Terraform** como hilo principal (más ejemplos puntuales de CDK/CloudFormation y Bicep). CI/CD con **GitHub Actions**.
- Commits con Conventional Commits en español, p. ej. `feat: fase 1 - esquema SQL de ticketing`.

## Plan de fases (estado)

| Fase | Tema | Estado |
|---|---|---|
| 0 | Cimientos: monorepo, Clean Architecture, docker compose, datos sintéticos, CI | ✅ Hecha |
| 1 | SQL avanzado (PostgreSQL, Alembic, SQLAlchemy async, índices, `EXPLAIN`) + `ticketing_api` (FastAPI) | ✅ Hecha |
| 2 | Capa de modelos: Azure OpenAI vs Bedrock Converse, LLM Gateway (LiteLLM), model routing, prompt caching, coste por tokens | 🔄 Código listo; falta validar en cloud |
| 3 | Indexación event-driven (RabbitMQ/SQS/Service Bus) en 3 carriles × 2 nubes (AI Search, Bedrock KB, LlamaIndex) | |
| 4 | Retrieval avanzado: híbrido, filtros, reranking, query rewriting, servicio RAG | |
| 5 | Evals de RAG: golden dataset, RAGAS/DeepEval, hit rate/MRR, gate en CI | |
| 6 | Tool calling avanzado (bucle manual) + servidores MCP con FastMCP + MCP gateway | |
| 7 | Un agente en 5 sabores: LangGraph, MAF, Strands, LlamaIndex, Foundry Agent Service / AgentCore; context engineering | |
| 8 | Agentic harness y guardrails: validación, autocorrección, SQL seguro, PII, prompt injection, human-in-the-loop | |
| 9 | Orquestación multi-agente (Triaje → Reembolsos / Creativo / Conocimiento), A2A, asíncrono | |
| 10 | Escala: backpressure, rate limiting, caché semántica, autoescalado por colas, pruebas de carga | |
| 11 | Observabilidad: OpenTelemetry GenAI, Langfuse, CloudWatch/X-Ray vs App Insights, alertas | |
| 12 | Evals de agentes, red teaming (PyRIT/promptfoo), LLMOps (prompts versionados, A/B) | |
| 13 | Despliegue en AWS con Terraform: ECS Fargate/EKS, Lambda, Bedrock, SQS, Aurora, OIDC | |
| 14 | Despliegue espejo en Azure: ACA/AKS, Functions, Foundry, Service Bus, AI Search | |
| 15+ | Extras: MLOps (fine-tuning de embeddings), recomendador, multi-región, FinOps, vLLM | |

Requisitos de las ofertas que deben quedar cubiertos: orquestación multi-agente con grafos, tool calling avanzado, harness y guardrails, RAG con búsqueda híbrida, context engineering + prompt caching + model routing, evals con métricas, Clean Architecture/DDD, patrones asíncronos y colas, SQL avanzado, CI/CD + observabilidad y equivalencias Azure ↔ AWS.

Actualiza la tabla de estado al cerrar cada fase.

## Pendientes "para más adelante" (Sergio los aplazó; recuérdaselos)

Desde la **fase 2** (no se puede cerrar la fase hasta completarlos):

- [ ] Instalar y autenticar **Azure CLI** (`az login`) y **AWS CLI** (`aws configure sso`, perfil en `BEDROCK__PROFILE`).
- [ ] **Azure AI Foundry**: desplegar `gpt-4.1-mini` (potente) y `gpt-4.1-nano` (barato); rellenar `AZURE_OPENAI__ENDPOINT` y dar el rol *Cognitive Services OpenAI User* a su usuario (Entra ID, sin clave).
- [ ] **Bedrock** (`eu-west-1`): habilitar el acceso a Claude Haiku 4.5 (formulario de Anthropic) y a Nova Lite.
- [ ] Ejecutar con coste (pedir confirmación): `scripts/llm_cache_benchmark.py --provider azure|aws` y `scripts/llm_gateway_demo.py` (motores `native` y `litellm`), y revisar las trazas en Langfuse.
- [ ] Langfuse local sigue en la imagen `langfuse:3` (OSS v3, parches de seguridad hasta 31-01-2027). El SDK Python v4 es compatible (requiere servidor ≥ 3.63.0). Valorar migrar a la imagen v4 en la fase 11.

## Entorno del usuario

- Windows + PowerShell 5.1, Docker Desktop (WSL2), `gh` autenticado como `SergioVelmay`. Repo privado: `SergioVelmay/GenAI_PoCs`.
- El terminal integrado a veces introduce un carácter espurio al inicio del comando. No encadenes acciones destructivas detrás de pasos que puedan fallar, y verifica el resultado antes de continuar.
