# Fase 4: retrieval avanzado y servicio RAG

## Estado y alcance

**Fase 4 Hecha\*: todo lo local está implementado y probado (carriles A/B/C, composición
cloud explícita, aprovisionamiento en dry-run e IaC validada). Falta la validación en
cloud, con coste y confirmación, igual que en las fases 2 y 3.**

No se han creado recursos cloud ni invocado modelos. No se ha regenerado ni modificado
el dataset LARGE, ni escrito en PostgreSQL. Los tests usan adaptadores en memoria.

Implementado:

- BM25 real con `rank-bm25`, vector local de hashing e híbrida con RRF.
- Filtros exactos por ciudad, evento, idioma, categoría, tipo y documento.
- ACL aplicada antes de seleccionar candidatos; acceso ausente se deniega.
- Reranking local por cobertura de palabras, identificado como baseline no neuronal.
- Parent-child: buscar chunks y devolver el documento padre, sin duplicarlo.
- Planificación de rewrite, HyDE, subpreguntas, router y auto-retrieval sobre `LLMPort`.
- Respuesta extractiva, selección literal asistida por LLM y validación de citas.
- FastAPI con una ruta por estrategia y resolución de fuentes autorizadas.
- Carril C LlamaIndex: BM25Retriever, VectorIndexRetriever y QueryFusionRetriever sobre
  los mismos chunks, embeddings e IDs del carril B.
- AutoMergingRetriever con docstore en memoria y padres autorizados; ratio configurable.
- Lectura SQL real verificada en ambos motores, sin escrituras.
- Comparación reproducible: `uv run python -m retrieval.compare`.
- Carril B Azure: SDK de AI Search para BM25/vector/híbrida y Semantic Ranker opcional,
  prefilter ACL, fuente por ID y rechazo de scores semánticos ausentes.
- Bedrock Rerank, incluido modelo Cohere mediante ARN explícito: mantiene los candidatos
  originales aunque el proveedor devuelva texto alternativo.
- Carril A AWS: SDK de KB Retrieve y RetrieveAndGenerate, filtros ACL, referencias S3
  y validación estricta de spans/cobertura de citas.

**Por defecto la API no hace llamadas cloud.** Los endpoints LLM devuelven 503 y sus
flujos se prueban con `FakeLLM`, un doble guionizado, no un modelo local. La nube solo
se activa componiéndola de forma explícita (ver "Composición cloud explícita").

## Conceptos y equivalencias

| Concepto | Local, carril B | Azure | AWS | Carril C local |
|---|---|---|---|---|
| Léxico | BM25 con `rank-bm25` | Búsqueda textual de AI Search | Depende del backend de KB; S3 Vectors no ofrece BM25 | BM25Retriever de LlamaIndex |
| Vector | Hashing de fase 3, **no semántico** | Embeddings de Foundry + AI Search | Titan + pgvector/S3 Vectors | VectorIndexRetriever |
| Híbrida | Rankings fusionados por RRF | Híbrida con RRF | KB híbrida solo donde el backend la admite | QueryFusionRetriever |
| Filtros | Igualdad de metadatos + ACL del servidor | Filtros OData | Filtros de Retrieve/KB | Prefiltrado antes de construir los retrievers |
| Reranking | Cobertura léxica, **no neuronal** | Semantic Ranker | Bedrock Rerank, incluido Cohere según región/modelo | Integración de reranker |
| Parent-child | Chunk -> documento padre | Campos de padre + recuperación adicional | Chunking jerárquico de KB | AutoMergingRetriever/docstore |
| RAG gestionado, carril A | Sin equivalente local real | Foundry IQ (`retrieve`) + Foundry Agent Service vía MCP | Retrieve / RetrieveAndGenerate de KB | No sustituye al carril A |

El mismo contrato permite comparar implementaciones, pero **ninguna equivalencia implica
idéntica calidad, seguridad o funcionalidades entre proveedores**. Los adaptadores
cloud se validan con dobles; aún no hay validación real de región, permisos o calidad.

### 4.3: SDKs sin ejecutar cloud

Los clientes se inyectan, nunca se crean durante el arranque local. La API conserva
su composición local: este incremento no añade un interruptor que active coste.
Los tests de AI Search capturan peticiones; los de AWS usan clientes con credenciales
ficticias y `botocore.stub.Stubber`. Se verifica también el enum de Rerank contra el
modelo instalado de botocore, porque Stubber no valida todos los valores de enums.

