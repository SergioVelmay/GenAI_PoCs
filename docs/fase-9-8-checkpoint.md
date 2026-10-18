# Checkpoint 8→9 · Puesta a punto antes de la orquestación multi-agente

**Objetivo:** dejar el repo, la forma de trabajar con IA y la nube preparados antes de la fase 9:
CI al día, documentación completa de todas las fases, instrucciones para agentes según las buenas
prácticas de Anthropic, material de entrevista recopilado y la conexión real con Azure para
abordar los pendientes con coste.

> Estado: 🚧 en curso. Este documento se va completando a medida que avanza el checkpoint.

| Commit | Qué |
|---|---|
| `f15404f` | CI: acciones fijadas por SHA, runner `ubuntu-26.04`, Dependabot; docs de las fases 0–2 |
| `dc19ca0` | `README.md`, `AGENTS.md`, instrucciones por área, skills, pendientes y hook de términos prohibidos |
| `c13d26a` | Recopilatorio de preguntas de entrevista (técnicas y de negocio) por fase |
| `f0d8f75` | Foundry en Terraform, AI Search efímero, presupuesto con alertas y despliegue en Azure |
| `04e635c` | Nombres de Azure con la convención CAF (sin sufijo aleatorio) |

## 1. CI

| Aviso | Causa | Solución |
|---|---|---|
| Node.js 20 deprecado | `setup-uv@v6`, `upload-artifact@v4`, `setup-terraform@v3` | Subida a `checkout` v7.0.1, `setup-uv` v10.3.0, `upload-artifact` v7.0.2, `setup-terraform` v4.0.1 |
| `ubuntu-latest` pasa a Ubuntu 26.04 (19-10-2026) | Imagen flotante | Runner fijado `ubuntu-26.04`: la imagen cambia cuando lo decidimos |

- **Acciones fijadas por SHA de commit** con la versión en un comentario: un tag se puede mover, un
  SHA no (defensa ante ataques a la cadena de suministro; `setup-uv` ya no publica tags mayores).
- **Dependabot** (`.github/dependabot.yml`) propone las subidas en una PR mensual agrupada.
- Se revisaron los cambios incompatibles de cada versión mayor antes de subir: ninguno nos afecta.

## 2. Documentación

- Docs de las fases que faltaban: [fase 0](fase-0-cimientos.md), [fase 1](fase-1-sql.md) (con el
  benchmark real de índices en escala `large`) y [fase 2](fase-2-modelos.md).
- [README.md](../README.md) en la raíz: visión general, diagrama, carriles, arranque rápido e índice.
- [preguntas-entrevista.md](preguntas-entrevista.md): **fichero único** de preparación de la
  entrevista (rol de AI Technical Lead: 50 % técnico, 50 % negocio y stakeholders). Chuleta
  Azure → AWS, 11 preguntas transversales de liderazgo y, por fase, preguntas técnicas y de negocio
  con respuesta modelo. Los docs de fase solo enlazan a su sección.
- [pendientes-cloud.md](pendientes-cloud.md) y [lecciones-aprendidas.md](lecciones-aprendidas.md):
  lo aplazado con coste y los errores reales con su causa, fuera del contexto permanente.

## 3. Desarrollo con IA: `AGENTS.md`, instrucciones y skills

Se analizaron las 9 sesiones de chat de las fases 0–8 (85 peticiones) para extraer peticiones
recurrentes, correcciones y problemas repetidos, y se reorganizó el contexto según las
recomendaciones de Anthropic (memoria de proyecto corta, reglas por ruta y procedimientos bajo
demanda).

| Fichero | Se carga | Contenido |
|---|---|---|
| `AGENTS.md` (~95 líneas) | Siempre (Copilot, Claude Code vía `CLAUDE.md`, Codex) | Contexto, reglas obligatorias, forma de trabajar, comandos, arquitectura, problemas conocidos, estado |
| `.github/instructions/*.instructions.md` (11) | Al tocar ficheros de su área (`applyTo`) | Python, SQL, LLM, RAG, evals, MCP, agentes, harness, Terraform, CI, docs |
| `.github/skills/*/SKILL.md` (4) | Bajo demanda | `iniciar-fase`, `cerrar-subfase`, `cerrar-fase`, `explicar-concepto` |

