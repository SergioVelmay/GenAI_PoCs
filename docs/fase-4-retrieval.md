# Fase 4: retrieval avanzado y servicio RAG

## Estado y alcance

**4.1/4.2 publicadas; 4.3 AI Search y Bedrock implementados y probados sin red.
La fase 4 sigue en curso; sustitucion del carril A Azure aprobada, implementacion
Foundry Agent Service/Foundry IQ y ejecucion cloud pendientes.**

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
- Carril B Azure: SDK de AI Search para BM25/vector/hibrida y Semantic Ranker opcional,
  prefilter ACL, fuente por ID y rechazo de scores semanticos ausentes.
- Bedrock Rerank, incluido modelo Cohere mediante ARN explicito: mantiene los candidatos
  originales aunque el proveedor devuelva texto alternativo.
- Carril A AWS: SDK de KB Retrieve y RetrieveAndGenerate, filtros ACL, referencias S3
  y validacion estricta de spans/cobertura de citas.

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
| RAG gestionado, carril A | Sin equivalente local real | Foundry Agent Service + Foundry IQ: sustitucion aprobada, implementacion pendiente | RetrieveAndGenerate de KB | No sustituye al carril A |

El mismo contrato permite comparar implementaciones, pero **ninguna equivalencia implica
identica calidad, seguridad o funcionalidades entre proveedores**. Los adaptadores
cloud se validan con dobles; aun no hay validacion real de region, permisos o calidad.

### 4.3: SDKs sin ejecutar cloud

Los clientes se inyectan, nunca se crean durante el arranque local. La API conserva
su composicion local: este incremento no anade un interruptor que active coste.
Los tests de AI Search capturan peticiones; los de AWS usan clientes con credenciales
ficticias y `botocore.stub.Stubber`. Se verifica tambien el enum de Rerank contra el
modelo instalado de botocore, porque Stubber no valida todos los valores de enums.

**Azure, carril B.** `AzureSearchRetrieval` implementa `RetrievalPort`. La hibrida envia
texto y `VectorizedQuery` al mismo indice: AI Search fusiona rankings mediante RRF.
Con `semantic=True`, se envia `semantic_query` y `knowledge-semantic`; el score expuesto
es `@search.rerankerScore`, no el score RRF. No es un `RerankerPort` generico al que
podamos pasar candidatos ajenos a ese indice. Las consultas vectoriales solicitan
50 vecinos para alimentar al ranker, con `vector_filter_mode=preFilter`; la ventana
semantica solicitada (`candidate_k`) no puede superar 50. La consulta textual sin
semantica no invoca embeddings. `get` recupera el ID codificado y comprueba ACL.
Un vector cloud exige el mismo encoder/configuracion que la ingesta: 1024 dimensiones
por si solas no prueban compatibilidad. Debe garantizarlo la composicion de 4.4.

El esquema de ingesta nativa ahora incluye `title`, `acl` filtrable y configuracion
semantica. **No se ha actualizado ningun indice cloud.** Antes de probarlo habra que
actualizar el esquema y reingerir metadatos ACL explicitos, con confirmacion. No se
asigna acceso publico por defecto en estos adaptadores cloud; una ACL ausente se
deniega. La politica didactica del cargador local no se aplica automaticamente a
los pipelines cloud. El indice gestionado Azure de fase 3 tiene otro esquema y no se
puede enchufar directamente a este adaptador del indice nativo.

**AWS, reranking B.** `BedrockReranker` implementa `RerankerPort`: recibe candidatos
previamente autorizados por `RetrievalService`, envia solo su texto y aplica los indices
devueltos sobre los registros originales. Rechaza indices duplicados/fuera de rango
y scores no finitos. No modifica las fuentes ni acepta el texto retornado por el modelo.
El ARN debe elegirse por disponibilidad regional; el ARN de Cohere del test no demuestra
que haya acceso al modelo en la cuenta. El reranker no decide la ACL.

**AWS, KB A.** `BedrockKnowledgeBase` implementa `ManagedKnowledgePort`, distinto del RAG
extractivo. KB crea el embedding de la consulta y, con RetrieveAndGenerate, tambien
orquesta la generacion internamente: no reutiliza nuestro gateway Python de `LLMPort`.
Es la diferencia deliberada entre carril A gestionado y B/C composables. Los filtros
de metadatos se combinan con ACL del servidor y las referencias se vuelven a comprobar.
Los sidecars de ingesta conservan `acl` y `event_id` explicitos; el corpus ya cargado
sin ACL requiere clasificacion y resincronizacion antes de validarlo en cloud.

La KB de fase 3 usa S3 Vectors: este incremento usa `SEMANTIC` y rechaza `HYBRID`, salvo
que una composicion declare un store compatible. No inventamos BM25 sobre S3 Vectors.
El adaptador solo acepta referencias textuales S3 con `document_id`; no soporta otros
conectores ni respuestas multimodales. Si Retrieve o Rerank devuelven `nextToken`, falla
explicitamente: la paginacion completa es un seguimiento, no se entrega un ranking
truncado como si estuviera completo.

Las citas gestionadas tienen texto generado, span normalizado `[start, end)` y fuentes
con texto literal, URI y un ID estable derivado de KB/documento/URI/texto. Se aceptan
offsets finales inclusivos o exclusivos solo si coinciden con el texto declarado.
No se reutilizan sesiones entre peticiones, evitando mezclar scopes de usuarios.
Sin citas o tras intervencion de guardrail, se abstiene. Spans falsos, fuentes sin ACL,
filtros incumplidos o texto no blanco fuera de los spans provocan rechazo completo.
Esta politica de cobertura es conservadora: formatos o marcadores del proveedor
pueden provocar rechazo y deben medirse en 4.4 antes de relajarla. Una fuente citada
es un snapshot retornado por la KB, no una relectura independiente de S3 ni un `href`
de la API local. La validacion estructural **no comprueba entailment ni fidelidad**.

