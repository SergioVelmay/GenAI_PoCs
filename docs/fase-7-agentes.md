# Fase 7 · Un agente, siete implementaciones: frameworks, servicios gestionados y context engineering

El **Knowledge & Booking Agent** de Headache (RAG + tools de ticketing + memoria) implementado
sobre el mismo núcleo en:

- **Carril B**: `native` (bucle propio, la referencia).
- **Carril C**: `langgraph`, `llamaindex`, `maf` (Microsoft Agent Framework) y `strands`.
- **Carril A**: `foundry` (Foundry Agent Service) y `agentcore` (Bedrock AgentCore Runtime + Memory).

Todos pasan la **misma suite** (`evals/datasets/agent-scenarios-v1.jsonl`, 14 escenarios) con el
mismo modelo guionizado, así que las diferencias que aparecen son del framework, no del modelo.

## 1. Piezas

| Pieza | Dónde | Qué hace |
|---|---|---|
| `AgentSpec` | `services/booking_agent/src/booking_agent/spec.py` | Prompt, tools, skills, política de contexto, `max_iterations` y fallback: lo único que ven todos los sabores. |
| `AgentRuntime` | `headache_core/application/agents/runtime.py` (reexportado en `booking_agent/runtime.py`) | Contrato común: `respond(thread_id, message, ToolContext) -> AgentTurn`. Desde la fase 8 vive en el núcleo para que el harness lo envuelva. |
| `JournaledExecutor` | `booking_agent/runtime.py` | Todas las tools pasan por aquí: validación, timeouts, saneamiento, lock de escrituras y diario que lee la eval. |
| Puentes al gateway | `booking_agent/bridges/` | Cada framework llama al modelo a través de nuestro `LLMPort` (routing, fallback, caché, coste, trazas). |
| Context engineering | `headache_core/application/agents/` | Orden del prompt, compactación con resumen, hilos con dueño, preferencias, skills bajo demanda. |
| Carril A | `booking_agent/managed/` | Cliente/emulador de Foundry Agent Service y contrato HTTP de AgentCore Runtime. |
| AgentCore Memory | `headache_core/adapters/agentcore_memory.py` | `CheckpointStore` (eventos) y `PreferenceStore` (registros a largo plazo). |
| Suite | `evals/src/rag_evals/agents/` | Mundo determinista, `ScenarioLLM`, invariantes de seguridad, informe `evals/reports/agents.{md,json}`. |

## 2. Context engineering (común a todos los sabores)

1. **Orden del prompt pensado para la caché**: instrucciones + índice de skills (estable para
   todos los clientes, primer checkpoint) → contexto de sesión (preferencias + resumen, marcado
   como *datos, no instrucciones*) → historial (segundo checkpoint al final) → mensaje nuevo.
   Bedrock lo traduce a `cachePoint`; Azure OpenAI cachea solo los prefijos idénticos.
2. **Memoria a corto plazo**: compactación por intercambios completos (nunca se parte una ronda de
   tools) con histéresis (`trigger_tokens` → `target_tokens`) y resumen incremental con el modelo
   barato. Sin resumen se pierde el dato antiguo: hay un test que lo demuestra.
3. **Memoria a largo plazo**: preferencias de lista cerrada (`language`, `home_city`,
   `favorite_category`) guardadas con una tool explícita (`remember_preference`).
4. **Checkpoints de hilo** con dueño (anti-IDOR) y concurrencia optimista (`expected_version`).
5. **Skills bajo demanda**: el prompt solo lleva el índice; `load_skill` trae las instrucciones y
   recursos cuando hacen falta (menos tokens fijos, prefijo estable).

## 3. Resultados de la suite (modo `scripted`, coste 0)

| Sabor | Escenarios | Llamadas LLM | Reutilización de prefijo | Invariantes | Coste del framework (p50 / p95 ms) | Código del sabor + puente (líneas) |
|---|---:|---:|---:|---:|---:|---:|
| native | 14/14 | 50 | 67,8 % | 0 | 2,7 / 6,8 | 49 |
| langgraph | 14/14 | 50 | 67,8 % | 0 | 11,9 / 20,3 | 154 + 163 |
| llamaindex | 14/14 | 51 | 68,4 % | 0 | 20,5 / 64,2 | 131 + 174 |
| maf | 14/14 | 50 | 67,8 % | 0 | 3,5 / 14,8 | 150 + 118 |
| strands | 14/14 | 50 | 67,8 % | 0 | 7,3 / 11,8 | 110 + 186 |
| foundry (emulado) | 13/14 | 48 | 68,7 % | 0 | 2,2 / 4,2 | 49 + 125 |
| agentcore (contrato en proceso, aloja strands) | 14/14 | 50 | 67,8 % | 0 | 7,3 / 13,2 | 29 + 76 |

- **Paridad**: los sabores de framework y `agentcore` ejecutan exactamente las mismas tools, en
  el mismo orden, que `native` en los 14 escenarios (test `test_framework_flavors_match_the_native_reference`).
