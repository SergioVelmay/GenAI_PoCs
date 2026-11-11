---
applyTo: "docs/**,README.md,AGENTS.md"
description: "Estructura y estilo de la documentación de fases."
---

# Documentación

## Doc de fase (`docs/fase-N-<tema>.md`)

Misma estructura en todas las fases:

1. Título `# Fase N · Tema`, **Objetivo** en 1–2 frases y commits o subfases.
2. Arquitectura con un diagrama Mermaid y una tabla de piezas (dónde vive cada cosa). Sergio prefiere diagramas **simplificados**: actores y relaciones clave, sin listar servicios ni modelos (el detalle va en la tabla). Etiquetas de flecha siempre entre comillas (`-->|"texto (x)"|`): los paréntesis sin comillas rompen el parser. Validar con `npx.cmd -y @mermaid-js/mermaid-cli` (PowerShell) o `npx -y ...` (Git Bash).
3. Qué se construye (por subfase), con comandos ejecutables.
4. Resultados medidos (tablas con números reales; si son de local o con embeddings `hashing`, decirlo).
5. Comparativa **Azure ↔ AWS ↔ GCP ↔ framework** (carriles A/B/C). En la pasada GCP, además, una sección "GCP (pasada N.G)" con lo construido, sus comandos y sus hallazgos.
6. Decisiones y límites (para defenderlas).
7. Pendiente con coste (enlazando a `docs/pendientes-cloud.md`).
8. **Checkpoint de entrevista**: solo un enlace a la sección de la fase en `docs/preguntas-entrevista.md` (`preguntas-entrevista.md#fase-N`). Las preguntas viven **únicamente** en ese fichero.

## Otros docs

- Checkpoints entre fases: `docs/fase-<N+1>-<N>-checkpoint.md` (p. ej. `fase-13-12-checkpoint.md`), para que en orden alfabético queden entre las dos fases.
- `docs/plan-fases.md`: plan de todas las fases (fuente de verdad). Un cambio de alcance se apunta ahí en el mismo commit.
- Al renombrar un título enlazado desde otro doc, conservar el ancla con `<a id="..."></a>` y comprobar los enlaces internos con ancla (slugs de GitHub).

## Recopilatorio de preguntas (`docs/preguntas-entrevista.md`)

- Fichero único para preparar la entrevista de AI Technical Lead (50 % técnico, 50 % negocio y stakeholders). Una sección por fase con ancla `<a id="fase-N"></a>`, título `## Fase N · Tema ([doc](fase-N-...md))` y dos subsecciones: `### Técnicas` (4–5) y `### Negocio y stakeholders` (2–3).
- Respuestas modelo de 2–4 frases, ancladas en lo construido (rutas, números medidos, hallazgos), sin depender de leer el doc de la fase.
- Negocio: valor para Headache y KPI, coste y ROI, riesgos y mitigación, cómo contarlo a operaciones, Legal o dirección, priorización.
- Nube: responder primero en AWS y dar el equivalente de Azure (y el de GCP si aporta). Si aparece un servicio nuevo, añadirlo a la chuleta Azure → AWS → GCP.
- Pasada GCP: lo de GCP de cada fase va en una subsección `### GCP (pasada N.G)` dentro de la sección de su fase, con preguntas técnicas y de negocio.
- Preguntas generales de liderazgo que surjan van a la sección "Transversales".

## Estilo

- Español, didáctico y concreto: rutas, comandos y números, no generalidades. Explicar el porqué de cada decisión.
- Nunca el nombre real del cliente ni marcas comerciales de terceros (tampoco en ejemplos): "Headache", "un partner de streaming", la categoría `candlelit`.
- `✅ Hecha*` = implementación cerrada; falta la validación en cloud. No escribir la palabra "asterisco".
- Enlaces relativos entre docs (`[fase 2](fase-2-modelos.md)`).
- Al cerrar una fase: crear o actualizar su doc, la tabla de estado de `AGENTS.md` y el índice de `README.md`, y mover lo aplazado a `docs/pendientes-cloud.md`.
- Un error que se repite o una corrección que debe sobrevivir a la sesión va a `docs/lecciones-aprendidas.md`.
