# Fase 5 · Evals de RAG: golden dataset, métricas, jueces y gate en CI

Cómo sabemos si el RAG de Headache funciona y cómo evitamos que empeore sin que nadie se dé cuenta.
Todo se mide con el mismo corpus congelado, el mismo golden dataset y las mismas métricas en los
carriles B (SDK nativo) y C (LlamaIndex). El carril A (evaluación gestionada) queda pendiente de nube.

> Estado: todo lo local está completo y en CI (coste 0). Las ejecuciones con LLM (generación y
> jueces) y el carril A están **pendientes de validación con coste** (ver el final).

## 1. Piezas

| Pieza | Qué hace | Código |
|---|---|---|
| Corpus congelado | Escala `small`, semilla 42: 116 documentos / 131 chunks, sha256 verificado | [evals/corpus/knowledge.jsonl](../evals/corpus/knowledge.jsonl) |
| Golden dataset v1 | 144 preguntas generadas por plantillas a partir de los documentos (gold por `document_id`, relevancia graduada, literales de evidencia) | [golden.py](../evals/src/rag_evals/golden.py), [golden-v1.jsonl](../evals/datasets/golden-v1.jsonl) |
| Métricas puras | Hit@k, Recall@k, MRR, nDCG graduado, precisión media, percentiles | [domain/evals.py](../packages/headache_core/src/headache_core/domain/evals.py) |
| Evals de retrieval | Todas las estrategias de B y C | [retrieval_eval.py](../evals/src/rag_evals/retrieval_eval.py) |
| Evals de generación | Citas auditadas, abstención, fugas ACL; modo LLM y jueces con presupuesto | [generation_eval.py](../evals/src/rag_evals/generation_eval.py) |
| Jueces LLM | Nativo (B) y RAGAS / DeepEval (C) sobre el mismo `LLMPort` | [judges.py](../evals/src/rag_evals/judges.py), [frameworks/](../evals/src/rag_evals/frameworks/) |
| Medidor de coste | Corta antes de cada llamada al agotar el presupuesto | [metering.py](../evals/src/rag_evals/metering.py) |
| Gate | Compara informes con un baseline versionado | [gate.py](../evals/src/rag_evals/gate.py), [baseline-v1.json](../evals/baselines/baseline-v1.json) |

Composición del golden dataset: 78 factuales, 19 multilingües (pregunta en un idioma, documento en el
otro), 17 con filtros, 16 sin respuesta en el corpus y 14 adversariales de ACL (preguntan por datos
de la guía interna, que un usuario público no debe ver).

## 2. Retrieval: carril B frente a carril C

Fuente: [evals/reports/retrieval.md](../evals/reports/retrieval.md) (114 preguntas con respuesta, top_k=5).

| Estrategia | Hit@1 B / C | MRR B / C | nDCG@5 B / C | Sin resultados en preguntas sin respuesta B / C | p50 ms B / C |
|---|---:|---:|---:|---:|---:|
| bm25 | 0.895 / 0.903 | 0.924 / 0.928 | 0.852 / 0.856 | 3/30 / 6/30 | 1.2 / 28.1 |
| vector | 0.947 / 0.947 | 0.974 / 0.974 | 0.897 / 0.897 | 2/30 / 2/30 | 0.2 / 34.3 |
| hybrid | 0.912 / 0.903 | 0.953 / 0.947 | 0.875 / 0.876 | 2/30 / 2/30 | 1.8 / 60.4 |
| rerank | 0.895 / 0.895 | 0.936 / 0.934 | 0.862 / 0.860 | 2/30 / 2/30 | 2.2 / 55.8 |
| parent-child | 0.912 / 0.903 | 0.953 / 0.947 | 0.875 / 0.876 | 2/30 / 2/30 | 1.6 / 56.6 |
| auto-merge | — / 0.947 | — / 0.974 | — / 0.897 | — / 2/30 | — / 40.7 |

Lectura:

- **Calidad equivalente.** Con el mismo corpus y los mismos chunks, B y C difieren menos de 0.01 en
  casi todo: el framework no aporta calidad, aporta componentes ya hechos.
- **Hit@5 está saturado (≈1.0)**: las plantillas comparten vocabulario con los documentos. Las métricas
  que discriminan son **Hit@1, MRR y nDCG** (orden), no Hit@k (presencia).
