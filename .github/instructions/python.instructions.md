---
applyTo: "**/*.py,**/pyproject.toml"
description: "Convenciones de Python del monorepo: Clean Architecture, tipado estricto, tests y configuración."
---

# Python en GenAI_PoCs

## Capas (Ports & Adapters)

- `domain/`: Python puro (dataclasses `frozen`/`slots`, `StrEnum`). Reglas de negocio deterministas. El LLM nunca decide negocio: decide el dominio. Sin imports de frameworks, nube ni base de datos.
- `application/`: casos de uso; dependen solo de puertos y del dominio.
- `ports/`: interfaces con `typing.Protocol` (tipado estructural, sin herencia).
- `adapters/`: única capa que importa SDKs (`memory` para tests; `postgres`, `azure_*`, `bedrock*`...).
- La composición (qué adaptador se usa) vive en factorías (`adapters/llm_factory.py`, `retrieval/cloud.py`, `booking_agent/guardrails.py`) o en el arranque del servicio, nunca dentro de un caso de uso.
- `harness` depende de `headache_core`, nunca al revés.

## Paquetes

- Compartido: `packages/headache_core` (extras opcionales por integración). Servicios en `services/<nombre>` con su `pyproject.toml`, añadidos a `[tool.uv.workspace]`, a `[tool.uv.sources]`, a `known-first-party` de ruff y a `files` de mypy en el `pyproject.toml` raíz.
- Dependencias: `uv add` (nunca editar `uv.lock` a mano). Extras pesados y opcionales (`evals`, `pii`) fuera de la CI, con sus tests saltados por `skipif` explícito (no `importorskip`).
- Fijar versiones con conflicto y explicar el motivo en un comentario (p. ej. `mcp<2.2` por `strands-agents`, `langchain-community<0.4.2` por `ragas`).

## Estilo y tipos

- `from __future__ import annotations`; `mypy --strict` con plugin de Pydantic; sin `Any` salvo en los bordes con librerías sin tipos (override en `pyproject.toml`).
- Línea de 100; reglas ruff `E, F, I, N, UP, B, SIM, ASYNC, RUF`.
- Comentarios solo donde aportan el porqué (en español). Docstring de módulo con el propósito y, en scripts, el uso.
- Async de punta a punta; SDKs síncronos (`boto3`) con `asyncio.to_thread`.
- Errores tipados por capa (`LLMError`, `NotFoundError`, `ConflictError`...) y traducidos en el borde (HTTP, resultado de tool).

## Configuración

- `headache_core/config.py`: Pydantic Settings, variables anidadas con `__` (`BEDROCK__REGION`), secretos como `SecretStr`. Al añadir una variable: valor por defecto seguro, entrada en `.env.example` y test en `test_config.py`.
- Por defecto todo es local y sin coste; la nube se activa solo de forma explícita por configuración.
- Hosts locales `127.0.0.1`, nunca `localhost`. Excepción: URLs que abre el navegador (`NEXTAUTH_URL`, `LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT` de Langfuse), que deben quedarse en `localhost` o el login falla.
- Si cambian variables, dar a Sergio las instrucciones exactas para su `.env` (clave, valor, bloque, añadir o modificar, para qué); su `.env` sigue el orden, los grupos y los comentarios de `.env.example`. Los valores de la nube salen de `terraform -chdir=infra/terraform/<nube> output` (con la CLI aislada). `AZURE_OPENAI__API_KEY` vacía = Entra ID.
- FastAPI con `from __future__ import annotations`: las dependencias (`Annotated[..., Depends(...)]`) a nivel de módulo; dentro de una *closure* no se resuelven y devuelven 422.

## Tests

- Adaptadores en memoria y fakes: deterministas, sin red ni coste. `pytest-asyncio` en modo `auto`.
- Herméticos: settings explícitos en cada test, nunca leídos de las variables del shell (`BEDROCK__*`, `AZURE_*` pueden estar definidas).
- Nombres de fichero de test únicos en todo el monorepo (`test_retrieval_api.py`, no otro `test_api.py`): mypy los trata como módulos globales.
- Antes de escribir un adaptador, leer la API instalada de la librería (firmas, código fuente): suele ir por delante de lo que se recuerda.
- `uv sync --all-packages` desinstala los extras opcionales (`evals`, `pii`, `redteam`, `llmops`); con uno instalado, `uv run --no-sync`.
- Integración: además del núcleo, los tests de Redis necesitan el perfil `scale` (`:6380`) y los contratos de colas los perfiles `sqs`, `servicebus` y `pubsub`; sin ellos se saltan. El emulador de Service Bus exige aceptar su EULA (`SERVICEBUS_ACCEPT_EULA=Y`; decisión de Sergio, ya aceptada en su `.env`).
- Integración con `@pytest.mark.integration` (excluida por defecto): `uv run pytest -m integration`, requiere `docker compose up -d` y migraciones.
- Un bug corregido lleva su test de regresión. Los tests de seguridad incluyen controles benignos (falsos positivos).
- Verificación mínima antes de dar algo por terminado: `uv run ruff check .` · `uv run ruff format --check .` · `uv run mypy` · `uv run pytest` (o el subconjunto afectado y después la suite).
