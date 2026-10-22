---
applyTo: "evals/**,packages/headache_core/src/headache_core/domain/evals.py"
description: "Evals de RAG, de agentes y tests de ataque (fases 5, 7 y 8.6)."
---

# Evals (paquete `rag_evals`)

Doc: `docs/fase-5-evals.md` (y secciones de evals en las fases 7 y 8).

## Datos versionados

- Corpus congelado `evals/corpus/knowledge.jsonl` (escala small, semilla 42) y golden `evals/datasets/golden-v1.jsonl` + `.meta.json` con el sha256 del corpus. Gold por `document_id` (cambiar el chunking no invalida el dataset).
- Regenerar/verificar: `uv run python -m rag_evals.golden [--check]`. Un cambio de corpus exige versión nueva del dataset, no editar la existente.
- Métricas puras en `headache_core/domain/evals.py` (hit rate, MRR, nDCG...), con sus tests.

## Ejecutar (coste 0)

- Retrieval: `uv run python -m rag_evals.retrieval_eval [--engine native] [--strategy bm25]` → `evals/reports/retrieval.{md,json}`. Con coste y solo explícito: `--engine azure --engine azure-semantic` (AI Search encendido, perfil de `az` aislado) → `evals/reports/azure/`; nunca en la CI ni en el baseline.
- Generación extractiva: `uv run python -m rag_evals.generation_eval [--strategy hybrid]` → citas auditadas, abstención, fugas ACL.
- Agentes: `uv run python -m rag_evals.agents [--flavor X] [--scenario ID] [--check]` (modo `scripted`, `ScenarioLLM` con marcadores `{tool:..}`, `{memory:..}`, `{seen:..}`; mundo determinista `rag_evals/agents/world.py`). Fase 12.1: precisión/recall de trayectoria, parámetros (`expect.tool_args`, subconjunto ordenado) y **resolución** (estado del mundo + invariantes, sin mirar el texto). `--check` (solo `scripted`, dataset completo) falla ante cualquier fallo fuera de `known_gaps` de `evals/baselines/agent-scenarios-v1.json` (fallos exactos); un hueco que deja de fallar se avisa y se quita a mano.
- LLM-juez de agentes (fase 12.2): `headache_core/application/agent_judge.py` (el modelo etiqueta: hechos respaldados por la evidencia, acciones dadas por hechas, resolución, idioma) y `domain/agent_eval.py` (el dominio decide `acceptable`). `--judge native --budget-usd N` en `rag_evals.agents` y `rag_evals.attacks` (informes en `evals/reports/azure/`). El juez ve lo mismo que el agente: mensajes ya redactados (`model_sees`) y salidas de sus tools. Calibración: `uv run python -m rag_evals.agent_calibration build|judge --budget-usd N|agreement` sobre `evals/calibration/agents-v1.jsonl` (32 muestras: buenas, reales de la fase 7, cifra cambiada, acción inventada, evasiva); etiquetas en `agents-labels-v1.csv`, solo de Sergio.
- Red teaming offline (fase 12.4): `uv run python -m rag_evals.redteam.mutations [--check]` (coste 0, sin PyRIT; la CI lo ejecuta) sobre `evals/datasets/attack-mutations-v1.jsonl` (100 mutaciones: 10 escenarios × 9 conversores de PyRIT + original, ASCII con escapes). Brechas (`ScenarioOutcome.breaches`: escritura o cambio de estado, literal prohibido al modelo o al cliente) solo las de `evals/baselines/attack-mutations-v1.json`, con su motivo; benignos bloqueados e inválidos, 0. Regenerar con PyRIT: `uv sync --all-packages --extra redteam` y `uv run python -m rag_evals.redteam.pyrit_offline --write-dataset`. Solo conversores deterministas; las plantillas de jailbreak de PyRIT no (citan marcas de terceros). Informe `evals/reports/redteam-pyrit.*`.
- Ataques: `uv run python -m rag_evals.attacks [--flavor X] [--check]` (modelo comprometido, con y sin harness, controles benignos).
- Multi-agente (fase 9.7): `uv run python -m rag_evals.multiagent [--engine langgraph|maf|async] [--check]` sobre `evals/datasets/multiagent-v1.jsonl` (guion legible: `triage`, `tool`, `text`, `draft`, `error`, `step` para el supervisor). Invariantes `unapproved_irreversible`, `refund_outside_specialist`, `identity_in_prompt`, `email_in_handoff` siempre 0; la CI lo ejecuta con `--check`. Con coste: `--mode llm --budget-usd N` → `evals/reports/azure/multiagent-llm.*`.
- Caché semántica (fase 10.3): `uv run python -m rag_evals.semantic_cache [--check]` sobre `evals/datasets/semantic-cache-v1.jsonl` (paráfrasis, trampas y personales; `known_gap` se informa pero no bloquea). Barre el umbral con y sin guarda de facetas → `evals/reports/semantic-cache.*`. Con coste: `--embeddings azure --budget-usd N` → `evals/reports/azure/`.
- Gate: `uv run python -m rag_evals.gate [--mode report|enforce]` contra `evals/baselines/baseline-v1.json` (tolerancia 0.01).

## Reglas

- Invariantes siempre 0 y siempre bloqueantes: `acl_leaks`, `cross_customer`, `identity_args`, `unexpected_writes`, `payload_to_model`, `unapproved_irreversible`; citas 100 % válidas; mismo corpus.
- Subir el baseline es deliberado: `--update-baseline` y revisión en el commit.
- Con coste: `--mode llm` y `--judge native|ragas|deepeval` exigen `--budget-usd` (`CostMeter` corta antes de cada llamada) y `--limit N` (muestra estratificada). Pedir confirmación.
- El juez va fijo y fuera del router (`build_judge_llm`, despliegue `AZURE_OPENAI__JUDGE_DEPLOYMENT=gpt-4.1`): nunca el mismo modelo que genera. Un veredicto incoherente cuenta como `judge_errors` (no se puntúa ni aborta). Informes con coste en `evals/reports/azure/`.
- Calibración: `uv run python -m rag_evals.calibration build|judge --judge X --budget-usd N|agreement`. El conjunto `evals/calibration/calibration-v1.jsonl` es determinista (test de reproducibilidad); las etiquetas humanas (`labels-v1.csv`) las pone Sergio, nunca el agente.
- Fidelidad del carril A: `uv run python -m rag_evals.managed_eval --budget-usd N` (AI Search encendido y Foundry IQ aprovisionado). Filtro de contenido y guardarraíl de citas son estados (`blocked`, `rejected`), no errores.
- Carril A (Foundry Evaluation): `uv run python -m rag_evals.foundry_eval [--log-to-foundry]` puntúa `calibration-v1` con `GroundednessEvaluator` y `RelevanceEvaluator` (juez `gpt-4.1`) y deja `calibration-foundry.json` para `calibration agreement`. Fiel = groundedness ≥ 3 (su aprobado; calibrado).
- La CI ejecuta el gate en `--mode enforce`: una regresión > 0,01 respecto al baseline bloquea el merge.
- RAGAS/DeepEval/azure-ai-evaluation en el extra `evals` (`uv sync --all-packages --extra evals`); la CI no lo instala y sus tests se saltan por `skipif`.
- Los embeddings `hashing` locales validan el arnés, no la calidad semántica: no sacar conclusiones de calidad sin embeddings reales.