- **LlamaIndex hace una llamada más** en el escenario de `max_iterations`: cuenta respuestas del
  modelo y corta *antes* de ejecutar las tools de la última, así que necesita N + 1.
- **Foundry falla un escenario a propósito**: responde bien al hilo largo, pero no compacta; el
  historial es del servicio. Lo fija `test_foundry_cannot_compact_a_service_side_conversation`.
- Las latencias son el sobrecoste del framework sin red; con un LLM real (cientos de ms a
  segundos por llamada) son irrelevantes. Lo que importa en coste es **el número de llamadas y los
  tokens**, y ahí todos empatan salvo LlamaIndex (+1 llamada al agotar iteraciones).

## 4. Comparativa

| | native | LangGraph | LlamaIndex | MAF | Strands | Foundry Agent Service | AgentCore |
|---|---|---|---|---|---|---|---|
| **Modelo mental** | Bucle explícito | Grafo de estado (nodos, aristas condicionales, reducers) | Workflow de eventos | Agente + cliente por capas (el bucle de tools vive en el cliente) | Bucle "model-driven" mínimo | Agente servidor + conversación | Tu contenedor en una microVM por sesión |
| **Control del bucle** | Total | Total (el grafo es tuyo) | Medio | Medio-alto (middleware de chat/función) | Medio (hooks, `limits`) | Bajo: decide el servicio, tú ejecutas tools | El del sabor que alojes |
| **Persistencia** | `CheckpointStore` propio | Checkpointer por paso (`InMemorySaver`, `PostgresSaver`) | No impone (aquí, nuestro store) | `HistoryProvider` (aquí, nuestro store) | `session_manager` (aquí, nuestro store) | `conversation` del servicio | AgentCore Memory (eventos + registros) |
| **Compactación** | Nuestra política | Nuestra política en un nodo (`RemoveMessage`) | Nuestra política antes del turno | Nuestra política | Nuestra política (`NullConversationManager`) | **No controlable** (truncado del servicio) | La del sabor alojado |
| **Identidad (fuera del modelo)** | `ToolContext` | `context_schema` del runtime | Closure por turno | `function_invocation_kwargs` | `invocation_state` | Las tools corren en tu backend | `runtimeUserId` (solo con `InvokeAgentRuntimeForUser`) |
| **Límite de iteraciones** | Fallback determinista | Contador + nodo `fallback` | Excepción con N + 1 | Llamada extra sin tools → `ChatMiddleware` la sustituye | `limits={"turns": N}` (igual que native) | Contado en el cliente | El del sabor alojado |
| **Observabilidad** | Gateway → Langfuse/OTel | + callbacks LangChain/LangSmith | + instrumentación de LlamaIndex | + OTel GenAI integrado (`ChatTelemetryLayer`) | + OTel integrado | + trazas del proyecto en Foundry (App Insights) | + AgentCore Observability (CloudWatch) |
| **Portabilidad** | Máxima | Alta (Python/JS, cualquier modelo) | Alta | Alta (Python/.NET; nativo en Foundry) | Alta (nativo en AgentCore) | Azure | AWS (pero el contenedor es portable) |
| **DX / curva** | Escribes todo | Verboso pero explícito; ideal para flujos con pasos y aprobación | Sencillo para RAG; agentes con sorpresas | Muchas piezas (capas, providers, middleware) bien pensadas | El más directo | Poco código; tools y prompt viven fuera | Poco código cliente; empaquetar y desplegar el contenedor |

## 5. Hallazgos (lo que no cuentan los "hello world")

1. **Identidad**: en ningún sabor el modelo elige `customer_id`. Cada framework tiene su canal
   (contexto de runtime, `invocation_state`, kwargs de invocación); el invariante `identity_args`
   lo comprueba en los siete.
2. **LangGraph persiste la entrada en el primer paso**: comprobar el dueño del hilo *dentro* del
   grafo dejaría escribir en hilos ajenos. Se comprueba antes de `ainvoke`.
3. **LlamaIndex `Memory` ordena por `time.time_ns()`** en SQLite: con la resolución del reloj de
   Windows, los mensajes del turno podían quedar antes que la semilla (fallo intermitente en frío).
   Se sustituyó por una memoria de lista. Lo detectó el test de paridad.
4. **MAF y Strands se tragan el JSON inválido** (MAF valida antes de llamar; Strands lo cambia por
   `{}`). Los puentes lo envuelven en `__raw_arguments__` para que el error llegue al ejecutor y al diario.
5. **Strands amplía en sitio** la lista de mensajes que le pasas: hay que fijar dónde empieza el
   turno antes de invocar.
6. **MAF, al agotar iteraciones, gasta una llamada extra** pidiendo respuesta sin tools; un
   `ChatMiddleware` la sustituye por el fallback sin llamar al modelo.