- **El reranker local (TokenCoverage) empeora** ligeramente: un reranker léxico sobre un ranking ya
  léxico no añade señal. Un cross-encoder real (Cohere Rerank en Bedrock, Semantic Ranker en AI
  Search) es lo que hay que medir en la nube.
- **`vector` gana**, pero con embeddings `hashing` (bolsa de palabras), así que valida el arnés, no la
  semántica. BM25 sí es una baseline legítima.
- **C es 20-40 veces más lento** en este proceso (sobrecarga del framework por consulta). En producción
  la latencia la dominan la red y el modelo, pero en un endpoint con 300 M de usuarios ese coste de
  CPU se paga en réplicas.
- **0 fugas de ACL** en todas las ejecuciones, verificadas por el evaluador de forma independiente.
- **Abstención casi nula**: el retrieval casi siempre devuelve algo (solo 2-6 de 30 preguntas sin
  respuesta quedan vacías). Decidir "no lo sé" no es trabajo del retrieval.

## 3. Generación

Fuente: [evals/reports/generation.md](../evals/reports/generation.md) (modo extractivo, coste 0).

| Motor / estrategia | Evidencia | Abstención correcta | Validez de citas | Precisión de citas | Citas por respuesta | Fugas |
|---|---:|---:|---:|---:|---:|---:|
| native / hybrid | 1.000 | 0.067 | 1.000 | 0.218 | 4.6 | 0 |
| native / bm25 | 1.000 | 0.100 | 1.000 | 0.218 | 4.6 | 0 |
| llamaindex / hybrid | 1.000 | 0.067 | 1.000 | 0.226 | 4.6 | 0 |
| llamaindex / bm25 | 1.000 | 0.200 | 1.000 | 0.218 | 4.6 | 0 |

El modo extractivo devuelve como respuesta los chunks recuperados: **siempre contiene el dato** y sus
citas son válidas por construcción, pero **solo ~1 de cada 5 citas es relevante** y **casi nunca se
abstiene**. Es la baseline que justifica el modo `llm-extractive` (el modelo elige citas literales y el
servicio rechaza las inventadas). La hipótesis a validar con coste es que sube la precisión y la
abstención correcta sin perder evidencia.

Un detalle importante para interpretar jueces: en modo extractivo la **faithfulness es 1.0 por
diseño** (la respuesta son citas literales). Una métrica perfecta no significa un sistema útil.

## 4. Jueces LLM: nativo frente a RAGAS y DeepEval

Las tres implementaciones calculan las mismas cuatro métricas (faithfulness, answer relevancy,
context precision, context recall) y llaman al modelo a través del mismo gateway (routing, fallback,
rate limit, trazas y `CostMeter`). Lo verificado en local con LLM falsos:

| | Nativo (carril B) | RAGAS 0.4.3 (C) | DeepEval 4.2.8 (C) |
|---|---|---|---|
| **Llamadas por muestra** (top_k=5, con referencia) | **1** (una salida estructurada) | ~11 (2 faithfulness + 3 relevancy + 5 precision, una por contexto + 1 recall) y 4 embeddings | ~7 (3 faithfulness + 2 relevancy + 1 precision + 1 recall, sin "reason") |
| **Quién calcula la nota** | El código, a partir de etiquetas | El framework | El framework |
| **Auditable** | Total: etiquetas guardables, fórmulas puras y testeadas | Media | Media (con `include_reason`, a costa de una llamada más) |
| **Dependencias** | Ninguna nueva | langchain, datasets, instructor... | posthog (telemetría), muchas utilidades |
| **Riesgos detectados** | — | CVE-2026-6587 (SSRF) en la faithfulness multimodal (no se usa); se rompe con `langchain-community` 0.4.2 (tope <0.4.2) | Telemetría por defecto (desactivada); crea `.deepeval/` |
| **Ventaja** | Barato, rápido y bajo nuestro control | Métricas con nombre estándar y literatura detrás | Integración con pytest y catálogo amplio |

Decisiones:

- Los frameworks van en un **extra opcional `evals`**: no entran en la imagen de producción ni en la
  CI, y sus tests se saltan si no están instalados (pero fallan si están instalados y rotos).
- El juez nativo **no pide una nota al modelo**: pide etiquetas atómicas y la nota la calcula el
  dominio. Rechaza veredictos incoherentes (contextos sin etiquetar) en lugar de puntuar a ciegas.
- Pregunta, respuesta y contextos se tratan como **datos no fiables** en el prompt del juez (prompt
  injection).
