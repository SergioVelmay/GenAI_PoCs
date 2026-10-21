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
| Tildes convertidas en `?` en textos insertados con `@' ... '@ \| python -` (fase 9.6) | PowerShell 5.1 envía la tubería a Python en la página de códigos de la consola: los caracteres no ASCII del script se pierden | Para insertar texto con tildes, usar la tool de edición; los scripts por tubería, solo con ASCII (o `$OutputEncoding = [Text.UTF8Encoding]::new()` antes). Buscar `[a-z]\?[a-z]` tras editar |
| Se repitió en las fases 10.1 y 10.2: 74 líneas con `?` en lugar de tildes, comillas angulares y flechas llegaron a dos commits | La regla anterior existía, pero se volvió a insertar texto en español por tubería; y el propio escáner, también por tubería, buscaba `?` en lugar de `¿` y no encontró nada | Antes de cada commit, escanear las líneas añadidas (`git diff -U0`) con un script **escrito con la tool de creación**, usando escapes (`"\u00bf"`) y no caracteres no ASCII |
| Al parar un servidor de pruebas se cerró la propia terminal (fase 10.3) | `Get-CimInstance Win32_Process \| Where CommandLine -like '*edge_api*'` también encontraba el `powershell.exe` que ejecutaba ese filtro | Parar por PID (el que devuelve `Start-Process -PassThru`) o filtrar también por `Name='python.exe'` |
| Un reemplazo con `Get-Content -Raw ... \| Set-Content -Encoding utf8` corrompió las tildes y añadió BOM (fase 9.2) | Lee el UTF-8 como ANSI y escribe UTF-8 **con** BOM: el texto queda doblemente codificado | Nunca reescribir ficheros con `Get-Content`/`Set-Content`: usar la tool de edición o Python (`encoding="utf-8"`, `newline="\n"`) |

## Verificación y CI

| Qué pasó | Causa | Regla |
|---|---|---|
| Un test de concurrencia con RabbitMQ pasó 5 veces en local y falló en la CI (fase 9.7) | Carrera dependiente del tiempo: en la CI la copia del email llegó después de triar y republicó el traspaso | Los tests con colas reales se repiten varias veces antes de dar algo por bueno y se razona cada orden de llegada posible, no solo el observado |
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

## Validación en la nube (checkpoint 8→9)

