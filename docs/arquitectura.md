# Arquitectura por niveles · De la vista general al detalle

Un diagrama único de todo el sistema es ilegible. Aquí va por **niveles de zoom**, como en un mapa:

- **Nivel 0**: el sistema completo en una pantalla (el del [README](../README.md), ampliado con la
  puerta pública, la observabilidad y las evals).
- **Nivel 1**: un zoom por bloque (modelos, RAG, tools, agente, multi-agente, escala,
  observabilidad, evals). Cada uno con "qué contar en 30 segundos" y enlace a su fase.
- **Nivel 2**: el recorrido de un caso real de punta a punta, para contarlo en una pizarra.

Siglas y términos: [glosario](glosario.md). Preguntas y respuestas: [preguntas-entrevista.md](preguntas-entrevista.md).

## Índice

- [Nivel 0 · Vista general](#n0)
- Nivel 1: [1. Capas del código](#n1-capas) · [2. Modelos y LLM gateway](#n1-modelos) ·
  [3. Indexación](#n1-indexacion) · [4. Retrieval (RAG)](#n1-rag) · [5. Tools y MCP](#n1-mcp) ·
  [6. Agente y harness](#n1-agente) · [7. Multi-agente](#n1-multiagente) ·
  [8. Edge y escala](#n1-escala) · [9. Observabilidad](#n1-observabilidad) ·
  [10. Evals y LLMOps](#n1-evals) · [11. Despliegue en AWS y Azure](#n1-despliegue)
- [Nivel 2 · Recorrido de un caso](#n2)

<a id="n0"></a>

## Nivel 0 · Vista general

```mermaid
flowchart LR
  PUB["Público<br/>(chat)"] --> EDGE["edge_api<br/>admisión · caché · SSE"]
  INT["Operaciones<br/>y soporte"] --> AG["booking_agent<br/>(7 sabores)"]
  MAIL["Emails de clientes"] --> EDGE
  EDGE -->|"POST /cases"| ORQ["orchestrator<br/>triaje → especialistas"]

  AG & ORQ --> H["harness<br/>guardrails · aprobación humana"]
  EDGE --> RET
  H --> GW["LLM gateway<br/>routing · fallback · coste"] --> LLM["Azure OpenAI · Bedrock"]
  H --> MCP["MCP gateway → servidores MCP"] --> API["ticketing_api"] --> PG[("PostgreSQL")]
  H --> RET["retrieval<br/>híbrido · rerank · citas"] --> VS[("Vector store")]
  DOCS["Documentos"] --> Q[["Cola"]] --> IDX["indexer"] --> VS

  OBS["Observabilidad<br/>OpenTelemetry"] -.-> EDGE & ORQ & GW
  EV["Evals + red teaming"] -.->|"gate en la CI"| AG & ORQ
```

**En 30 segundos:** tres entradas (chat público, agente interno y emails) llegan a agentes que
nunca tocan nada directamente: todo pasa por el **harness** (seguridad), el **LLM gateway**
(modelos y coste), el **MCP gateway** (acciones sobre el negocio) y el **retrieval** (conocimiento).
El conocimiento se indexa por eventos. Todo está trazado con OpenTelemetry y ningún cambio se
despliega sin pasar las evals.

---

<a id="n1-capas"></a>

## Nivel 1 · 1. Capas del código (Clean Architecture)

```mermaid
flowchart TB
  subgraph SVC["services/ (FastAPI, workers, agentes)"]
    S1["ticketing_api · retrieval · indexer · edge_api"]
    S2["booking_agent · orchestrator · mcp_servers"]
  end
  subgraph HAR["packages/harness"]
    H1["validación · guardas · PII · SQL seguro · aprobaciones"]
  end
  subgraph CORE["packages/headache_core"]
    APP["application/<br/>casos de uso"]
    DOM["domain/<br/>reglas de negocio (Python puro)"]
    PORTS["ports/<br/>typing.Protocol"]
    ADP["adapters/<br/>memory · postgres · azure_* · bedrock*"]
  end
  SVC --> HAR --> CORE
  SVC --> APP
  APP --> DOM
  APP --> PORTS
  ADP -.->|"implementa"| PORTS
```

**Qué contar:**

- Las flechas solo van hacia dentro: el dominio no importa nada externo; `harness` depende de
  `core`, nunca al revés.
- Cada proveedor (Azure, AWS, Postgres) es un **adaptador** de un **puerto**; los tests usan el
  adaptador `memory` (deterministas, sin red ni coste).
- El LLM propone; el **dominio decide** (`evaluate_refund`). Detalle: [fase 0](fase-0-cimientos.md).

<a id="n1-modelos"></a>

## Nivel 1 · 2. Modelos y LLM gateway

```mermaid
flowchart LR
  UC["Caso de uso / agente"] --> R["Routing<br/>barato o potente"]
  R --> F["Fallback<br/>otro proveedor o región"]
  F --> CB["Circuit breaker<br/>+ bulkhead"]
  CB --> Q["Cuota TPM compartida<br/>(Redis, espera acotada)"]
  Q --> O["Observado<br/>tokens · coste · trazas"]
  O --> AZ["Azure OpenAI"]
  O --> BR["Bedrock Converse"]
  O -.-> LF[("Langfuse / OTel")]
```

**Qué contar:**

- Es una cadena de **decoradores** sobre el mismo `LLMPort`: cada pieza añade una
  responsabilidad (routing, fallback, resiliencia, cuota, observabilidad) sin que el código de
  negocio lo sepa.
- Routing: 18/20 emails al modelo barato con 100 % de acierto; las escrituras, siempre al potente.
- Prompt caching: lo estable primero (Azure automático, Bedrock con `cachePoint`).
- Detalle: [fase 2](fase-2-modelos.md) y resiliencia en la [fase 10](fase-10-escala.md).

<a id="n1-indexacion"></a>

## Nivel 1 · 3. Indexación event-driven

```mermaid
flowchart LR
  SRC["Documento publicado<br/>(políticas, FAQs, fichas)"] --> Q[["Cola<br/>RabbitMQ · SQS · Service Bus"]]
  Q --> W["indexer (worker)"]
  W --> L{"¿Hash cambiado?<br/>(ledger)"}
  L -->|"no"| SKIP["Nada que hacer<br/>(0 embeddings)"]
  L -->|"sí"| CH["Chunking"] --> EMB["Embeddings"] --> UP["Upsert chunks nuevos<br/>→ borrar obsoletos"]
  UP --> VS[("pgvector · AI Search · S3 Vectors")]
  W -->|"falla N veces"| DLQ[["DLQ"]]

  BLOB["S3 / Blob"] --> MAN["Carril A: Bedrock KB /<br/>indexer de AI Search"] --> VS
```

**Qué contar:**

- Por eventos, no por lotes: publicar un documento encola su indexación (frescura en minutos).
- **Idempotente**: hash en un ledger (republicar 116 documentos = 0 embeddings) y orden
  upsert → borrado para que una consulta nunca vea el documento vacío.
- Tres carriles con el mismo corpus: A gestionado (Bedrock KB / AI Search), B SDK propio, C
  LlamaIndex. Detalle: [fase 3](fase-3-comparativa.md).

<a id="n1-rag"></a>

## Nivel 1 · 4. Retrieval (RAG)

```mermaid
flowchart LR
  QY["Pregunta<br/>+ identidad"] --> ACL["Filtro ACL y metadatos<br/>(en el servidor)"]
  ACL --> BM["BM25<br/>(léxica)"]
  ACL --> VEC["Vectorial<br/>(embeddings)"]
  BM & VEC --> RRF["Fusión RRF"]
  RRF --> RR["Reranker<br/>Semantic Ranker · Bedrock Rerank"]
  RR --> PAR["Parent-child<br/>(contexto del padre)"]
  PAR --> GEN["Respuesta con citas<br/>literales"]
  GEN --> AB{"¿Hay evidencia?"}
  AB -->|"no"| NO["Abstención / escalado"]
  AB -->|"sí"| OUT["Respuesta"]
```

**Qué contar:**

- La **ACL se aplica antes del ranking**, en el backend: el LLM no puede ampliar el alcance.
- Híbrida no es gratis: en Azure, sola rindió peor que el vector (recall@5 0,79 frente a 0,95);
  con reranker, la mejor (0,97). HyDE y query rewriting medidos y descartados (×3 latencia).
- Mejor "no lo sé" que una política inventada. Detalle: [fase 4](fase-4-retrieval.md).

<a id="n1-mcp"></a>

## Nivel 1 · 5. Tools y MCP

```mermaid
flowchart LR
  AG["Agente"] -->|"token aud=gateway"| GW["MCP gateway<br/>auth · auditoría · rate limit"]
  GW -->|"OBO: aud=catalog<br/>catalog:read"| C["catalog"]
  GW -->|"aud=ticketing<br/>orders:read"| T["ticketing"]
  GW -->|"aud=refunds<br/>refunds:write"| R["refunds"]
  C & T & R --> API["ticketing_api<br/>(la política de dominio decide)"]
```

**Qué contar:**

- El modelo solo **propone** llamadas; la identidad sale del token, nunca de los argumentos.
- Tres servidores por **mínimo privilegio**; el gateway da un único endpoint y cambia el token por
  uno nuevo para cada servidor (OBO), sin *token passthrough*.
- En AWS, AgentCore Gateway; en Azure, APIM. Detalle: [fase 6](fase-6-tools-mcp.md).

<a id="n1-agente"></a>

## Nivel 1 · 6. Agente y harness

```mermaid
flowchart LR
  U["Usuario"] --> IN["Guardas de entrada<br/>inyección · PII"]
  IN --> RT["AgentRuntime<br/>native · LangGraph · LlamaIndex · MAF<br/>Strands · Foundry · AgentCore"]
  RT <--> CTX["Context engineering<br/>prompt estable · memoria · compactación"]
  RT --> EX["JournaledExecutor + GuardedExecutor<br/>validación · políticas · diario"]
  EX --> TOOLS["Tools / MCP / RAG"]
  TOOLS --> DS["DocumentShield<br/>(inyección indirecta)"] --> RT
  EX -.->|"irreversible"| AP["Aprobación humana<br/>(cuatro ojos)"]
  RT --> OUT["Guardas de salida<br/>afirmaciones sin respaldo"] --> U
```

**Qué contar:**

- **Siete sabores** del mismo agente detrás de un contrato (`AgentRuntime`): el framework
  orquesta, pero identidad, tools, modelo y evals son nuestros.
- El **harness** es el punto de paso obligatorio: "el modelo propone; el harness dispone".
  Sin harness, 0/6 ataques contenidos; con harness, 6/6 en los siete sabores.
- La última capa no es un filtro sino arquitectura: identidad fuera del modelo y aprobación
  humana en lo irreversible. Detalle: [fase 7](fase-7-agentes.md) y [fase 8](fase-8-harness.md).

<a id="n1-multiagente"></a>

## Nivel 1 · 7. Multi-agente y asíncrono

```mermaid
flowchart LR
  E["Email"] --> API["POST /cases (202)"]
  API --> TX[("Caso + outbox<br/>en una transacción")]
  TX --> RL["Relay"] --> Q[["Cola"]]
  Q --> TRI["Triaje<br/>LLM clasifica · dominio enruta"]
  TRI -->|"sobre estructurado"| REF["Reembolsos"]
  TRI --> CRE["Creativo"]
  TRI --> KNO["Conocimiento"]
  REF & CRE & KNO --> HR["Revisión humana"]
  HR --> OUT["Respuesta al cliente / partner"]
```

**Qué contar:**

- Patrón **router**: el LLM clasifica y una tabla del dominio elige el especialista (auditable,
  3 llamadas; el supervisor costaba 6).
- Entre agentes viaja un **sobre** (intención, idioma, urgencia), no el email: −63 % de tokens y la
  inyección del cliente no llega al especialista.
- **Idempotencia**: el caso se reclama antes de llamar al LLM y el *transactional outbox* evita
  perder o duplicar mensajes. Mismo flujo en LangGraph, MAF y con colas; especialistas en proceso
  o remotos por **A2A**. Detalle: [fase 9](fase-9-multiagente.md).

<a id="n1-escala"></a>

## Nivel 1 · 8. Edge y escala

```mermaid
flowchart LR
  U["10.000 usuarios<br/>en una venta"] --> V["Validación 422<br/>JWT 401"]
  V --> SH["Load shedding<br/>por plan (503)"]
  SH --> RL["Rate limit GCRA<br/>por usuario (429)"]
  RL --> C{"¿Caché<br/>semántica?"}
  C -->|"acierto"| SSE["SSE al usuario"]
  C -->|"fallo"| M["Modelo con espera<br/>acotada (2 s)"]
  M -->|"saturado"| DEG["Respuesta degradada<br/>(citas literales)"] --> SSE
  M --> SSE
  RL & C <--> R[("Redis")]
```

**Qué contar:**

- Decidir **pronto y barato**, por capas: cada filtro descarta antes de llegar al modelo.
- Caché semántica con **guarda de facetas** (ciudad, fecha, IDs): 12/17 paráfrasis reutilizadas y
  0 falsos aciertos. Con protección: de 6,8 a 75 req/s y TTFT p95 de 42 ms.
- Workers escalados por **longitud de cola**, con el techo que pone la cuota del modelo
  (ley de Little). Detalle: [fase 10](fase-10-escala.md).

<a id="n1-observabilidad"></a>

## Nivel 1 · 9. Observabilidad

```mermaid
flowchart LR
  APP["Servicios<br/>TelemetryPort → OpenTelemetry"] -->|"OTLP"| COL["Collector<br/>borra prompts y PII"]
  COL --> LOC["Local: Jaeger ·<br/>Prometheus · Grafana"]
  COL --> AWS["AWS: X-Ray ·<br/>CloudWatch (ADOT)"]
  COL --> AZ["Azure: Application<br/>Insights"]
  COL -.->|"spans GenAI"| LF["Langfuse<br/>coste · prompts · scores"]
  LOC --> AL["71 alertas de burn rate<br/>sobre 6 SLO → runbook"]
```

**Qué contar:**

- Se instrumenta **una vez** (convención GenAI de OTel detrás de un puerto) y el collector decide
  el destino: cambiar de backend es configuración.
- El `traceparent` viaja por colas, outbox y A2A: un caso = 14 spans con un solo `trace_id`.
- Sin PII en trazas ni métricas; alertas de burn rate multiventana, cada una con su runbook.
  Detalle: [fase 11](fase-11-observabilidad.md).

<a id="n1-evals"></a>

## Nivel 1 · 10. Evals y LLMOps (el ciclo de calidad)

```mermaid
flowchart LR
  subgraph PRE["Antes de desplegar (CI, coste 0)"]
    direction TB
    RAG["Evals de RAG<br/>MRR · nDCG · citas"]
    AGT["Evals de agentes<br/>trayectoria · resolución"]
    ATK["Ataques + red teaming<br/>PyRIT · promptfoo"]
    LOCK["Candado de prompts"]
  end
  PRE --> GATE{{"release-gate"}} --> PROD["Producción"]
  PROD -->|"muestra"| JUDGE["Juez online<br/>(calibrado, kappa ≥ 0,6)"]
  PROD --> DRIFT["Drift (PSI)"]
  JUDGE & DRIFT -->|"casos nuevos"| PRE
  AB["A/B pareado<br/>McNemar · MLflow"] -->|"nueva versión"| LOCK
```

**Qué contar:**

- **Offline** (CI, gratis, en cada PR) y **online** (muestra del tráfico real, con presupuesto).
- Se evalúa el **resultado** (estado final, invariantes), no la redacción; el juez etiqueta
  hechos y el dominio decide.
- Cerrar el ciclo: lo que detectan el juez online y el drift vuelve como casos al dataset.
  Detalle: [fase 5](fase-5-evals.md) y [fase 12](fase-12-evals-llmops.md).

<a id="n1-despliegue"></a>

## Nivel 1 · 11. Despliegue en AWS y Azure (objetivo de las fases 13–14)

```mermaid
flowchart LR
  subgraph AWS["AWS (producción de Headache)"]
    direction TB
    A1["API Gateway + WAF"] --> A2["ECS Fargate<br/>servicios y workers"]
    A2 --> A3["Bedrock · AgentCore"]
    A2 --> A4[("Aurora PostgreSQL<br/>+ pgvector")]
    A2 --> A5[["SQS"]]
    A2 --> A6[("ElastiCache")]
    A2 -.-> A7["X-Ray · CloudWatch"]
  end
  subgraph AZ["Azure (espejo)"]
    direction TB
    Z1["API Management"] --> Z2["Container Apps<br/>+ KEDA"]
    Z2 --> Z3["Foundry"]
    Z2 --> Z4[("PostgreSQL Flexible<br/>· AI Search")]
    Z2 --> Z5[["Service Bus"]]
    Z2 --> Z6[("Managed Redis")]
    Z2 -.-> Z7["Application Insights"]
  end
  GH["GitHub Actions<br/>OIDC · Terraform"] --> AWS & AZ
```

**Qué contar:**

- El mismo código en ambas nubes: solo cambian los **adaptadores** y el Terraform.
- Identidad sin secretos: IAM Roles ↔ Managed Identity; OIDC desde la CI.
- Aún no desplegado: es el plan de las fases 13 (AWS) y 14 (Azure). Equivalencias completas en la
  [chuleta Azure → AWS](preguntas-entrevista.md#chuleta).

---

<a id="n2"></a>

## Nivel 2 · Recorrido de un caso de punta a punta

Un cliente escribe: *"Quiero el reembolso del pedido 1234, el concierto se canceló"*.

```mermaid
sequenceDiagram
  autonumber
  participant C as Cliente
  participant E as edge_api
  participant DB as PostgreSQL
  participant Q as Cola
  participant T as Triaje
  participant R as Reembolsos
  participant H as Harness
  participant M as MCP gateway
  participant P as Operaciones

  C->>E: email (POST /cases)
  E->>DB: caso + outbox (una transacción)
  E-->>C: 202 Aceptado
  DB->>Q: relay publica (con traceparent)
  Q->>T: mensaje
  T->>T: reclama el caso (idempotencia)
  T->>T: LLM clasifica: reembolso · es · urgente
  T->>R: sobre estructurado (sin el email)
  R->>H: tool request_refund(1234)
  H->>M: guardas OK → llamada con token OBO
  M-->>H: política de dominio: reembolsable, pendiente de aprobación
  H-->>R: approval_required
  R->>P: borrador + propuesta de reembolso
  P->>H: aprueba (cuatro ojos)
  H->>M: ejecuta lo congelado (sin el modelo)
  H-->>C: respuesta validada (sin promesas sin respaldo)
```

**Cómo contarlo (2 minutos en una pizarra):**

1. **Entrada asíncrona y segura**: el edge acepta en milisegundos (202) y guarda caso y mensaje en
   una transacción; un reintento o un email duplicado no duplica nada.
2. **Triaje barato**: el modelo barato clasifica; el dominio decide a quién va. El especialista
   recibe un sobre, no el texto del cliente.
3. **El especialista propone, no ejecuta**: la tool pasa por el harness y el MCP gateway; la
   política de reembolso es código y lo irreversible espera a una persona.
4. **Lo aprobado se ejecuta sin el modelo**, con los argumentos congelados.
5. **Todo es una traza** (un solo `trace_id` de principio a fin) y el caso cuenta para las métricas, el juez
   online y el drift.
