# Fase 3 · Comparativa de los tres carriles de ingesta

Indexación del mismo corpus de conocimiento de Headache (políticas, FAQs, guías de recintos, fichas de
eventos y guía del centro de ayuda) en tres carriles y dos nubes, con la misma cola de eventos delante.

> Estado: implementación, IaC y tests completos y verificados en local (sin coste). Las cifras de
> latencia y coste reales en la nube están **pendientes de validación** (ver el final).

## 1. Qué se compara

| | Carril A · Gestionado | Carril B · SDK nativo | Carril C · Framework |
|---|---|---|---|
| **Quién trocea y embebe** | El servicio | Nuestro código (`DocumentIndexer`) | LlamaIndex (`IngestionPipeline`) |
| **Azure** | Indexer + skillset de AI Search (vectorización integrada) | `azure-search-documents` + Azure OpenAI | LlamaIndex + `PGVectorStore` (*) |
| **AWS** | Bedrock Knowledge Base (S3 → S3 Vectors) | `boto3` S3 Vectors + Titan v2 | LlamaIndex + `PGVectorStore` (*) |
| **Local (sin coste)** | — (no hay emulador) | pgvector + embeddings deterministas | pgvector + embeddings deterministas |
| **Código** | [azure_search_managed.py](../packages/headache_core/src/headache_core/adapters/azure_search_managed.py), [bedrock_kb.py](../packages/headache_core/src/headache_core/adapters/bedrock_kb.py) | [indexer.py](../packages/headache_core/src/headache_core/application/ingestion/indexer.py), [azure_search.py](../packages/headache_core/src/headache_core/adapters/azure_search.py), [s3_vectors.py](../packages/headache_core/src/headache_core/adapters/s3_vectors.py) | [llamaindex.py](../packages/headache_core/src/headache_core/adapters/llamaindex.py) |
| **IaC** | [aws/lane_a.tf](../infra/terraform/aws/lane_a.tf), [azure/lane_a.tf](../infra/terraform/azure/lane_a.tf) | [aws/main.tf](../infra/terraform/aws/main.tf), [azure/main.tf](../infra/terraform/azure/main.tf) | Esquema `llamaindex` en PostgreSQL |

(*) En la nube, el carril C usaría Aurora PostgreSQL / Azure Database for PostgreSQL (fases 13-14).
LlamaIndex también tiene integraciones con AI Search y OpenSearch; se eligió pgvector para comparar
el framework, no el almacén.

Delante de los carriles B y C hay la **misma cola** con tres brokers intercambiables (RabbitMQ, SQS,
Service Bus) y un test de contrato común. El carril A no la necesita: el servicio sondea el origen
(indexer programado) o se sincroniza bajo demanda (ingestion job).

## 2. Resumen

| Dimensión | A · Gestionado | B · SDK nativo | C · Framework |
|---|---|---|---|
| **Coste fijo** | Azure: AI Search **Basic** si se quiere identidad gestionada (~70 €/mes); Free solo con claves. AWS: ~0 | ~0 en ambas (AI Search Free, S3 Vectors por uso) | El de PostgreSQL (ya existente para el negocio) |
| **Coste variable** | Embeddings + operaciones; en Free, 20 documentos enriquecidos/día por indexer | Embeddings + escrituras | Embeddings + escrituras |
| **Control** | Bajo: troceo por caracteres o tokens configurables, sin código propio en la cadena | Total: chunking, metadatos, idempotencia, orden de escritura | Alto, dentro de lo que exponen los componentes |
| **Tiempo de implantación** | Horas: IaC + subir documentos | Días: cola, worker, ledger, adaptadores, tests | Horas-días: pipeline + stores; el tiempo se va en los bordes |
| **Lock-in** | Alto: definición y semántica propias de cada nube | Bajo: puertos propios, adaptadores pequeños | Medio: lock-in al framework y a su esquema |
| **Incremental y borrados** | Nativo (marca de agua y soft delete / sincronización con `DELETE`) | Propio: hash de contenido + ledger + borrado de chunks obsoletos | `DocstoreStrategy.UPSERTS` (hash del documento) |
| **Huecos durante un cambio** | Gestionado por el servicio | Ninguno: primero upsert y después borrado de lo obsoleto | Sí: borra los nodos antiguos antes de insertar los nuevos |
| **Estrategias de troceo** | Páginas/tokens fijos (+ semántico/jerárquico en Bedrock) | Fijo y por frases (propios) | Fijo, frases, semántico, jerárquico |
| **Observabilidad** | Estado del indexer / ingestion job | Logs propios, DLQ, métricas a medida | Igual que B + callbacks del framework |

## 3. Detalle por dimensión

### Coste

- Los **embeddings no son el problema** en este corpus: `text-embedding-3-small` y Titan v2 rondan
  0,02 $ por millón de tokens, y el corpus completo son del orden de 10⁵ tokens (céntimos).
  Lo que pesa es la **capacidad fija** y las **reindexaciones completas** (cambiar de chunker o de
  modelo obliga a reembeber todo; por eso `pipeline_version` vive en el ledger).