- Ningún juez decide un merge todavía: primero hay que **calibrarlo** contra etiquetas humanas.

## 5. Gate en CI

El job `evals` de la CI regenera los informes en Linux y los compara con
[baseline-v1.json](../evals/baselines/baseline-v1.json):

| Regla | Ejemplos | Efecto |
|---|---|---|
| Invariantes | 0 fugas de ACL, citas 100 % válidas, 0 rechazos en extractivo, mismo dataset y corpus | Bloquean siempre |
| Regresiones | Hit@1, MRR, nDCG, evidencia o abstención empeoran más de 0.01 | Informativas (`report`); bloquean con `enforce` |
| Mejoras | Suben más de 0.01 | Se sugiere subir el baseline (trinquete) |

Primera ejecución en Linux: **0 diferencias** con el baseline generado en Windows (evals
deterministas). Pasar a `enforce` es un cambio de una línea cuando haya varias ejecuciones estables.
Subir el baseline es deliberado (`--update-baseline`) y se revisa en la PR como cualquier otro cambio.

## 6. Equivalencias Azure ↔ AWS ↔ framework

| Necesidad | Azure | AWS | Framework / propio |
|---|---|---|---|
| Dataset de evaluación | Dataset en Foundry (JSONL) | Dataset en S3 (JSONL) | `golden-v1.jsonl` versionado en Git |
| Métricas de retrieval | Evaluadores de Foundry (document retrieval) | Bedrock Evaluations (retrieve only) | `domain/evals.py` |
| LLM-as-a-judge para RAG | Groundedness, Relevance, Retrieval... en Foundry Evaluation | Bedrock Evaluations (retrieve and generate) | Juez nativo, RAGAS, DeepEval |
| Gate en el pipeline | Evaluación en Azure DevOps / GitHub Actions | Job de evaluación + umbral en CodePipeline / GitHub Actions | `rag_evals.gate` |
| Control de coste | Presupuestos de suscripción | AWS Budgets | `CostMeter` por ejecución |

## 7. Pendiente de validación con coste

(Con confirmación previa; requiere los pendientes de la fase 2.)

1. `generation_eval --mode llm --judge native --limit 20 --budget-usd 0.50` y después `--judge ragas`
   y `--judge deepeval` sobre la misma muestra: coste, latencia y acuerdo entre jueces.
2. Calibración: unas 20 respuestas etiquetadas a mano y acuerdo humano-juez antes de usar el juez
   como gate.
3. Juez con un modelo distinto del generador y embeddings reales para la AnswerRelevancy de RAGAS.
4. Carril A: Azure AI Foundry Evaluation y Amazon Bedrock Evaluations con el mismo golden dataset.
5. Repetir el retrieval con embeddings reales y reranker de nube (fase 4) y fijar un baseline nuevo.

## 8. Checkpoint de entrevista

- **"¿Cómo construiste el golden dataset?"** Con plantillas deterministas sobre documentos
  sintéticos: cada pregunta sabe qué documento la responde y qué literal debe aparecer. Es barato y
  reproducible, pero sobreestima la calidad porque las preguntas comparten vocabulario con los
  documentos (de ahí Hit@5 ≈ 1.0). El siguiente paso es añadir preguntas reales anonimizadas de
  soporte y parafrasear con un LLM, revisando a mano.
- **"¿Qué métrica mirarías primero?"** Para retrieval, MRR y nDCG (orden), porque el generador lee los
  primeros resultados. Para generación, precisión de citas y abstención correcta. Y por encima de
  todo los invariantes de seguridad: una fuga de ACL invalida cualquier media.
- **"¿RAGAS o algo propio?"** Ambos, para cosas distintas. RAGAS/DeepEval sirven como referencia
  estándar y para comparar con la literatura; el juez propio es lo que corre de forma recurrente,
  porque cuesta una llamada por muestra en vez de 7-11 y su nota es auditable.
- **"¿Cómo evitas que las evals se queden obsoletas?"** El dataset guarda el hash del corpus y el gate
  falla si no coinciden; el baseline está versionado y solo se sube con revisión.
- **"LlamaIndex es mucho más lento, ¿lo descartas?"** No por eso: la calidad es la misma y acelera el
  desarrollo. Lo uso donde aporta (auto-merge, parsers) y mantengo el camino caliente en código
  propio cuando la latencia de CPU importa a escala.
