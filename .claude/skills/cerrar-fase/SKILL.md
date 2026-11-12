---
name: cerrar-fase
description: Cierra una fase completa del plan de GenAI_PoCs (documentación, estado, pendientes, checkpoint de entrevista y PR lista para revisión). Úsala cuando Sergio pida "cerrar la fase", "damos por cerrado el bloque N" o "resúmeme el estado con el cierre de la fase".
---

# Cerrar una fase

1. **Comprobar lo hecho** frente al plan de la fase (`docs/plan-fases.md` o el plan de GCP) y sus entregables: listar lo cumplido, lo aplazado (coste o nube) y lo descartado con su motivo.
2. **Doc de la fase**: crear o completar `docs/fase-N-<tema>.md` con la estructura de `.github/instructions/docs.instructions.md` (resultados medidos, comparativa A/B/C, decisiones y enlace al checkpoint). En la pasada GCP, la sección "GCP (pasada N.G)" y la columna GCP de las comparativas.
3. **Preguntas de entrevista**: añadir la sección `fase-N` (o la subsección "GCP (pasada N.G)") a `docs/preguntas-entrevista.md` (técnicas y de negocio/stakeholders, con respuesta modelo) y actualizar la chuleta Azure → AWS → GCP si hay servicios nuevos.
4. **Pendientes**: mover lo aplazado a `docs/pendientes-cloud.md`, en su sección. Lo que se descarta o queda para otra fase, a `docs/plan-fases.md` (fase destino o "Ideas aparcadas").
5. **Estado**: actualizar la tabla de fases en `AGENTS.md` (`✅ Hecha` o `✅ Hecha*` si queda validación en cloud; en la pasada, la columna GCP y "siguiente: N.G") y el índice de `README.md`.
6. **Instrucciones**: si la fase creó un área nueva, añadir su `.github/instructions/<area>.instructions.md` con `applyTo` y regenerar `.claude/rules/` (`uv run python scripts/sync_agent_rules.py`); si cambió algo que aplica siempre, actualizar `AGENTS.md` (y que siga por debajo de ~150 líneas).
7. **Lecciones**: añadir a `docs/lecciones-aprendidas.md` los errores o correcciones de la fase que no deban repetirse. Nada que deba sobrevivir a la sesión se queda solo en el chat o en la memoria del agente.
8. **Resumen para Sergio**: estado de la fase, métricas clave, pendientes y qué viene en la fase siguiente (sin implementarla).
9. Commit `docs: cierre de la fase N ...` con la skill `cerrar-subfase`, tras su visto bueno.
10. **PR lista para revisión**: con la CI en verde, completar el cuerpo de la PR (todas las subfases marcadas, resultados, pendientes y el trailer del agente) y `gh pr ready`. No fusionarla: Sergio la revisa y hace el merge a mano (solo *merge commit*).
11. **Mensaje para la próxima sesión**: dar a Sergio el texto exacto para abrir la siguiente, p. ej. `Vamos a por la 3.G de la pasada GCP. Usa la skill iniciar-fase.`, y recordarle que antes fusione la PR. Tras el merge, la sesión siguiente vuelve a `main` y la actualiza (`git switch main ; git pull`); la rama local fusionada se borra con `git branch -d` solo con su permiso.