- **Deduplicación = ahorro**: los carriles B y C no vuelven a embeber un documento sin cambios
  (comprobado: republicar el corpus produce `unchanged` sin llamadas al modelo).
- **AI Search Free no admite identidad gestionada**: el carril A en Azure obliga a elegir entre
  ~0 € con claves (cuenta de Storage con claves habilitadas y API key de Azure OpenAI en el skillset)
  o Basic (~70 €/mes) sin secretos. La variable `search_sku` de Terraform activa uno u otro modo.
- **S3 Vectors frente a OpenSearch Serverless**: sin capacidad mínima reservada; se paga
  almacenamiento y consultas. A cambio no hay BM25 nativo (la búsqueda híbrida en AWS exigirá
  OpenSearch o un BM25 aparte en la fase 4).

### Control

- El carril B decide **cada detalle**: chunking por frases sin cortar títulos, cabecera contextual con
  el título, metadatos filtrables, ids deterministas, orden de escritura seguro ante fallos.
- El carril A trocea por **caracteres o tokens** con solape. Bedrock admite troceo semántico y
  jerárquico; AI Search necesita una custom skill (una Azure Function) para cualquier lógica propia.
- El carril C da muchas estrategias por configuración, pero sus decisiones por defecto hay que
  revisarlas. Ejemplo: por defecto **embebe todos los metadatos** (ids y hashes incluidos), lo que
  ensucia los embeddings. Aquí se excluyen explícitamente.

### Tiempo de implantación

- **A**: la vía más rápida a un RAG funcional; la IaC de este repo (KB + data source + rol) son
  ~150 líneas de Terraform. Las sorpresas están en los límites del tier y en los permisos.
- **B**: lo más lento de construir (cola, worker, ledger, tres brokers, tres stores), pero cada pieza
  es pequeña, testeable con adaptadores en memoria y verificada con tests de contrato.
- **C**: arranque rápido; el tiempo se va en los bordes del framework (ver hallazgos).

### Lock-in

- **A**: la definición (skillset, index projections, ingestion job) no se puede llevar de una nube a otra.
  Migrar significa rehacer la ingesta.
- **B**: el caso de uso no conoce ninguna nube. Cambiar de AI Search a S3 Vectors es cambiar
  `INDEXER__VECTOR_STORE`; cambiar de RabbitMQ a SQS, `INDEXER__BROKER`.
- **C**: portable entre nubes, pero atado al framework: su esquema de tablas (esquema `llamaindex`,
  fuera de Alembic) y sus dependencias (obligó a bajar SQLAlchemy de 2.1 a 2.0).

## 4. Hallazgos durante la implementación (verificados)

| Hallazgo | Carril | Cómo se detectó | Resolución |
|---|---|---|---|
| RabbitMQ 4.3: `nack(requeue)` ya no cuenta para `x-delivery-limit` (bucle infinito) | B/C (cola) | Test de integración colgado | `reject(requeue=True)` + *delayed retry* del broker |
| Service Bus: `delivery_count` vale 0 en la primera entrega | B/C (cola) | Test de contrato | Entrega = `delivery_count + 1` |
| LlamaIndex 0.14: `HierarchicalNodeParser` no trocea por la ruta asíncrona | C | Test unitario (1 nodo en vez de 37) | Componente propio que usa la ruta síncrona |
| `PostgresDocumentStore.from_params` roto en la combinación de versiones | C | Lectura del código fuente | Construcción explícita con `PostgresKVStore` |
| Chunker nativo dejaba títulos huérfanos (29 % de cortes en guías largas) | B | `chunking_report.py` | Los títulos viajan con la frase siguiente (0 %) |
| Troceo semántico sin tope de tamaño (chunk de 562 tokens) | C | `chunking_report.py` | `SentenceSplitter` encadenado como tope |
| AI Search Free no admite identidad gestionada | A | Documentación oficial | Modo dual (claves en Free, MI en Basic) |
| LocalStack exige cuenta desde marzo de 2026 | Local | Documentación oficial | ElasticMQ (Apache-2.0) para SQS |

Calidad del troceo (guías largas, sin coste; `scripts/chunking_report.py --doc-type handbook`):

| Estrategia | Chunks | Tokens (mediana) | Máx. | Cortan frases |
|---|---|---|---|---|
| B nativo · fijo (80 palabras) | 16 | 109 | 143 | 75 % |
| B nativo · frases (80 palabras) | 17 | 98 | 133 | 0 % |
| C LlamaIndex · fijo (128 tokens) | 16 | 115 | 120 | 81 % |
| C LlamaIndex · frases (128 tokens) | 16 | 98 | 115 | 0 % |
| C LlamaIndex · semántico (con tope) | 11 | 116 | 241 | 0 % |
| C LlamaIndex · jerárquico (hojas) | 18 | 92 | 116 | 0 % |

El impacto real en la calidad de la recuperación (hit rate, MRR) se mide en la **fase 5** con el
mismo corpus y un golden dataset.

## 5. Recomendación para Headache

- **Conocimiento interno** (operaciones, soporte, partners): **carril A en AWS** (Bedrock Knowledge
  Base sobre S3). Producción en AWS, menor tiempo hasta valor y sin servidores que operar; el volumen
  es pequeño y el lock-in, aceptable.
