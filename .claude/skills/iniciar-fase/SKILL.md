---
name: iniciar-fase
description: Arranca una fase nueva del plan de GenAI_PoCs en una sesión limpia y propone sus subfases sin implementar. Úsala cuando Sergio diga "vamos a por la fase N", "vamos a por la 3.G", "procede con la implementación de la fase N" o pegue el plan de una fase. Si la fase ya tiene rama o PR, usa retomar-fase.
---

# Iniciar una fase

Objetivo: entender antes de tocar código, y que Sergio apruebe el plan de subfases. Todo el contexto
está en el repo: no hace falta que Sergio pegue el plan (si lo pega, manda lo que pegue y se
actualiza `docs/plan-fases.md`).

## 1. Cargar contexto (solo lectura)

1. `AGENTS.md` (ya cargado): tabla de estado, reglas y vocabulario.
2. El plan de la fase: `docs/plan-fases.md`. En la **pasada GCP** (`N.G`): `docs/fase-13-12-checkpoint.md#plan-gcp` (§3.3 equivalencias, §3.4 subfases, §3.5 convenciones, §3.6 riesgos).
3. El doc de la propia fase (`docs/fase-N-*.md`), el de la fase anterior y su sección de `docs/pendientes-cloud.md`. En la pasada GCP, la sección "GCP (pasada 0.G)" de `docs/fase-0-cimientos.md` (qué hay ya: `GcpSettings`, `unsupported_provider`, Terraform, emulador).
4. `git branch --show-current`, `git --no-pager log --oneline -15` y `git status --short`. Si hay cambios sin commit que no son de esta sesión, **parar y analizarlos** con Sergio antes de nada. Si queda una PR abierta (`gh pr list --author @me`), recordárselo: la fase nueva sale de `main` actualizada. Si ya existe la rama de la fase, cambiar a la skill `retomar-fase`.
5. Estado de la CI de `main`: `gh run list --branch main --limit 1`.
6. Explorar el código que la fase va a tocar o reutilizar (puertos, contratos, sabores, harness) y, en la pasada GCP, los adaptadores de Azure y AWS que hay que igualar.
7. **Verificación previa** con fecha: versiones actuales de las librerías y del provider de Terraform, y en la documentación oficial que los servicios no han cambiado de nombre, API, región o estado (preview, retirada). Leer la API **instalada** (`uv run python -c "import inspect, x; ..."`), no la que se recuerda.

## 2. Proponer (sin implementar)

Responder con:

1. **Concepto** de la fase, explicado de forma didáctica, y **el problema de negocio de Headache** que resuelve (a quién ayuda, qué KPI movería, qué cuesta y qué riesgo reduce).
2. **Equivalencias** con lo que Sergio ya conoce y tabla **Azure ↔ AWS ↔ GCP ↔ framework** (carriles A/B/C).
3. **Verificación previa**: tabla "comprobado → resultado → consecuencia en el plan".
4. **Subfases** `N.1`, `N.2`... (o `N.G1`...) pequeñas y verificables, cada una con entregables, comandos de verificación, coste y riesgo real.
5. Qué se hace **en local y sin coste** ahora, y qué irá a `docs/pendientes-cloud.md`.
6. Decisiones que necesitan a Sergio: solo las que bloquean, con la opción recomendada primero, y diciendo si alguna **cambia el plan** de `docs/plan-fases.md` o del plan de GCP.

Esperar su confirmación. Si el plan choca con la realidad (servicio retirado, versión incompatible, coste), decirlo y proponer la alternativa.

## 3. Ejecutar

1. Antes del primer cambio, crear la rama desde `main` actualizada: `git switch main ; git pull ; git switch -c fase/<N>` (o `fase/<N>-g`). `main` está protegida y solo recibe PRs.
2. Si el plan aprobado cambia respecto al escrito, actualizar `docs/plan-fases.md` o el plan de GCP en el commit de la primera subfase: es lo que leerá la próxima sesión.
3. Implementar subfase a subfase. Al terminar cada una, verificar y **parar para revisión**; con el "revisado", skill `cerrar-subfase` (su PR draft guarda la lista de subfases con casillas). No empezar la siguiente sin su visto bueno ("dale").
