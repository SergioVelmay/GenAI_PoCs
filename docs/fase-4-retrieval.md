# Fase 4: retrieval avanzado y servicio RAG

## Estado y alcance

**Incrementos 4.1 y 4.2: base local y carril C implementados. La fase 4 sigue en curso.**

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
- Carril C LlamaIndex: BM25Retriever, VectorIndexRetriever y QueryFusionRetriever sobre
  los mismos chunks, embeddings e IDs del carril B.
- AutoMergingRetriever con docstore en memoria y padres autorizados; ratio configurable.
- Lectura SQL real verificada en ambos motores, sin escrituras.
- Comparacion reproducible: `uv run python -m retrieval.compare`.

**Los endpoints LLM devuelven 503 en la composicion local.** Sus flujos se prueban con
`FakeLLM`, un doble de test guionizado, no un modelo local. No hay una variable de entorno
que active accidentalmente llamadas cloud. El gateway de fase 2 se conectara en otro
incremento, con confirmacion antes de ejecutar cualquier llamada con coste.

## Conceptos y equivalencias

| Concepto | Local, carril B | Azure | AWS | Carril C local |
|---|---|---|---|---|
| Lexico | BM25 con `rank-bm25` | Busqueda textual de AI Search | Depende del backend de KB; S3 Vectors no ofrece BM25 | BM25Retriever de LlamaIndex |
| Vector | Hashing de fase 3, **no semantico** | Embeddings de Foundry + AI Search | Titan + pgvector/S3 Vectors | VectorIndexRetriever |
| Hibrida | Rankings fusionados por RRF | Hibrida con RRF | KB hibrida solo donde el backend la admite | QueryFusionRetriever |
| Filtros | Igualdad de metadatos + ACL del servidor | Filtros OData | Filtros de Retrieve/KB | Prefiltrado antes de construir los retrievers |
| Reranking | Cobertura lexica, **no neuronal** | Semantic Ranker | Bedrock Rerank, incluido Cohere segun region/modelo | Integracion de reranker |
| Parent-child | Chunk -> documento padre | Campos de padre + recuperacion adicional | Chunking jerarquico de KB | AutoMergingRetriever/docstore |
| RAG gestionado, carril A | Sin equivalente local real | Revisar disponibilidad actual de On Your Data antes de implementarlo | RetrieveAndGenerate de KB | No sustituye al carril A |

El mismo contrato permite comparar implementaciones, pero **ninguna equivalencia implica
identica calidad, seguridad o funcionalidades entre proveedores**. El carril A y los
adaptadores de retrieval/reranking cloud siguen pendientes para esta fase.

### Por que RRF

BM25 y cosine similarity no comparten escala. No sumamos sus scores directamente:

$$
\operatorname{RRF}(d) = \sum_{r \in rankings} \frac{1}{60 + position_r(d)}
$$

Solo contribuyen los rankings en los que aparece el documento; las posiciones empiezan
en 1. Se fusionan IDs de chunk, no titulos, y el desempate es determinista por ID.
Esta formula corresponde al carril nativo; el carril C delega en la implementacion
RRF del framework. No exigimos igualdad de scores entre carriles. Los duplicados dentro
de un ranking no cuentan dos veces. `candidate_k` controla el
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
  Es expansion explicita, disponible en ambos motores.
- **Auto-merge:** en LlamaIndex, sustituye los hijos recuperados por el padre si su
  proporcion supera `merge_ratio` (por defecto 0.5). Un hijo de tres no amplia; dos de
  tres si. Los hits vectoriales con score cero se descartan antes de calcular el ratio.
  Puede reducir el numero final de hits: no se rellena con fuentes irrelevantes.

El plan del LLM se valida con los structured outputs de fase 2. Puede proponer busquedas,
pero no decide acceso, reembolsos ni otras reglas de negocio.

## Ejecutar en local

Desde la raiz del monorepo:

```powershell
uv sync --all-packages --locked
uv run uvicorn retrieval.app:create_app --factory --host 127.0.0.1 --port 8010
```

Swagger: http://127.0.0.1:8010/docs. Diagnostico: http://127.0.0.1:8010/health.

Para ejecutar el carril C en otro puerto:

```powershell
$env:RETRIEVAL__ENGINE = "llamaindex"
uv run uvicorn retrieval.app:create_app --factory --host 127.0.0.1 --port 8012
```

