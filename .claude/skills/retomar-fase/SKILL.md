---
name: retomar-fase
description: Retoma en una sesión nueva una fase ya empezada (con rama o PR draft) y continúa por la siguiente subfase pendiente. Úsala cuando Sergio diga "seguimos con la 2.G", "continúa la fase N", "retoma la subfase N.M" o abra sesión a mitad de una fase.
---

# Retomar una fase empezada

Una sesión por subfase es tan válida como una por fase: el estado vive en git, en la PR y en los
docs, no en el chat anterior. Esta skill lo reconstruye.

## 1. Reconstruir el estado (solo lectura)

1. `AGENTS.md` (ya cargado) y el plan de la fase: `docs/plan-fases.md` o, en la pasada GCP, `docs/fase-13-12-checkpoint.md#plan-gcp`.
2. Rama: `git branch --show-current`. Si se está en `main`, buscar la de la fase (`git branch -a --list "*fase/*"`) y cambiar a ella (`git switch fase/<...>`); si no existe, es una fase nueva: skill `iniciar-fase`.
3. `git status --short`. Si hay cambios sin commit, **parar**: pueden ser una subfase a medias de la sesión anterior o restos ajenos. Enseñar el resumen (`git --no-pager diff --stat`) y decidir con Sergio (seguir con ellos, guardarlos en `%TEMP%` o descartarlos, esto último con su confirmación).
4. Lo hecho: `git --no-pager log --oneline main..HEAD` (un commit por subfase) y el cuerpo de la PR: `gh pr view --json number,title,isDraft,body,url` (casillas `- [x]` hechas, `- [ ]` pendientes, decisiones y pendientes de nube).
5. CI de la rama: `gh run list --branch <rama> --limit 1`. Si la última está en rojo, eso es lo primero.
6. El doc de la fase (sección "GCP (pasada N.G)" en la pasada) y lo que la subfase siguiente va a tocar.

## 2. Proponer

Resumir en pocas líneas: rama, PR, subfases hechas (con commit), CI, y la **siguiente subfase** con sus entregables, verificación y coste tal como están en el plan o en la PR. Señalar si algo de lo encontrado obliga a cambiar el plan. Esperar el "dale".

## 3. Ejecutar

Igual que en `iniciar-fase` §3: implementar la subfase, verificar, parar para revisión y, con el "revisado", skill `cerrar-subfase`. Si la sesión se alarga, cerrar la subfase en curso y proponer seguir en una sesión nueva con esta misma skill.
