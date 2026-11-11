---
name: iniciar-fase
description: Arranca una fase o subfase nueva del plan de GenAI_PoCs en una sesión limpia. Úsala cuando Sergio diga "ya realizamos la fase N en otra sesión", "vamos a por la fase N", "procede con la implementación de la fase N" o pegue el plan original de una fase.
---

# Iniciar una fase

Objetivo: entender antes de tocar código, y que Sergio apruebe el plan de subfases.

## 1. Cargar contexto (solo lectura)

1. `AGENTS.md`: tabla de estado y reglas.
2. El doc de la fase anterior (`docs/fase-<N-1>-*.md`) y `docs/pendientes-cloud.md`. En la **pasada GCP** (`N.G`): el plan (`docs/fase-13-12-checkpoint.md#plan-gcp`), el doc de la propia fase N y los adaptadores de Azure y AWS que hay que igualar; y comprobar en la documentación actual de Google que los servicios de la subfase no han cambiado de nombre, API o estado.
3. `git branch --show-current`, `git --no-pager log --oneline -15` y `git status --short`. Si hay cambios sin commit que no son de esta sesión, **parar y analizarlos** con Sergio antes de nada (ver `docs/lecciones-aprendidas.md`). Si queda una PR de la fase anterior sin fusionar (`gh pr list --author @me`), recordárselo: la fase nueva sale de `main` actualizada.
4. Estado de la CI de `main`: `gh run list --branch main --limit 1`.
5. Explorar el código que la fase va a tocar o reutilizar (puertos, contratos, sabores, harness).

## 2. Proponer (sin implementar)

Responder con:

1. **Concepto** de la fase, explicado de forma didáctica, y **el problema de negocio de Headache** que resuelve (a quién ayuda, qué KPI movería, qué cuesta y qué riesgo reduce).
2. **Equivalencias** con lo que Sergio ya conoce y tabla **Azure ↔ AWS ↔ framework** (carriles A/B/C).
3. **Subfases** `N.1`, `N.2`... pequeñas y verificables, cada una con entregables, comandos de verificación y si tiene coste.
4. Qué se hace **en local y sin coste** ahora, y qué irá a `docs/pendientes-cloud.md`.
5. Decisiones que necesitan a Sergio, con la opción recomendada primero.

Esperar su confirmación. Si el plan original choca con la realidad (servicio retirado, versión incompatible, coste), decirlo y proponer la alternativa.

## 3. Ejecutar

Antes del primer cambio, crear la rama de la fase desde `main` actualizada (`git switch main ; git pull ; git switch -c fase/<N>` o `fase/<N>-g`): `main` está protegida y solo recibe PRs. Implementar subfase a subfase: al terminar cada una, verificar y **parar para revisión** (skill `cerrar-subfase`). No empezar la siguiente sin su visto bueno.
