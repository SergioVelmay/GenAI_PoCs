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
| Modelos en **DataZoneStandard** (50 K TPM) | Procesamiento dentro de la UE (RGPD) con cuota suficiente (16 M TPM disponibles) |
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
| Despliegues | `gpt-4.1-mini`, `gpt-4.1-nano`, `text-embedding-3-small` | Por token |
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

## 5. Pendiente en este checkpoint

- Abordar los pendientes de Azure fase a fase ([pendientes-cloud.md](pendientes-cloud.md)),
  empezando por la fase 2 (benchmark de prompt caching y demo del gateway con trazas en Langfuse).
- Cierre: estado ✅ en `AGENTS.md` y en el `README.md`, y preguntas de entrevista del checkpoint.