**Azure, carril B.** `AzureSearchRetrieval` implementa `RetrievalPort`. La híbrida envía
texto y `VectorizedQuery` al mismo índice: AI Search fusiona rankings mediante RRF.
Con `semantic=True`, se envía `semantic_query` y `knowledge-semantic`; el score expuesto
es `@search.rerankerScore`, no el score RRF. No es un `RerankerPort` genérico al que
podamos pasar candidatos ajenos a ese índice. Las consultas vectoriales solicitan
50 vecinos para alimentar al ranker, con `vector_filter_mode=preFilter`; la ventana
semántica solicitada (`candidate_k`) no puede superar 50. La consulta textual sin
semántica no invoca embeddings. `get` recupera el ID codificado y comprueba ACL.
Un vector cloud exige el mismo encoder/configuración que la ingesta: 1024 dimensiones
por sí solas no prueban compatibilidad. Debe garantizarlo la composición de 4.4.

El esquema de ingesta nativa ahora incluye `title`, `acl` filtrable y configuración
semántica. **No se ha actualizado ningún índice cloud.** Antes de probarlo habrá que
actualizar el esquema y reingerir metadatos ACL explícitos, con confirmación. No se
asigna acceso público por defecto en estos adaptadores cloud; una ACL ausente se
deniega. La política didáctica del cargador local no se aplica automáticamente a
los pipelines cloud. El índice gestionado Azure de fase 3 tiene otro esquema y no se
puede enchufar directamente a este adaptador del índice nativo.

**AWS, reranking B.** `BedrockReranker` implementa `RerankerPort`: recibe candidatos
previamente autorizados por `RetrievalService`, envía solo su texto y aplica los índices
devueltos sobre los registros originales. Rechaza índices duplicados/fuera de rango
y scores no finitos. No modifica las fuentes ni acepta el texto retornado por el modelo.
El ARN debe elegirse por disponibilidad regional; el ARN de Cohere del test no demuestra
que haya acceso al modelo en la cuenta. El reranker no decide la ACL.

**AWS, KB A.** `BedrockKnowledgeBase` implementa `ManagedKnowledgePort`, distinto del RAG
extractivo. KB crea el embedding de la consulta y, con RetrieveAndGenerate, también
orquesta la generación internamente: no reutiliza nuestro gateway Python de `LLMPort`.
Es la diferencia deliberada entre carril A gestionado y B/C composables. Los filtros
de metadatos se combinan con ACL del servidor y las referencias se vuelven a comprobar.
Los sidecars de ingesta conservan `acl` y `event_id` explícitos; el corpus ya cargado
sin ACL requiere clasificación y resincronización antes de validarlo en cloud.

La KB de fase 3 usa S3 Vectors: este incremento usa `SEMANTIC` y rechaza `HYBRID`, salvo
que una composición declare un store compatible. No inventamos BM25 sobre S3 Vectors.
El adaptador solo acepta referencias textuales S3 con `document_id`; no soporta otros
conectores ni respuestas multimodales. Si Retrieve o Rerank devuelven `nextToken`, falla
explícitamente: la paginación completa es un seguimiento, no se entrega un ranking
truncado como si estuviera completo.

Las citas gestionadas tienen texto generado, span normalizado `[start, end)` y fuentes
con texto literal, URI y un ID estable derivado de KB/documento/URI/texto. Se aceptan
offsets finales inclusivos o exclusivos solo si coinciden con el texto declarado.
No se reutilizan sesiones entre peticiones, evitando mezclar scopes de usuarios.
Sin citas o tras intervención de guardrail, se abstiene. Spans falsos, fuentes sin ACL,
filtros incumplidos o texto no blanco fuera de los spans provocan rechazo completo.
Esta política de cobertura es conservadora: formatos o marcadores del proveedor
pueden provocar rechazo y deben medirse en 4.4 antes de relajarla. Una fuente citada
es un snapshot retornado por la KB, no una relectura independiente de S3 ni un `href`
de la API local. La validación estructural **no comprueba entailment ni fidelidad**.

