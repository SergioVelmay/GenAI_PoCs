# Fase 4: retrieval avanzado y servicio RAG

## Estado y alcance

**Incremento 4.1: base local implementada. La fase 4 completa sigue en curso.**

No se han creado recursos cloud ni invocado modelos. No se ha regenerado ni modificado
el dataset LARGE, ni escrito en PostgreSQL. Los tests usan adaptadores en memoria.

Implementado:

- BM25 real con `rank-bm25`, vector local de hashing e hibrida con RRF.
- Filtros exactos por ciudad, evento, idioma, categoria, tipo y documento.
- ACL aplicada antes de seleccionar candidatos; acceso ausente se deniega.
- Reranking local por cobertura de palabras, identificado como baseline no neuronal.
- Parent-child: buscar chunks y devolver el documento padre, sin duplicarlo.
- Planificacion de rewrite, HyDE, subpreguntas, router y auto-retrieval sobre `LLMPort`.
- Respuesta extractiva, seleccion literal asistida por LLM y validacion de citas.
- FastAPI con una ruta por estrategia y resolucion de fuentes autorizadas.

**Los endpoints LLM devuelven 503 en la composicion local.** Sus flujos se prueban con
`FakeLLM`, un doble de test guionizado, no un modelo local. No hay una variable de entorno
que active accidentalmente llamadas cloud. El gateway de fase 2 se conectara en otro
incremento, con confirmacion antes de ejecutar cualquier llamada con coste.

## Conceptos y equivalencias

| Concepto | Local, carril B | Azure | AWS | Carril C, pendiente |
|---|---|---|---|---|
| Lexico | BM25 con `rank-bm25` | Busqueda textual de AI Search | Depende del backend de KB; S3 Vectors no ofrece BM25 | BM25Retriever de LlamaIndex |
| Vector | Hashing de fase 3, **no semantico** | Embeddings de Foundry + AI Search | Titan + pgvector/S3 Vectors | VectorIndexRetriever |
| Hibrida | Rankings fusionados por RRF | Hibrida con RRF | KB hibrida solo donde el backend la admite | QueryFusionRetriever |
| Filtros | Igualdad de metadatos + ACL del servidor | Filtros OData | Filtros de Retrieve/KB | MetadataFilters |
| Reranking | Cobertura lexica, **no neuronal** | Semantic Ranker | Bedrock Rerank, incluido Cohere segun region/modelo | Integracion de reranker |
| Parent-child | Chunk -> documento padre | Campos de padre + recuperacion adicional | Chunking jerarquico de KB | AutoMergingRetriever/docstore |
| RAG gestionado, carril A | Sin equivalente local real | Revisar disponibilidad actual de On Your Data antes de implementarlo | RetrieveAndGenerate de KB | No sustituye al carril A |

El mismo contrato permite comparar implementaciones, pero **ninguna equivalencia implica
identica calidad, seguridad o funcionalidades entre proveedores**. Los carriles A y C
todavia no estan implementados para esta fase.

### Por que RRF

BM25 y cosine similarity no comparten escala. No sumamos sus scores directamente:

$$
\operatorname{RRF}(d) = \sum_{r \in rankings} \frac{1}{60 + position_r(d)}
$$

Solo contribuyen los rankings en los que aparece el documento; las posiciones empiezan
en 1. Se fusionan IDs de chunk, no titulos, y el desempate es determinista por ID.
Los duplicados dentro de un ranking no cuentan dos veces. `candidate_k` controla el
recall previo a la fusion/reranking; `top_k` controla el contexto final.

### Estrategias avanzadas

- **Rewrite:** el LLM devuelve una consulta mas precisa; despues se busca con hibrida.
- **HyDE:** genera un pasaje hipotetico, lo embebe y busca por vector. El pasaje nunca
  se incorpora como fuente ni como respuesta.
- **Subpreguntas:** hasta cuatro consultas; sus resultados se deduplican y fusionan por RRF.
- **Router:** selecciona un indice logico mediante `doc_type`. En este incremento son
  particiones del mismo corpus, no indices fisicos independientes.