- **Producto público de alta escala** (búsqueda y asistente para unos 300 M de usuarios): **carril B**.
  Control fino del troceo y los metadatos (idiomas, ciudades, categorías), coste predecible, ingesta
  incremental idempotente y sin huecos, y portabilidad. En AWS: SQS + S3 Vectors, o OpenSearch si la
  búsqueda híbrida lo exige (decisión de la fase 4 con datos).
- **Carril C** como **laboratorio**: probar estrategias de troceo y retrieval avanzadas
  (jerárquico, auto-merging) antes de llevarlas al carril B o descartarlas.

## 6. Resultados en Azure (18-10-2026)

Corpus congelado de evals (116 documentos) **clasificado con ACL explícita**
(`scripts/classify_corpus.py`: 114 `public`, 2 `internal`). Foundry y Service Bus en
`swedencentral`; AI Search **Basic en `francecentral`** (Sweden, West y North Europe no admiten
servicios nuevos de AI Search por alta demanda). Entra ID en todo, sin claves.

| Medida | Carril A · Gestionado (indexer + skillset) | Carril B · SDK nativo (Service Bus + worker) |
|---|---|---|
| Ingesta completa | 116 docs → **118 chunks** (páginas de 2 000 caracteres); 10 s en el servicio, 36 s con la subida de blobs | 116 docs → **131 chunks** (frases, 80 palabras); 7,6 s en el worker, 25 s de extremo a extremo; 0 reintentos, 0 en DLQ |
| Republicar sin cambios | **0** procesados tras el arreglo (antes, 116 de 116: ver hallazgos); 1 documento cambiado = 1 procesado | **0** embeddings: 116 `unchanged` por el hash del ledger |
| 1 documento modificado | — | **1** embedding; solo se reindexa ese documento |
| Latencia de consulta (p50 / p95) | 165 ms / 694 ms (la p95 es la primera, en frío); AI Search vectoriza la consulta dentro de Azure | 205–213 ms / 227–270 ms; el cliente embebe en Suecia y busca en Francia |
| Coste de la ingesta | Céntimos de céntimo en embeddings (~12 K tokens); el indexer va incluido en Basic | Igual; Service Bus por operación (~0) |
| Coste fijo | AI Search Basic ~0,10 $/h mientras existe (efímero, `search_enabled`) | El mismo servicio de AI Search |

**Calidad (muestra de 5 preguntas):** ambos carriles devuelven el documento esperado en 4 de 5.
En "¿es accesible el recinto en silla de ruedas?", A devuelve una guía de recinto (pública) y B el
manual interno (`handbook`): la búsqueda vectorial cruda no aplica ACL. Por eso el servicio de
retrieval de la fase 4 filtra por ACL en el servidor antes del top-k. La comparación rigurosa se
hace con el golden dataset (fase 5).

### Hallazgos en la nube

| Hallazgo | Causa | Solución |
|---|---|---|
| Carril A: el indexer terminaba `success` con **0 chunks** (232 avisos) | Con `parsingMode=markdown` y `oneToOne`, el texto queda en `/document/document_content`, no en `/document/content`; el SplitSkill no recibía nada | Entrada del SplitSkill corregida (`MARKDOWN_TEXT_SOURCE`) y test de regresión. Un indexer en verde no prueba nada: hay que contar los documentos del índice |
| Carril A: el indexer reprocesaba todos los blobs aunque no cambiaran | Resuelto (18-10-2026). No era el indexer: era **nuestro script**, que resubía los 116 blobs con `overwrite=True`; resubir cambia la fecha de modificación, que es lo que usa el indexer para detectar cambios. Medido: relanzar el indexer sin subir nada procesa 0. Además, la marca de agua va ~30 s por detrás del inicio de cada ejecución, así que un blob subido justo antes se procesa dos veces | `managed_ingestion.py` solo sube si cambia el MD5 del contenido o los metadatos (`needs_upload`). Validado: sin cambios = 0 subidos y 0 procesados; 1 cambio = 1 y 1. Igual que el ledger del carril B: la idempotencia es responsabilidad de quien publica |
| Avisos `Unclosed client session` en el indexer | Los adaptadores de AI Search y Service Bus no cerraban la credencial asíncrona que creaban, y la composición no cerraba el almacén | Cada adaptador cierra lo que crea y `open_pipeline` cierra el almacén de la nube |
| 58 avisos de proyección en el carril A | Documentos sin `category` o `city` en los metadatos del blob | Benignos: el campo queda vacío |
| AI Search no se puede crear en `swedencentral` | `InsufficientResourcesAvailable`: región de alta demanda | `search_location = "francecentral"`; Foundry y el resto siguen en Suecia |

Pendiente: AWS (Bedrock KB + S3 Vectors y SQS) y coste real con Cost Management tras unos días.

## 7. Checkpoint de entrevista

Las preguntas técnicas y de negocio/stakeholders de esta fase, con sus respuestas modelo, están en
[preguntas de entrevista · fase 3](preguntas-entrevista.md#fase-3).
