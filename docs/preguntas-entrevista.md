# Preguntas de entrevista · AI Technical Lead en Headache

Recopilatorio único para preparar la entrevista: preguntas técnicas y de negocio/stakeholders de
cada fase, con una respuesta modelo anclada en lo que se ha construido en este repo. Los docs de
fase explican el detalle; aquí está lo que hay que saber contar.

**El rol:** 50 % liderazgo técnico (arquitectura, decisiones, calidad, equipo) y 50 % negocio y
gestión de stakeholders (impacto, coste, riesgo, priorización, comunicación). Producción en **AWS**.

## Índice

- [Cómo responder](#como-responder) · [Chuleta Azure → AWS → GCP](#chuleta) · [Transversales: liderazgo y stakeholders](#transversales)
- Siglas, términos y librerías: [glosario e inventario](glosario.md) · Diagramas por niveles: [arquitectura](arquitectura.md)
- Fases: [0](#fase-0) · [1](#fase-1) · [2](#fase-2) · [3](#fase-3) · [4](#fase-4) · [5](#fase-5) · [6](#fase-6) · [7](#fase-7) · [8](#fase-8) · [checkpoint 8→9](#checkpoint-8-9) · [9](#fase-9) · [10](#fase-10) · [11](#fase-11) · [12](#fase-12) · [checkpoint 12→13](#checkpoint-12-13)

<a id="como-responder"></a>

## Cómo responder

- **Técnica**: decisión → por qué → qué se sacrifica (trade-off) → cómo se verifica (un dato o un test concreto). Terminar con una evidencia: "lo medí", "hay un test que lo demuestra".
- **Negocio**: problema de Headache → impacto (KPI) → opciones con coste y riesgo → recomendación → cómo se mide el éxito. Primero la versión de 30 segundos; el detalle, si lo piden.
- **Nube**: responder primero en AWS y después "en Azure, donde tengo 7 años de experiencia, el equivalente es…". Convierte la falta de experiencia en AWS en experiencia transferible.
- Decir "no lo sé, lo comprobaría así…" es mejor que inventar. Es lo mismo que pedimos al RAG: abstenerse antes que alucinar.

<a id="chuleta"></a>

## Chuleta Azure → AWS → GCP (lo que se usa en este repo)

Azure es la experiencia, AWS el rol y GCP la tercera nube de la pasada GCP. En GCP, Vertex AI se
presenta ahora como **Gemini Enterprise Agent Platform** (Agent Engine pasa a Agent Runtime); las
APIs y los recursos de Terraform pueden conservar el nombre antiguo. Las filas de GCP de las fases
2–12 se confirman en su subfase de la pasada.

| Necesidad | Azure (experiencia) | AWS (rol) | GCP |
|---|---|---|---|
| Unidad de aislamiento y facturación | Suscripción + grupo de recursos | Cuenta (Organizations) | **Proyecto**: facturación, cuotas, APIs e IAM cuelgan de él |
| Habilitar un servicio | Resource providers (casi siempre automático) | Implícito | **Cada API por proyecto** (`google_project_service`); sin ella, `SERVICE_DISABLED` |
| Modelos | Azure OpenAI / AI Foundry | Bedrock (Converse API, perfiles de inferencia entre regiones) | Gemini en Agent Platform con `google-genai` (endpoint `global` o regional) |
| RAG gestionado | AI Search (indexer + skillset), Foundry IQ | Bedrock Knowledge Bases (S3 → S3 Vectors / OpenSearch) | RAG Engine (Cloud Storage → corpus), Vertex AI Search (respuestas citadas) |
| Vector store | AI Search, PostgreSQL + pgvector | S3 Vectors, OpenSearch, Aurora PostgreSQL + pgvector | Vector Search, Cloud SQL / AlloyDB + pgvector |
| Reranking | Semantic Ranker | Bedrock Rerank (Cohere, Amazon) | Ranking API |
| Agentes gestionados | Foundry Agent Service | Bedrock AgentCore (Runtime, Memory, Gateway, Identity) / Bedrock Agents | Agent Runtime (Sessions, Memory Bank) + ADK |
| Guardrails | Content Safety, Prompt Shields | Bedrock Guardrails | Model Armor, Sensitive Data Protection, *safety settings* de Gemini |
| Gateway de APIs/MCP | API Management (AI Gateway) | API Gateway, AgentCore Gateway | Apigee, Agent Gateway (parte en preview) |
| Colas y eventos | Service Bus, Event Grid | SQS, SNS, EventBridge | Pub/Sub (temas + suscripciones, *dead-letter topic*), Eventarc |
| Emulador local de colas | Emulador de Service Bus (EULA + SQL Server) | ElasticMQ, LocalStack | Emulador oficial de Pub/Sub (perfil `pubsub`) |
| Rate limiting y caché en el borde | API Management (`llm-token-limit`, caché semántica), Azure Managed Redis | API Gateway (*usage plans*), WAF (*rate-based rules*), ElastiCache (Valkey/Redis) | Apigee (cuotas), Cloud Armor (*rate limiting*), Memorystore (Valkey) |
| Autoescalado por cola | KEDA en Container Apps / AKS | ECS *target tracking* sobre SQS, KEDA en EKS | Worker pools de Cloud Run, KEDA `gcp-pubsub` en GKE |
| Pruebas de carga | Azure Load Testing (JMeter, Locust) | *Distributed Load Testing on AWS*, Locust en ECS | Locust en GKE o Cloud Run jobs (sin servicio propio) |
| Multi-agente gestionado | Foundry Agent Service (workflows, tool A2A) | AgentCore Runtime (protocolo A2A), Bedrock Agents (*multi-agent collaboration*) | Agent Runtime con A2A, multi-agente de ADK |
| Flujo durable con espera humana | Durable Functions, Logic Apps | Step Functions (*wait for callback*) | Workflows (*callbacks*) |
| Cómputo | Functions, Container Apps, AKS | Lambda, ECS Fargate / App Runner, EKS | Cloud Run functions, Cloud Run, GKE |
| Base de datos | PostgreSQL Flexible Server | RDS / Aurora PostgreSQL | Cloud SQL para PostgreSQL, AlloyDB |
| Analítica | Fabric / Synapse | Redshift, Athena | BigQuery |
| Identidad | Entra ID, Managed Identity | IAM Roles, IAM Identity Center, Cognito (usuarios finales) | IAM + cuentas de servicio adjuntas, Identity Platform (usuarios finales) |
| Credenciales en local | `az login` | `aws sso login` + perfil | ADC de usuario + *impersonation* de una cuenta de servicio (sin claves JSON) |
| CI sin secretos | Federated credentials de Entra ID | OIDC → `AssumeRoleWithWebIdentity` | Workload Identity Federation (pool + proveedor OIDC de GitHub) |
| Secretos | Key Vault | Secrets Manager, KMS | Secret Manager, Cloud KMS |
| Observabilidad | Application Insights, Azure Monitor | CloudWatch, X-Ray (OpenTelemetry en ambos) | Cloud Monitoring, Logging y Trace (OpenTelemetry) |
| Trazas distribuidas | Application Insights (*transaction search*, mapa de aplicación) | X-Ray (Transaction Search sobre OTLP) | Cloud Trace (OTLP) |
| Logs centralizados | Log Analytics (KQL) | CloudWatch Logs (Logs Insights) | Cloud Logging (Log Analytics con SQL) |
| Métricas y alertas | Azure Monitor Metrics, *log search alerts* + *action groups* | CloudWatch Metrics (EMF), *alarms* + SNS | Cloud Monitoring (alertas en PromQL) + canales de notificación |
| Distribución de OpenTelemetry | Azure Monitor OpenTelemetry Distro, exporter `azuremonitor` | ADOT (AWS Distro for OpenTelemetry) | Collector de OpenTelemetry con los exporters `googlecloud` |
| Trazas de agentes gestionados | Foundry Tracing | AgentCore Observability | Trazas de Agent Runtime en Cloud Trace |
| Prometheus y Grafana gestionados | Managed Prometheus, Managed Grafana | Amazon Managed Prometheus, Amazon Managed Grafana | Managed Service for Prometheus (Grafana autogestionado) |
| Evals de agentes y LLM-juez | Foundry evaluators (`ToolCallAccuracy`, `IntentResolution`) | Bedrock evaluations (LLM-as-a-judge), AgentCore Evaluations | Gen AI Evaluation Service (métricas de trayectoria) |
| Red teaming | AI Red Teaming Agent de Foundry (sobre PyRIT) | PyRIT o promptfoo contra Bedrock (sin servicio propio) | PyRIT o promptfoo contra Gemini (sin servicio propio) |
| Prompts versionados | Prompts de Foundry | Bedrock Prompt Management | Gestión de prompts de Agent Platform |
| Experimentos (MLflow gestionado) | Azure ML (URI de MLflow) | SageMaker con MLflow gestionado | Vertex AI Experiments |
| Drift | Azure ML data drift, alertas dinámicas de Azure Monitor | SageMaker Model Monitor, CloudWatch Anomaly Detection | Model Monitoring de Vertex AI, alertas de Cloud Monitoring |
| IaC y CI/CD | Bicep, Azure DevOps | CloudFormation/CDK, CodePipeline | Infrastructure Manager (Terraform gestionado), Cloud Build / Cloud Deploy (Terraform y GitHub Actions en las tres) |
| Coste | Cost Management, presupuestos | Cost Explorer, AWS Budgets | Informes de Cloud Billing, presupuestos (ojo: miden neto de créditos por defecto) |

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

### GCP (pasada 0.G)

**Técnicas**

1. **¿Qué cambia al llevar el mismo sistema a GCP?** Tres cosas que no tienen equivalente directo.
   El *proyecto* es la unidad de facturación, cuotas e IAM, más fuerte que un grupo de recursos.
   Cada API se habilita por proyecto (`google_project_service`), y hay que añadirla al plan de
   infraestructura. Y la identidad va por cuentas de servicio que se suplantan, sin claves. Lo demás
   (dominio, puertos, evals, harness) no cambia: en la 0.G solo se añadieron configuración, IaC y
   un emulador.
2. **¿Cómo das acceso a GCP a la CI sin guardar claves?** Con Workload Identity Federation: GitHub
   emite un token OIDC por ejecución y STS lo cambia por uno temporal de una cuenta de servicio. Es
   el `AssumeRoleWithWebIdentity` de AWS. El matiz importante es la condición: en `identity.tf` uso
   los IDs numéricos del repositorio y del dueño (los nombres se pueden volver a registrar) y solo
   `refs/heads/main`, así que una PR con el workflow modificado no obtiene credenciales.
3. **Al añadir el SDK de una nube nueva el lock no resolvía. ¿Qué hiciste?** `google-genai` iba
   por la 3.0 y ADK, el framework que lo envuelve, exigía `<3`. En un monorepo con varios
   frameworks manda el techo del framework, no la última versión del SDK. Por eso metí todas las
   dependencias de GCP en una subfase propia y temprana, con la suite completa, la CI y la imagen
   de agentes como prueba: descubrirlo cinco fases después cuesta mucho más.
4. **¿Cómo evitas que un proveedor nuevo "funcione" por accidente?** Las raíces de composición
   eligen el adaptador con una cadena de `if` cuyo último caso era Bedrock. Al añadir `gcp` al enum,
   el sistema habría construido Bedrock sin avisar. Ahora un proveedor declarado sin adaptador
   lanza un error que dice en qué fase llega, y un test lo fija.

**Negocio y stakeholders**

1. **Producción está en AWS. ¿Para qué una tercera nube?** No para operar en tres a la vez, que
   multiplica el coste operativo. Es *opcionalidad*: poder mover una pieza (por ejemplo, el
   proveedor de modelos) por precio, calidad o una caída regional. La prueba de que la arquitectura
   es portable es que la base para GCP cupo en cuatro subfases pequeñas, con coste 0 y las mismas
   evals. Esa prueba también da poder de negociación con el proveedor actual.
2. **Finanzas teme la factura de una cuenta nueva. ¿Qué controles pones el primer día?** El
   presupuesto existe antes que cualquier recurso, con alertas al 50, 75 y 100 % y previsión al
   100 %. Mide el gasto *bruto*: por defecto GCP descuenta los créditos de prueba y las alertas no
   saltarían hasta agotarlos. Además, lo que se cobra por hora nace apagado (`*_enabled = false`) y
   se enciende solo en sesiones de trabajo.
3. **¿Cómo explicas la protección de `main` a alguien no técnico?** Como la firma de dos personas
   en un pago: nada llega a la versión buena sin pasar por una revisión y por todas las
   comprobaciones automáticas (el check `release-gate`). Aplica también a quien administra el
   repositorio y a la IA que escribe código.

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

### GCP (pasada 2.G)

**Técnicas**

1. **¿Qué tuviste que cambiar en el puerto para añadir Gemini?** Un campo: `ToolCall.signature`.
   Gemini 3 y 4 devuelven una *thought signature*, un estado opaco de su razonamiento, en la
   primera llamada a tool de cada paso, y si no se la devuelves en el turno siguiente responde 400.
   El SDK lo hace solo si reenvías sus objetos, pero nuestro historial es neutro (`ChatMessage`),
   así que la firma tiene que viajar en él: es lo único que sobrevive a reintentos, fallback y
   checkpoints. Va en base64 para que el historial siga siendo JSON.
2. **¿Qué pasa si el gateway salta a Gemini a mitad de una conversación que empezó en Azure?** Las
   llamadas de Azure no tienen firma y Gemini daría 400. Google documenta un valor ficticio
   (`skip_thought_signature_validator`) para ese caso, que el adaptador pone solo en la primera
   llamada de cada paso (duplicarla también da 400). Es un "último recurso" que degrada algo el
   razonamiento, pero mantiene vivo el fallback, que es lo que importa en una caída.
3. **¿Por qué el adaptador de Gemini ignora la temperatura?** Porque los modelos actuales (3.5+ y
   Gemini 4) la ignoran y Google anuncia que la rechazará. Lo comprobé en la documentación antes de
   escribir el adaptador: el plan, de hace una semana, todavía nombraba modelos que ya eran
   *legacy*. Lo que se controla es `thinking_level`, por tier, porque el razonamiento se factura
   como salida: Hydrogen piensa en `high` por defecto y eso multiplica coste y latencia.

**Negocio y stakeholders**

1. **Google duplica los precios de dos modelos en las próximas semanas. ¿Qué haces?** Es el riesgo
   de proveedor que justifica el gateway: los precios viven en una tabla versionada (ya con las
   subidas), el coste se mide por petición y cambiar de modelo es configuración, no un proyecto.
   Antes de cambiar, las evals de la fase 5 dicen si el modelo alternativo mantiene la calidad, así
   que la decisión se toma con datos de coste y calidad, no por miedo a la factura.

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

<a id="checkpoint-8-9"></a>

## Checkpoint 8→9 · De local a la nube ([doc](fase-9-8-checkpoint.md))

### Técnicas

1. **¿Qué encontraste al llevar a la nube algo que pasaba todos los tests?** Que los dobles de
   test solo prueban lo que supones. En un día aparecieron más de veinte fallos que no se veían
   en local: versiones de API (el endpoint MCP de Foundry IQ solo existe en preview), formatos de
   respuesta reales, un indexer "en verde" con 0 documentos, un juez que devolvía espacios hasta
   agotar tokens, un sabor que nunca llamó al servicio real... Cada uno quedó con su test de
   regresión construido a partir de la respuesta real capturada.
2. **¿Cómo demuestras que una prueba "en la nube" tocó la nube?** Con evidencia del servicio, no
   con una respuesta correcta: tokens facturados, ids de recursos, la ejecución visible en el
   portal o, al final, la línea en Cost Management. Un sabor de agente respondía bien usando un
   emulador local; solo se vio al pedir el uso de tokens del servicio.
3. **¿Cómo controlas el coste de experimentar con modelos?** Tres capas: un medidor en la
   aplicación que corta antes de cada llamada (`--budget-usd`), un presupuesto de suscripción con
   alertas al 50/75/100 % y recursos caros efímeros (AI Search solo existe mientras se usa). Y se
   contrasta con la factura: mi medidor se quedó un 40 % corto por repeticiones y precio de
   DataZone.
4. **¿Por qué tantas correcciones en el diseño de tools y esquemas?** Porque con modelos baratos
   el esquema es el guardarraíl más eficaz: una consulta en vez de una lista, enumeraciones del
   dominio en vez de texto libre, prefijos de ID exactos. Hacer imposible el error funciona mejor
   que validarlo después.

### Negocio y stakeholders

1. **¿Cuánto cuesta validar un sistema así en la nube?** Lo medí: 3,76 € para validar en Azure
   siete fases (modelos, RAG, evals, tools, agentes, guardrails). Dos tercios fueron el juez de
   evaluación, no el sistema. Servir es barato; lo que hay que presupuestar es evaluar bien y los
   recursos con coste por hora.
2. **¿Cómo priorizas lo que validas y lo que aplazas?** Por riesgo y por dependencia. Primero lo
   que puede invalidar decisiones (calidad del retrieval, fiabilidad del juez, seguridad); lo que
   solo confirma infraestructura (HTTPS, APIM) va con el despliegue. Y nada que deje objetos a mano
   en un entorno que no controlo: las App Registrations de Entra irán como código.
3. **¿Qué le contarías a dirección de este checkpoint en un minuto?** Que el sistema funciona con
   modelos reales con un coste por debajo de 4 €, que la seguridad aguantó (0 fugas, 0 acciones
   irreversibles sin aprobación con un modelo real), que la medida de calidad está calibrada contra
   personas, y que tres decisiones salieron de datos: el modelo barato no hace escrituras, la
   búsqueda no necesita reescritura con LLM y la analítica no va contra la base transaccional.

<a id="fase-9"></a>

## Fase 9 · Orquestación multi-agente ([doc](fase-9-multiagente.md))

### Técnicas

1. **¿Router, supervisor, handoff o plan-and-execute?** Para soporte de Headache, router: el LLM
   clasifica y una tabla del dominio elige el especialista. Es auditable, se prueba sin modelo y
   no cuesta llamadas extra. Medí el supervisor con un email de reembolso y queja: atiende las dos
   intenciones (el router solo la principal) a cambio del doble de llamadas (6 frente a 3). Lo
   activaría por regla solo cuando el triaje detecte varias intenciones.
2. **¿Qué pasas de un agente a otro?** Un sobre estructurado (intención, idioma, urgencia, pedido,
   resumen), no el email. La identidad va fuera del prompt, en el contexto de las tools. En un
   hilo largo el especialista recibe un 63 % menos de tokens, pero el valor principal es otro:
   el texto del cliente, y cualquier inyección que traiga, no llega al especialista. Un test de
   invariantes lo comprueba en cada cambio.
3. **¿LangGraph o MAF?** Mismo flujo en ambos, con un test de paridad. LangGraph comparte un
   estado y trae reintentos por nodo y checkpoints por hilo en PostgreSQL; MAF pasa mensajes
   tipados, valida el grafo al construirlo y tiene `request_info` para la revisión humana, pero
   no reintenta por nodo y sus checkpoints en disco son pickle (hay que limitar los tipos). En
   AWS elegiría LangGraph (o Strands); en un stack Microsoft, MAF.
4. **¿MCP o A2A?** MCP conecta un agente con sus tools; A2A, un agente con otro agente opaco de
   otro equipo, framework o nube. Mis especialistas son servicios A2A con Agent Card, token de
   servicio con un audience por especialista y un contenedor cada uno. El orquestador no cambia:
   el cliente A2A cumple el mismo contrato que el especialista en proceso.
5. **¿Cómo haces idempotente un flujo asíncrono con agentes?** El caso se identifica por el email
   y se **reclama** antes de llamar al LLM; cada paso avanza el estado con compare-and-set y lo
   irreversible pasa por aprobaciones idempotentes. Lo encontré con RabbitMQ real: dos copias del
   mismo email llegaban a la vez y ambas pagaban el triaje. Guardar y publicar no eran atómicos;
   lo cerré en la fase 10.5 con un *transactional outbox*.

### Negocio y stakeholders

1. **¿Por qué varios agentes y no uno que lo haga todo?** Por riesgo y por equipo. Cada
   especialista solo tiene sus tools: un prompt injection no puede reembolsar desde Conocimiento
   porque esa tool no existe para él. Y cada especialista se evalúa, se despliega y escala por
   separado; el de reembolsos lo puede gobernar finanzas sin tocar el resto.
2. **¿Qué KPI pondrías y cómo lo enseñarías a operaciones?** Tasa de resolución sin intervención,
   tiempo hasta la primera respuesta, precisión del enrutado (medida con modelo real sobre una
   muestra etiquetada) y tasa de aprobación de los borradores. A operaciones les enseñaría la
   cola de casos pendientes con su borrador y el motivo, no el grafo.
3. **¿Qué dejarías fuera de la primera versión?** El supervisor, la memoria compartida entre
   agentes y la autoaprobación por importe. Primero router + aprobación humana en todo lo que sale
   hacia el cliente, medir dos o tres semanas y automatizar solo donde los datos lo justifiquen.

<a id="fase-10"></a>

## Fase 10 · Escala y patrones asíncronos ([doc](fase-10-escala.md))

### Técnicas

1. **Sale a la venta un festival y llegan 10.000 usuarios a la vez. ¿Qué evita que el sistema
   caiga?** Decidir pronto y barato, por capas:
   - validación (422) e identidad del token (401);
   - *load shedding* en memoria por plan (503 con `Retry-After`);
   - rate limit por usuario en Redis (429);
   - caché semántica;
   - modelo con espera acotada y, si no llega, una respuesta degradada.

   En mi prueba con 200 usuarios simulados, el sistema sin protección servía 6,8 req/s con un
   p95 de 60 s; con protección, 75 req/s con el 97 % desde la caché y un TTFT p95 de 42 ms.
   En AWS lo pondría tras API Gateway/WAF con ElastiCache; en Azure, tras APIM con Managed Redis.
2. **¿Cómo haces rate limiting con varias réplicas?** GCRA en un script Lua atómico en Redis, con
   el reloj de Redis (no el de cada réplica) y claves con *hash tag* para Redis Cluster. Si Redis
   cae, *fail-open* con un límite local por réplica y sin reintentos que bloqueen. La cuota del
   modelo también se comparte: N réplicas no pueden pedir N veces el TPM.
3. **¿No es peligrosa una caché semántica?** Sí: el riesgo es responder lo de Madrid a quien
   pregunta por Barcelona. Por eso la similitud solo propone, y el dominio exige las mismas
   facetas (ciudad, categoría, operación, IDs y números). Lo personal nunca entra, y el
   espacio de la caché va por ACL, idioma y versión del índice. Con la guarda: 12/17 paráfrasis
   reutilizadas y 0 falsos aciertos. Sin ella: 11 falsos aciertos. Es un gate en la CI.
4. **¿Por qué se rompió tu sistema en la prueba de carga y qué cambiaste?** Encontré dos límites
   que los tests unitarios no veían:
   - Las preguntas lentas al modelo ocupaban toda la capacidad de la réplica, y el 72 % de las
     altas de casos (una transacción barata) recibían 503. Lo arreglé con un bulkhead por ruta.
   - La espera de cuota de 10 s, pensada para *workers*, dejaba el chat con un TTFT p50 de 9,8 s.
     La bajé a 2 s en el edge y, si el modelo no llega, respondo con citas literales marcadas
     como degradadas (no se cachean).
5. **¿Cómo escalas los workers de una cola de agentes?** Por longitud de cola, no por CPU: un
   worker de LLM pasa el tiempo esperando al modelo.
   - En AWS: ECS con *target tracking* sobre mensajes visibles por tarea.
   - En Azure: KEDA en Container Apps con la regla `azure-servicebus`.

   El techo lo pone la cuota del modelo, no las réplicas. Con 150 K TPM y 4.000 tokens por caso,
   más de 2 réplicas solo producen 429; lo calculo con la ley de Little antes de fijar
   `max_replicas`.

### Negocio y stakeholders

1. **¿Cuánto nos ahorra y qué KPI vigilarías en un pico?** En la prueba, la caché bajó las
   llamadas al modelo de 505 a 68 con más del doble de respuestas entregadas: el coste por cada
   1.000 respuestas pasó de ~0,79 $ a ~0,009 $. Vigilaría:
   - tasa de acierto de la caché y falsos aciertos reportados;
   - TTFT p95;
   - porcentaje de 429 y 503 por plan;
   - respuestas degradadas;
   - consumo de cuota del modelo.

   Todo eso, con alertas antes de la venta, no después.
2. **¿Cómo explicas a dirección que en un pico rechacemos peticiones a propósito?** Igual que la
   cola virtual de una venta de entradas: es mejor decir "vuelve en 2 segundos" a unos pocos que
   dejar a todos mirando una pantalla que no carga.
   - Sin control, todos esperaban hasta 60 s y la mitad del gasto en el modelo se perdía en
     usuarios que ya se habían ido.
   - Con control, el personal interno y las altas de casos nunca fallan.
   - El público recibe respuesta inmediata, de la caché o degradada, o un "inténtalo en 2 s".
3. **¿Qué priorizarías para la primera venta grande y qué dejarías fuera?** Primero, lo que más
   valor da por día de trabajo:
   1. rate limit por usuario y shedding;
   2. caché semántica con la guarda (el ahorro y la latencia salen de ahí);
   3. degradación;
   4. una prueba de carga con el patrón del pico real.

   Dejaría fuera el autoescalado fino y la caché multi-región. Antes de la venta pediría ampliar
   la cuota del modelo con el proveedor, porque es el techo que ninguna réplica supera.

<a id="fase-11"></a>

## Fase 11 · Observabilidad de sistemas agénticos ([doc](fase-11-observabilidad.md))

### Técnicas

1. **¿Cómo instrumentas un sistema agéntico sin atarte a un proveedor?** Con OpenTelemetry y su
   convención GenAI (`gen_ai.request.model`, `gen_ai.usage.input_tokens`...) detrás de un puerto,
   `TelemetryPort`: el código no importa el SDK. Un collector decide el destino:
   - en local, Jaeger y Prometheus;
   - para el equipo de IA, Langfuse;
   - en AWS, X-Ray por OTLP y CloudWatch con EMF (la imagen ADOT);
   - en Azure, Application Insights con el exporter `azuremonitor`.

   Cambiar de backend es configuración. La CI valida los tres collectors con la misma imagen.
2. **¿Cómo sigues un caso que pasa por una cola, tres workers y un agente remoto?** Propagando el
   contexto W3C (`traceparent`) fuera del cuerpo del mensaje: atributos de SQS, cabeceras AMQP,
   propiedades de Service Bus, una columna del outbox y una cabecera HTTP en A2A. Cada consumidor
   continúa la traza como hijo, no como *link*: un caso son 14 spans con un solo `trace_id`, desde
   el `POST /cases` hasta el resultado. Lo pruebo con el relay y los workers en contextos vacíos,
   porque el contexto heredado de asyncio ocultaba que no viajaba.
3. **¿Qué métricas y alertas pondrías a un agente en producción?** RED por servicio (peticiones,
   errores, duración) más las de GenAI: TTFT, tokens y coste por agente, 429/503 por plan, acierto
   de la caché, respuestas degradadas, backlog de colas y faithfulness muestreada. Las alertas son
   de *burn rate* multiventana sobre 6 SLO (14,4× en 1 h y 5 min para despertar, 6×/3×/1× para
   ticket) más coste desbocado. Se generan desde el dominio (71 reglas) y se prueban con
   `promtool test rules` en la CI.
4. **¿Cómo evitas que la observabilidad filtre PII?** En tres capas:
   - el contenido (prompts, respuestas, argumentos) está apagado por defecto y, si se activa,
     pasa por el redactor;
   - el collector borra esos atributos antes de los backends genéricos;
   - las etiquetas de métricas nunca llevan IDs.

   Solo Langfuse, con acceso restringido, ve el contenido. Los logs JSON llevan `trace_id` para
   saltar del error a su traza sin copiar datos.
5. **Langfuse, Phoenix, LangSmith o X-Ray: ¿cuál?** No es uno u otro. Langfuse (o Phoenix) entiende
   de LLM: sesiones, prompts, coste por modelo, scores. X-Ray o App Insights ven el sistema
   entero: colas, 503, Redis. Con OTel instrumento una vez y mando cada span a quien lo
   aprovecha. LangSmith encaja si todo es LangChain, pero es SaaS y ata el formato.

### Negocio y stakeholders

1. **¿Qué gana Headache con esto y qué KPI mirarías?** Menos tiempo en detectar y resolver
   incidentes (MTTD, MTTR) y coste bajo control. Una alerta de coste desbocado salta en 15
   minutos, no a fin de mes en la factura. Miraría:
   - coste por caso resuelto y por agente;
   - TTFT p95;
   - porcentaje de errores del modelo frente al presupuesto de error;
   - faithfulness muestreada.

   Cada alerta enlaza a un runbook, así que cualquiera de guardia sabe qué hacer.
2. **¿Cuánto cuesta observar y cómo lo contienes?** La ingesta se paga por GB, y un agente genera
   muchos spans. Lo contengo así:
   - sin prompts en los backends genéricos;
   - muestreo configurable (`TELEMETRY__SAMPLE_RATIO`);
   - tope diario de ingesta en Log Analytics;
   - solo las dimensiones de métricas que usan las alarmas (en CloudWatch, cada combinación se
     factura);
   - retenciones cortas.

   La evaluación online con juez está apagada por defecto y, cuando se active, va con muestreo y
   tope por minuto. Antes de producción, mediría los GB/mes reales.
3. **¿Cómo explicas un SLO y un presupuesto de error a dirección?** "Nos comprometemos a que 99 de
   cada 100 respuestas salgan bien; el 1 % restante es el margen para cambiar cosas". Si lo
   gastamos muy deprisa, alerta y se congela lo arriesgado. Si sobra, podemos ir más rápido. Así
   la conversación deja de ser "¿hay errores?" (siempre los hay) y pasa a "¿estamos dentro de lo
   pactado?".

<a id="fase-12"></a>

## Fase 12 · Evals de agentes, red teaming y LLMOps ([doc](fase-12-evals-llmops.md))

### Técnicas

1. **¿Cómo evalúas un agente y no solo su respuesta?** Por tres ejes que no dependen de la
   redacción:
   - trayectoria: precisión y recall de las tools como subsecuencia ordenada;
   - parámetros: los argumentos clave de cada tool (`order_id`, idioma, filtros);
   - resolución: el estado del mundo después del turno (pedido, aprobación, preferencias) y cero
     invariantes rotos.

   Un agente puede decir «reembolsado» sin haberlo hecho; lo que cuenta es el pedido. Con 14
   escenarios × 7 sabores, la CI bloquea cualquier fallo fuera de la baseline (job `release-gate`).
2. **¿Cómo te fías de un LLM-juez?** Primero, el juez no decide: etiqueta hechos verificables
   (afirmaciones respaldadas, acciones dadas por hechas, resolución, idioma) y una regla del dominio
   decide si es aceptable. Segundo, se calibra contra etiquetas humanas a ciegas (32 muestras con
   errores sembrados: cifra cambiada, acción inventada, evasiva) y solo se usa si el kappa de Cohen
   es ≥ 0,6. Tercero, va fijo y fuera del router: nunca el mismo modelo que genera.
3. **¿Cómo haces red teaming sin quemar presupuesto ni volver la CI inestable?** Genero las
   variantes una vez con PyRIT (9 conversores) y promptfoo (9 estrategias locales, sin generación
   remota) y las congelo en el repo. La CI las ejecuta con un modelo comprometido que obedece al
   atacante, así que mide el harness: 0 acciones irreversibles sin una persona. Encontró 4 problemas,
   uno un bug que afectaba a clientes normales (una tarjeta precedida de otro número no se
   redactaba). Los ataques adaptativos con un LLM atacante (Crescendo, AI Red Teaming Agent de
   Foundry) van aparte y con presupuesto.
4. **¿Cómo versionas prompts y decides si uno nuevo es mejor?** Los prompts viven en Git
   (`<paquete>/prompts/*.md`) con versión y un candado de hashes: cambiar el texto sin subir la
   versión rompe la CI. Langfuse recibe una copia para enlazar cada generación con su versión. Para
   decidir, un A/B pareado sobre los mismos casos con McNemar exacto y una regla escrita antes: un
   invariante roto descarta y con menos de 10 casos discordantes es «falta muestra». Harness sí
   frente a harness no: 7 a 0, p = 0,016, desplegar.
5. **¿Cómo detectas drift en un sistema agéntico?** Con el PSI entre el último día y la semana
   anterior en tres señales: intenciones de los casos, tools que usan los agentes y resolución
   según el juez online. Alerta a partir de 0,25 y con muestra mínima. Lo útil es el diagnóstico: si
   cambian las intenciones y no la resolución, es externo y el sistema aguanta; si cambia la
   resolución sin cambiar lo que se pide, es interno (prompt, modelo del proveedor, índice). En AWS,
   las mismas reglas en Amazon Managed Prometheus o CloudWatch Anomaly Detection; en Azure, Managed
   Prometheus o alertas dinámicas.

### Negocio y stakeholders

1. **¿Por qué invertir en evals si el agente ya «funciona»?** Porque sin ellas cada cambio
   (prompt, modelo, proveedor) es una apuesta. El gate convierte «creo que va bien» en «14
   escenarios, 7 sabores, 0 invariantes rotos», y el A/B evita discusiones de opinión. KPI: tasa de
   resolución, regresiones que llegan a producción (objetivo 0) y tiempo de un cambio de prompt de
   la idea al despliegue.
2. **¿Cómo cuentas el red teaming a Legal o dirección?** Con el informe consolidado: «ninguna
   acción irreversible sin una persona en ningún ataque», qué se encontró y se arregló, y el riesgo
   residual aceptado con su mitigación (PII codificada en base64, que para el guardrail del
   proveedor). Lo importante es que el riesgo está escrito y firmado, no escondido.
3. **¿Qué priorizarías si solo tuvieras un mes?** El gate de trayectoria y resolución en la CI y
   el red teaming congelado: cuestan 0 por ejecución y bloquean lo grave. Después, etiquetar y
   calibrar el juez (unas horas de una persona y menos de 1 $), porque sin eso el juez online y los
   A/B con modelo real no son fiables. El drift y MLflow gestionado, al final: necesitan tráfico real
   para aportar.

<a id="checkpoint-12-13"></a>

## Checkpoint 12→13 · Comunicar la arquitectura y añadir un proveedor ([doc](fase-13-12-checkpoint.md))

### Técnicas

1. **¿Cómo explicarías tu arquitectura en una pizarra en 5 minutos?** Por niveles de zoom: primero
   el sistema entero en una pantalla (tres entradas y cuatro pasos obligatorios: harness, LLM
   gateway, MCP gateway y retrieval), luego solo el bloque que interese y, al final, el recorrido
   de un caso real de punta a punta. Un diagrama único con todo no lo entiende nadie; los tengo en
   [arquitectura.md](arquitectura.md).
2. **¿Cuánto cuesta añadir otra nube a tu sistema?** Lo planifiqué con GCP: casi todo son
   adaptadores de puertos que ya existen (`LLMPort`, broker, vector store, escudo, memoria), más
   Terraform y documentación. Lo que no es gratis son los sabores nuevos (ADK, Agent Runtime), que
   pasan la misma suite de 14 escenarios, y el mantenimiento: más dependencias y una CI más larga.
3. **¿Qué compruebas antes de diseñar sobre un servicio gestionado?** Su ciclo de vida en la
   documentación oficial, no lo que recuerdo. Al planificar GCP vi que Vertex AI había pasado a
   llamarse Gemini Enterprise Agent Platform y que el módulo generativo de su SDK se retiró el
   24-06-2026: con un plan de memoria habría construido sobre algo retirado. También pruebo que las
   librerías nuevas resuelven con las nuestras en una copia del workspace antes de tocar el lock.
4. **¿Qué cambia entre Gemini, Bedrock y Azure OpenAI para tu código?** Para el dominio, nada:
   todos cumplen `LLMPort`. En el adaptador sí: la caché de prompts (automática en Azure, explícita
   con `cachePoint` en Bedrock, implícita y explícita con almacenamiento por horas en Gemini), el
   formato de las tools y cómo se señala un bloqueo de seguridad, que el gateway traduce al mismo
   error.

### Negocio y stakeholders

1. **Producción de Headache está en AWS. ¿Por qué invertir en una tercera nube?** En producción no
   lo haría sin un motivo de negocio (precio, un modelo concreto, un cliente o una región). Lo que sí
   defiendo es diseñar para poder hacerlo: con puertos y evals comunes, evaluar Gemini frente a
   Bedrock es una tarde y una tabla de coste y calidad, no un proyecto. Eso da poder de negociación
   con los proveedores.
2. **¿Cómo comunicas la arquitectura a dirección?** Solo el nivel 0 y una historia: "un email de
   reembolso entra, la IA lo clasifica y redacta, una persona aprueba lo que mueve dinero y todo
   queda registrado". Los zooms son para el equipo técnico.
3. **¿Cómo planificas un trabajo largo con un equipo?** Como esta incorporación: decisiones
   escritas con su porqué, verificación de riesgos antes de empezar (servicios retirados,
   dependencias), subfases pequeñas con criterio de terminado, coste 0 hasta validar y un orden de
   sesiones. Así cualquiera puede retomarlo.