- **Auto-retrieval:** infiere filtros de una lista cerrada. No acepta ACL y no puede
  reemplazar filtros explicitos del usuario. Un conflicto se rechaza.
- **Parent-child:** recupera pequenos chunks y amplia al documento padre autorizado.
  Es expansion explicita, no el auto-merging jerarquico de LlamaIndex.

El plan del LLM se valida con los structured outputs de fase 2. Puede proponer busquedas,
pero no decide acceso, reembolsos ni otras reglas de negocio.

## Ejecutar en local

Desde la raiz del monorepo:

```powershell
uv sync --all-packages --locked
uv run uvicorn retrieval.app:create_app --factory --host 127.0.0.1 --port 8010
```

Swagger: http://127.0.0.1:8010/docs. Diagnostico: http://127.0.0.1:8010/health.

Por defecto lee **los primeros 500 documentos** del JSONL existente, con chunks de
80 palabras y solape de 15, igual que la ingesta nativa por defecto. `/health` devuelve
el numero efectivo de documentos/chunks y los baselines utilizados. Esta muestra
depende del orden del corpus; no es representativa para medir calidad.

```powershell
$body = @{
    query = "reembolso gastos de gestion"
    filters = @{ language = "es"; category = "candlelit" }
    top_k = 3
    candidate_k = 20
} | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8010/search/hybrid -ContentType "application/json" -Body $body
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8010/rag/extractive -ContentType "application/json" -Body $body
```

| Ruta POST | Comportamiento local |
|---|---|
| `/search/bm25` | Ranking lexico |
| `/search/vector` | Ranking por cosine de hashing |
| `/search/hybrid` | BM25 + vector + RRF |
| `/search/rerank` | Hibrida + cobertura lexica |
| `/search/parent-child` | Hibrida + expansion de padres |
| `/search/rewrite` | 503 sin LLM configurado |
| `/search/hyde` | 503 sin LLM configurado |
| `/search/subquestions` | 503 sin LLM configurado |
| `/search/router` | 503 sin LLM configurado |
| `/search/auto` | 503 sin LLM configurado |
| `/rag/extractive` | Texto literal con citas; admite `strategy` |
| `/rag/llm-extractive` | 503 sin LLM, salvo abstencion sin candidatos |

Cada cita incluye `source_id`, `document_id`, `title`, `quote` y `href`. Un GET a
`href` devuelve el texto del que procede. Una fuente inexistente o no autorizada
devuelve el mismo 404. No hay acceso al sistema de ficheros desde esta ruta.

### Reutilizar PostgreSQL de fase 3

Con PostgreSQL arrancado y migrado, y el indice nativo `knowledge` ya cargado con hashing:

```powershell
$env:RETRIEVAL__SOURCE = "postgres"
$env:RETRIEVAL__INDEX_NAME = "knowledge"
$env:RETRIEVAL__MAX_DOCUMENTS = "500"
uv run uvicorn retrieval.app:create_app --factory --host 127.0.0.1 --port 8011
```

Esto toma una **instantanea en memoria al arrancar**, solo con SELECT. El limite se
aplica a documentos completos, no a chunks aislados. Cambios posteriores de la ingesta
requieren reiniciar el servicio. BM25 y vectores se consultan en memoria, **no con HNSW
en PostgreSQL**. Los padres SQL se reconstruyen concatenando los chunks, conservando
sus solapes; no son una copia byte a byte del Markdown original.

Se verifica que los embeddings coincidan con hashing del titulo + texto de fase 3.
Un indice Titan/OpenAI de 1024 dimensiones no es compatible con consultas de hashing:
el arranque lo rechaza. Las tablas propias de LlamaIndex no se leen en este incremento.

La prueba real de este cargador requiere PostgreSQL disponible; los tests unitarios
no certifican una conexion SQL real.

## Seguridad y limites de la respuesta

