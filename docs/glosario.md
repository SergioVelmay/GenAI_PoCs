# Glosario e inventario de librerías · Fases 0–12

Chuleta para la entrevista: los términos y siglas que más se repiten en el repo y las librerías
que usamos, con **qué son**, **para qué sirven** y **dónde aparecen aquí** (para poder anclar
cada respuesta en algo construido). Las respuestas largas viven en
[preguntas-entrevista.md](preguntas-entrevista.md); la equivalencia de servicios, en su
[chuleta Azure → AWS → GCP](preguntas-entrevista.md#chuleta).

Cómo usarlo: leer la columna "Qué es" en voz alta como si se lo explicaras a alguien de negocio
(una frase) y la columna "En este repo" como la evidencia que das si piden detalle.

## Índice

- Glosario: [1. Fundamentos de GenAI](#g1) · [2. Modelos, gateway y coste](#g2) ·
  [3. RAG: indexación y retrieval](#g3) · [4. Evals](#g4) · [5. Tools, MCP e identidad](#g5) ·
  [6. Agentes y context engineering](#g6) · [7. Harness, guardrails y seguridad](#g7) ·
  [8. Multi-agente y asíncrono](#g8) · [9. Escala y resiliencia](#g9) ·
  [10. Observabilidad](#g10) · [11. Red teaming y LLMOps](#g11) ·
  [12. Arquitectura, datos e ingeniería](#g12) · [13. Negocio y gestión](#g13)
- Librerías: [Modelos y gateway](#l1) · [Frameworks de agentes](#l2) · [RAG y datos](#l3) ·
  [Tools, MCP y A2A](#l4) · [Harness y seguridad](#l5) · [Evals, red teaming y LLMOps](#l6) ·
  [Observabilidad](#l7) · [Servicios, colas y caché](#l8) · [Calidad y herramientas](#l9) ·
  [Infraestructura local (Docker)](#l10)

---

# Glosario

<a id="g1"></a>

## 1. Fundamentos de GenAI

| Término | Qué es | En este repo |
|---|---|---|
| **LLM** (*Large Language Model*) | Modelo que predice el siguiente token; entiende y genera texto. No es determinista ni "sabe" el negocio. | Solo modelos cloud: Azure OpenAI/Foundry y Bedrock, siempre detrás de `LLMPort`. |
| **Token** | Unidad en la que el modelo lee y escribe (≈ ¾ de palabra en inglés). Se paga por tokens de entrada y de salida. | Toda la contabilidad de coste y de cuotas (TPM) va en tokens. |
| **Ventana de contexto** | Máximo de tokens que el modelo ve en una llamada (prompt + historial + documentos + respuesta). | Motivo de la compactación del historial (fase 7). |
| **Prompt de sistema** | Instrucciones fijas que definen el papel y las reglas del modelo. | En `<paquete>/prompts/*.md`, versionados con candado (fase 12.6). |
| **Embedding** | Vector numérico que representa el significado de un texto; textos parecidos quedan cerca. | `text-embedding-3-small` (Azure) y Titan v2 (Bedrock); en local, embeddings `hashing` deterministas. |
| **Alucinación** | Respuesta fluida pero falsa o no respaldada por las fuentes. | Se combate con citas, abstención y un juez de fidelidad (fases 4–5). |
| **Abstención** | Que el sistema diga "no lo sé" / escale cuando no tiene evidencia. | Métrica propia en las evals de RAG: "abstención correcta". |
| **Temperatura / determinismo** | Cuánta aleatoriedad hay al elegir tokens. A 0 es más estable, pero nunca 100 % determinista. | Por eso los tests usan modelos guionizados y adaptadores en memoria. |
| **Structured output** | Pedir al modelo una salida que cumple un esquema JSON (Pydantic) en vez de texto libre. | Triaje, extracción, juez: el dominio solo acepta lo que valida el esquema. |
| **Tool / function calling** | El modelo no ejecuta nada: **propone** llamar a una función con argumentos; tu código la ejecuta y le devuelve el resultado. | Bucle manual en la fase 6.1; después, en 7 sabores de framework. |
| **Agente** | LLM + tools + un bucle que decide el siguiente paso hasta terminar (patrón ReAct: razonar → actuar → observar). | `booking_agent` (reservas y reembolsos) y los especialistas del orquestador. |
| **Agéntico** (*agentic*) | Sistema donde el LLM elige pasos/herramientas, no solo responde. Más potente y más difícil de controlar. | Todo el repo desde la fase 6. |
| **Few-shot / zero-shot** | Dar (o no) ejemplos resueltos dentro del prompt. | Las descripciones de tools evitan ejemplos copiables (lección de la fase 6). |

<a id="g2"></a>

## 2. Modelos, gateway y coste

| Término | Qué es | En este repo |
|---|---|---|
| **Gateway** (genérico) | Punto de entrada único que centraliza lo transversal (auth, cuotas, logs) delante de varios servicios. | Hay tres: LLM gateway, MCP gateway y el edge (`edge_api`). |
| **LLM gateway** | Capa entre el código y los proveedores de modelos: routing, fallback, cuotas, coste, trazas. | `adapters/llm_factory.build_gateway`; propio o LiteLLM tras el mismo puerto (fase 2). |
| **Model routing** | Elegir el modelo más barato capaz para cada petición. | Triaje: 18/20 emails al modelo barato con 100 % de acierto; las escrituras siempre al potente. |
| **Fallback** | Si un modelo/proveedor falla, pasar a otro configurado. | Configuración del gateway; en los agentes, fallback determinista al agotar iteraciones. |
| **Prompt caching** | El proveedor reutiliza el prefijo estable del prompt: menos coste y latencia. | Azure: automático ≥ 1.024 tokens (98 % cacheado, −58 % coste). Bedrock: explícito con `cachePoint`. |
| **TPM / RPM** | *Tokens/Requests Per Minute*: la cuota de un despliegue de modelo. | Un 429 con 50 K TPM y prompts de 10 K; la cuota es el techo del autoescalado (fase 10). |
| **429** | "Too Many Requests": has superado la cuota o el rate limit. | No se reintenta contra el mismo despliegue: limitar en cliente y saltar de región/proveedor. |
| **PTU / throughput aprovisionado** | Capacidad reservada de modelo a precio fijo (Azure PTU, Bedrock *Provisioned Throughput*). | Solo compensa con volumen estable; aquí, despliegues Standard (pago por uso). |
| **Perfil de inferencia entre regiones** | En Bedrock, enrutar la misma petición a varias regiones para tener más cuota. | Respuesta estándar en AWS a un 429. |
| **Converse API** | API unificada de Bedrock para chat y tools con cualquier modelo. | `adapters/bedrock.py`. |
| **Coste por petición** | Tokens × precio de cada modelo, medido por el gateway. | Fórmula de presupuesto: conversaciones × llamadas × tokens × precio. |

<a id="g3"></a>

## 3. RAG: indexación y retrieval

| Término | Qué es | En este repo |
|---|---|---|
| **RAG** (*Retrieval-Augmented Generation*) | Buscar fragmentos relevantes y dárselos al modelo para que responda con ellos y los cite. | `services/retrieval` y `indexer`; 3 carriles × 2 nubes. |
| **Chunk / chunking** | Trocear documentos en fragmentos indexables; el tamaño y el corte afectan a la calidad. | Troceo por estructura; de un 29 % a un 0 % de cortes en guías largas (fase 3). |
| **Parent-child / auto-merging** | Indexar trozos pequeños (precisos) y devolver el padre grande (contexto). Auto-merging solo sube si aciertan varios hijos. | Fase 4, con LlamaIndex. |
| **Ingesta event-driven** | Publicar un documento encola su indexación (no un batch nocturno). | RabbitMQ / SQS / Service Bus → `indexer`. |
| **Ledger de hashes** | Registro del hash de cada documento para no re-embeber lo que no cambia (idempotencia). | Republicar 116 documentos = 0 llamadas de embeddings. |
| **Vector store** | Base de datos que busca por similitud de embeddings. | pgvector en local; AI Search en Azure; S3 Vectors en AWS. |
| **pgvector / HNSW** | Extensión de PostgreSQL para vectores; HNSW es su índice aproximado (rápido, no exacto). | `pgvector/pgvector:pg17`; Aurora PostgreSQL en AWS. |
| **BM25** | Búsqueda léxica clásica (por palabras), buena con IDs, nombres y términos exactos. | `rank-bm25` en local; S3 Vectors no lo tiene. |
| **Búsqueda híbrida** | Combinar léxica (BM25) y vectorial. | Medida en Azure: sola rindió **peor** (recall@5 0,79 vs 0,95); con reranker, la mejor (0,97). |
| **RRF** (*Reciprocal Rank Fusion*) | Fusionar listas por **posición**, no por score (las escalas no son comparables). | Fusión de la híbrida (fase 4). |
| **Reranking** | Un segundo modelo reordena los N candidatos con más precisión. | Semantic Ranker (Azure) ↔ Bedrock Rerank. |
| **Top-k** | Número de fragmentos que se pasan al modelo. | Métricas @5 en las evals. |
| **HyDE** (*Hypothetical Document Embeddings*) | El LLM escribe una respuesta hipotética y se busca con ella. | Probado: no mejoró y triplicó la latencia → descartado con datos. |
| **Query rewriting** | El LLM reformula la consulta antes de buscar. | Igual que HyDE: medido y no adoptado. |
| **ACL / security trimming** | Filtrar por permisos **antes** de rankear, en el backend; el LLM no puede ampliar el alcance. | Invariante de seguridad: una fuga de ACL invalida cualquier media. |
| **Cita** | Referencia al fragmento que respalda la respuesta. Da trazabilidad, no garantiza fidelidad. | Extracción literal + juez de fidelidad. |
| **Knowledge base gestionada** | Servicio que indexa y busca por ti. | Bedrock Knowledge Bases ↔ AI Search + Foundry IQ (carril A). |
| **Carriles A / B / C** | Misma tarea con servicio gestionado (A), SDK nativo (B) o framework (C), mismo dataset y evals. | Estructura de las fases 3, 4, 7. |
| **DLQ** (*Dead-Letter Queue*) | Cola donde van los mensajes que fallan N veces, para no bloquear al resto. | Bug real: en RabbitMQ 4.3 `nack(requeue)` ya no cuenta → `reject`. |

<a id="g4"></a>

## 4. Evals (evaluación)

| Término | Qué es | En este repo |
|---|---|---|
| **Eval** | Prueba de calidad de un sistema con LLM sobre un dataset fijo, con métricas. Es el "test" de la IA. | Paquete `evals/` (`rag_evals`). |
| **Golden dataset** | Conjunto de preguntas con respuesta/documento esperado, versionado. | 144 preguntas; guarda el hash del corpus para detectar que quedó obsoleto. |
| **Recall@k / Hit@k** | ¿Está el documento correcto entre los k primeros? | Recall@5 en la comparativa de Azure. |
| **MRR** (*Mean Reciprocal Rank*) | Premia que el documento correcto salga **arriba** (1/posición). | Métrica principal de retrieval. |
| **nDCG** (*normalized Discounted Cumulative Gain*) | Calidad del orden completo con relevancia graduada. | Junto a MRR. |
| **Faithfulness / groundedness** | ¿La respuesta se deduce de las fuentes (no inventa)? | Juez propio 0,82 de kappa; DeepEval no detectaba afirmaciones inventadas. |
| **LLM-as-a-judge / LLM-juez** | Usar un LLM para puntuar respuestas. Barato pero hay que calibrarlo. | Juez fijo `gpt-4.1`, fuera del router; en la 12.2, etiqueta hechos y decide el dominio. |
| **Kappa de Cohen** | Acuerdo entre dos evaluadores corregido por azar (0 = azar, 1 = perfecto). | Umbral ≥ 0,6 para usar un juez. |
| **Sesgo de autopreferencia** | Un modelo tiende a aprobar sus propias respuestas. | Por eso el juez nunca es el modelo que genera. |
| **Gate / baseline** | Umbrales en la CI que bloquean un cambio si empeora respecto a una referencia guardada. | `rag_evals.gate`; `release-gate` en la fase 12. |
| **Report → enforce** | Primero el gate solo informa; tras demostrarse estable, bloquea. | 15 ejecuciones sin ruido antes de `enforce`. |
| **Invariante** | Regla que nunca puede romperse (fuga de ACL, reembolso sin aprobación). Se exige a 0, no en media. | En todas las suites. |
| **Eval de trayectoria / parámetros / resolución** | Evaluar el **camino** del agente (tools en orden), sus **argumentos** y el **estado final** del mundo. | 14 escenarios × 7 sabores (fase 12.1). |
| **Muestra estratificada / `--budget-usd`** | Evaluar con LLM una muestra representativa con tope de gasto. | Lo que tiene coste nunca corre en cada PR. |
| **Eval online** | Puntuar con el juez una muestra del tráfico real en producción. | `OnlineEvaluator`, scores en Langfuse (apagado por defecto). |

<a id="g5"></a>

## 5. Tools, MCP e identidad

| Término | Qué es | En este repo |
|---|---|---|
| **MCP** (*Model Context Protocol*) | Estándar para que un agente descubra y llame tools/recursos de un servidor ("USB-C de las tools"). | 3 servidores FastMCP en `services/mcp_servers` (fase 6). |
| **`tools/list`** | Operación MCP que devuelve las tools y su esquema; la descripción es parte del contrato. | Contratos versionados en la CI. |
| **MCP gateway / agent gateway** | Gateway delante de varios servidores MCP: un endpoint, auth, políticas, auditoría. | Propio en local; AgentCore Gateway ↔ APIM. |
| **A2A** (*Agent-to-Agent*) | Protocolo para que un agente llame a **otro agente** opaco (otro equipo, framework o nube). MCP = agente↔tools; A2A = agente↔agente. | Especialistas como servicios A2A (fase 9). |
| **Agent Card** | Ficha pública de un agente A2A: qué sabe hacer y cómo autenticarse (no expone tools internas). | Una por especialista. |
| **OAuth 2.x** | Estándar de autorización por tokens con permisos acotados. | Servidores MCP protegidos con OAuth (fase 6.3). |
| **JWT** (*JSON Web Token*) | Token firmado con claims (quién, para quién, qué permisos, caducidad). | Validado en cada servidor. |
| **Scope** | Permiso concreto dentro del token (`refunds:write`). | El agente de consultas nunca recibe `refunds:write`. |
| **Audience (`aud`)** | Para qué servicio se emitió el token; otro servicio debe rechazarlo. | Un audience por especialista A2A. |
| **Token passthrough** | Reenviar el token del usuario a otro servicio. Antipatrón: rompe audiencia y auditoría. | Prohibido; AgentCore Gateway ni lo permite. |
| **OBO / token exchange** (*On-Behalf-Of*, RFC 8693) | Cambiar un token por otro nuevo, para un servicio concreto y con menos permisos, en nombre del usuario. | El MCP gateway lo hace por servidor. |
| **Mínimo privilegio / radio de impacto** | Dar solo los permisos necesarios para que un fallo o ataque afecte a lo mínimo. | Tres servidores MCP en vez de uno. |
| **Cedar** | Lenguaje de políticas de autorización de AWS. | Políticas de AgentCore Gateway (comparativa). |
| **Entra ID / Managed Identity ↔ IAM Role** | Identidad de personas y de servicios sin secretos en el código. | Regla del repo: nunca secretos; identidades gestionadas en la nube. |
| **OIDC en CI** | La CI obtiene credenciales temporales de la nube sin guardar claves. | GitHub Actions → AWS/Azure/GCP (fases 13–15). |
| **Cuenta de servicio (GCP)** | Identidad de una aplicación en GCP; se adjunta al recurso o la suplanta una persona. Equivale a Managed Identity / IAM Role. | `headache-dev-app` en `infra/terraform/gcp/identity.tf`, sin claves. |
| **ADC** (*Application Default Credentials*) | Cadena de búsqueda de credenciales de las librerías de Google: usuario en local, cuenta de servicio en la nube. Como `DefaultAzureCredential`. | `gcloud auth application-default login` con `CLOUDSDK_CONFIG` aislado. |
| **Impersonation (suplantación)** | Una persona obtiene un token temporal (1 h) de una cuenta de servicio; queda auditado a su nombre. Sustituye a las claves JSON. | `GCP__IMPERSONATE_SERVICE_ACCOUNT`; rol `serviceAccountTokenCreator`. |
| **Workload Identity Federation** | GCP acepta tokens de otro proveedor (el OIDC de GitHub) y los cambia por uno de una cuenta de servicio. Con condición, solo para un repo y una rama. | Pool `headache-dev-github`; solo `main` de este repo (por IDs numéricos). |

<a id="g6"></a>

## 6. Agentes y context engineering

| Término | Qué es | En este repo |
|---|---|---|
| **Sabor** | Una implementación del mismo agente con otro framework/servicio, con la misma suite. | 7: nativo, LangGraph, LlamaIndex, MAF, Strands, Foundry Agent Service, AgentCore. |
| **`AgentRuntime` / `AgentSpec`** | Contrato común de todos los agentes / lo único que ven todos los sabores (prompt, tools, límites, fallback). | `booking_agent/spec.py`. |
| **Context engineering** | Decidir **qué** entra en la ventana y en qué orden: instrucciones, memoria, tools, historial, documentos. | Lo estable primero (caché), lo variable al final, compactación con histéresis. |
| **Compactación** | Resumir o recortar el historial al acercarse a un límite. | Política propia en todos los sabores salvo Foundry (no controlable). |
| **Memoria corta / larga** | Historial de la conversación / datos que persisten entre sesiones. | Memoria de preferencias de lista cerrada (idioma, ciudad, categorías); AgentCore Memory. |
| **Diario de tools** (`JournaledExecutor`) | Registro de cada llamada a tool para auditar y comparar sabores. | Detectó bugs de frameworks (orden no determinista, JSON inválido silenciado). |
| **Test de paridad** | El mismo escenario debe producir las mismas tools en todos los sabores/motores. | Fases 7 y 9. |
| **Modelo guionizado** | LLM falso que devuelve respuestas predefinidas: tests deterministas y gratis. | Base de todas las suites de la CI. |
| **Agente gestionado** | El proveedor ejecuta el bucle, la memoria y el escalado. | Foundry Agent Service ↔ Bedrock Agents / AgentCore Runtime. |
| **Skill** | Paquete de instrucciones que el agente carga solo cuando lo necesita. | Skills del `AgentSpec` y de `.claude/skills/` para los agentes de desarrollo (Copilot y Claude Code). |

<a id="g7"></a>

## 7. Harness, guardrails y seguridad

| Término | Qué es | En este repo |
|---|---|---|
| **Harness (agentic harness)** | El código que **rodea** al modelo y decide qué entra, qué se ejecuta y qué sale. "El modelo propone; el harness dispone". | `packages/harness` (fase 8). |
| **Guardrail** | Un control concreto dentro del harness (filtro de entrada, guarda de tool, validador de salida). | `GuardedExecutor`, `DocumentShield`, `GuardedRuntime`. |
| **Defensa en profundidad** | Varias capas independientes; la última es de arquitectura y no se puede engañar con texto. | Prompt → guardas → aprobación humana + dominio. |
| **Prompt injection directa** | El usuario intenta que el modelo ignore sus reglas ("ignora lo anterior y…"). | Tests de ataque: sin harness 0/6 contenidos, con harness 6/6. |
| **Prompt injection indirecta** | Las instrucciones maliciosas llegan **dentro de datos** (un documento, un email, la salida de una tool). | `DocumentShield` retiene el fragmento; Prompt Shields. |
| **Jailbreak** | Técnica para saltarse las políticas de seguridad del modelo. | Estrategias de promptfoo y conversores de PyRIT (fase 12). |
| **PII** (*Personally Identifiable Information*) | Datos personales (email, teléfono, tarjeta, DNI). RGPD. | Redacción con regex con checksum + Presidio; nunca en trazas ni métricas. |
| **Redacción / anonimización** | Sustituir la PII por marcadores (`<EMAIL_ADDRESS>`) antes de enviarla al proveedor o a logs. | Bug encontrado en red teaming: tarjeta precedida de otro número. |
| **Content Safety / Prompt Shields ↔ Bedrock Guardrails** | Servicios gestionados de filtrado (odio, violencia, inyección, PII, temas vetados). | Content Safety detectó 7/12; con el heurístico propio, 9/12. |
| **HITL** (*Human-in-the-loop*) | Una persona aprueba antes de una acción irreversible. | `ApprovalGate`: argumentos congelados, cuatro ojos, compare-and-set. |
| **Cuatro ojos** | Quien solicita no puede aprobar. | Aprobaciones de reembolsos y emails a partners. |
| **Allow-list** | Lista de lo permitido (todo lo demás, prohibido). Más segura que una lista negra. | SQL: solo `SELECT` sobre vistas y columnas permitidas. |
| **AST** (*Abstract Syntax Tree*) | Árbol que representa el código/SQL ya analizado; se valida la estructura, no el texto. | `sqlglot` parsea el SQL generado por LLM (fase 8.2). |
| **Autocorrección** | Devolver al modelo los errores de validación con códigos para que reintente. | `generate_validated` con N intentos y escalado. |
| **Modelo comprometido** | Modelo falso que **obedece al atacante**: mide el harness, no la "bondad" del modelo. | Tests de ataque y red teaming en la CI. |

<a id="g8"></a>

## 8. Multi-agente y asíncrono

| Término | Qué es | En este repo |
|---|---|---|
| **Orquestador** | Componente que coordina varios agentes especializados. | `services/orchestrator` (fase 9). |
| **Router / supervisor / handoff / plan-and-execute** | Patrones multi-agente: clasificar y derivar / un LLM que reparte y revisa / un agente pasa el control a otro / planificar y luego ejecutar. | Router elegido (auditable, 3 llamadas); supervisor medido: 6 llamadas. |
| **Triaje → especialistas** | Un agente clasifica y deriva a Reembolsos, Creativo o Conocimiento. | Una tabla del dominio elige el especialista, no el LLM. |
| **Sobre (*envelope*)** | Mensaje estructurado que pasa entre agentes (intención, idioma, urgencia, resumen), no el texto original. | −63 % de tokens y la inyección del cliente no llega al especialista. |
| **Grafo / estado / checkpointer** | Flujo como nodos y aristas con un estado compartido que se guarda por hilo para reanudar. | LangGraph con checkpoints en PostgreSQL; MAF como alternativa. |
| **Asíncrono / cola** | El que pide no espera: deja un mensaje y otro proceso lo atiende. Absorbe picos. | RabbitMQ en local, SQS en AWS, Service Bus en Azure. |
| **At-least-once** | Las colas garantizan entregar **al menos** una vez: puede llegar duplicado. | Por eso todo consumidor es idempotente. |
| **Idempotencia** | Repetir la misma operación produce el mismo efecto que hacerla una vez. | `Idempotency-Key` en la API; casos reclamados antes de llamar al LLM. |
| **Compare-and-set (CAS)** | Actualizar solo si el valor sigue siendo el que leíste; evita carreras. | Avance del estado de los casos y de las aprobaciones. |
| **Transactional outbox** | Guardar el cambio y el mensaje a publicar en la **misma transacción**; un relay lo publica después. | Cerró el doble triaje por emails duplicados (fase 10.5). |

<a id="g9"></a>

## 9. Escala y resiliencia

| Término | Qué es | En este repo |
|---|---|---|
| **Backpressure** | Señal de "voy lleno, frena" que se propaga hacia atrás en vez de acumular trabajo. | Admisión en el edge, colas y cuotas. |
| **Load shedding** | Rechazar a propósito parte de las peticiones (503 + `Retry-After`) para que el resto funcione. | Por plan en `edge_api`; interno y altas de casos nunca fallan. |
| **Rate limiting** | Limitar peticiones por usuario/cliente y ventana de tiempo (429). | Por usuario en Redis. |
| **GCRA** (*Generic Cell Rate Algorithm*) | Algoritmo de rate limit equivalente a un *token bucket*, que guarda un solo timestamp por clave. | Script Lua atómico en Redis con el reloj de Redis; *fail-open* si Redis cae. |
| **Token bucket** | Cubo que se rellena a ritmo fijo; cada petición gasta fichas. | Límite de TPM en el cliente del LLM (fase 2). |
| **Fail-open / fail-closed** | Si el control falla, dejar pasar / bloquear. | Rate limit: fail-open con límite local; seguridad: fail-closed. |
| **Bulkhead** | Compartimentos estancos de capacidad por ruta para que lo lento no ahogue lo rápido. | Arregló que el 72 % de altas de casos recibiera 503. |
| **Circuit breaker** | Tras varios fallos, dejar de llamar a un servicio un tiempo y fallar rápido. | `CircuitBreakerLLM`. |
| **Caché semántica** | Reutilizar respuestas de preguntas **parecidas** (no idénticas) por similitud de embeddings. | Guarda de facetas: 12/17 paráfrasis reutilizadas y 0 falsos aciertos (sin guarda, 11). |
| **Degradación** | Respuesta peor pero útil cuando el modelo no llega (citas literales marcadas). | Espera de cuota de 2 s en el edge. |
| **Streaming / SSE** (*Server-Sent Events*) | Enviar la respuesta token a token por HTTP mientras se genera. | Respuestas del edge y `POST /cases` con 202 + SSE (fases 10.4–10.5). |
| **TTFT** (*Time To First Token*) | Tiempo hasta el primer token: la latencia que **percibe** el usuario. | p95 de 42 ms con protección frente a 60 s sin ella. |
| **p50 / p95 / p99** | Percentiles de latencia: el 95 % de peticiones tarda menos que el p95. La media engaña. | Todas las métricas de carga y SLO. |
| **Ley de Little** | Concurrencia = tasa de llegada × tiempo en el sistema. Dimensiona réplicas y colas. | Con 150 K TPM, más de 2 réplicas solo producen 429. |
| **Autoescalado por cola** | Escalar workers por mensajes pendientes, no por CPU (un worker de LLM espera, no calcula). | KEDA en Container Apps ↔ ECS *target tracking* sobre SQS. |
| **KEDA** (*Kubernetes Event-Driven Autoscaling*) | Autoescalador por eventos (colas, métricas) para Kubernetes y Container Apps. | Regla `azure-servicebus`. |
| **Prueba de carga** | Simular muchos usuarios para encontrar el límite antes que los clientes. | Locust con 200 usuarios: de 6,8 a 75 req/s. |

<a id="g10"></a>

## 10. Observabilidad

| Término | Qué es | En este repo |
|---|---|---|
| **Observabilidad** | Poder responder "qué pasa y por qué" desde fuera, con trazas, métricas y logs. | Fase 11. |
| **Traza / span / `trace_id`** | Traza = recorrido completo de una petición; span = cada paso con su duración; `trace_id` las une. | Un caso = 14 spans con un solo `trace_id`, de `POST /cases` al resultado. |
| **OpenTelemetry (OTel)** | Estándar abierto para instrumentar una vez y enviar a cualquier backend. | Detrás de `TelemetryPort`; el código no importa el SDK. |
| **OTLP** (*OpenTelemetry Protocol*) | Formato/protocolo con el que se envía la telemetría. | De los servicios al collector; X-Ray acepta OTLP. |
| **Collector** | Proceso intermedio que recibe, filtra (borra PII) y reenvía la telemetría. | Tres configuraciones: local, AWS (ADOT) y Azure. |
| **Convención GenAI** | Nombres estándar de atributos para LLM (`gen_ai.request.model`, `gen_ai.usage.input_tokens`). | Spans de cada llamada al modelo. |
| **Propagación de contexto / W3C `traceparent`** | Pasar el ID de la traza entre servicios y colas para que sea una sola traza. | Atributos de SQS, cabeceras AMQP, Service Bus, columna del outbox, cabecera A2A. |
| **Métricas RED** | *Rate, Errors, Duration*: las tres básicas de un servicio. | Más las de GenAI: TTFT, tokens, coste, 429/503, caché. |
| **SLI / SLO / SLA** | Indicador medido / objetivo interno ("99 % bien") / compromiso contractual con penalización. | 6 SLO en el dominio (`domain/slo.py`). |
| **Presupuesto de error** | Lo que se permite fallar (100 % − SLO). Si se gasta rápido, se congela lo arriesgado. | Explicación a dirección de la fase 11. |
| **Burn rate (multiventana)** | A qué velocidad se gasta el presupuesto; dos ventanas evitan falsas alarmas. | 14,4× en 1 h y 5 min despierta; 6×/3×/1× abre ticket. 71 reglas probadas con `promtool`. |
| **Runbook** | Guía paso a paso de qué hacer cuando salta una alerta. | `docs/runbook-incidentes.md`. |
| **MTTD / MTTR** | Tiempo medio en **detectar** / en **resolver** un incidente. | KPI de negocio de la fase 11. |
| **Muestreo (*sampling*)** | Guardar solo una parte de las trazas para controlar coste. | `TELEMETRY__SAMPLE_RATIO`. |
| **Cardinalidad** | Número de combinaciones de etiquetas de una métrica; alta = caro y lento. | Las métricas nunca llevan IDs. |
| **EMF / ADOT** | *Embedded Metric Format* (métricas dentro de logs de CloudWatch) / *AWS Distro for OpenTelemetry*. | Collector de AWS. |
| **X-Ray / CloudWatch ↔ Application Insights / Azure Monitor** | Trazas y métricas gestionadas de cada nube. | Pendiente de validar en la nube. |

<a id="g11"></a>

## 11. Red teaming y LLMOps

| Término | Qué es | En este repo |
|---|---|---|
| **Red teaming** | Atacar tu propio sistema como lo haría un adversario para encontrar fallos antes que él. | PyRIT y promptfoo (fases 12.4–12.5): 4 problemas encontrados. |
| **Conversor / estrategia / mutación** | Transformaciones de un ataque (base64, leetspeak, traducción, role-play) para saltarse filtros. | 9 conversores de PyRIT y 9 estrategias de promptfoo, congelados en el repo. |
| **Ataque adaptativo / Crescendo** | Un LLM atacante ajusta el ataque en varios turnos según las respuestas. | Fuera de la CI, con presupuesto (AI Red Teaming Agent de Foundry). |
| **LLMOps** | MLOps aplicado a LLM: versionar prompts, evaluar, desplegar, vigilar en producción. | Fase 12.6–12.8. |
| **Prompt versionado / candado** | Cada prompt en Git con versión y hash en `prompts.lock.json`; cambiar texto sin subir versión rompe la CI. | Copia publicada en Langfuse para enlazar generación ↔ versión. |
| **A/B test pareado** | Comparar dos variantes **sobre los mismos casos**; elimina el ruido de casos distintos. | Harness sí/no: 7 a 0, p = 0,016. |
| **McNemar exacto / casos discordantes** | Test estadístico para A/B pareados que solo mira los casos donde las variantes discrepan. | < 10 discordantes = "falta muestra". |
| **p-valor** | Probabilidad de ver esa diferencia si en realidad no hubiera ninguna. < 0,05 suele considerarse significativo. | Regla de decisión escrita antes del experimento. |
| **Drift** | El comportamiento o los datos cambian con el tiempo (usuarios, modelo del proveedor, índice). | Tres señales: intenciones, tools usadas y resolución del juez online. |
| **PSI** (*Population Stability Index*) | Medida de cuánto cambia una distribución entre dos periodos. < 0,1 estable; > 0,25 cambio relevante. | Alerta a partir de 0,25 con muestra mínima. |
| **Release gate** | Job de la CI que bloquea el despliegue si alguna eval cae fuera de la baseline. | `release-gate` (fase 12.1). |

<a id="g12"></a>

## 12. Arquitectura, datos e ingeniería

| Término | Qué es | En este repo |
|---|---|---|
| **Clean Architecture / Ports & Adapters (hexagonal)** | El dominio no depende de nada externo; los proveedores se enchufan por interfaces. | `packages/headache_core`: `domain`, `application`, `ports`, `adapters`. |
| **Dominio** | Reglas de negocio en código determinista. El LLM nunca decide negocio. | `domain/policies.py` (`evaluate_refund`). |
| **Puerto / adaptador** | Interfaz (`typing.Protocol`) / implementación concreta (memoria, Postgres, Azure, Bedrock). | `LLMPort`, `TelemetryPort`, `VectorStorePort`... |
| **Caso de uso** | Orquesta dominio y puertos para una operación concreta. | `application/`. |
| **DDD** (*Domain-Driven Design*) | Modelar el software con el lenguaje y los límites del negocio. | Entidades `Event`, `Order`, `Refund`; contexto de soporte. |
| **Adaptador en memoria** | Implementación falsa de un puerto para tests sin red ni coste. | Toda la CI. |
| **Monorepo / uv workspace** | Varios paquetes en un repo con un solo lockfile. | `uv sync --all-packages`. |
| **OLTP vs OLAP** | Transaccional (muchas escrituras pequeñas) vs analítico (lee mucho). No mezclar. | Consulta de ingresos de 640 ms sobre 3 M de pedidos → réplica o warehouse. |
| **Bloqueo optimista / pesimista** | Detectar conflicto al escribir (versión) / bloquear la fila antes. | `UPDATE ... WHERE available >= :q RETURNING` evita la sobreventa. |
| **`CREATE INDEX CONCURRENTLY`** | Crear índice sin bloquear escrituras (puede quedar `INVALID`). | Migraciones de la fase 1. |
| **Migración (Alembic)** | Cambio de esquema versionado y reproducible. | `packages/headache_core/migrations`. |
| **IaC** (*Infrastructure as Code*) | Infraestructura definida en ficheros versionados. | Terraform en `infra/terraform/{aws,azure,gcp}`, recursos con coste apagados. |
| **Proyecto (GCP)** | Unidad de aislamiento de GCP: facturación, cuotas, APIs e IAM cuelgan de él. Más fuerte que un grupo de recursos de Azure. | `GCP__PROJECT`; se crea a mano y Terraform gestiona lo de dentro. |
| **Habilitar una API (GCP)** | En GCP cada servicio se enciende por proyecto antes de usarlo; si no, `SERVICE_DISABLED`. | `google_project_service` en `infra/terraform/gcp/main.tf`. |
| **Gemini Enterprise Agent Platform** | Nombre actual de Vertex AI como plataforma de agentes y modelos (Agent Runtime, Sessions, Memory Bank, evaluación...). | Pasada GCP (fases 2.G–12.G). |
| **Emulador** | Imitación local de un servicio de nube para probar sin cuenta ni coste. | ElasticMQ (SQS), Service Bus y Pub/Sub en `docker-compose.yml`. |
| **Branch protection / ruleset** | Reglas de GitHub sobre una rama: PR obligatoria, checks en verde, sin force-push. | `main` protegida con `proteger-main` y el check `release-gate` (desde la 0.G). |
| **CI/CD** | Integración continua (calidad automática en cada push) y despliegue continuo. | GitHub Actions: ruff, mypy, pytest, gates de evals, validación de IaC. |
| **Datos sintéticos** | Datos generados con semilla, sin PII real. | `scripts/generate_synthetic_data.py`. |
| **`✅ Hecha*`** | Implementación cerrada en local; falta validar en la nube. | `docs/pendientes-cloud.md`. |

<a id="g13"></a>

## 13. Negocio y gestión

| Término | Qué es | En este repo |
|---|---|---|
| **KPI** | Indicador que mide si se cumple un objetivo de negocio. | Resolución sin humano, TTFT p95, coste por caso resuelto. |
| **ROI** | Retorno de la inversión: valor obtenido frente a lo que cuesta. | Coste por conversación resuelta vs ticket humano. |
| **CSAT** | Satisfacción del cliente medida con encuesta. | KPI del asistente de soporte. |
| **TCO** | Coste total de propiedad: licencias + nube + operación + personas. | Argumento de gestionado vs propio. |
| **Build vs buy** | Construir o comprar (servicio gestionado). | Comprar lo que no diferencia; construir dominio, harness y evals. |
| **Lock-in** | Dependencia de un proveedor difícil de abandonar. | Mitigado con puertos y Terraform en las tres nubes. |
| **Opcionalidad** | Poder cambiar de proveedor de forma barata cuando convenga, sin estar en varios a la vez (multi-cloud activo-activo multiplica la operación). | La pasada GCP: tercera nube con las mismas evals. |
| **PoC → piloto → producción** | Prueba de concepto, uso real acotado y despliegue general, con criterios de salida. | Transversales de `preguntas-entrevista.md`. |
| **RGPD / DPO** | Reglamento europeo de protección de datos / delegado de protección de datos. | Datos sintéticos y redacción de PII. |
| **Postmortem sin culpables** | Análisis de un incidente centrado en el sistema, no en personas, con acciones. | Cada ataque encontrado se convierte en test de regresión. |

---

# Inventario de librerías

Solo las que aparecen en los `pyproject.toml` del monorepo y en el `docker-compose.yml`, más las
herramientas externas que usamos (promptfoo, Terraform). Formato: qué es → para qué la usamos →
equivalente o alternativa.

<a id="l1"></a>

## Modelos y gateway

| Librería | Qué es | Para qué la usamos | Alternativa / equivalente |
|---|---|---|---|
| `openai` | SDK oficial de OpenAI, compatible con Azure OpenAI. | Adaptador `azure_openai.py` (chat, tools, embeddings). | `azure-ai-inference`. |
| `boto3` | SDK oficial de AWS para Python. | Bedrock (Converse, Knowledge Bases, Guardrails, Rerank), SQS, S3. | `aioboto3` para async. |
| `litellm` | Librería/proxy que unifica +100 proveedores de LLM con la API de OpenAI. | Segundo gateway tras `LLMPort` para comparar con el propio (fase 2). | Gateway propio, APIM AI Gateway, Portkey. |
| `azure-identity` | Autenticación con Entra ID (`DefaultAzureCredential`). | Llamar a Azure sin claves (CLI en local, Managed Identity en la nube). | Credenciales de IAM en `boto3`. |
| `google-genai` | SDK oficial unificado de Gemini (Gemini API y Vertex). Sustituye al módulo generativo de `vertexai`, retirado el 24-06-2026. | Adaptador de Gemini (fase 2.G1). En 2.x: ADK exige `<3`. | `openai` / `boto3` (Bedrock). |
| `google-cloud-aiplatform` | SDK de Agent Platform (Vertex) para lo que no es generar texto. | RAG Engine, Agent Runtime, evaluación y experimentos (pasada GCP). | `azure-ai-projects`, `boto3` (AgentCore). |
| `google-cloud-*` (`pubsub`, `storage`, `discoveryengine`, `dlp`, `modelarmor`, `monitoring`) | SDKs por servicio de GCP. | Pub/Sub, Cloud Storage, Vertex AI Search, Sensitive Data Protection, Model Armor y métricas (pasada GCP). | `azure-*` / `boto3`. |

<a id="l2"></a>

## Frameworks de agentes

| Librería | Qué es | Para qué la usamos | Alternativa / equivalente |
|---|---|---|---|
| `langgraph` | Framework de LangChain para agentes como **grafos de estado** con nodos, aristas y checkpoints. | Sabor del agente (fase 7) y motor principal del orquestador (fase 9). | MAF Workflows, Step Functions. |
| `langgraph-checkpoint-postgres` | Persistencia del estado de LangGraph en PostgreSQL. | Reanudar casos por hilo tras una caída o una aprobación humana. | Checkpointer de Redis o en memoria. |
| `langchain-core` | Abstracciones base de LangChain (mensajes, tools, modelos). | Tipos que necesita LangGraph. | — |
| `llama-index-core` | Framework centrado en **datos/RAG**: ingesta, índices, retrievers, también agentes. | Carril C de indexación y retrieval (fases 3–4) y un sabor del agente. | LangChain retrievers, Haystack. |
| `agent-framework-core` (MAF) | Microsoft Agent Framework: sucesor de Semantic Kernel + AutoGen; agentes y workflows tipados. | Sabor del agente y motor alternativo del orquestador. | LangGraph. |
| `strands-agents` | SDK de agentes de AWS, *model-driven* (el modelo decide el bucle), muy ligero. | Sabor del agente; candidato natural en AWS con AgentCore. | Bedrock Agents, LangGraph. |
| `google-adk` (ADK) | Agent Development Kit de Google: agentes, multi-agente y despliegue en Agent Runtime. | Sabor `adk` (7.G1) y cuarto motor del orquestador (9.G1); en el lock desde la 0.G1. | Strands, MAF. |

<a id="l3"></a>

## RAG y datos

| Librería | Qué es | Para qué la usamos | Alternativa / equivalente |
|---|---|---|---|
| `sqlalchemy[asyncio]` | ORM y toolkit SQL de Python, en modo asíncrono. | Repositorios y Unit of Work de `ticketing_api` (fase 1). | SQLModel, asyncpg directo. |
| `asyncpg` | Driver asíncrono y muy rápido de PostgreSQL. | Conexión de SQLAlchemy a Postgres. | `psycopg` 3. |
| `psycopg[binary]` | Driver de PostgreSQL (sync y async). | Lo exige el checkpointer de LangGraph. | — |
| `alembic` | Migraciones de esquema para SQLAlchemy. | `packages/headache_core/migrations` (roles, vistas, outbox, aprobaciones). | Flyway, Liquibase. |
| `pgvector` | Cliente Python de la extensión de vectores de PostgreSQL. | Vector store local y en Aurora/PostgreSQL Flexible. | AI Search, S3 Vectors, OpenSearch. |
| `rank-bm25` | Implementación de BM25 en Python puro. | Parte léxica de la búsqueda híbrida en local. | BM25 de AI Search u OpenSearch. |
| `llama-index-retrievers-bm25`, `-vector-stores-postgres`, `-storage-docstore-postgres` | Integraciones de LlamaIndex. | Carril C: BM25, pgvector y docstore para parent-child. | — |
| `azure-search-documents` | SDK de Azure AI Search. | Índice vectorial/híbrido, Semantic Ranker, Foundry IQ (carriles A y B). | OpenSearch en AWS. |
| `azure-storage-blob` | SDK de Blob Storage. | Origen de documentos del carril gestionado de Azure. | S3 con `boto3`. |
| `numpy` | Cálculo numérico vectorial. | Similitud coseno, embeddings `hashing`, estadística de evals. | — |
| `faker` | Generador de datos falsos realistas. | Datos sintéticos deterministas por semilla (fase 0). | — |
| `sqlglot` | Parser y transpilador de SQL a AST, multi-dialecto. | Validar el SQL generado por LLM: solo `SELECT`, allow-list, `LIMIT` (fase 8.2). | `sqlparse` (menos estricto). |

<a id="l4"></a>

## Tools, MCP y A2A

| Librería | Qué es | Para qué la usamos | Alternativa / equivalente |
|---|---|---|---|
| `fastmcp` | Framework para crear servidores y clientes MCP con decoradores. | 3 servidores MCP con OAuth y el cliente del `support_agent` (fase 6). | SDK oficial `mcp`. |
| `a2a-sdk` | SDK oficial del protocolo A2A (JSON-RPC, Agent Card). | Especialistas como servicios A2A y su cliente en el orquestador (fase 9). | `agent-framework-a2a` (MAF, beta). |
| `httpx` | Cliente HTTP sync/async moderno. | Llamadas entre servicios, tests de APIs. | `requests`, `aiohttp`. |

<a id="l5"></a>

## Harness y seguridad

| Librería | Qué es | Para qué la usamos | Alternativa / equivalente |
|---|---|---|---|
| `presidio-analyzer` | Detector de PII de Microsoft (reglas + NLP), open source. | Segunda capa de detección de PII junto a las regex con checksum. | Azure AI Language PII, Bedrock Guardrails, Amazon Comprehend. |
| `pydantic` | Validación de datos con tipos de Python. | Esquemas de salida estructurada, contratos de tools, validación del harness. | `attrs`, `msgspec`. |
| `pydantic-settings` | Configuración desde variables de entorno y `.env` con tipos. | Settings anidados (`BEDROCK__REGION`) y secretos `SecretStr`. | `dynaconf`. |

<a id="l6"></a>

## Evals, red teaming y LLMOps

| Librería | Qué es | Para qué la usamos | Alternativa / equivalente |
|---|---|---|---|
| `ragas` | Framework de métricas de RAG (faithfulness, context precision/recall). | Referencia estándar frente al juez propio (fase 5). | DeepEval, TruLens. |
| `deepeval` | Framework de evals tipo "pytest para LLM" (G-Eval, faithfulness). | Comparativa; hallazgo: su faithfulness no detecta afirmaciones inventadas. | RAGAS. |
| `azure-ai-evaluation` | Evaluadores de Foundry (groundedness, `ToolCallAccuracy`, `IntentResolution`). | Calibración frente al juez propio (~3× más caro). | Bedrock Evaluations. |
| `langchain-community` | Integraciones de la comunidad LangChain. | Dependencia que necesita RAGAS (versión fijada). | — |
| `pyrit` | *Python Risk Identification Toolkit* de Microsoft para red teaming de IA generativa. | Generar variantes de ataque con 9 conversores (fase 12.4). | Garak, AI Red Teaming Agent de Foundry (que lo usa por dentro). |
| promptfoo (npm) | CLI de evals y red teaming de LLM con estrategias de jailbreak. | 9 estrategias locales sin generación remota (fase 12.5). | PyRIT, Garak. |
| `mlflow` | Plataforma de experimentos: parámetros, métricas y artefactos de cada ejecución. | Registrar los A/B de prompts y harness (fase 12.7). | Azure ML ↔ SageMaker con MLflow gestionado. |
| `langfuse` | Plataforma open source de observabilidad de LLM: trazas, coste, prompts, scores. | Trazas de LLM, scores del juez online y copia de prompts versionados. | Arize Phoenix, LangSmith. |

<a id="l7"></a>

## Observabilidad

| Librería | Qué es | Para qué la usamos | Alternativa / equivalente |
|---|---|---|---|
| `opentelemetry-sdk` | SDK de OpenTelemetry: crea trazas, métricas y logs. | Implementación de `TelemetryPort` (`adapters/otel.py`). | SDKs propietarios (App Insights, X-Ray). |
| `opentelemetry-exporter-otlp-proto-http` | Exporta la telemetría por OTLP/HTTP. | Envío al collector. | Exporter gRPC. |
| `opentelemetry-instrumentation-fastapi` / `-httpx` | Instrumentación automática de servidores y clientes HTTP. | Spans de cada petición sin tocar el código. | — |
| OpenTelemetry Collector (`contrib`) | Proceso que recibe, transforma y reenvía telemetría. | Borra PII y enruta a Jaeger/Prometheus, X-Ray/CloudWatch (ADOT) o App Insights. | ADOT, Azure Monitor Agent. |
| Jaeger | Visor de trazas distribuidas open source. | Trazas en local (`:16686`). | X-Ray ↔ Application Insights. |
| Prometheus (+ `promtool`) | Base de datos de métricas por *scraping* y motor de alertas. | Métricas RED/GenAI, 71 reglas de burn rate probadas en la CI. | Amazon/Azure Managed Prometheus, CloudWatch. |
| Grafana | Dashboards sobre métricas, trazas y logs. | Dashboard como código (`:3001`). | Amazon/Azure Managed Grafana. |

<a id="l8"></a>

## Servicios, colas y caché

| Librería | Qué es | Para qué la usamos | Alternativa / equivalente |
|---|---|---|---|
| `fastapi` | Framework web asíncrono con validación Pydantic y OpenAPI automático. | `ticketing_api`, `retrieval`, `edge_api`, agentes. | Flask, Django REST. |
| `uvicorn` | Servidor ASGI que ejecuta FastAPI. | Arranque de todos los servicios. | Gunicorn + workers uvicorn, Hypercorn. |
| `aio-pika` | Cliente AMQP asíncrono para RabbitMQ. | Colas de indexación y de casos en local. | `pika` (sync). |
| `azure-servicebus` | SDK de Azure Service Bus. | Cola de Azure (y su emulador local). | SQS con `boto3`. |
| `redis` | Cliente de Redis (sync/async). | Rate limit GCRA en Lua, cuota compartida de TPM, caché semántica. | ElastiCache (Valkey) ↔ Azure Managed Redis. |
| `locust` | Herramienta de pruebas de carga escrita en Python. | 200 usuarios simulados contra el edge (fase 10.7). | k6, JMeter, Azure Load Testing. |

<a id="l9"></a>

## Calidad y herramientas

| Herramienta | Qué es | Para qué la usamos |
|---|---|---|
| `uv` | Gestor de paquetes y entornos de Python (Rust), con workspaces y lockfile. | Monorepo: `uv sync --all-packages`, `--locked` en la CI. |
| `ruff` | Linter y formateador de Python muy rápido. | `ruff check` y `ruff format --check` en la CI. |
| `mypy` | Comprobador de tipos estático. | Tipado estricto en todo el monorepo. |
| `pytest` + `pytest-asyncio` | Framework de tests y soporte de async. | Tests unitarios; `-m integration` para los que necesitan Docker. |
| Terraform | IaC multi-nube declarativa. | `infra/terraform/{aws,azure,gcp}`, `fmt -check` y `validate` en la CI. |
| GitHub Actions | CI/CD integrado en GitHub. | Calidad, gates de evals, `release-gate`, validación de IaC. |
| `gh` | CLI de GitHub. | Vigilar la CI tras cada push. |

<a id="l10"></a>

## Infraestructura local (Docker)

| Imagen | Qué es | Perfil / uso |
|---|---|---|
| `pgvector/pgvector:pg17` | PostgreSQL 17 con pgvector. | Base de datos y vector store (por defecto). |
| `rabbitmq:4-management` | Broker de mensajería AMQP con consola web. | Colas en local (por defecto). |
| `softwaremill/elasticmq-native` | Emulador compatible con la API de SQS. | Perfil `sqs`. |
| `servicebus-emulator` + `mssql/server` | Emulador oficial de Service Bus (necesita SQL Server). | Perfil `servicebus`. |
| `google-cloud-cli:<versión>-emulators` | CLI de Google con sus emuladores (Pub/Sub y otros), ~1,7 GB. | Perfil `pubsub` (`--host-port=0.0.0.0:8085`) y job `integration`. |
| `redis` | Almacén en memoria clave-valor. | Perfil `scale` (y dependencia de Langfuse). |
| `langfuse/langfuse` + worker, ClickHouse, MinIO | Langfuse v3 autoalojado (analítica en ClickHouse, ficheros en MinIO, compatible con S3). | Perfil `observability`. |
| `otel/opentelemetry-collector-contrib`, `jaegertracing/jaeger`, `prom/prometheus`, `grafana/grafana` | Stack de telemetría. | Perfil `telemetry`. |
| `headache-mcp:local`, `headache-agents:local` | Imágenes propias de los servidores MCP y de los especialistas A2A. | Perfiles `mcp` y `agents`. |
