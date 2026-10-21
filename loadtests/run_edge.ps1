# Arranca el edge para una prueba de carga (fase 10.7), sin el .env (sin Langfuse ni Azure):
# proveedor simulado (coste 0), Redis y PostgreSQL locales. Perfiles:
#   protegido: la configuración de la fase 10 (caché, límites por usuario, shedding, colas acotadas).
#   sin_cache: la misma protección pero sin caché semántica (aísla qué aporta cada pieza).
#   ingenuo:   sin caché ni límites por usuario y con esperas de cuota sin tope (lo de antes).
#
#   powershell -File loadtests/run_edge.ps1 -Profile protegido
param(
    [ValidateSet("protegido", "sin_cache", "ingenuo")][string]$Profile = "protegido",
    [int]$Port = 8200
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dotenv = Get-Content (Join-Path $root ".env") | Where-Object { $_ -match '^(POSTGRES__PASSWORD|SCALE_REDIS__PASSWORD)=' }

$envs = @{
    "ENVIRONMENT"                       = "local"
    "LLM_PROVIDER"                      = "simulated"
    "LLM_GATEWAY__REQUESTS_PER_MINUTE"  = "1000"
    "LLM_GATEWAY__TOKENS_PER_MINUTE"    = "150000"   # Por debajo de los 200 K del proveedor.
    "EDGE__ANSWER_MODE"                 = "generative"
    "EDGE__AUTH"                        = "jwt"
    "EDGE__DEV_PRIVATE_KEY_FILE"        = (Join-Path $root ".secrets/mcp-dev-key.pem")
    "EDGE__CASES_BACKEND"               = "postgres"
    "EDGE__CASES_TOPIC"                 = "load.support.emails"  # Ningún worker lo consume.
    "RETRIEVAL__DOCUMENTS_PATH"         = (Join-Path $root "data/synthetic/knowledge/documents.jsonl")
    "RATE_LIMIT__BACKEND"               = "redis"
    "SEMANTIC_CACHE__BACKEND"           = "redis"
    "SEMANTIC_CACHE__INDEX_VERSION"     = "load-$(Get-Date -Format 'HHmmss')"
}
if ($Profile -ne "ingenuo") {
    # Interactivo: esperar cuota como mucho 2 s y, si no llega, responder en modo degradado.
    $envs["LLM_GATEWAY__MAX_QUEUE_SECONDS"] = "2"
}
if ($Profile -ne "protegido") {
    $envs["SEMANTIC_CACHE__ENABLED"] = "false"
}
if ($Profile -eq "ingenuo") {
    $envs["RATE_LIMIT__ANONYMOUS_PER_MINUTE"] = "100000"
    $envs["RATE_LIMIT__PUBLIC_PER_MINUTE"] = "100000"
    $envs["RATE_LIMIT__INTERNAL_PER_MINUTE"] = "100000"
    $envs["EDGE__CAPACITY"] = "100000"
    $envs["EDGE__CASES_CAPACITY"] = "100000"
    $envs["EDGE__REQUEST_BUDGET_SECONDS"] = "60"
    $envs["LLM_GATEWAY__MAX_QUEUE_SECONDS"] = "3600"   # Esperar la cuota sin tope práctico.
    $envs["LLM_GATEWAY__MAX_CONCURRENCY"] = "100000"
    $envs["EDGE__DEGRADE_ON_SATURATION"] = "false"
}
foreach ($line in $dotenv) { $k, $v = $line -split '=', 2; $envs[$k] = $v }
foreach ($k in $envs.Keys) { Set-Item -Path "Env:$k" -Value $envs[$k] }

# Desde una carpeta sin .env: solo cuenta la configuración de arriba.
$work = Join-Path $env:TEMP "headache-load"
New-Item -ItemType Directory -Force $work | Out-Null
Set-Location $work
& uv run --project $root uvicorn edge_api.app:create_app --factory --host 127.0.0.1 --port $Port --no-access-log