El cargador asigna acceso `public` a `policy`, `faq`, `venue_guide` y `event_sheet`, e
`internal` a `handbook` y tipos desconocidos. Una ACL explicita se conserva. Esta es
una **politica didactica de clasificacion**: en produccion, el propietario del dato
debe clasificarlo y revisar si una ficha de evento contiene informacion interna.

El puerto deniega metadatos sin ACL. La API local siempre usa el scope publico; no
confia en cabeceras ni en el cuerpo para ampliar permisos. El servicio vuelve a
comprobar acceso y filtros antes de entregar candidatos y padres. No se ha implementado
autenticacion Entra/Cognito ni acceso interno por HTTP.

La respuesta local copia evidencia literal, con un maximo de 4000 caracteres por
fuente. Sin candidatos se abstiene. Con LLM, solo se permiten selecciones de citas
literales de los candidatos autorizados; no IDs externos ni texto inventado. Un fallo
de cita devuelve 502, nunca una respuesta parcialmente valida.

**Una cita literal prueba procedencia, no relevancia, vigencia ni verdad del contenido.**
La seleccion puede ser equivocada; hashing puede tener colisiones. No afirmamos que el
sistema recupere siempre el fragmento correcto. Tampoco generamos todavia una sintesis
libre: para permitirla hacen falta comprobaciones de fidelidad y evals.

Este backend es para aprender y validar contratos, no para 300 M de usuarios: carga
una muestra en RAM, recalcula BM25 sobre candidatos autorizados y hace busqueda vectorial
exacta. Persistencia lexical, ejecucion CPU no bloqueante, cachés, limites de contexto
por tokens, autenticacion y escala se deben resolver antes de produccion.

## Verificacion y siguientes incrementos

```powershell
uv run pytest packages/headache_core/tests/test_retrieval.py services/retrieval/tests/test_retrieval_api.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

Cobertura local: RRF por rango y deduplicacion; filtros previos a top-k; ACL y fuentes
privadas; estrategias con `FakeLLM`; parent-child; citas literales, citas inventadas y
abstencion; contrato HTTP y limites del cargador sin modificar el corpus.

Pendientes antes de cerrar fase 4:

- **4.2, local:** comprobar la lectura SQL real y añadir carril C LlamaIndex usando el
  mismo corpus y casos. Comparar expansion de padres y auto-merging del docstore.
- **4.3, adaptadores sin coste:** implementar y probar con dobles los SDKs de AI Search
  (hibrida/semantic ranker), Bedrock Rerank/Cohere y KB RetrieveAndGenerate; verificar
  la disponibilidad actual del carril gestionado de Azure. No asumir que todos los
  backends de KB ofrecen hibrida.
- **4.4, cloud con confirmacion:** conectar gateway y embeddings reales, autenticar,
  ejecutar los carriles A/B/C y medir coste, latencia y fidelidad. Se mantienen los
  pendientes cloud de fases 2 y 3.
- **Fase 5:** golden dataset, hit rate/MRR y evals de relevancia/fidelidad; los tests
  actuales validan contratos, no sustituyen esas metricas.

## Checkpoint de entrevista

**Por que RRF y no sumar scores?** BM25 y cosine tienen escalas distintas. RRF combina
posiciones y evita calibrarlas; el recall sigue dependiendo de la ventana de candidatos.

**Donde se aplica ACL?** Antes del ranking/top-k, en el backend, y de nuevo en la
aplicacion. El LLM no puede ampliar el scope ni generar ACL como filtro.

**HyDE no introduce alucinaciones?** Su texto es hipotetico y solo guia la busqueda.
La respuesta usa exclusivamente fuentes reales recuperadas; puede empeorar el recall
y hay que medirlo frente a hibrida sin HyDE.

**Una respuesta con citas es fiable?** Las citas demuestran trazabilidad, no entailment.
Aqui reducimos el riesgo usando extraccion literal; aun debemos evaluar relevancia,
vigencia y suficiencia de la evidencia.

**Por que hashing local?** Permite tests deterministas sin red/coste ni modelos locales.
No demuestra recuperacion semantica: eso requiere embeddings cloud reales y evals.