`RETRIEVAL__ENGINE=native|llamaindex` selecciona el motor. `/health` identifica el motor
activo; no depende del motor de ingesta de fase 3. Cambiar esta variable requiere
reiniciar el proceso. No se modifica `Settings.llm` global de LlamaIndex: la fusión usa
una sola consulta y un guard que falla si intenta invocar un modelo; los embeddings
siempre se pasan mediante `PortEmbedding`.

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
| `/search/auto-merge` | Carril C: vector + AutoMergingRetriever; nativo: 503 explicito |
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
el arranque lo rechaza. Ambos motores leen la misma tabla nativa `knowledge_chunks`.
Las tablas propias de ingesta de LlamaIndex no se leen en este incremento: el carril C
reconstruye nodos y un docstore en memoria a partir de la instantanea comun.

La lectura real se verifico el 2026-10-08 con 134 documentos y 134 chunks existentes;
ambos motores respondieron a `refund` filtrando idioma y categoria, con fuente
`policy-candlelit-en`. PostgreSQL se arranco conservando su volumen, sin migraciones,
cargas ni borrados. Hay un test marcado `integration` de solo lectura: funciona tambien
contra un esquema migrado sin datos; en ese caso valida el camino SQL vacio, no calidad.

```powershell
uv run pytest services/retrieval/tests/test_retrieval_api.py -m integration
```

### Comparacion reproducible de carriles B/C

```powershell
uv run python -m retrieval.compare "reembolso gastos de gestion" --filter language=es --filter category=candlelit --top-k 3
uv run python -m retrieval.compare "refund" --source postgres --filter language=en --filter category=candlelit
```

El comando no genera datos, escribe ficheros ni invoca modelos. Carga una sola instantanea
y la comparte entre motores, con el mismo limite y chunking; no hace dos lecturas SQL
entre las que una ingesta concurrente pudiera cambiar los datos. Informa de IDs
resolubles y tiempo de consulta.
La version local del carril C construye los retrievers/indices autorizados en cada
consulta, fuera del event loop con `asyncio.to_thread`; ese coste esta incluido en sus
tiempos. No es un benchmark de servicios persistentes ni de calidad semantica.

BM25 nativo usa BM25Plus (`rank-bm25`); LlamaIndex usa BM25S y su politica predeterminada
de stopwords inglesas, sin stemming. Normalizamos mayusculas y acentos antes de BM25
en ambos carriles, pero los scores y el desempate pueden diferir por las implementaciones.
El vector reutiliza exactamente los embeddings de cada chunk, sin reembeber metadatos.

En el corpus actual hay un chunk por documento: ambas estrategias de padres devuelven
el mismo padre. Los tests con tres hijos distinguen expansion explicita y auto-merging.
La jerarquia reconstituida tiene un nivel padre/hijos, no los tres niveles del parser
jerarquico de ingesta de fase 3; integrar su docstore persistente sigue siendo un extra.

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
exacta. En carril C, la construccion y retrieval se ejecutan fuera del event loop; en
carril B el trabajo CPU sigue siendo directo. Persistencia lexical, cachés, limites de contexto
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
abstencion; contrato HTTP y limites del cargador sin modificar el corpus. Los mismos
tests se ejecutan con ambos motores. Se verifican tambien el umbral de auto-merging,
los padres privados, hits con score cero y el bloqueo de los modelos por defecto.

Pendientes antes de cerrar fase 4:

- **4.2, local: implementada y verificada.** Lectura SQL, carril C sobre corpus comun,
  comparacion de padres y auto-merging. Pendiente de revision del usuario.
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

**Parent-child y auto-merging son lo mismo?** No. La expansion explicita siempre intenta
devolver el padre de un hit. Auto-merging necesita una proporcion suficiente de hijos
recuperados; reduce ruido cuando solo coincide un detalle aislado y amplia contexto
cuando coinciden varios. El umbral y el tamano del padre deben evaluarse.

**Que aporta LlamaIndex si ya tengo puertos?** Compone retrievers y docstores existentes,
sin mover seguridad ni reglas de negocio al framework. El coste es adaptar contratos,
conocer sus defaults y asumir cambios de API. Aqui conserva los mismos IDs y fuentes,
de modo que la comparacion prueba ranking, no diferencias de ingesta.