7. **Foundry: el dueño del hilo es tuyo** (el servicio no sabe de clientes) y **el contexto no
   se compacta**: el historial solo crece y su coste no lo controlas tú.
8. **AgentCore restringe identificadores** (`sessionId` `[a-zA-Z0-9-_]`, sesión de Runtime ≥ 33
   caracteres): se derivan con hash. Derivar la sesión de cliente + hilo aísla las microVM por cliente.
9. **Dependencias**: `strands-agents` exige `mcp<2.2`; el lock resolvió `mcp` 2.1.1 (compatible
   con FastMCP 4.1, tests MCP verdes). En un monorepo, cada framework añade restricciones.

## 6. Equivalencias Azure ↔ AWS ↔ framework

| Concepto | Azure | AWS | Frameworks |
|---|---|---|---|
| Agente gestionado | Foundry Agent Service (prompt agents) | Bedrock Agents / AgentCore Runtime | — |
| Hospedar tu propio agente | Foundry hosted agents / Container Apps | AgentCore Runtime | LangGraph Platform |
| Historial de conversación | `conversation` (Responses API) | AgentCore Memory (eventos) | Checkpointer (LangGraph), `HistoryProvider` (MAF), `session_manager` (Strands) |
| Memoria a largo plazo | Memoria de Foundry (preview) | AgentCore Memory (registros + estrategias) | `BaseStore` (LangGraph), memory blocks (LlamaIndex) |
| Tools del cliente | *Function tools* | *Return of control* / tools del contenedor | `@tool`, `FunctionTool`, `AgentTool` |
| Middleware / hooks | Middleware de MAF | Hooks de Strands | Middleware de LangChain `create_agent` |
| Observabilidad | Foundry tracing + App Insights | AgentCore Observability + CloudWatch | LangSmith, OTel GenAI |
| SDK "nativo" de la nube | Microsoft Agent Framework | Strands Agents | — |

## 7. Recomendación por escenario

- **Flujos con pasos, ramas y aprobación humana (fase 8)** → LangGraph (estado explícito y
  checkpoint por paso) o el bucle propio si el flujo es pequeño.
- **Plataforma Azure / .NET en el equipo** → MAF; desplegarlo como agente alojado en Foundry.
- **Plataforma AWS** → Strands en AgentCore Runtime + Memory.
- **Agente "estándar" con poco código y sin requisitos de contexto** → Foundry Agent Service
  (asumiendo que el servicio gestione el historial).
- **RAG avanzado con agentes ligeros** → LlamaIndex (fuerte en ingesta/retrieval, más sorpresas en agentes).
- **Siempre**: tools, identidad, evals y gateway fuera del framework (puertos), para que cambiar de
  framework sea escribir un sabor, no reescribir el producto.

## 8. Cómo ejecutarlo

```powershell
uv run python -m rag_evals.agents                          # 7 sabores, coste 0
uv run python -m rag_evals.agents --flavor maf --scenario refund-approved-es
uv run pytest services/booking_agent evals/tests/test_agent_evals.py
uv run python scripts/provision_foundry_agent.py           # definición del agente (dry-run)
# Con coste (confirmar antes): uv run python -m booking_agent --flavor strands --customer cus_... "pregunta"
```

### De extremo a extremo en Azure (18-10-2026)

`booking_agent` con el conocimiento en AI Search (`RETRIEVAL__BACKEND=azure_search`, embeddings
de Foundry y Semantic Ranker), las tools por los servidores MCP en Docker y la API de ticketing:

| Sabor | Pregunta | Tools | Resultado |
|---|---|---|---|
| `native` | Apertura de puertas en París y cancelación de un tour | 2 × `search_knowledge` (con `city` y `doc_type`) | Correcto (45 min; reembolso del 100 %); 3 llamadas, 0,0002 $ |
| `foundry` | Apertura en París y mis pedidos | `list_my_orders` + `search_knowledge` | Correcto; agente `headache-booking-agent` publicado con `--apply` |

Hallazgo: con la pregunta "reembolso de gastronomía y mis pedidos", el agente `native` respondió
con la política que trae `search_events` sin consultar la base de conocimiento, y afirmó que el
cliente tenía "varias reservas de gastronomía" sin comprobarlo. Es el tipo de fallo que tiene que
cazar la suite de agentes con LLM real y un juez (pendiente).

## 9. Pendiente de validación con coste

Ver la sección "Fase 7" de [pendientes con coste](pendientes-cloud.md): suite con LLM real
(`--mode llm --budget-usd`), agente en Foundry (`--apply`), imagen del Runtime en ECR (fase 13) con
`agentcore_enabled=true`, y estrategias de extracción de AgentCore Memory frente a la tool explícita.

## 10. Checkpoint de entrevista

Las preguntas técnicas y de negocio/stakeholders de esta fase, con sus respuestas modelo, están en
[preguntas de entrevista · fase 7](preguntas-entrevista.md#fase-7).
