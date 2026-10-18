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
- Agentes: `uv run python -m rag_evals.agents [--flavor X] [--scenario ID]` (modo `scripted`, `ScenarioLLM` con marcadores `{tool:..}`, `{memory:..}`, `{seen:..}`; mundo determinista `rag_evals/agents/world.py`).
- Ataques: `uv run python -m rag_evals.attacks [--flavor X] [--check]` (modelo comprometido, con y sin harness, controles benignos).
- Gate: `uv run python -m rag_evals.gate [--mode report|enforce]` contra `evals/baselines/baseline-v1.json` (tolerancia 0.01).

## Reglas

- Invariantes siempre 0 y siempre bloqueantes: `acl_leaks`, `cross_customer`, `identity_args`, `unexpected_writes`, `payload_to_model`, `unapproved_irreversible`; citas 100 % válidas; mismo corpus.
- Subir el baseline es deliberado: `--update-baseline` y revisión en el commit.
- Con coste: `--mode llm` y `--judge native|ragas|deepeval` exigen `--budget-usd` (`CostMeter` corta antes de cada llamada) y `--limit N` (muestra estratificada). Pedir confirmación.
- El juez va fijo y fuera del router (`build_judge_llm`, despliegue `AZURE_OPENAI__JUDGE_DEPLOYMENT=gpt-4.1`): nunca el mismo modelo que genera. Un veredicto incoherente cuenta como `judge_errors` (no se puntúa ni aborta). Informes con coste en `evals/reports/azure/`.
- RAGAS/DeepEval en el extra `evals` (`uv sync --all-packages --extra evals`); la CI no lo instala y sus tests se saltan por `skipif`.
- Los embeddings `hashing` locales validan el arnés, no la calidad semántica: no sacar conclusiones de calidad sin embeddings reales.
