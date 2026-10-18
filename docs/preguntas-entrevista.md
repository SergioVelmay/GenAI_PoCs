# Preguntas de entrevista · AI Technical Lead en Headache

Recopilatorio único para preparar la entrevista: preguntas técnicas y de negocio/stakeholders de
cada fase, con una respuesta modelo anclada en lo que se ha construido en este repo. Los docs de
fase explican el detalle; aquí está lo que hay que saber contar.

**El rol:** 50 % liderazgo técnico (arquitectura, decisiones, calidad, equipo) y 50 % negocio y
gestión de stakeholders (impacto, coste, riesgo, priorización, comunicación). Producción en **AWS**.

## Índice

- [Cómo responder](#como-responder) · [Chuleta Azure → AWS](#chuleta) · [Transversales: liderazgo y stakeholders](#transversales)
- Fases: [0](#fase-0) · [1](#fase-1) · [2](#fase-2) · [3](#fase-3) · [4](#fase-4) · [5](#fase-5) · [6](#fase-6) · [7](#fase-7) · [8](#fase-8)

<a id="como-responder"></a>

## Cómo responder

- **Técnica**: decisión → por qué → qué se sacrifica (trade-off) → cómo se verifica (un dato o un test concreto). Terminar con una evidencia: "lo medí", "hay un test que lo demuestra".
- **Negocio**: problema de Headache → impacto (KPI) → opciones con coste y riesgo → recomendación → cómo se mide el éxito. Primero la versión de 30 segundos; el detalle, si lo piden.
- **Nube**: responder primero en AWS y después "en Azure, donde tengo 7 años de experiencia, el equivalente es…". Convierte la falta de experiencia en AWS en experiencia transferible.
- Decir "no lo sé, lo comprobaría así…" es mejor que inventar. Es lo mismo que pedimos al RAG: abstenerse antes que alucinar.

<a id="chuleta"></a>

## Chuleta Azure → AWS (lo que se usa en este repo)

| Necesidad | Azure (experiencia) | AWS (rol) |
|---|---|---|
| Modelos | Azure OpenAI / AI Foundry | Bedrock (Converse API, perfiles de inferencia entre regiones) |
| RAG gestionado | AI Search (indexer + skillset), Foundry IQ | Bedrock Knowledge Bases (S3 → S3 Vectors / OpenSearch) |
| Vector store | AI Search, PostgreSQL + pgvector | S3 Vectors, OpenSearch, Aurora PostgreSQL + pgvector |
| Reranking | Semantic Ranker | Bedrock Rerank (Cohere, Amazon) |
| Agentes gestionados | Foundry Agent Service | Bedrock AgentCore (Runtime, Memory, Gateway, Identity) / Bedrock Agents |
| Guardrails | Content Safety, Prompt Shields | Bedrock Guardrails |
| Gateway de APIs/MCP | API Management (AI Gateway) | API Gateway, AgentCore Gateway |
| Colas y eventos | Service Bus, Event Grid | SQS, SNS, EventBridge |
| Cómputo | Functions, Container Apps, AKS | Lambda, ECS Fargate / App Runner, EKS |
| Base de datos | PostgreSQL Flexible Server | RDS / Aurora PostgreSQL |
| Identidad | Entra ID, Managed Identity | IAM Roles, IAM Identity Center, Cognito (usuarios finales) |
| Secretos | Key Vault | Secrets Manager, KMS |
| Observabilidad | Application Insights, Azure Monitor | CloudWatch, X-Ray (OpenTelemetry en ambos) |
| IaC y CI/CD | Bicep, Azure DevOps | CloudFormation/CDK, CodePipeline (Terraform y GitHub Actions en ambas) |
| Coste | Cost Management, presupuestos | Cost Explorer, AWS Budgets |

<a id="transversales"></a>

## Transversales: liderazgo técnico y stakeholders

1. **Tu experiencia es en Azure y Headache está en AWS. ¿Por qué tú?** Los problemas difíciles de
   un sistema agéntico (calidad medible, seguridad, coste, operación) no dependen de la nube. He
   construido el mismo sistema en ambas, con la nube detrás de puertos y Terraform para las dos, así
   que sé qué cambia (servicios, IAM, límites) y qué no (arquitectura, evals, harness). Mi plan de
   las primeras semanas: aprender la plataforma interna y los límites de cuotas de Bedrock, no los
   conceptos.
2. **¿Qué caso de uso de IA abordarías primero en Headache?** El de mayor valor con menor riesgo:
   el asistente interno de soporte (reembolsos, información de eventos). Tiene volumen, datos
   propios, un KPI claro (resolución sin humano, tiempo de gestión) y usuarios internos que toleran
   iterar. El asistente público llega después, con las evals y los guardrails ya probados.
3. **Un directivo pide "un agente que lo haga todo". ¿Qué respondes?** Que lo descompondría en
   agentes especializados con un triaje delante (fase 9), cada uno con su KPI y sus permisos
   mínimos. Propongo un primer alcance acotado con criterios de éxito acordados y lo amplío con datos.
   Un agente con todas las herramientas es más caro, más difícil de evaluar y más peligroso.
4. **¿Cómo mides el ROI de un agente de soporte?** Coste por conversación resuelta frente al coste
   de un ticket humano, tasa de resolución sin escalado, escalados correctos (no solo menos
   escalados), tiempo de respuesta y CSAT. Y el coste evitado de errores: un reembolso indebido o una
   promesa falsa también entran en la cuenta.
5. **¿Build o buy?** Comprar (gestionado) lo que no diferencia y da tiempo hasta el valor: RAG
   interno con Bedrock KB, ejecución de agentes con AgentCore. Construir lo que es el negocio o
   necesita control a escala: reglas de dominio, harness, evals y el retrieval del producto público.
   Siempre detrás de puertos, para poder cambiar de opinión.
6. **¿Cómo explicas a dirección que un LLM no es determinista?** Como un empleado nuevo muy rápido:
   necesita reglas, supervisión y evaluación continua. Lo traduzco a números: tasa de acierto en un
   dataset fijo, invariantes de seguridad a 0, coste por petición y un plan de contención (guardrails,
   aprobación humana, desactivación inmediata).
7. **¿Cómo llevas una PoC de IA a producción?** Con criterios de salida acordados antes de empezar:
   umbrales de evals, invariantes de seguridad, coste y latencia objetivo, observabilidad y un plan
   de despliegue gradual (interno → porcentaje de usuarios → todos) con vuelta atrás.
8. **¿Cómo lideras al equipo técnico?** Estándares que se comprueban solos (CI con calidad, evals y
   tests de ataque), decisiones escritas con su porqué (como los docs de fase), revisión de código
   centrada en riesgos y pairing en lo nuevo. Mi trabajo es que el equipo tome buenas decisiones sin
   mí, no tomarlas todas.
9. **Producto quiere una fecha que el equipo considera imposible. ¿Qué haces?** Hago visibles los
   trade-offs: alcance, fecha y calidad. Propongo qué entra en la fecha (un MVP con guardrails) y qué
   no, con el riesgo de cada opción. Lo que no negocio son la seguridad y las evals mínimas.
10. **RGPD y privacidad con proveedores de LLM.** Minimizar lo que se envía (las tools devuelven
    proyecciones, no el registro completo), redactar PII antes del proveedor y de las trazas,
    residencia de datos en la UE (perfiles `eu.` de Bedrock), acuerdos de tratamiento con el
    proveedor y auditoría de cada acción del agente.
11. **¿Cómo usas la IA en tu propio trabajo de desarrollo?** Como en este repo: instrucciones del
    proyecto versionadas (`AGENTS.md`), reglas por área, procedimientos como skills, verificación con
    los mismos comandos que la CI y revisión humana antes de cada commit. Con controles automáticos
    para lo que no puede fallar (el hook de términos prohibidos).

<a id="fase-0"></a>

## Fase 0 · Cimientos ([doc](fase-0-cimientos.md))

### Técnicas

1. **¿Por qué Clean Architecture en una PoC de IA?** Porque el LLM, la nube y el framework son lo
   que más cambia. Con puertos, cambiar Bedrock por Azure OpenAI o LangGraph por Strands es un
   adaptador nuevo, y los tests del dominio no se tocan.
2. **¿Dónde está la lógica de negocio en un sistema con agentes?** En el dominio, determinista y
   probado. El modelo extrae, clasifica y propone; el dominio decide (p. ej., `evaluate_refund`).
3. **¿Cómo pruebas sin gastar?** Adaptadores en memoria que implementan los mismos puertos; los
   tests de integración y los que tienen coste van aparte y se ejecutan de forma explícita.
4. **¿Por qué uv?** Resolución y sincronización muy rápidas, workspaces nativos y lockfile
   multiplataforma; `--locked` en CI garantiza builds reproducibles.

### Negocio y stakeholders

1. **Dirección quiere una demo de agente en dos semanas. ¿Por qué dedicar tiempo a arquitectura y
   CI?** Porque la demo que convence acaba en producción. Limito la inversión a lo que evita
   reescribirla: puertos, tests en memoria y CI (un par de días). A cambio, la demo puede cambiar de
   modelo o de nube sin rehacerse y cada mejora se verifica sola.
2. **¿Por qué datos sintéticos y no datos reales?** Por velocidad, reproducibilidad y RGPD: ninguna
   PII sale de producción para experimentar. La limitación es que son más "limpios" que la
   realidad; el plan es validar con una muestra anonimizada y aprobada por el DPO antes de producción.
3. **¿Cómo explicas Clean Architecture a un product manager?** Como enchufes estándar: el negocio
   es el aparato y los proveedores de IA o de nube son los enchufes. Cambiar de proveedor por precio
   o por calidad es cambiar el adaptador, no rehacer el producto.

<a id="fase-1"></a>

## Fase 1 · SQL y `ticketing_api` ([doc](fase-1-sql.md))

### Técnicas

1. **¿Cómo evitas la sobreventa con miles de compradores a la vez?** Un `UPDATE ... WHERE available
   >= :q RETURNING` atómico, más un `CHECK` en la tabla. Nada de leer y escribir en dos pasos: el
   test de integración demuestra que pierde actualizaciones.
2. **¿Optimista o pesimista?** Pesimista cuando la contención es alta o la decisión lee varias filas
   (reembolsos); optimista (versión) cuando los conflictos son raros. Aquí, además, la sentencia
   condicional evita bloquear en la compra.
3. **¿Cómo haces idempotente un `POST`?** `Idempotency-Key` con índice único: misma clave y mismo
   cuerpo ⇒ misma respuesta; distinto cuerpo ⇒ `409`.
4. **¿Por qué `CONCURRENTLY`?** Un `CREATE INDEX` normal bloquea las escrituras de la tabla durante
   toda la creación. Coste: no va en transacción y puede quedar `INVALID`.
5. **Una consulta no mejora con índices, ¿qué haces?** Si es analítica (recorre todo), sacarla del
   OLTP: réplica de lectura de Aurora, vista materializada o warehouse (Redshift/Athena).

### Negocio y stakeholders

1. **Sale a la venta un evento muy popular y producto pide "que no se caiga y no vendamos de
   más". ¿Qué les prometes?** Sobreventa cero garantizada por la base de datos, no por el código.
   El riesgo es la contención en la fila de la sesión: si el pico lo exige, cola virtual o reparto
   del inventario en varias filas. Lo mediría con p95 de compra, tasa de `409` y conversión.
2. **¿Por qué un proyecto de agentes empieza por una API transaccional?** Porque los agentes
   reintentan y se equivocan: sin idempotencia ni bloqueos, un reintento es un cobro o un reembolso
   doble. Eso cuesta dinero y confianza, y ningún prompt lo arregla.
3. **Finanzas quiere ingresos por ciudad en tiempo real sobre producción.** Les enseño el dato: esa
   consulta tarda ~640 ms y recorre los 3 M de pedidos con o sin índices, compitiendo con las
   compras. Negocio con ellos la frescura real que necesitan y la sirvo desde una réplica o un
   warehouse.

<a id="fase-2"></a>

## Fase 2 · Capa de modelos y LLM gateway ([doc](fase-2-modelos.md))

### Técnicas

1. **¿Por qué un LLM gateway?** Para centralizar lo transversal (cuotas, fallback, routing, trazas,
   coste, seguridad) fuera del código de negocio y cambiar de proveedor sin tocar casos de uso.
2. **¿Cómo bajas el coste sin perder calidad?** Routing al modelo más barato capaz, prompt caching
   con prefijos estables, salidas estructuradas cortas y medir el coste por petición.
3. **Prompt caching en Bedrock frente a Azure.** Bedrock: explícito con `cachePoint` al final del
   prefijo estable; en Claude la escritura cuesta un 25 % más y la lectura un 90 % menos. Azure:
   automático para prefijos ≥ 1024 tokens, sin coste de escritura. Medido en Azure con un manual de
   ~9 K tokens: el 98 % del prompt sale de caché desde la segunda llamada, el coste baja un 58 % y
   la latencia p50 un 27 % en `gpt-4.1-mini`.
4. **¿Qué haces ante un 429?** No reintentar contra el mismo despliegue: limitar en cliente (token
   bucket por TPM) y saltar a otro proveedor o región. En AWS, perfiles de inferencia entre regiones
   y, si el volumen es estable, throughput aprovisionado; en Azure, APIM con backends balanceados.
   Y revisar la capacidad: me pasó con 50 K TPM y prompts de 10 K tokens (5 peticiones por minuto).
   La capacidad de un despliegue Standard no se paga, así que se dimensiona según la carga y el
   límite en cliente se deja por debajo, con margen.
5. **¿LiteLLM o gateway propio?** LiteLLM da rapidez y cobertura de proveedores; el propio da control
   y tests deterministas. Aquí conviven tras el mismo puerto y, medidos con los mismos 20 emails,
   dan la misma calidad (20/20), el mismo enrutado y el mismo coste: se elige por control y operación.

### Negocio y stakeholders

1. **¿Cuánto costará el asistente de soporte al mes?** Lo estimo con una fórmula y no con una
   intuición: conversaciones × llamadas por conversación × tokens × precio. Orden de magnitud con
   precios orientativos de `gpt-4.1-mini`: 100 000 conversaciones con 4 llamadas de 3 000 tokens de
   entrada y 300 de salida salen por unos 670 $/mes; con el prefijo cacheado y routing al modelo
   barato baja a la mitad o menos. El gateway mide el coste real por petición desde el primer día.
2. **¿Qué riesgo tiene depender de un proveedor de modelos?** Caídas, cuotas, cambios de precio y
   retirada de modelos. Lo mitigo con el gateway (fallback entre proveedores por configuración),
   evals que permiten validar un modelo nuevo en horas y precios en una tabla versionada.
3. **¿Por qué no usar siempre el mejor modelo?** Porque la mayoría de peticiones (clasificar,
   extraer, FAQ) no lo necesitan y el modelo grande multiplica coste y latencia. El routing envía
   lo difícil al potente y las evals confirman que la calidad no baja. En el triaje de soporte, 18
   de 20 emails fueron al modelo barato con un 100 % de acierto. Y un router "inteligente" con otro
   LLM tomó las mismas decisiones a un 42 % más de coste: lo sencillo ganó, y lo demuestro con datos.

<a id="fase-3"></a>

## Fase 3 · Indexación event-driven ([doc](fase-3-comparativa.md))

### Técnicas

1. **¿Cómo haces la ingesta idempotente y sin huecos?** Hash de contenido en un ledger: un documento
   sin cambios no se vuelve a embeber. Al actualizar, primero upsert de los chunks nuevos y después
   borrado de los obsoletos, así una consulta nunca ve el documento vacío (LlamaIndex, por defecto,
   borra antes de insertar).
2. **¿Qué pasa con un documento que siempre falla?** Tras N entregas va a la cola de mensajes
   fallidos (DLQ) para revisarlo, sin bloquear al resto. Un test de integración destapó que en
   RabbitMQ 4.3 `nack(requeue)` ya no cuenta para el límite (bucle infinito); se resolvió con `reject`.
3. **¿Bedrock Knowledge Bases o pipeline propio?** Bedrock KB para llegar rápido y sin operar
   servidores (conocimiento interno). Pipeline propio cuando hace falta control del troceo, los
   metadatos y el coste a escala (producto público). Mismo corpus y misma cola en los tres carriles.
4. **¿Por qué S3 Vectors y no OpenSearch Serverless?** S3 Vectors no tiene capacidad mínima
   reservada: se paga almacenamiento y consultas. A cambio no tiene BM25 nativo, y la búsqueda
   híbrida necesita OpenSearch o un BM25 aparte.
5. **¿Qué estrategia de chunking?** Por frases y sin títulos huérfanos (el chunker inicial dejaba
   un 29 % de cortes en guías largas; ahora un 0 %). La elección final se decide con evals de
   retrieval (fase 5), no a ojo.
6. **¿Cómo sabes que la ingesta incremental funciona en producción?** Midiéndola: en Azure,
   republicar los 116 documentos generó **0** llamadas de embeddings (hash en el ledger) y cambiar
   uno generó **1**. En el carril gestionado, el indexer reprocesaba todo en cada ejecución y
   la causa no era el servicio sino nuestro script: resubía todos los blobs y eso cambia su
   fecha de modificación. Con una comprobación de MD5 antes de subir, 0 cambios = 0 procesados.
   Lección: "gestionado" no te quita la responsabilidad de la idempotencia.

### Negocio y stakeholders

1. **¿Qué recomiendas a Headache y por qué dos respuestas?** Conocimiento interno: Bedrock KB, por
   tiempo hasta el valor y cero operación; el lock-in es aceptable. Producto público: pipeline
   propio sobre SQS, por control, coste predecible y portabilidad. Y un carril de framework como
   laboratorio para probar estrategias antes de llevarlas a producción.
2. **¿Cuánto cuesta indexar el conocimiento?** Los embeddings cuestan céntimos para este corpus (del
   orden de 10⁵ tokens). Lo caro es la capacidad fija (un AI Search Basic ronda los 70 €/mes) y las
   reindexaciones completas al cambiar de modelo o de troceo: por eso se versiona el pipeline.
3. **Operaciones cambia una política y quiere que el asistente la sepa ya.** La ingesta es por
   eventos: publicar el documento encola su indexación. Acordaría un objetivo de frescura (p. ej.,
   minutos) y lo mediría como latencia de ingesta, con alertas sobre la DLQ.

<a id="fase-4"></a>

## Fase 4 · Retrieval avanzado ([doc](fase-4-retrieval.md))

### Técnicas

1. **¿Por qué RRF y no sumar scores?** BM25 y coseno tienen escalas distintas. RRF combina
   posiciones y evita calibrarlas; el recall sigue dependiendo de la ventana de candidatos. Y
   no es gratis: medido en Azure con 144 preguntas, la híbrida RRF rindió **peor** que el vector
   solo (recall@5 0,79 frente a 0,95), porque la parte léxica diluía el orden. Con el Semantic
   Ranker encima fue la mejor (0,97). La híbrida se valida con datos; no se asume.
2. **¿Dónde se aplica la ACL?** Antes del ranking y del top-k, en el backend, y de nuevo en la
   aplicación. El LLM no puede ampliar el alcance ni generar la ACL como filtro.
3. **¿HyDE no introduce alucinaciones?** Su texto es hipotético y solo guía la búsqueda; la
   respuesta usa exclusivamente fuentes reales recuperadas. Puede empeorar el recall, así que se
   mide frente a la híbrida sin HyDE.
4. **¿Una respuesta con citas es fiable?** Las citas demuestran trazabilidad, no que la afirmación
   se deduzca de la fuente. Aquí se reduce el riesgo con extracción literal, y aun así hay que
   evaluar relevancia, vigencia y suficiencia de la evidencia.
5. **¿Por qué embeddings `hashing` en local?** Permiten tests deterministas sin red, coste ni
   modelos locales. No demuestran recuperación semántica: eso requiere embeddings reales y evals.
6. **¿Parent-child y auto-merging son lo mismo?** No. La expansión explícita siempre devuelve el
   padre de un acierto; auto-merging solo sube al padre si se recupera una proporción suficiente de
   hijos. El umbral y el tamaño del padre se evalúan.
7. **¿Qué aporta LlamaIndex si ya tienes puertos?** Compone retrievers y docstores existentes sin
   mover seguridad ni reglas de negocio al framework. El coste: adaptar contratos, conocer sus
   valores por defecto y asumir cambios de API.
8. **¿Bedrock Rerank equivale al Semantic Ranker de Azure?** Ambos reordenan, pero Bedrock Rerank
   acepta candidatos de nuestra aplicación y el Semantic Ranker trabaja sobre los primeros
   resultados de su propio índice. No comparten escalas de score.
9. **¿Qué garantiza una cita de un servicio gestionado (Bedrock KB, Foundry IQ)?** Que el fragmento
   existe y apunta a una referencia autorizada devuelta por la knowledge base. No que la afirmación
   sea fiel a la fuente: eso lo miden las evals.
10. **¿Cómo aseguras la ACL si es el agente quien decide la llamada?** En el servidor, no en el
    prompt: una knowledge source por audiencia con filtro base y el agente vinculado a ella. El
    adaptador rechaza filtros que no puede garantizar. Para ACL por usuario, permisos de identidad
    en el índice y el token del usuario.
11. **¿Cómo evitas activar la nube sin querer?** Composición explícita: cada pieza cloud es una
    variable con valor local por defecto, las combinaciones incoherentes fallan al arrancar y
    `/health` declara qué está activo. Falta de configuración = error, no fallback silencioso.
12. **¿Usarías reescritura de consultas, HyDE o filtros inferidos por LLM?** Solo si lo demuestran
    las evals. En Azure, con el golden dataset, ninguna mejoró a la híbrida + Semantic Ranker y
    todas triplicaron la latencia (de ~220 ms a 700–1.200 ms). Y la primera medición fue
    engañosa: los fallos venían del esquema (permitía varias consultas y texto libre en los
    filtros), no de la técnica. Lo arreglé haciendo imposible el error en el esquema y volví a
    medir antes de decidir.

### Negocio y stakeholders

1. **Soporte dice que el asistente "se inventa cosas". ¿Qué haces?** Primero medir: qué preguntas,
   con qué frecuencia y por qué (el documento no se recuperó o el modelo no lo usó). Después, citas
   obligatorias, abstención cuando no hay evidencia y un dataset fijo para comprobar la mejora. El
   KPI es la tasa de respuestas con cita válida y la de abstenciones correctas.
2. **¿Por qué es mejor "no lo sé" que una respuesta?** Porque una política de reembolso inventada
   cuesta dinero, reclamaciones y confianza. Un "no lo sé" bien gestionado escala a una persona; es
   un coste conocido y acotado. En la prueba con el agente gestionado de Azure: 10/10 respuestas
   con la cita correcta y 4/5 abstenciones; la quinta respondió con una política cercana que no
   contestaba a lo preguntado. Por eso, además de exigir citas, hace falta un juez de fidelidad.
3. **Un proveedor retira el servicio en el que te apoyabas. ¿Cómo lo gestionas?** Como con *On Your
   Data*, retirado el 14-10-2026: comprobar el ciclo de vida antes de diseñar, documentar la
   decisión y su alternativa (Foundry Agent Service + Foundry IQ) y, gracias a los puertos, cambiar
   un adaptador en lugar del producto.
4. **¿Cómo valida un equipo que un servicio gestionado hace lo que promete?** Con el mismo dataset
   y las mismas métricas que el resto, en la nube y no solo con simulaciones. En la primera
   validación real del carril gestionado aparecieron seis fallos que los tests locales no podían
   ver (versión de API, valores por defecto, formato de citas). Cada uno quedó con su test de
   regresión basado en la respuesta real capturada.

<a id="fase-5"></a>

## Fase 5 · Evals de RAG ([doc](fase-5-evals.md))

### Técnicas

1. **¿Cómo construiste el golden dataset?** Con plantillas deterministas sobre documentos
   sintéticos: cada pregunta sabe qué documento la responde y qué literal debe aparecer. Es barato y
   reproducible, pero sobreestima la calidad porque las preguntas comparten vocabulario con los
   documentos (Hit@5 ≈ 1.0). Siguiente paso: preguntas reales anonimizadas de soporte y paráfrasis
   con LLM revisadas a mano.
2. **¿Qué métrica miras primero?** Para retrieval, MRR y nDCG (orden), porque el generador lee los
   primeros resultados. Para generación, precisión de citas y abstención correcta. Y por encima de
   todo los invariantes de seguridad: una fuga de ACL invalida cualquier media.
3. **¿RAGAS o algo propio?** Ambos, para cosas distintas. RAGAS/DeepEval como referencia estándar;
   el juez propio es el que corre de forma recurrente, porque cuesta una llamada por muestra en vez
   de 7–11 y su nota es auditable.
4. **¿Cómo evitas que las evals queden obsoletas?** El dataset guarda el hash del corpus y el gate
   falla si no coincide; el baseline está versionado y solo se sube con revisión.
5. **LlamaIndex es mucho más lento, ¿lo descartas?** No por eso: la calidad es la misma y acelera el
   desarrollo. Lo uso donde aporta y mantengo el camino caliente en código propio cuando la latencia
   importa a escala.
6. **¿Cómo sabes que tu juez LLM es fiable?** Calibrándolo contra un humano antes de usarlo como
   gate. Monté 20 muestras con 12 respuestas estropeadas a propósito (cifra cambiada, afirmación
   inventada, respuesta de otra pregunta, evasiva), las etiqueté a ciegas y medí el acuerdo con
   kappa de Cohen. El juez propio llegó a 0,82 en fidelidad y 1,0 en relevancia. Y salió algo que
   no habría visto leyendo la documentación: la fidelidad de DeepEval no detecta afirmaciones
   inventadas, solo contradicciones.
7. **¿El juez puede ser el mismo modelo que genera?** No: tiende a darse la razón (sesgo de
   autopreferencia). En este repo el router mandaba los dos al mismo modelo sin que nadie lo
   decidiera; ahora el juez es `gpt-4.1`, fijo y fuera del router, y el informe desglosa el coste
   por modelo para que se vea.

### Negocio y stakeholders

1. **¿Cómo demuestras a dirección que el asistente está listo para salir?** Con criterios de salida
   acordados antes: umbrales de acierto en un dataset fijo, invariantes de seguridad a 0, coste y
   latencia objetivo, y un piloto interno con revisión humana. Si los números no llegan, no sale,
   y todos sabían de antemano por qué.
2. **Un ingeniero quiere cambiar el prompt "porque funciona mejor". ¿Qué le pides?** El mismo eval
   antes y después. El gate de CI lo hace automático: una regresión se ve en la PR, no en
   producción. Así las discusiones de opinión se convierten en datos.
3. **¿Cuánto cuesta evaluar?** Las evals deterministas cuestan 0 y corren en cada PR. Los jueces LLM
   se ejecutan con presupuesto máximo (`--budget-usd`) sobre una muestra estratificada, a mano o
   programados, nunca en cada PR.
4. **¿Por qué dedicar tiempo de una persona a etiquetar si ya tienes un juez automático?** Porque
   el juez es el instrumento de medida: si mide mal, todas las decisiones que tomes con él están
   mal. Calibrarlo costó ~1 $ y 15 minutos de etiquetado, y evitó adoptar una métrica (la de un
   framework conocido) que dejaba pasar justo el fallo más peligroso para Headache: respuestas que
   inventan condiciones. Además, hasta el humano se equivoca (2 despistes de 20): por eso se
   etiqueta a ciegas y se adjudica de forma documentada.
5. **¿Cuándo conviertes un control de calidad en bloqueante?** Cuando ha demostrado ser estable:
   primero informa (no frena a nadie y se ve si da falsos positivos) y, tras un periodo sin ruido,
   bloquea. Aquí, 15 ejecuciones seguidas sin regresiones antes de pasarlo a `enforce`. Un gate
   que falla por ruido lo acaba desactivando el equipo; uno que nunca bloquea no protege nada.
6. **¿Por qué no usar directamente el evaluador del proveedor (Foundry Evaluation)?** Lo usé y
   lo calibré: acierta igual que nuestro juez en fidelidad, pero cuesta ~3 veces más y, con su
   escala 1-5, el umbral importa (exigiendo la nota máxima no servía para nada). Lo dejo para
   revisiones periódicas en el portal, que es lo que ve negocio, y el juez propio para la CI.

<a id="fase-6"></a>

## Fase 6 · Tool calling y MCP ([doc](fase-6-tools-mcp.md))

### Técnicas

1. **¿Por qué un bucle de tools propio si existen frameworks?** Para saber qué controlan y qué no:
   límites, idempotencia, errores como datos, saneamiento y observabilidad. En la fase 7 comparo el
   mismo agente en LangGraph, MAF, Strands y servicios gestionados.
2. **¿Cómo evitas que un prompt injection reembolse pedidos de otro cliente?** La identidad sale
   del token, no de los argumentos; la autorización se comprueba en el backend en cada llamada; los
   scopes limitan qué servidor puede usar el agente; y la política de dominio decide.
3. **¿Qué es el token passthrough y cómo lo evitas con un gateway?** Reenviar el token del usuario
   a otro servicio: rompe la audiencia y la auditoría. El gateway valida un token emitido para él y
   obtiene, por token exchange (RFC 8693 / OBO), uno nuevo para cada servidor con su audiencia y solo
   su scope. AgentCore Gateway ni siquiera admite passthrough hacia destinos MCP.
4. **¿AgentCore Gateway o APIM?** Para un *agent gateway* (agregar servidores, OBO, búsqueda
   semántica de tools, políticas Cedar), AgentCore. Si la organización ya gobierna sus APIs con
   APIM y quiere las mismas políticas, cuotas y observabilidad para MCP, APIM.
5. **¿Por qué tres servidores y no uno?** Mínimo privilegio y radio de impacto: el agente de
   consultas nunca recibe `refunds:write`, y cada servidor se despliega, escala y audita por
   separado. El gateway devuelve la comodidad de un único endpoint.
6. **¿Cómo evitas romper a los agentes al cambiar una tool?** Contratos versionados de `tools/list`
   en CI. Para el modelo, la descripción es parte del contrato.
7. **¿Qué aprendiste al poner el agente con un modelo real?** Que las tools son una interfaz para
   el modelo y se diseñan como tal. Con `gpt-4.1-nano`, un reembolso fallaba: pasaba el ID de la
   sesión como si fuera el de un evento, o copiaba el ID de ejemplo de la descripción. Lo arreglé
   con prefijos exactos por tipo de ID, sin ejemplos copiables, y diciendo en el prompt que la
   política la aplica el dominio. Y aun así, las escrituras van al modelo potente.

### Negocio y stakeholders

1. **Un partner quiere que su agente consulte nuestro catálogo. ¿Cómo lo abrirías?** Como un canal
   de producto: un servidor MCP de solo lectura detrás del gateway, con OAuth y scopes por partner,
   límites de uso, auditoría y contrato versionado. Es la versión agéntica de una API pública, con
   el mismo gobierno.
2. **Seguridad pregunta si un agente puede reembolsar por error. ¿Qué respondes?** Que el diseño lo
   impide en capas: el agente de consultas no tiene el permiso, la identidad no la decide el
   modelo, el dominio valida cada reembolso y las acciones irreversibles requieren aprobación humana
   (fase 8). Y que cada llamada queda auditada.
3. **¿MCP gateway propio o gestionado?** En AWS, AgentCore Gateway: menos operación y OBO integrado.
   El propio sirve para entender el patrón y para necesidades que el gestionado no cubre. APIM solo
   compensa si ya es el estándar de la organización (el tier Developer ronda 50 $/mes).
4. **Finanzas quiere usar siempre el modelo más barato. ¿Qué le dices?** Que sí para leer y no
   para escribir. Medido: el barato resolvía las consultas pero fallaba los reembolsos; el
   potente cuesta ~0,001 $ por turno. Un reembolso mal gestionado cuesta una reclamación y
   tiempo de soporte. La regla es simple y auditable: si el turno puede cambiar algo, modelo
   potente.

<a id="fase-7"></a>

## Fase 7 · Un agente en siete sabores ([doc](fase-7-agentes.md))

### Técnicas

1. **¿Por qué siete implementaciones del mismo agente?** Para separar lo que es del framework de lo
   que es del producto. La misma suite con el mismo modelo guionizado convierte "me gusta más X" en
   datos: llamadas, tokens, paridad de tools, invariantes de seguridad y casos límite.
2. **¿Qué dejarías siempre fuera del framework?** Identidad y autorización, ejecución de tools
   (validación, idempotencia, escrituras en serie), acceso al modelo (gateway: coste, fallback,
   trazas) y evals. El framework orquesta; el producto decide.
3. **¿Agente gestionado o propio?** Gestionado: operación, escalado y aislamiento por sesión sin
   esfuerzo, a cambio de control del contexto y portabilidad. AgentCore es el punto intermedio:
   gestiona la ejecución, pero el agente es tu contenedor. Foundry Agent Service gestiona más y
   deja controlar menos (no permite compactar el historial).
4. **¿Cómo haces que el prompt aproveche la caché?** Lo estable primero y lo variable al final;
   contexto de sesión separado de las instrucciones; checkpoints al final del prefijo estable y del
   historial; y no reescribir el historial salvo al compactar (con histéresis para que ocurra poco).
5. **¿Cómo detectas que un framework cambia el comportamiento?** Con un diario de tools propio y
   tests de paridad contra una referencia. Aquí cazaron un orden de mensajes no determinista
   (LlamaIndex), una lista mutada en sitio (Strands) y JSON inválido silenciado (MAF y Strands).
6. **¿Por qué tu suite de agentes daba 5 de 12 con un modelo real?** Porque estaba diseñada para
   un modelo guionizado: comprobaba la trayectoria exacta y literales. Leyendo los fallos uno a
   uno, la mitad eran respuestas correctas con otro formato. Con modelos reales se mide el
   resultado (estado final, escrituras, invariantes) y el contenido con un juez calibrado. Los
   fallos reales (un bucle, el idioma, una memoria guardada sin pedirla) eran los importantes.
7. **¿Gestionar la conversación tú o dejarla al servicio (Foundry)?** Medido en 14 turnos: el
   servicio guarda también las salidas de las tools y cuesta un 33 % más, y no recorta hasta
   acercarse a la ventana del modelo. Gestionarla tú da control de coste y de qué se recuerda; el
   servicio da menos código. En soporte con conversaciones largas, lo haría yo.

### Negocio y stakeholders

1. **¿Qué framework elegirías para el equipo de Headache?** Depende del equipo y de la plataforma,
   no de la moda. En AWS: Strands o LangGraph sobre AgentCore Runtime, según si el flujo necesita
   un grafo explícito. Lo decidiría con la suite comparativa y lo que el equipo ya sabe, y los
   contratos propios limitan el coste de equivocarse.
2. **¿Gestionado o propio para el lanzamiento?** Ejecución gestionada (AgentCore) para no operar
   infraestructura desde el primer día, con el harness, las tools y las evals en código propio.
   Así se gana tiempo sin regalar el control de lo que diferencia al producto.
3. **¿Cómo personalizas el asistente sin problemas de privacidad?** Memoria de preferencias de
   lista cerrada (idioma, ciudad, categorías), guardada cuando el usuario lo pide y nunca inferida
   de datos sensibles. Transparente, borrable y fácil de explicar a Legal.
4. **Un directivo ve "el agente acierta el 42 %" en un informe. ¿Qué le explicas?** Que ese número
   mide al comprobador, no al agente: la mitad de los "fallos" eran respuestas correctas con otro
   formato. Le daría tres cifras separadas: tareas resueltas, errores reales con su impacto y
   errores bloqueados por las defensas (aquí, un reembolso ajeno que frenó el backend). Y un plan
   para medir mejor antes de decidir nada con ese dato.

<a id="fase-8"></a>

## Fase 8 · Agentic harness y guardrails ([doc](fase-8-harness.md))

### Técnicas

1. **¿Qué es un agentic harness?** El código que rodea al modelo y decide qué entra, qué se ejecuta
   y qué sale: validación, guardas, autocorrección, aprobación y trazas. El modelo propone; el
   harness dispone.
2. **¿Cómo pones guardrails a agentes de frameworks distintos?** Con un punto de paso obligatorio
   que controlas: todas las tools por tu ejecutor y todos los turnos por tu contrato. Aquí, siete
   sabores con el mismo harness y los mismos tests de ataque.
3. **¿Cómo demuestras que funciona?** Tests de ataque con un modelo que obedece al atacante, con y
   sin harness, más controles benignos para medir falsos positivos, y un gate en CI. Resultado: sin
   harness 0/6 ataques contenidos; con harness 6/6 y 4/4 controles en los siete sabores.
4. **¿Qué no resuelve ningún guardrail?** Que un modelo con poder haga daño si lo engañan. Por eso la
   capa decisiva es de arquitectura: identidad fuera del modelo, mínimos privilegios y acciones
   irreversibles con aprobación humana.
5. **¿Bedrock Guardrails o Prompt Shields?** Bedrock Guardrails cubre en una llamada inyección
   directa, PII (anonimiza o bloquea) y temas vetados; Prompt Shields está diseñado específicamente
   para inyección indirecta en documentos. Ambos van detrás de un puerto, así que se comparan con
   los mismos ataques.
6. **¿Basta con Azure Content Safety para frenar inyecciones?** No. Lo medí con 24 textos: detecta
   7 de 12 ataques, igual que mi escudo heurístico gratuito, pero **otros 7**; juntos llegan a 9
   de 12, sin falsos positivos. Los tres que pasan los para la arquitectura: las escrituras
   requieren aprobación y la autorización está en el backend. Además, Content Safety F0 tarda ~2 s
   por llamada: hay que pensar en el tier y en paralelizarlo.

### Negocio y stakeholders

1. **Legal pregunta qué garantías hay de que el agente no hace reembolsos indebidos.** Defensa en
   profundidad: el agente propone, las reglas del dominio deciden, las acciones irreversibles
   esperan a una persona y cada paso queda auditado. Y una prueba continua: los tests de ataque
   corren en cada cambio.
2. **Operaciones dice que la aprobación humana les frena y quiere quitarla.** Empezaría con
   aprobación en todo lo irreversible y mediría la tasa de aprobación y los motivos de rechazo. Con
   datos, automatizaría por umbrales (importes bajos, clientes y casos sin riesgo) y mantendría la
   persona en el resto. Es una decisión de riesgo que se toma con números, no un todo o nada.
3. **¿Cómo comunicarías un incidente de seguridad del agente?** Rápido y con hechos: qué pasó, a
   quién afectó, cómo se contuvo (desactivar la tool o el agente) y qué cambia. Después, un
   postmortem sin culpables y el ataque convertido en un test de regresión que corre en la CI.
4. **Operaciones quiere preguntar en lenguaje natural a la base de datos. ¿Lo abrirías?** Sí, con
   límites: solo lectura sobre vistas sin datos personales, consulta validada antes de ejecutar y
   tiempo máximo. Al probarlo con 3 M de pedidos, 3 de 5 preguntas superaron los 3 s y se
   escalaron a un analista: la seguridad funcionaba, pero la base transaccional no es el sitio.
   La propuesta a negocio: réplica de lectura o almacén con vistas agregadas antes de abrirlo.