**Azure, carril A: sustitucion aprobada el 2026-10-08.** La [documentacion oficial de On Your Data](https://learn.microsoft.com/en-us/azure/foundry-classic/openai/concepts/use-your-data)
consultada el 2026-10-08 anuncia su retirada el **14 de octubre de 2026**; solo admite
las versiones listadas de GPT-4o/GPT-4o-mini, no los GPT-4.1 mini/nano previstos aqui.
Por este motivo, Sergio confirma sustituir On Your Data por **Foundry Agent Service
con Foundry IQ**, la alternativa recomendada por Microsoft. Es una decision de
arquitectura, no una implementacion terminada ni autorizacion para crear recursos o
invocar modelos con coste. El siguiente incremento debe verificar las APIs/SDKs
vigentes, fuentes admitidas, filtros de acceso y formato de citas, e implementar y
probar el adaptador/composicion con dobles locales antes de ejecutar cloud.
No afecta al carril B AI Search implementado en este incremento.

Referencias tecnicas: [Semantic Ranker](https://learn.microsoft.com/en-us/azure/search/semantic-how-to-query-request),
[Bedrock Rerank](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_Rerank.html)
y [RetrieveAndGenerate](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_RetrieveAndGenerate.html).

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
uv run pytest packages/headache_core/tests/test_cloud_retrieval.py packages/headache_core/tests/test_cloud_vector_stores.py packages/headache_core/tests/test_managed_ingestion.py
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

### Informe de estado al 2026-10-08

| Bloque | Estado | Lo que falta |
|---|---|---|
| 4.1, carril B local + FastAPI | Publicado (`78b004a`) | Embeddings/gateway reales y rutas cloud explicitamente conectadas |
| 4.2, carril C LlamaIndex + SQL | Publicado (`e426802`) | Comparacion con modelos reales; integracion del docstore jerarquico persistente es un extra |
| 4.3, SDK AI Search y Bedrock | Revisado, tests offline correctos | Composicion de clientes, permisos, ingesta ACL y validacion real |
| Carril A Azure | Sustitucion por Agent Service + Foundry IQ aprobada | Adaptador, fuentes/citas/ACL, wiring e infraestructura necesarios |
| 4.4, comparativa cloud A/B/C | Pendiente | Ejecucion con confirmacion y medidas de coste, latencia y fidelidad |

Las estrategias rewrite, HyDE, subpreguntas, router y auto-retrieval estan implementadas
y probadas con dobles de `LLMPort`; **no estan activas con modelos reales en la API**.
Hay RAG extractivo local con citas verificables; la generacion gestionada AWS esta
implementada como adaptador separado, no como ruta HTTP conectada. No hay todavia un
RAG gestionado Azure ni una comparativa cloud ejecutada. La fase 4 no se declara cerrada.

Orden de los siguientes incrementos:

1. **Preparacion sin coste:** implementar el nuevo carril A Azure con APIs vigentes y
  dobles locales; preparar factories/rutas explicitas para los adaptadores cloud y
  el gateway de fase 2, manteniendo la composicion local sin llamadas cloud por defecto.
2. **Validacion cloud con confirmacion independiente:** autenticacion, despliegues
  minimos necesarios, schema/metadatos ACL y encoder compatible, ingesta y pruebas
  de estrategias. Usar el mismo corpus y preguntas en A/B/C; registrar diferencias
  de chunking y de funcionalidades, sin asumir soporte HYBRID en S3 Vectors.
3. **Cierre de fase 4:** medir coste, latencia y comportamiento de citas/abstencion;
  documentar resultados y limitaciones. Las evals sistematicas con golden dataset
  y gates de calidad corresponden a fase 5.

Pendientes antes de cerrar fase 4:

- **4.2, local: revisada y publicada** (`e426802`). Lectura SQL, carril C sobre corpus
  comun, comparacion de padres y auto-merging.
- **4.3, adaptadores sin coste: AI Search y Bedrock implementados, probados y revisados.**
  Sustitucion del carril A Azure por Foundry Agent Service/Foundry IQ aprobada el
  2026-10-08; su implementacion sigue pendiente. No se afirma validacion cloud.
- **4.4, cloud con confirmacion:** conectar gateway y embeddings reales, autenticar,
  clasificar/reingerir ACL, montar clientes y rutas cloud explicitamente, ejecutar los
  carriles A/B/C y medir coste, latencia y fidelidad. Se mantienen los
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

**Semantic Ranker equivale a Bedrock Rerank?** Ambos reordenan, pero AI Search trabaja
sobre los primeros resultados de su indice; Bedrock Rerank acepta candidatos de nuestra
aplicacion. No comparten puertos ni escalas de scores necesariamente.

**Por que no implementar On Your Data?** Su retirada anunciada y su lista limitada de
modelos lo hacen una mala dependencia nueva. Validamos el ciclo de vida del producto
y Sergio aprobo el 2026-10-08 sustituirlo por Foundry Agent Service + Foundry IQ.
La decision no sustituye la comprobacion de APIs, seguridad y costes de la alternativa.

**Que garantiza una cita gestionada?** Que el span existe en la respuesta y apunta a
una referencia autorizada retornada por la KB. No prueba que la afirmacion sea fiel a
esa fuente: hacen falta evals y validacion cloud, igual que en los demas carriles.