Antes, `.github/copilot-instructions.md` ocupaba 34 KB (~8 500 tokens) y se cargaba entero en cada
turno; se eliminó y su contenido se repartió sin pérdidas.

**Hook de términos prohibidos** (`.githooks/pre-commit` + `scripts/check_forbidden_terms.py`):
bloquea el commit si lo preparado contiene un término de `.secrets/forbidden_terms.txt` (ignorado
por Git, ~70 términos). Sin mayúsculas ni tildes, por palabra completa, y sin mostrar nunca el
término. Verificado: bloquea un commit de prueba y el repo actual está limpio. El historial solo
contiene cuatro nombres de artistas en títulos de la fase 0, ya sustituidos en la fase 1; se decidió
no reescribirlo.

## 4. Conexión con Azure

### Decisiones

| Decisión | Motivo |
|---|---|
| Suscripción *MSDN - Sergio Velasco*, perfil de `az` **aislado** (`AZURE_CONFIG_DIR=%USERPROFILE%\.azure-headache`) | El perfil por defecto tiene una sesión de otra organización con un proceso activo: cambiarla le habría roto la autenticación |
| Región `swedencentral` | La región de la UE con más modelos y cuota |
| Modelos en **DataZoneStandard** (chat 200 K TPM, embeddings 50 K) | Procesamiento dentro de la UE (RGPD) con cuota suficiente (16 M TPM disponibles). La capacidad no se paga: se dimensiona según la carga (ver §5) |
| Foundry **en Terraform** (`foundry.tf`) | Cuenta AI Services + proyecto + despliegues como código, en lugar del portal |
| AI Search **Basic y efímero** (`search_enabled`) | Basic da identidad gestionada, Semantic Ranker y Foundry IQ, pero cuesta ~70 €/mes aunque no se use: solo existe en las sesiones de trabajo |
| Tiers gratuitos donde no recortan (Content Safety F0) | El crédito se reserva para los modelos |
| Presupuesto del grupo de recursos: 100 €/mes, alertas al 50/75/100 % + previsión | Independiente de los presupuestos de la suscripción; solo avisa, no corta |
| Nombres CAF `<tipo>-headache-dev-swc-001` | Se leen de un vistazo (tipo, proyecto, entorno, región, instancia); el sufijo aleatorio inicial (`erauor`) parecía un error |
| `*.tfvars` ignorados; plantilla `terraform.tfvars.example` | La suscripción y el email no entran en el repo |

### Recursos (`rg-headache-dev`)

| Recurso | Nombre | Coste |
|---|---|---|
| Foundry (AI Services) + proyecto `headache` | `aif-headache-dev-swc-001` | Por token |
| Despliegues | `gpt-4.1-mini`, `gpt-4.1-nano`, `text-embedding-3-small` y `gpt-4.1` (juez de evals) | Por token |
| Content Safety F0 | `cs-headache-dev-swc-001` | 0 € (cuota mensual) |
| Service Bus Basic + cola `knowledge.documents` | `sb-headache-dev-swc-001` | Por operación |
| Storage + contenedor `knowledge-docs` | `stheadachedevswc001` | Por uso |
| AI Search Basic (apagado por defecto) | `srch-headache-dev-swc-001` | ~70 €/mes mientras existe |
| Presupuesto | `budget-headache-dev` | — |

Sin claves en ningún recurso: autenticación local desactivada y roles de Entra ID para el usuario
(*Cognitive Services OpenAI User*, *Foundry User*, *Cognitive Services User*, *Storage Blob Data
Contributor*, *Azure Service Bus Data Owner*).

### Verificación

- `terraform plan -detailed-exitcode` = 0 tras el despliegue (la nube coincide con el código).
- Prueba de humo leyendo el `.env` real: `gpt-4.1-nano` responde con Entra ID (16 + 2 tokens,
  0,0000024 $) y `text-embedding-3-small` devuelve 1024 dimensiones.

### Rutina de AI Search (efímero)