**Azure, carril A: sustitución aprobada el 2026-10-08.** La [documentación oficial de On Your Data](https://learn.microsoft.com/en-us/azure/foundry-classic/openai/concepts/use-your-data)
consultada el 2026-10-08 anuncia su retirada el **14 de octubre de 2026**; solo admite
las versiones listadas de GPT-4o/GPT-4o-mini, no los GPT-4.1 mini/nano previstos aquí.
Por este motivo, Sergio confirma sustituir On Your Data por **Foundry Agent Service
con Foundry IQ**, la alternativa recomendada por Microsoft. La aprobación es de
arquitectura: no autoriza crear recursos ni invocar modelos con coste.

**Azure, carril A: implementación offline.** `FoundryIQKnowledge` implementa el mismo
`ManagedKnowledgePort` que Bedrock KB. Foundry IQ es una *knowledge base* de AI Search
que orquesta *knowledge sources*; aquí una knowledge source `searchIndex` reutiliza el
índice nativo del carril B (`knowledge-semantic`, campos `chunk_id/text/metadata_json`).
Verificado contra `azure-search-documents` 12.0 instalado: `KnowledgeBaseRetrievalClient`
envía `POST /knowledgebases('kb')/retrieve?api-version=2026-04-01` (GA).

- **`retrieve` (determinista):** intent semántico, ACL y filtros como `filterAddOn` del
  servidor, referencias con `sourceData`. Cada referencia se vuelve a validar: tipo
  `searchIndex`, `docKey` igual a la clave del `chunk_id`, ACL y filtros. Devuelve el
  `chunk_id` canónico, el mismo `source_id` que los carriles B/C. Una respuesta parcial
  (`206`, `activity[].error`) se rechaza en lugar de entregar resultados incompletos.
- **`answer` (Agent Service):** el agente invoca la herramienta MCP
  `knowledge_base_retrieve` y cita con `【mensaje:resultado†fuente】`. Se aceptan solo
  llamadas completadas a esa herramienta del `server_label` configurado; cada marca
  debe apuntar a un resultado real de esa llamada y no se admite texto sin cita.
  `I don't know` o una respuesta vacía es abstención. Responder sin consultar la KB
  se rechaza. Las marcas se eliminan y los spans se calculan sobre el texto limpio.

**Límite de seguridad importante.** La app no puede añadir filtros a la llamada MCP
que decide el agente, y la salida MCP solo trae los campos de la configuración semántica
(título y texto): ni `chunk_id`, ni `document_id`, ni ACL. Por eso:

- El aislamiento se garantiza en el servidor: **una knowledge source por audiencia** con
  `baseFilter` (p. ej. `acl eq 'public'`), una knowledge base y un agente vinculados a
  ella. `baseFilter` solo existe en la API preview `2026-08-01-preview`; las
  definiciones REST están en `knowledge_source_definition`/`agent_definition`.
- El adaptador exige que el scope pedido coincida con la audiencia del agente y rechaza
  filtros por petición, en lugar de ignorarlos en silencio.
- La referencia de una respuesta del agente tiene `source_id` hash por contenido y
  `document_id` vacio: **no es comparable 1:1 con B/C** y no se recomprueba la ACL por
  referencia. Es una diferencia real del carril gestionado frente a Bedrock y B/C.

La knowledge base se define **sin modelos** y el endpoint MCP usa la API GA: retrieval
minimal y extractivo, sin planificación ni síntesis con LLM dentro de AI Search; el
único LLM es el modelo del agente (`gpt-4.1-mini`). Activar *query planning* o *answer
synthesis* (preview) añade coste y otro punto de generación a evaluar. Si más adelante
se usan ACL de Entra (`x-ms-query-source-authorization`), recordar que, sin token,
las fuentes con permisos devuelven resultados **sin filtrar**.

No se ha instalado `azure-ai-projects`: el agente se consume con la API Responses de
`openai` (ya instalado), apuntando a `{project_endpoint}/openai/v1` con un token de Entra
(`https://ai.azure.com/.default`). Referencias: [Foundry IQ](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/what-is-foundry-iq),
[conectar un agente](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/foundry-iq-connect)
y [retrieve/MCP](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-how-to-retrieve).

Referencias técnicas: [Semantic Ranker](https://learn.microsoft.com/en-us/azure/search/semantic-how-to-query-request),
[Bedrock Rerank](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_Rerank.html)
y [RetrieveAndGenerate](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_RetrieveAndGenerate.html).

### Por que RRF

BM25 y cosine similarity no comparten escala. No sumamos sus scores directamente:

$$
\operatorname{RRF}(d) = \sum_{r \in rankings} \frac{1}{60 + position_r(d)}
$$

Solo contribuyen los rankings en los que aparece el documento; las posiciones empiezan
en 1. Se fusionan IDs de chunk, no títulos, y el desempate es determinista por ID.
Esta fórmula corresponde al carril nativo; el carril C delega en la implementación
RRF del framework. No exigimos igualdad de scores entre carriles. Los duplicados dentro
de un ranking no cuentan dos veces. `candidate_k` controla el
recall previo a la fusión/reranking; `top_k` controla el contexto final.

### Estrategias avanzadas

- **Rewrite:** el LLM devuelve una consulta más precisa; después se busca con híbrida.
- **HyDE:** genera un pasaje hipotético, lo embebe y busca por vector. El pasaje nunca
  se incorpora como fuente ni como respuesta.
- **Subpreguntas:** hasta cuatro consultas; sus resultados se deduplican y fusionan por RRF.
- **Router:** selecciona un índice lógico mediante `doc_type`. En este incremento son
  particiones del mismo corpus, no índices físicos independientes.
- **Auto-retrieval:** infiere filtros de una lista cerrada. No acepta ACL y no puede
  reemplazar filtros explícitos del usuario. Un conflicto se rechaza.
- **Parent-child:** recupera pequeños chunks y amplía al documento padre autorizado.
  Es expansión explícita, disponible en ambos motores.
- **Auto-merge:** en LlamaIndex, sustituye los hijos recuperados por el padre si su
  proporción supera `merge_ratio` (por defecto 0.5). Un hijo de tres no amplía; dos de
  tres si. Los hits vectoriales con score cero se descartan antes de calcular el ratio.
  Puede reducir el número final de hits: no se rellena con fuentes irrelevantes.

El plan del LLM se valida con los structured outputs de fase 2. Puede proponer búsquedas,
pero no decide acceso, reembolsos ni otras reglas de negocio.

## Ejecutar en local

Desde la raíz del monorepo:

```powershell
uv sync --all-packages --locked
uv run uvicorn retrieval.app:create_app --factory --host 127.0.0.1 --port 8010
```

Swagger: http://127.0.0.1:8010/docs. Diagnóstico: http://127.0.0.1:8010/health.

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
el número efectivo de documentos/chunks y los baselines utilizados. Esta muestra
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
| `/search/bm25` | Ranking léxico |
| `/search/vector` | Ranking por cosine de hashing |
| `/search/hybrid` | BM25 + vector + RRF |
| `/search/rerank` | Híbrida + cobertura léxica |
| `/search/parent-child` | Híbrida + expansión de padres |
| `/search/auto-merge` | Carril C: vector + AutoMergingRetriever; nativo: 503 explícito |
| `/search/rewrite` | 503 sin LLM configurado |
| `/search/hyde` | 503 sin LLM configurado |
| `/search/subquestions` | 503 sin LLM configurado |
| `/search/router` | 503 sin LLM configurado |
| `/search/auto` | 503 sin LLM configurado |
| `/rag/extractive` | Texto literal con citas; admite `strategy` |
| `/rag/llm-extractive` | 503 sin LLM, salvo abstención sin candidatos |

Cada cita incluye `source_id`, `document_id`, `title`, `quote` y `href`. Un GET a
`href` devuelve el texto del que procede. Una fuente inexistente o no autorizada
devuelve el mismo 404. No hay acceso al sistema de ficheros desde esta ruta.

### Reutilizar PostgreSQL de fase 3

Con PostgreSQL arrancado y migrado, y el índice nativo `knowledge` ya cargado con hashing:

```powershell
$env:RETRIEVAL__SOURCE = "postgres"
$env:RETRIEVAL__INDEX_NAME = "knowledge"
$env:RETRIEVAL__MAX_DOCUMENTS = "500"
uv run uvicorn retrieval.app:create_app --factory --host 127.0.0.1 --port 8011
```

Esto toma una **instantánea en memoria al arrancar**, solo con SELECT. El límite se
aplica a documentos completos, no a chunks aislados. Cambios posteriores de la ingesta
requieren reiniciar el servicio. BM25 y vectores se consultan en memoria, **no con HNSW
en PostgreSQL**. Los padres SQL se reconstruyen concatenando los chunks, conservando
sus solapes; no son una copia byte a byte del Markdown original.

Se verifica que los embeddings coincidan con hashing del título + texto de fase 3.
Un índice Titan/OpenAI de 1024 dimensiones no es compatible con consultas de hashing:
el arranque lo rechaza. Ambos motores leen la misma tabla nativa `knowledge_chunks`.
Las tablas propias de ingesta de LlamaIndex no se leen en este incremento: el carril C
reconstruye nodos y un docstore en memoria a partir de la instantánea común.

La lectura real se verificó el 2026-10-08 con 134 documentos y 134 chunks existentes;
ambos motores respondieron a `refund` filtrando idioma y categoría, con fuente
`policy-candlelit-en`. PostgreSQL se arrancó conservando su volumen, sin migraciones,
cargas ni borrados. Hay un test marcado `integration` de solo lectura: funciona también
contra un esquema migrado sin datos; en ese caso valida el camino SQL vacio, no calidad.

```powershell
uv run pytest services/retrieval/tests/test_retrieval_api.py -m integration
```

### Comparación reproducible de carriles B/C

```powershell
uv run python -m retrieval.compare "reembolso gastos de gestion" --filter language=es --filter category=candlelit --top-k 3
uv run python -m retrieval.compare "refund" --source postgres --filter language=en --filter category=candlelit
```

El comando no genera datos, escribe ficheros ni invoca modelos. Carga una sola instantánea
y la comparte entre motores, con el mismo límite y chunking; no hace dos lecturas SQL
entre las que una ingesta concurrente pudiera cambiar los datos. Informa de IDs
resolubles y tiempo de consulta.
La versión local del carril C construye los retrievers/índices autorizados en cada
consulta, fuera del event loop con `asyncio.to_thread`; ese coste está incluido en sus
tiempos. No es un benchmark de servicios persistentes ni de calidad semántica.

BM25 nativo usa BM25Plus (`rank-bm25`); LlamaIndex usa BM25S y su política predeterminada
de stopwords inglesas, sin stemming. Normalizamos mayúsculas y acentos antes de BM25
en ambos carriles, pero los scores y el desempate pueden diferir por las implementaciones.
El vector reutiliza exactamente los embeddings de cada chunk, sin reembeber metadatos.

En el corpus actual hay un chunk por documento: ambas estrategias de padres devuelven
el mismo padre. Los tests con tres hijos distinguen expansión explícita y auto-merging.
La jerarquía reconstituida tiene un nivel padre/hijos, no los tres niveles del parser
jerárquico de ingesta de fase 3; integrar su docstore persistente sigue siendo un extra.

## Composición cloud explícita

`retrieval/cloud.py` es la raíz de composición cloud. Nada se crea por defecto; cada
opción distinta del valor por defecto **tiene coste** al consultar. Construir los
clientes no llama a la red; si falta configuración, el arranque falla en vez de degradar
en silencio a local, y lo ya construido se cierra. Los clientes se cierran al apagar.

| Variable | Valores | Efecto |
|---|---|---|
| `RETRIEVAL__BACKEND` | `local` \| `azure_search` | Corpus en memoria o carril B en AI Search (`AZURE_SEARCH__*`) |
| `RETRIEVAL__EMBEDDINGS` | `hashing` \| `azure` \| `bedrock` | Encoder de consulta; debe ser el de la ingesta. `local` exige `hashing` |
| `RETRIEVAL__SEMANTIC_RANKER` | `false` \| `true` | Semantic Ranker de AI Search (solo con `azure_search`) |
| `RETRIEVAL__RERANKER` | `token-coverage` \| `bedrock` | `/search/rerank` con Bedrock Rerank (`BEDROCK__RERANK_MODEL_ARN`) |
| `RETRIEVAL__LLM` | `none` \| `gateway` | Gateway de fase 2: rewrite, HyDE, subpreguntas, router, auto y `llm-extractive` |
| `RETRIEVAL__MANAGED` | `none` \| `bedrock_kb` \| `foundry_iq` | Carril A en `POST /managed/retrieve` y `POST /managed/answer` |

Las combinaciones incoherentes fallan al validar la configuración: AI Search con hashing
(no hay un encoder compatible) o Semantic Ranker sin AI Search. Con `backend=local`, un
reranker o un LLM cloud se combinan con el corpus local. `/health` informa de cada pieza
activa y `cloud_calls`. Las rutas `/managed/*` usan siempre el scope público y devuelven
503 si no hay carril gestionado; en Foundry IQ, `answer` exige además
`FOUNDRY_IQ__PROJECT_ENDPOINT`.

**Aprovisionamiento de Foundry IQ.** `scripts/provision_foundry_iq.py` imprime en dry-run
la knowledge source (con `baseFilter` de la audiencia), la knowledge base sin modelos, la
conexión `RemoteTool` y el agente. Con `--apply` (nube, con confirmación) crea la knowledge
source por la API preview y **relee el `baseFilter` y falla si no se conservó**: enviarla
por la API GA lo descartaría y el agente vería todo el índice. Después crea la knowledge
base (GA), la conexión por ARM y el agente.

**Terraform.** `foundry_project_principal_id` (opcional) asigna *Search Index Data
Reader* a la identidad del proyecto de Foundry, que usa el agente para invocar el MCP de
la knowledge base. Con `search_sku = "basic"` se activa el plan gratuito del Semantic
Ranker (1000 consultas/mes), necesario para el ranker del carril B y para Foundry IQ. El
proyecto de Foundry y el despliegue del modelo se crean en el portal, como en fase 2.
Validado sin coste con `fmt -check`, `init -backend=false` y `validate`.

## Seguridad y límites de la respuesta

El cargador asigna acceso `public` a `policy`, `faq`, `venue_guide` y `event_sheet`, e
`internal` a `handbook` y tipos desconocidos. Una ACL explícita se conserva. Esta es
una **política didáctica de clasificación**: en producción, el propietario del dato
debe clasificarlo y revisar si una ficha de evento contiene información interna.

El puerto deniega metadatos sin ACL. La API local siempre usa el scope público; no
confía en cabeceras ni en el cuerpo para ampliar permisos. El servicio vuelve a
comprobar acceso y filtros antes de entregar candidatos y padres. No se ha implementado
autenticación Entra/Cognito ni acceso interno por HTTP.

La respuesta local copia evidencia literal, con un máximo de 4000 caracteres por
fuente. Sin candidatos se abstiene. Con LLM, solo se permiten selecciones de citas
literales de los candidatos autorizados; no IDs externos ni texto inventado. Un fallo
de cita devuelve 502, nunca una respuesta parcialmente valida.

**Una cita literal prueba procedencia, no relevancia, vigencia ni verdad del contenido.**
La selección puede ser equivocada; hashing puede tener colisiones. No afirmamos que el
sistema recupere siempre el fragmento correcto. Tampoco generamos todavía una síntesis
libre: para permitirla hacen falta comprobaciones de fidelidad y evals.

Este backend es para aprender y validar contratos, no para 300 M de usuarios: carga
una muestra en RAM, recalcula BM25 sobre candidatos autorizados y hace búsqueda vectorial
exacta. En carril C, la construcción y retrieval se ejecutan fuera del event loop; en
carril B el trabajo CPU sigue siendo directo. Persistencia lexical, cachés, límites de contexto
por tokens, autenticación y escala se deben resolver antes de producción.

## Verificación y siguientes incrementos

```powershell
uv run pytest packages/headache_core/tests/test_retrieval.py services/retrieval/tests/test_retrieval_api.py
uv run pytest packages/headache_core/tests/test_cloud_retrieval.py packages/headache_core/tests/test_cloud_vector_stores.py packages/headache_core/tests/test_managed_ingestion.py
uv run pytest packages/headache_core/tests/test_foundry_iq.py services/retrieval/tests/test_retrieval_cloud.py
uv run python scripts/provision_foundry_iq.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

Cobertura local: RRF por rango y deduplicación; filtros previos a top-k; ACL y fuentes
privadas; estrategias con `FakeLLM`; parent-child; citas literales, citas inventadas y
abstención; contrato HTTP y límites del cargador sin modificar el corpus. Los mismos
tests se ejecutan con ambos motores. Se verifican también el umbral de auto-merging,
los padres privados, hits con score cero y el bloqueo de los modelos por defecto.

### Informe de estado al 2026-10-08

| Bloque | Estado | Lo que falta |
|---|---|---|
| 4.1, carril B local + FastAPI | Publicado (`78b004a`) | Nada local |
| 4.2, carril C LlamaIndex + SQL | Publicado (`e426802`) | Extra: docstore jerárquico persistente de fase 3 |
| 4.3, SDK AI Search y Bedrock | Publicado (`c1cb588`) | Validación real |
| Carril A Azure (Foundry IQ + Agent Service) | Publicado (`a9b9fc1`) | Crear KS/KB/agente y validación real |
| Composición cloud, rutas `/managed/*`, aprovisionamiento e IaC | Implementado y probado en local | Validación real |
| Comparativa cloud A/B/C | Azure hecha (18-10-2026, ver resultados abajo) | AWS: ver [pendientes con coste](pendientes-cloud.md) |

Las estrategias rewrite, HyDE, subpreguntas, router y auto-retrieval están implementadas,
probadas con dobles de `LLMPort` y conectadas al gateway con `RETRIEVAL__LLM=gateway`;
**no se han ejecutado con modelos reales**. La generación gestionada de AWS y de Azure
está expuesta en `/managed/answer`. No hay comparativa cloud ejecutada.

Pendientes en la nube (aplazados, como en las fases 2 y 3; todos con coste y confirmación):

1. Clasificar el corpus con `acl` explícita y reingerir en AI Search y Bedrock KB.
2. AI Search `basic`, proyecto de Foundry, rol de su identidad y
  `provision_foundry_iq.py --apply`; habilitar Cohere Rerank y el modelo de generación
  de Bedrock KB.
3. Comparar A/B/C con el mismo corpus y preguntas: coste, latencia, citas y abstención,
  sin asumir soporte HYBRID en S3 Vectors. Documentar los resultados aquí.

Las evals sistemáticas con golden dataset, hit rate/MRR y gates de calidad son la fase 5;
los tests actuales validan contratos, no la calidad del retrieval.

## Resultados en Azure (18-10-2026)

Corpus de evals clasificado con ACL explícita (114 chunks `public` y 17 `internal` en el índice
`knowledge` del carril B), embeddings `text-embedding-3-small` (1024), AI Search Basic en
`francecentral` con Semantic Ranker (plan gratuito) y Foundry IQ + Agent Service en el proyecto
`headache`. Golden dataset completo: 144 preguntas (114 con respuesta, 30 sin ella o internas).

**Carriles B y C frente a la nube** (`python -m rag_evals.retrieval_eval --engine azure
--engine azure-semantic`, informe en `evals/reports/azure/`):

| Carril / estrategia | hit@1 | MRR | nDCG@5 | recall@5 | Fugas ACL | Latencia p50 / p95 |
|---|---|---|---|---|---|---|
| Local nativo · vector (`hashing`) | 0,947 | 0,974 | 0,897 | 0,759 | 0 | 0 ms / 0 ms |
| Local nativo · híbrida (`hashing`) | 0,912 | 0,953 | 0,875 | 0,741 | 0 | 2 ms / 2 ms |
| Azure · BM25 | 0,983 | 0,990 | 0,904 | 0,746 | 0 | 54 ms / 92 ms |
| Azure · vector | **1,000** | **1,000** | 0,979 | 0,952 | 0 | 173 ms / 257 ms |
| Azure · híbrida (RRF) | **1,000** | **1,000** | 0,922 | 0,789 | 0 | 191 ms / 262 ms |
| Azure · híbrida + Semantic Ranker | 0,991 | 0,993 | **0,980** | **0,974** | 0 | 228 ms / 418 ms |

- Los embeddings reales suben el recall@5 del vector de 0,76 a 0,95: las cifras locales con
  `hashing` validaban el arnés, no la calidad semántica (como avisaba la fase 5).
- **La híbrida RRF rinde peor que el vector solo** con embeddings buenos: la parte BM25 diluye
  el orden (nDCG 0,922 frente a 0,979). El Semantic Ranker lo recupera y da el mejor recall
  (0,974) a cambio de ~40 ms de p50. Conclusión para Headache: vector, o híbrida **con** ranker;
  no híbrida a secas.
- 0 fugas de ACL en todas las estrategias: la ACL viaja como filtro del servidor y se vuelve a
  comprobar en la aplicación.

**Carril A · Foundry IQ** (`retrieve` en las 144 preguntas; `answer` con el agente en una
muestra de 15 sin filtros: 10 con respuesta y 5 sin ella):

| Medida | Resultado |
|---|---|
| `retrieve` (determinista) | hit@5 **1,000** · MRR 0,993 · 0 documentos prohibidos · p50 100 ms / p95 151 ms |
| `answer` · con respuesta | **10/10** respondidas con cita al documento correcto; el literal esperado aparece en 8/10 (las otras 2 parafrasean) |
| `answer` · sin respuesta | **4/5** abstenciones correctas; 1 sobre-respuesta (ver hallazgos) |
| `answer` · latencia | p50 2,6 s / p95 4,3 s (agente + MCP + `gpt-4.1-mini`) |
| Coste | ~0,02 $ los 15 `answer`; `retrieve` sin LLM (Semantic Ranker en plan gratuito) |

Las referencias del agente ya traen `chunk_id` y `document_id` reales: el carril A **sí es
comparable 1:1** con B y C, y la ACL de cada documento citado se vuelve a comprobar.

### Hallazgos en la nube

| Hallazgo | Causa | Solución |
|---|---|---|
| El agente no podía enumerar herramientas (400) | El endpoint MCP de la knowledge base **solo existe en versiones preview**; con la GA (`2026-04-01`, válida para gestionar y para `retrieve`) responde "api-versión does not exist" | `MCP_API_VERSION = PREVIEW_API_VERSION` y test de regresión |
| La herramienta fallaba con "A Knowledge Base model must be specified" | En preview, el esfuerzo de razonamiento por defecto exige un modelo en la KB; la nuestra no tiene (retrieval extractivo, sin LLM) | KB creada en preview con `retrievalReasoningEffort: minimal` y `outputMode: extractiveData`, verificados tras el `PUT` |
| El formato real del agente no era el supuesto | Agent Service devuelve `{"documents": [{"id", "content"}]}` con los `sourceDataFields` en `content`; las marcas `【4:0†source】` no se pueden mapear por `message_idx`, pero cada una trae una anotación `url_citation` con la clave del chunk | El parser mapea cada cita por su anotación a un documento devuelto por la KB, comprueba su ACL (sin ACL se deniega) y falla cerrado ante cualquier otra cosa |
| Abstenciones rechazadas | `"I don't know."` con punto; en español, el modelo no repetía la frase pactada | Abstención tolerante a la puntuación; si la KB no devolvió nada y no hay citas, se descarta el texto del modelo y cuenta como abstención |
| Hasta 34 documentos por llamada y citas de relleno | La KB no tenía límite de salida | `retrieveDefaults.maxOutputDocuments = 5`, coherente con el `top_k` de B y C |
| `POST /agents` falla con 409 al reaprovisionar | Foundry versiona los agentes | `publish_agent`: crea o publica una versión nueva (también en `provision_foundry_agent.py`) |
| Sobre-respuesta en una pregunta interna (`acl-schedule-change-en`) | El agente respondió con políticas públicas cercanas (cambios de fecha) en lugar de abstenerse; cita bien, pero no responde a lo preguntado | Ningún validador estructural lo detecta: necesita un juez de fidelidad (pendiente de la fase 5) |

### Estrategias con LLM sobre AI Search (18-10-2026)

`python -m rag_evals.retrieval_eval --engine azure --engine azure-semantic --budget-usd 0.50`
(informe en `evals/reports/azure/retrieval-llm.md`). El planificador va por el gateway (router →
`gpt-4.1-nano`): 1.444 llamadas, **0,10 $** y ~23 min con 144 preguntas × 5 estrategias × 2
motores. Sobre la híbrida + Semantic Ranker (la mejor sin LLM):

| Estrategia | hit@1 | nDCG@5 | recall@5 | Planes fallidos | p50 |
|---|---:|---:|---:|---:|---:|
| Híbrida + Semantic Ranker (sin LLM) | 0,991 | **0,980** | **0,974** | — | **223 ms** |
| rewrite | 0,991 | 0,981 | 0,969 | 2 | 897 ms |
| HyDE | 0,982 | 0,975 | 0,974 | 2 | 729 ms |
| subpreguntas | 0,912 | 0,902 | 0,925 | 2 | 1.192 ms |
| router (`doc_type`) | 0,947 | 0,938 | 0,934 | 4 | 734 ms |
| auto (filtros inferidos) | 0,781 | 0,757 | 0,750 | 2 | 828 ms |

- **Ninguna estrategia con LLM mejora a la búsqueda sin LLM**, y todas multiplican la latencia
  por 3–5. Con preguntas bien formadas (las del golden dataset lo son) no hay nada que reescribir.
  Pueden compensar con preguntas reales ambiguas o largas; habría que medirlo con tráfico real
  antes de pagarlas. Decisión para Headache: sin planificación con LLM por defecto.
- **auto es peligrosa**: inferir filtros convierte un error del modelo en 0 resultados (un filtro
  equivocado excluye la respuesta correcta). Pierde sobre todo en fichas de evento (ciudad o
  evento escritos de otra forma).
- Los "planes fallidos" son las preguntas de inyección de prompt: **Prompt Shields** bloquea al
  planificador y la pregunta se queda sin resultados (lo seguro).

Hallazgos (con su test de regresión):

| Hallazgo | Arreglo |
|---|---|
| Con un esquema de "lista de 1 a 4 consultas", `nano` devolvía varias en rewrite, HyDE, router y auto (10–35 % de las preguntas), y el código las rechazaba: la primera medición daba hit@5 de 0,61 en router | Esquema propio de una sola consulta (`SingleQueryPlan`) para esas estrategias: el modo *strict* hace imposible devolver varias. **El esquema es el guardarraíl**, no la validación posterior |
| auto copiaba la etiqueta visible («Gastronomía y bebidas») en vez del código (`food_drink`) y filtraba por un valor inexistente | `category` y `doc_type` como enumeraciones del dominio en el esquema; el idioma de la pregunta deja de ser un filtro. auto sube de 0,69 a 0,83 de hit@5 |

Pendiente: AWS (Bedrock KB y Bedrock Rerank).

## Checkpoint de entrevista

Las preguntas técnicas y de negocio/stakeholders de esta fase, con sus respuestas modelo, están en
[preguntas de entrevista · fase 4](preguntas-entrevista.md#fase-4).
