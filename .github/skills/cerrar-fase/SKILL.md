---
name: cerrar-fase
description: Cierra una fase completa del plan de GenAI_PoCs (documentación, estado, pendientes y checkpoint de entrevista). Úsala cuando Sergio pida "cerrar la fase", "damos por cerrado el bloque N" o "resúmeme el estado con el cierre de la fase".
---

# Cerrar una fase

1. **Comprobar lo hecho** frente al plan de la fase y sus entregables: listar lo cumplido, lo aplazado (coste o nube) y lo descartado con su motivo.
2. **Doc de la fase**: crear o completar `docs/fase-N-<tema>.md` con la estructura de `.github/instructions/docs.instructions.md` (resultados medidos, comparativa A/B/C, decisiones, checkpoint de entrevista con preguntas técnicas y de negocio/stakeholders).
3. **Pendientes**: mover lo aplazado a `docs/pendientes-cloud.md`, en su sección.
4. **Estado**: actualizar la tabla de fases en `AGENTS.md` (`✅ Hecha` o `✅ Hecha*` si queda validación en cloud) y el índice de `README.md`.
5. **Instrucciones**: si la fase creó un área nueva, añadir su `.github/instructions/<area>.instructions.md` con `applyTo`; si cambió algo que aplica siempre, actualizar `AGENTS.md` (y que siga por debajo de ~150 líneas).
6. **Lecciones**: añadir a `docs/lecciones-aprendidas.md` los errores o correcciones de la fase que no deban repetirse.
7. **Resumen para Sergio**: estado de la fase, métricas clave, pendientes y qué viene en la fase siguiente (sin implementarla).
8. Commit `docs: cierre de la fase N ...` con la skill `cerrar-subfase`, tras su visto bueno.
