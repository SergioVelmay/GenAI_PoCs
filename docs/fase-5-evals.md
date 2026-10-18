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

### Resultados en Azure (18-10-2026)

Misma muestra estratificada de 20 preguntas (`--limit 20`, motor `native`, híbrida), generador
enrutado (`gpt-4.1-mini` / `nano`) y **juez fijo `gpt-4.1`**, fuera del router: con el router
heurístico, generador y juez acababan los dos en `mini` (sesgo de autopreferencia). Se juzgan las
10 respuestas no abstenidas. Informes en [evals/reports/azure/](../evals/reports/azure/).

| | Nativo | RAGAS | DeepEval |
|---|---:|---:|---:|
| Llamadas al juez por muestra | **1** | 9,4 (+ embeddings reales) | 7 |
| Coste del juez (10 muestras) | **0,029 $** | 0,208 $ (×7,3) | 0,138 $ (×4,8) |
| Duración total (20 preguntas) | **35 s** | 152 s | 95 s |
| faithfulness | 1,00 | 1,00 | 1,00 |
| answer_relevancy | 1,00 | 0,58 | 0,88 |
| context_precision | 0,74 | 0,74 | 0,74 |
| context_recall | 1,00 | 1,00 | 1,00 |
| Errores del juez | 0 | 0 | 0 (tras el arreglo) |

Lectura:

- **faithfulness = 1 en los tres**: en modo `llm` la respuesta son citas literales validadas por el
  servicio, así que es fiel por construcción. La métrica no discrimina aquí; tendrá sentido con
  respuestas redactadas (Foundry IQ, agentes).
- **answer_relevancy no es comparable entre jueces**: el nativo (escala 0–3) da 1,0 a todo
  (demasiado indulgente); RAGAS la calcula con la similitud coseno entre la pregunta y preguntas
  regeneradas, que comprime la escala (0,36–0,84); DeepEval penaliza 2 respuestas con frases de
  relleno. **Por eso hay que calibrar** con etiquetas humanas antes de fijar umbrales.
- context_precision y context_recall coinciden en los tres: con contextos cortos y claros los
  jueces están de acuerdo.
- Coste: el juez nativo es ~5–7 veces más barato y rápido por diseño (una sola llamada
  estructurada).

Hallazgos (con su test):

| Hallazgo | Arreglo |
|---|---|
| Un veredicto incoherente del juez abortaba la eval entera | *Fail-closed* por muestra: se cuenta como `judge_errors`, no se puntúa y la eval sigue |
| DeepEval con `gpt-4.1`: `{"verdict":"yes"` seguido de espacios hasta `max_tokens` | Sus prompts piden `reason` solo para los "no", pero el modo *strict* exige todas las claves: el modelo no puede cerrar el objeto. Se añade una nota al prompt (rellenar con `null`) |
| Con el extra `evals`, 2 tests de retrieval fallaban | El plugin de pytest de DeepEval importa `deepeval`, que vuelca el `.env` en `os.environ`: `-p no:deepeval` y `DEEPEVAL_DISABLE_DOTENV=1` |
| No se sabía qué modelo juzgaba | El `CostMeter` desglosa llamadas y coste por modelo; el informe guarda cada muestra juzgada (para calibrar) |

### Calibración de los jueces (18-10-2026)

Con respuestas reales casi todo sale fiel, así que etiquetarlas no enseña nada. El conjunto
[calibration-v1](../evals/calibration/) mezcla 8 respuestas correctas con 12 estropeadas a
propósito, de forma determinista y sin LLM (`rag_evals.calibration build`): cifra cambiada
(`fabricated`), afirmación inventada añadida (`unsupported`), respuesta de otra pregunta
(`off_topic`) y evasiva (`non_answer`). Sergio etiqueta a ciegas en `labels-v1.csv` (la vista
`.md` no muestra el tipo) y `agreement` calcula acierto y **kappa de Cohen** humano-juez.

Detección por tipo (el juez da la fidelidad esperada), antes de las etiquetas humanas:

| Juez | good | fabricated | unsupported | off_topic | non_answer | Coste (20) |
|---|---:|---:|---:|---:|---:|---:|
| Nativo | 8/8 | 4/4 | **4/4** | 2/2 | 0/2 | **0,06 $** |
| RAGAS | 8/8 | 4/4 | **4/4** | 2/2 | 0/2 | 0,31 $ |
| DeepEval | 8/8 | 3/4 | **0/4** | 1/2 | 0/2 | 0,29 $ |

Lectura:

- **La faithfulness de DeepEval no detecta afirmaciones inventadas**: su veredicto `idk` (no
  aparece en el contexto) cuenta como fiel; solo penaliza contradicciones. Para RAG, donde lo
  peligroso es lo que el modelo añade, eso es un hueco. Nativo y RAGAS sí lo detectan.
