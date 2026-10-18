# Lecciones aprendidas (desarrollo con IA en este repo)

Errores reales de las fases 0–8, su causa y la regla que dejaron. Las reglas que aplican siempre
están resumidas en `AGENTS.md`; aquí está el porqué. Añade una entrada cuando un error se repita o
cuando una corrección tuya deba sobrevivir a la sesión.

## Entorno (Windows, PowerShell, terminal)

| Qué pasó | Causa | Regla |
|---|---|---|
| Se borró una carpeta al renombrar un paquete (fase 0) | El terminal integrado metió un carácter espurio al inicio de un comando: la copia falló y el borrado encadenado detrás sí se ejecutó | Nunca encadenar una acción destructiva detrás de un paso que pueda fallar. Verificar el resultado antes de continuar |
| Un commit no se hizo aunque el comando "terminó" (fase 6) | El mismo carácter espurio rompió `git add` | Tras un comando de git, comprobar con `git status` / `git log -1` |
| Cada conexión local tardaba ~2 s (fase 1) | `localhost` intenta antes IPv6 en Windows | Desde el código, `127.0.0.1` (PostgreSQL, RabbitMQ, Langfuse) |
| PowerShell 5.1 rechaza `&&`, `\|\|` y heredocs | No es PowerShell 7 | Usar `;` con `if ($?) { ... }` y `@' ... '@ \| python -` |
| `az` apuntaba a la suscripción de otra organización con un proceso activo (checkpoint 8→9) | La CLI guarda una sola sesión por defecto para todo el equipo; cambiar de tenant o suscripción afecta a cualquier proceso que pida tokens a la CLI | Perfil aislado con `AZURE_CONFIG_DIR` para este repo; comprobar `az account show` antes de cualquier `plan`/`apply` |
| Texto con tildes mal mostrado en la consola | PowerShell 5.1 lee UTF-8 sin BOM como ANSI | Solo es visual: el fichero está bien. Leer con `view` o Python |

## Verificación y CI

| Qué pasó | Causa | Regla |
|---|---|---|
| La CI de la 7.4 falló y nadie lo vio | En local se ejecutó `terraform fmt`, no `terraform fmt -check -recursive` como la CI | Verificar con **el mismo comando que la CI**. Tras cada push, vigilar la ejecución hasta el final (`gh run watch`) |
| Un "sin coincidencias" en la búsqueda del nombre real era falso (fase 0) | La búsqueda falló por el orden de los argumentos y no devolvió error visible | Comprobar el código de salida de las búsquedas de seguridad. Ahora lo hace el hook de pre-commit |
| NLTK fallaba en la CI de Linux (fase 5) | uv instala con hardlinks y NLTK rechaza ficheros con `st_nlink > 1` | `UV_LINK_MODE=copy` en la CI |
| Tests de extras opcionales que "pasaban" sin ejecutarse | `pytest.importorskip` también salta si el import falla por un bug | `skipif` explícito según si el paquete está instalado; con el extra instalado, una rotura falla |
| 429 en el benchmark de caché de Azure (checkpoint 8→9) | Despliegue de 50 K TPM con prompts de ~10 K tokens (~5 peticiones/min) y un script que no pasaba por el limitador; además, un *token bucket* del tamaño de la capacidad admite ráfaga + recarga en el primer minuto | Dimensionar la capacidad según la carga (en Standard no se paga); todo cliente por `RateLimitedLLM`; límite en cliente por debajo de la capacidad (25 % de margen) |
| Avisos de Node.js 20 y del cambio de `ubuntu-latest` (fase 8) | Acciones antiguas e imagen flotante | Acciones fijadas por SHA, runner fijado, Dependabot mensual |

## Sesiones de chat

| Qué pasó | Causa | Regla |
|---|---|---|
| El historial "desapareció" al abrir la carpeta (fase 0) | VS Code guarda las sesiones por espacio de trabajo | El contexto del proyecto vive en `AGENTS.md` y los docs, no en el chat |
| Al volver a una sesión había 4 ficheros sin guardar que no se reconocían (fase 4) | Cambio de sesión y de modelo a mitad de trabajo: eran restos de otra sesión | Si aparecen cambios sin guardar desconocidos: *File: Revert File*, no guardarlos. Si ya están guardados: diff, copia en `%TEMP%` y decidir |
| Los pasos se hacían en contextos muy largos (una fase por sesión) | El rendimiento del modelo cae cuando el contexto se llena | Una sesión por fase; si se alarga, cerrar la subfase, hacer commit y abrir sesión nueva con la skill `iniciar-fase` |

## Dominio y diseño

| Qué pasó | Causa | Regla |
|---|---|---|
| Carril A de Azure en la fase 4 | Microsoft retiró *On Your Data* el 14-10-2026 | Antes de diseñar sobre un servicio gestionado, comprobar su estado (retiradas, previews) en la documentación oficial |
| `strands-agents` exige `mcp<2.2` (fase 7) | Conflicto de versiones entre frameworks | Documentar el pin y el motivo en el `pyproject.toml` y en las instrucciones |
| AgentCore Memory rechazaba ids (fase 7) | `sessionId` solo admite `[a-zA-Z0-9-_]` | Derivar ids externos con hash; probar con `Stubber`, que valida los patrones |
| PostgreSQL comprueba permisos antes que `READ ONLY` (fase 8) | Son dos capas independientes | Defensa en profundidad: cada capa debe funcionar sola |

## Estilo de documentación

- No usar la palabra "asterisco": `✅ Hecha*` significa "implementación cerrada; falta la validación en cloud".
- Nunca el nombre real del cliente ni marcas de terceros (ni en ejemplos: "un partner de streaming", no su marca).