```powershell
$env:AZURE_CONFIG_DIR = "$env:USERPROFILE\.azure-headache"; az account show -o table
# Al empezar: search_enabled = true en terraform.tfvars
terraform -chdir=infra/terraform/azure apply
uv run python scripts/provision_search_index.py --apply   # y reindexar
# Al terminar: search_enabled = false
terraform -chdir=infra/terraform/azure apply
```

### Hallazgos

| Hallazgo | Arreglo |
|---|---|
| El proyecto de Foundry falló con `409 Conflict` | Azure no admite operaciones simultáneas sobre la misma cuenta: proyecto y despliegues en serie (`depends_on`) |
| El rol `Azure AI User` no existe | Microsoft lo renombró a **`Foundry User`** (comprobado con `az role definition list`) |
| La redirección `>` de PowerShell 5.1 escribe UTF-16 | Redirigir con `cmd /c` o llamar a `az` desde Python |
| Un nombre global ocupado | Si es nuestro en la papelera (soft-delete), purgar; si es de otro, subir `instance` para todos los recursos. Un sufijo aleatorio, solo con consonantes y dígitos |

## 5. Pendientes de Azure por fase

Cada fase se valida con los mismos scripts que ya existían, con el perfil de `az` aislado y
confirmación previa del coste estimado. El detalle y las cifras quedan en el doc de cada fase.

### Fase 2 · Modelos y gateway (18-10-2026, ~0,06 $)

| Validación | Resultado |
|---|---|
| Prompt caching en Azure (`gpt-4.1-mini` y `nano`) | 98 % de tokens cacheados desde la 2.ª llamada; **−58 %** de coste; −27 % de latencia p50 en `mini` |
| Gateway `native` frente a `litellm` (20 emails) | 20/20 en ambos, mismo enrutado (18 `nano` / 2 `mini`) y mismo coste |
| Router heurístico frente a router LLM | Misma decisión; el router LLM duplica las llamadas (+42 % de coste): no compensa aquí |
| Trazas en Langfuse | 107 *generations* con uso (incluidos tokens cacheados) y coste |