- Ningún juez trata la evasiva como "sin afirmaciones" (`na`): nativo y RAGAS la puntúan como
  no fiel (0) y DeepEval mezcla 1 y 0. Ahí manda la etiqueta humana.
- La relevancia de RAGAS para respuestas correctas y cortas va de 0,24 a 0,87: su escala
  (similitud de preguntas regeneradas) no sirve para un umbral sin recalibrarla.
- El juez nativo detecta lo mismo que RAGAS por una quinta parte del coste.

Acuerdo con las etiquetas humanas ([informe](../evals/reports/azure/calibration.md)). Se
conservan dos versiones: **a ciegas** y **adjudicada** (corrige 2 despistes del etiquetado, una
frase inventada y una cifra cambiadas que pasaron como fieles, y unifica el criterio: la relevancia
mide si responde a lo preguntado, con independencia de que sea verdad; cada cambio se explica en
`labels-v1-adjudicated.csv`).

| Juez | Fiel: kappa (ciegas → adjudicada) | Relevante: kappa | Relevancia: MAE |
|---|---:|---:|---:|
| **Nativo** | 0,64 → **0,82** | 0,63 → **1,00** | 0,12 → **0,05** |
| RAGAS | 0,64 → 0,82 | 0,06 → 0,03 | 0,33 → 0,37 |
| DeepEval | 0,43 → 0,30 | 0,21 → 0,47 | 0,27 → 0,10 |

Decisión: **el juez nativo es el candidato a gate** (kappa > 0,8 en las dos métricas, ~5 veces
más barato). La relevancia de RAGAS no sirve con respuestas cortas y la fidelidad de DeepEval no
detecta invenciones. Las 2 discrepancias que quedan son las evasivas: el humano dice "sin
afirmaciones" y el juez "no fiel"; para un gate da igual, porque ambas cosas bloquean.
Limitaciones: 20 muestras y un solo anotador; antes de bloquear merges, ampliar el conjunto y
sumar un segundo anotador.

### Fidelidad de las respuestas de Foundry IQ (18-10-2026)

Las respuestas del carril A son texto redactado por el agente, no citas literales: aquí la
fidelidad sí hay que medirla. `rag_evals.managed_eval` pregunta al agente y el juez nativo
calibrado juzga cada respuesta frente a los textos que ella misma cita (sin citas = no fiel).
Muestra de 30 preguntas (20 con respuesta y 10 sin ella, sin filtros), ~0,05 $ de juez.
Informe en [managed-foundry_iq.md](../evals/reports/azure/managed-foundry_iq.md).

| Respondidas | Fieles | Faithfulness media | Abstenciones correctas | Fugas ACL | p50 |
|---:|---:|---:|---:|---:|---:|
| 21 | **19/21** | 0,96 | 9/10 | 0 | 3,2 s |

- El juez detecta la **sobre-respuesta** que ya se vio en la fase 4 (`acl-schedule-change-es`,
  0,57): el agente compone un plazo con políticas de varias categorías que no responden a la
  pregunta.
- Y una que nadie había visto: `policy-cancelled-food_drink-es` (0,60) acierta en lo
  principal pero **añade condiciones** («sin importar el tiempo previo», «los gastos de gestión no
  son reembolsables») que no están en el texto citado. Es justo el fallo que una métrica de
  evidencia literal no ve.
- Dos defensas actuaron antes del juez y cuentan como abstención: **Prompt Shields** bloqueó
  `acl-prompt-injection-es` (filtro de contenido del despliegue, `LLMContentFilterError`) y el
  guardarraíl de citas del adaptador rechazó `acl-companion-entry-es` (texto sin cita).

Hallazgos (con su test): un filtro de contenido y un rechazo del guardarraíl abortaban la eval
(ahora son estados `blocked` y `rejected`); el adaptador de Foundry IQ dejaba escapar los
errores del SDK de OpenAI (ahora se traducen como en el gateway); y el ledger del indexer seguía
dando por indexados los documentos de un AI Search recreado vacío (ahora
`provision_search_index --apply` olvida el ledger de ese destino cuando el índice es nuevo).

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

1. ~~`generation_eval --mode llm` con los tres jueces sobre la misma muestra~~ (hecho, ver arriba).
2. ~~Calibración humano-juez~~ (hecho: el juez nativo, kappa 0,82 en fidelidad y 1,0 en
   relevancia).
3. ~~Juez con un modelo distinto del generador y embeddings reales para RAGAS~~ (hecho: `gpt-4.1`
   y `text-embedding-3-small`).
4. Carril A: Azure AI Foundry Evaluation y Amazon Bedrock Evaluations con el mismo golden dataset.
5. Repetir el retrieval con embeddings reales y reranker de nube (fase 4) y fijar un baseline nuevo.

## 8. Checkpoint de entrevista

Las preguntas técnicas y de negocio/stakeholders de esta fase, con sus respuestas modelo, están en
[preguntas de entrevista · fase 5](preguntas-entrevista.md#fase-5).