| Qué pasó | Causa | Regla |
|---|---|---|
| Un indexer de AI Search terminaba `success` con 0 chunks | El skillset leía `/document/content`, que el parser de Markdown `oneToOne` deja vacío | Un estado en verde no prueba nada: verificar el **resultado** (número de documentos en el índice) |
| Adaptadores de la fase 4 (Foundry IQ) que pasaban todos sus tests fallaron al primer uso real | Versión de API, valores por defecto y formato de respuesta supuestos sin poder probarlos | Los dobles de test se construyen a partir de **respuestas reales capturadas**; tras la primera validación en la nube, se actualizan y se fija un test de regresión por hallazgo |
| La API GA servía para gestionar la KB pero no para su endpoint MCP | Las capacidades nuevas llegan antes a preview | Comprobar la disponibilidad de cada operación por versión de API, no solo del servicio |
| No se pudo crear AI Search en la región elegida | Regiones de alta demanda bloquean servicios nuevos | Consultar la tabla de regiones del servicio antes de fijar la región; separar la región de cada recurso cuando haga falta |
| 2 tests fallaban solo en la suite completa al rellenar el `.env` con Azure | `import litellm` (modo DEV) vuelca el `.env` en `os.environ` de todo el proceso | `LITELLM_MODE=PRODUCTION` antes de importarlo y test de regresión en un proceso limpio. Ante un fallo que solo ocurre en la suite completa, buscar estado global compartido (variables de entorno, singletons) |
| Con el extra `evals` instalado, 2 tests de retrieval volvieron a fallar | El plugin de pytest de DeepEval (`pytest11`) importa `deepeval`, que también vuelca el `.env` | Al instalar una librería, revisar sus *entry points* de pytest y si carga `.env` al importarse; desactivar con `-p no:<plugin>` y su variable de entorno |
| DeepEval con `gpt-4.1` devolvía espacios hasta `max_tokens` | Su prompt pide omitir `reason`, pero el modo *strict* obliga a escribirlo | Si el prompt y el esquema estricto se contradicen, el modelo degenera: alinear el prompt con el esquema. `finish_reason=length` con JSON a medias es la pista |
| RAGAS agotó el presupuesto antes de terminar y se perdió lo ya pagado | El coste por muestra se estimó con contextos más cortos; el corte abortaba sin guardar | Estimar con una muestra pequeña del mismo conjunto y, al cortar por presupuesto, conservar lo ya juzgado |
| Una métrica con el mismo nombre medía otra cosa | La faithfulness de DeepEval solo penaliza contradicciones (`idk` cuenta como fiel) | No fiarse del nombre: comprobar cada métrica con casos estropeados a propósito antes de usarla como gate |
| El etiquetado humano tenía 2 despistes y un criterio de relevancia distinto al del juez | Etiquetar 20 muestras es tedioso; "relevante" era ambiguo (¿responde o es verdad?) | Guía de etiquetado explícita, etiquetar a ciegas y, si se corrige tras ver al juez, adjudicar de forma documentada conservando las dos versiones |
| Al recrear AI Search, reindexar no indexaba nada | El ledger de idempotencia seguía dando por indexados los documentos del índice destruido | Un ledger describe un destino concreto: si el destino se recrea vacío, se olvida su ledger (lo hace `provision_search_index --apply`). Con infraestructura efímera, revisar todo estado que la describa |
| El indexer gestionado reprocesaba todos los blobs en cada ejecución | No era el servicio: nuestro script resubía todo con `overwrite=True` y eso cambia la fecha que usa la detección de cambios | Antes de culpar al servicio gestionado, reproducir aislando variables (relanzar sin subir). La idempotencia la garantiza quien publica (MD5 antes de subir) |
| Las estrategias con LLM rendían muy por debajo de lo esperado (router hit@5 0,61) | El esquema permitía 1–4 consultas y el modelo barato devolvía varias; y los filtros eran texto libre, así que copiaba etiquetas en vez de códigos | Diseñar el esquema para que el error sea imposible (una consulta, enumeraciones del dominio), no validar después. Antes de concluir que "la técnica no funciona", mirar los fallos uno a uno |
| El agente no completaba un reembolso con el modelo barato | IDs con patrón genérico, un ID de ejemplo en la descripción y una pregunta corta que el router mandaba a `nano` | Las tools son interfaz para el modelo: prefijos exactos, sin ejemplos copiables y sin obligarle a navegar entre entidades. Y las escrituras, al modelo potente |
| El sabor `foundry` "funcionaba en Azure" pero nunca llamó al servicio | `create()` siempre montaba el emulador local; la respuesta era correcta y no había forma de notarlo | Una prueba "en la nube" debe demostrar que tocó la nube: tokens o ids del servicio en la salida, no solo una respuesta correcta |
| Con modelo real, la suite de agentes pasaba 4 de 12 | Comprobaba trayectorias exactas y literales pensados para un modelo guionizado | Con modelos reales se evalúa el resultado (estado final, escrituras, invariantes) y el contenido con un juez, no con literales |
| Sin harness, un jailbreak tumbaba el agente con una excepción | El filtro del proveedor (Prompt Shields) bloqueó la petición y nadie capturaba el error | Toda defensa externa necesita su ruta de "bloqueado" en el código: que funcione la defensa no basta si el usuario ve un 500 |
| La eval del agente abortaba por un filtro de contenido o una cita inválida | Se trataban como fallos, pero son defensas actuando | Las defensas son resultados medibles (`blocked`, `rejected`), no excepciones que tumban la eval |
| La estimación de coste de la sesión se quedó un 40 % corta frente a Cost Management | Se sumó cada eval una vez aunque varias se repitieron, y la tabla de precios era la global (DataZone es más cara) | El medidor de la aplicación sirve para cortar; el coste se informa con la factura (Cost Management / Cost Explorer) al cabo de 24 h |
| Generador y juez eran el mismo modelo sin saberlo | El router heurístico manda los prompts largos (los dos) al modelo potente | El juez va fijo, fuera del router, y el informe desglosa llamadas y coste por modelo |
| `POST /agents` falló al repetir el aprovisionamiento | Los agentes de Foundry se versionan | Todo aprovisionamiento debe ser idempotente: crear o actualizar, y probarlo ejecutándolo dos veces |

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