**Hallazgo:** 429 con 50 K TPM. Se subió la capacidad a 200 K (sin coste) y el benchmark pasa ahora
por `RateLimitedLLM`, con el límite en cliente a 150 K (margen del 25 %). Detalle en
[fase 2](fase-2-modelos.md#resultados-en-azure-18-10-2026).

### Fases 3 y 4 · Indexación y retrieval (18-10-2026)

Una sola sesión de AI Search Basic (efímero), creado en `francecentral` porque `swedencentral`
no admite servicios nuevos. El corpus de evals se clasificó con **ACL explícita**
(`scripts/classify_corpus.py`, regla única `classify_acl` en el dominio) y se ingirió una sola vez.

| Validación | Resultado |
|---|---|
| Carril B: Service Bus + worker + Foundry + AI Search | 116 docs → 131 chunks en 25 s; republicar = 0 embeddings; 1 cambio = 1 embedding |
| Carril A: indexer + skillset | 116 docs → 118 chunks en 10 s (tras corregir la entrada del skillset) |
| Retrieval con el golden dataset (144 preguntas) | Vector recall@5 0,95 (local 0,76); híbrida + Semantic Ranker la mejor (recall@5 0,974, nDCG 0,980); **0 fugas de ACL** |
| Foundry IQ `retrieve` | hit@5 1,0 · MRR 0,993 · p50 100 ms |
| Foundry IQ `answer` (agente, 15 preguntas) | 10/10 con cita correcta, 4/5 abstenciones, p50 2,6 s |

Hallazgos que solo aparecieron en la nube (todos con su test de regresión): skillset leyendo un
campo vacío (indexer en verde con 0 chunks), endpoint MCP solo en preview, esfuerzo de
razonamiento que exige modelo, formato real de las citas del agente, `POST /agents` no
idempotente y credenciales asíncronas sin cerrar. Resueltos después: el indexer del carril A
reprocesaba todos los blobs (era nuestro script, que los resubía siempre) y la sobre-respuesta
del agente (la detecta el juez de fidelidad de la fase 5). Detalle en
[fase 3](fase-3-comparativa.md#6-resultados-en-azure-18-10-2026) y
[fase 4](fase-4-retrieval.md#resultados-en-azure-18-10-2026).

AI Search se destruyó al terminar (de 11:17 a 12:08, ~0,09 $) y `terraform plan` quedó sin cambios.

### Fase 5 · Jueces LLM (18-10-2026, ~0,39 $)

| Validación | Resultado |
|---|---|
| Juez separado del generador | Nuevo despliegue `gpt-4.1` fuera del router; antes generador y juez eran el mismo `mini` |
| Nativo frente a RAGAS y DeepEval (misma muestra de 20) | Juez nativo ×5–7 más barato (0,029 $ frente a 0,208 $ y 0,138 $) y el más rápido; mismas precision y recall de contexto; `answer_relevancy` no comparable entre jueces |
| Fugas de ACL y citas | 0 fugas, citas 100 % válidas, 4/4 abstenciones correctas |
| Calibración (20 muestras, 12 estropeadas a propósito, ~0,96 $) | Juez nativo: kappa 0,82 en fidelidad y 1,0 en relevancia (etiquetas adjudicadas); **DeepEval no detecta ninguna afirmación inventada** (0/4). El nativo queda como candidato a gate |
| Fidelidad de Foundry IQ (30 preguntas, AI Search encendido de nuevo) | 19/21 respuestas fieles, 0 fugas; el juez detecta la sobre-respuesta conocida y una respuesta que añade condiciones no citadas; Prompt Shields bloquea la inyección de prompt |

Hallazgos: un veredicto incoherente abortaba la eval (ahora *fail-closed* por muestra), DeepEval
degeneraba en espacios con el modo *strict* y su plugin de pytest cargaba el `.env`. Detalle en
[fase 5](fase-5-evals.md#resultados-en-azure-18-10-2026).

### Pendientes que necesitan AI Search (segunda sesión, 18-10-2026, ~0,25 $ de modelos)

Antes de apagar AI Search se repasaron los pendientes de Azure de las fases 2 a 8 y se hicieron
los que lo necesitan; el resto (Foundry Evaluation, MCP con Entra ID, suite de agentes con LLM,
Content Safety...) no depende de él.

| Validación | Resultado |
|---|---|
| F3 · Indexer del carril A que reprocesaba todo | No era el servicio: nuestro script resubía los 116 blobs. Con comprobación de MD5: 0 cambios = 0 procesados; 1 cambio = 1 |
| F3 · Reindexar tras recrear AI Search | El ledger del carril B daba por indexado lo que ya no existía; `provision_search_index --apply` lo olvida al crear el índice |
| F4 · Estrategias con LLM (5 × 2 motores × 144 preguntas, 0,10 $) | Ninguna mejora a la híbrida + Semantic Ranker y todas triplican la latencia; auto pierde un 20 % por filtros mal inferidos. Dos fallos de esquema corregidos |
| F7 · `booking_agent` de extremo a extremo (sabores `native` y `foundry`) | Respuestas correctas con `search_knowledge` contra AI Search y tools MCP; agente de Foundry publicado |

Detalle en [fase 3](fase-3-comparativa.md#6-resultados-en-azure-18-10-2026),
[fase 4](fase-4-retrieval.md#estrategias-con-llm-sobre-ai-search-18-10-2026) y
[fase 7](fase-7-agentes.md#de-extremo-a-extremo-en-azure-18-10-2026).

## 6. Pendiente en este checkpoint

- Fase 5: Foundry Evaluation y CI en `enforce`.
- Fase 6: `tool_loop_demo` y `support_agent` con Azure OpenAI; MCP con Entra ID y OBO.
- Fase 7: suite de agentes con LLM real; truncado de Foundry frente a nuestra compactación;
  memoria de Foundry frente a `remember_preference`.
- Fase 8: `analytics_sql_demo`; Content Safety frente al escudo heurístico; ataques con LLM real.
- Aplazados (no son de este checkpoint): APIM y MCP en HTTPS (fase 14), Langfuse v4 (fase 11),
  tool de compra con `Idempotency-Key` (fase 9), coste real por carril (al cierre).
- Cierre: estado ✅ en `AGENTS.md` y en el `README.md`, y preguntas de entrevista del checkpoint.
