---
applyTo: "**/prompts/*.md,**/prompts/__init__.py,prompts.lock.json,packages/headache_core/src/headache_core/application/prompts.py,packages/headache_core/src/headache_core/ports/prompts.py,packages/headache_core/src/headache_core/adapters/langfuse_prompts.py,evals/src/rag_evals/llmops/**"
description: "Prompts versionados (fase 12.6): ficheros por paquete, candado y publicación en Langfuse."
---

# Prompts versionados (fase 12.6)

- Git es la fuente canónica. Cada paquete guarda sus prompts en `<paquete>/prompts/<nombre>.md` (cabecera `name`, `version`, `description`, `format: plain|python`, opcionales `fragment: true` y `final_newline: false`) y los expone con `PROMPTS = PromptCatalog.from_directory(...)` en `prompts/__init__.py`. Los módulos usan `PROMPTS.get("nombre").text` (o `.render(...)` con `format: python`).
- Nunca un prompt nuevo como literal en el código: un fichero nuevo con `version: 1`. Fragmentos compartidos con `{{> nombre}}` (se resuelven al cargar; se publican resueltos).
- Cambiar el texto de un prompt exige subir su `version` y ejecutar `uv run python -m rag_evals.llmops.prompts lock`. La CI (`... prompts check`) falla si el texto cambia sin versión, si el candado no está al día o si una carpeta `prompts/` no está en `CATALOGS`. Cambiar un fragmento cambia a quien lo incluye: sube también su versión.
- Migrar un prompt sin cambiar ni un byte (caché de prefijo y comportamiento): comparar el texto antes y después.
- En ejecución el prompt se lee del paquete, nunca de Langfuse. Cada llamada al modelo lleva `langfuse.observation.prompt.name/version` y `headache.prompt.name/version` (`current_prompt`: bloque `prompt_scope` o reconocimiento por el texto fijo inicial del mensaje de sistema, ≥ 40 caracteres).
- Publicar: `uv run python -m rag_evals.llmops.prompts publish` (dry-run) y `--apply` (Langfuse local o de la nube según `LANGFUSE__*`; pedir confirmación). La versión de Git es la de Langfuse: se crea solo si Langfuse va por la anterior; nunca se reescribe una versión. Etiquetas `production` y `git-vN`; `config` con el commit y el sha256.
