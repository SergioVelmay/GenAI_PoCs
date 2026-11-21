variable "project_id" {
  description = "Proyecto de GCP (gcloud config get-value project). Se crea a mano con la cuenta de facturación de prueba."
  type        = string
}

variable "billing_account" {
  description = "Cuenta de facturación (gcloud billing accounts list): XXXXXX-XXXXXX-XXXXXX."
  type        = string

  validation {
    condition     = can(regex("^[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}$", var.billing_account))
    error_message = "Formato XXXXXX-XXXXXX-XXXXXX (hexadecimal en mayúsculas)."
  }
}

variable "region" {
  description = "Región de los recursos: RAG Engine es GA en europe-west4 (fase 3.G3)."
  type        = string
  default     = "europe-west4"
}

variable "project" {
  type    = string
  default = "genaipocs"
}

variable "environment" {
  type    = string
  default = "dev"

  # "<project>-<environment>-github" es el ID del pool de federación (4–32 caracteres) y
  # "<project>-<environment>-app", el de la cuenta de servicio (6–30).
  validation {
    condition     = length("${var.project}-${var.environment}-github") <= 32
    error_message = "project + environment demasiado largos para los IDs de GCP (máx. 32 con -github)."
  }
}

variable "services" {
  description = "APIs que se habilitan en el proyecto. En GCP todas vienen apagadas: sin esto, SERVICE_DISABLED."
  type        = set(string)
  default = [
    # Cimientos (0.G): identidad, facturación, IAM y federación de la CI.
    "billingbudgets.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "serviceusage.googleapis.com",
    "sts.googleapis.com",
    # Pasada GCP (2.G–12.G): habilitar una API es gratis; se paga el uso.
    "agentidentity.googleapis.com",
    "agentregistry.googleapis.com",
    "aiplatform.googleapis.com",
    "apigee.googleapis.com",
    "cloudtrace.googleapis.com",
    "compute.googleapis.com",
    "discoveryengine.googleapis.com",
    "dlp.googleapis.com",
    "eventarc.googleapis.com",
    "identitytoolkit.googleapis.com",
    "logging.googleapis.com",
    "memorystore.googleapis.com",
    "modelarmor.googleapis.com",
    "monitoring.googleapis.com",
    "networkconnectivity.googleapis.com",
    "networkservices.googleapis.com",
    "parametermanager.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "vectorsearch.googleapis.com",
    "workflows.googleapis.com",
  ]
}

# ---------- Cola de indexación (pubsub.tf) ----------

variable "max_deliveries" {
  description = "Entregas antes de mandar el mensaje al dead-letter topic (max_delivery_attempts)."
  type        = number
  default     = 5

  validation {
    condition     = var.max_deliveries >= 5 && var.max_deliveries <= 100
    error_message = "Pub/Sub admite de 5 a 100 entregas."
  }
}

# ---------- Carril B: Agent Retrieval (vector_search.tf) ----------

variable "vector_search_enabled" {
  description = "Crea la colección de Agent Retrieval (con coste por uso). Efímera: true durante la sesión."
  type        = bool
  default     = false
}

variable "vector_search_collection" {
  description = "GCP_VECTOR_SEARCH__COLLECTION (RFC1035: minúsculas, dígitos y guiones)."
  type        = string
  default     = "knowledge"
}

variable "embedding_dimensions" {
  description = "Dimensiones del embedding (las mismas en las tres nubes)."
  type        = number
  default     = 1024
}

# ---------- Carril A: RAG Engine (lane_a.tf) ----------

variable "rag_engine_enabled" {
  description = "RAG Engine en nivel Basic (Spanner por horas) y sus corpus. false = Unprovisioned: borra los datos y para la facturación."
  type        = bool
  default     = false
}

variable "rag_embedding_model" {
  description = "Modelo de embeddings de los corpus de RAG Engine (el mismo que el carril B; verificar que RAG Engine lo admite en la región)."
  type        = string
  default     = "gemini-embedding-001"
}

# ---------- Carril A: Agent Search (agent_search.tf) ----------

variable "agent_search_enabled" {
  description = "Data store, esquema y motor de Agent Search (antes Vertex AI Search). Pago por consulta, sin coste por hora; false = borra el índice."
  type        = bool
  default     = false
}

variable "agent_search_location" {
  description = "GCP_AGENT_SEARCH__LOCATION: multirregión de Agent Search (global, us o eu)."
  type        = string
  default     = "eu"

  validation {
    condition     = contains(["global", "us", "eu"], var.agent_search_location)
    error_message = "Agent Search solo admite las ubicaciones global, us y eu."
  }
}

# ---------- MCP Gateway (mcp_gateway.tf) ----------

variable "identity_platform_enabled" {
  description = "Identity Platform como IdP de los clientes (6.G2). Gratis hasta 50 000 MAU, pero activarlo NO se deshace: false hasta la validación."
  type        = bool
  default     = false
}

variable "identity_platform_authorized_domains" {
  description = "Dominios autorizados para las redirecciones OAuth de Identity Platform."
  type        = list(string)
  default     = ["localhost"]
}

variable "mcp_server_urls" {
  description = "URL HTTPS de cada servidor MCP (Cloud Run, fase 15): catalog, ticketing y refunds."
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for k, v in var.mcp_server_urls : contains(["catalog", "ticketing", "refunds"], k) && startswith(v, "https://")])
    error_message = "Claves catalog, ticketing o refunds y URLs https://."
  }
}

variable "agent_gateway_enabled" {
  description = "Agent Gateway + servicios de Agent Registry (6.G3). Limited Preview para Cloud Run; true solo durante la validación."
  type        = bool
  default     = false
}

variable "agent_gateway_region" {
  description = "Región del gateway y del registro: los gateways de salida con MCP propios solo están en us-central1 y europe-west1 (no en europe-west4)."
  type        = string
  default     = "europe-west1"

  validation {
    condition     = contains(["us-central1", "europe-west1"], var.agent_gateway_region)
    error_message = "Agent Gateway con MCP propios: us-central1 o europe-west1."
  }
}

variable "apigee_enabled" {
  description = "Organización de evaluación de Apigee con un proxy por servidor MCP (6.G3). Gratis 60 días; tarda ~1 h en crearse."
  type        = bool
  default     = false
}

variable "apigee_hostnames" {
  description = "Hostnames del grupo de entornos de Apigee (cabecera Host con la que llegan las peticiones)."
  type        = list(string)
  default     = ["mcp.genaipocs.internal"]
}

variable "apigee_bundle_dir" {
  description = "Carpeta con los bundles generados por `python -m genaipocs_mcp.apigee` (relativa al módulo)."
  type        = string
  default     = "../../../.tmp/apigee"
}

# ---------- Agente en Agent Runtime (agent_platform.tf, 7.G4) ----------

variable "agent_platform_enabled" {
  description = "Motor standalone con Sessions + Memory Bank (pago por uso, sin coste por hora) y, si hay imagen, el agente en Agent Runtime."
  type        = bool
  default     = false
}

variable "agent_platform_runtime_image" {
  description = "Imagen del agente en Artifact Registry (fase 15). Vacío = sin Agent Runtime con contenedor."
  type        = string
  default     = ""

  validation {
    condition     = var.agent_platform_runtime_image == "" || can(regex("^[a-z0-9-]+-docker\\.pkg\\.dev/", var.agent_platform_runtime_image))
    error_message = "Agent Runtime solo ejecuta imágenes de Artifact Registry (<región>-docker.pkg.dev/...)."
  }
}

variable "agent_platform_max_instances" {
  description = "Tope de instancias del agente (el mínimo es 0: escala a cero)."
  type        = number
  default     = 2

  validation {
    condition     = var.agent_platform_max_instances >= 1 && var.agent_platform_max_instances <= 10
    error_message = "Entre 1 y 10 (el servicio admite hasta 1000; aquí es una PoC)."
  }
}

variable "agent_platform_memory_ttl_days" {
  description = "Caducidad de las memorias de Memory Bank (días), como event_expiry_duration de AgentCore."
  type        = number
  default     = 30

  validation {
    condition     = var.agent_platform_memory_ttl_days >= 1 && var.agent_platform_memory_ttl_days <= 365
    error_message = "Entre 1 y 365 días."
  }
}

variable "agent_platform_invokers" {
  description = "Quién puede invocar al agente (reasoningEngines.query): la cuenta de servicio del backend, nunca clientes finales."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for m in var.agent_platform_invokers : startswith(m, "serviceAccount:") || startswith(m, "user:") || startswith(m, "group:")])
    error_message = "serviceAccount:, user: o group: (para probar desde local)."
  }
}

variable "organization_id" {
  description = "ID numérico de la organización del proyecto (Agent Identity usa org-<id> como dominio de confianza). Vacío = proyecto sin organización (proj-<número>)."
  type        = string
  default     = ""

  validation {
    condition     = can(regex("^[0-9]*$", var.organization_id))
    error_message = "Solo dígitos (gcloud projects describe <proyecto> --format='value(parent.id)')."
  }
}

variable "agent_platform_mcp_tools" {
  description = "Tools MCP permitidas al agente por servidor en la política de acceso (vacío = todas las del contrato)."
  type        = map(list(string))
  default     = {}
}

variable "agent_platform_egress_hosts" {
  description = "Hosts sin registrar en Agent Registry a los que puede salir el agente, además de Gemini, Memory Bank y la telemetría."
  type        = list(string)
  default     = []
}

# ---------- Guardrails (model_armor.tf, 8.G3) ----------

variable "a2a_enabled" {
  description = "Especialistas A2A en Cloud Run (9.G4): pago por petición y escala a cero. Necesita a2a_image."
  type        = bool
  default     = false
}

variable "a2a_image" {
  description = "Imagen de services/orchestrator/Dockerfile en Artifact Registry (fase 15). Vacío = sin servicios."
  type        = string
  default     = ""

  validation {
    condition     = var.a2a_image == "" || can(regex("^[a-z0-9-]+-docker\\.pkg\\.dev/", var.a2a_image))
    error_message = "Cloud Run despliega aquí imágenes de Artifact Registry (<región>-docker.pkg.dev/...)."
  }
}

variable "a2a_max_instances" {
  description = "Tope de instancias por especialista (el mínimo es 0: escala a cero)."
  type        = number
  default     = 2

  validation {
    condition     = var.a2a_max_instances >= 1 && var.a2a_max_instances <= 10
    error_message = "Entre 1 y 10 (es una PoC)."
  }
}

variable "case_review_workflow_enabled" {
  description = "Espera humana con SLA en Workflows + disparador de Eventarc (9.G5). Pago por paso: ~0 $ en reposo."
  type        = bool
  default     = false
}

variable "review_sla_seconds" {
  description = "Plazo para decidir un caso pendiente; al vencer, el dominio lo escala (12 h = defecto de await_callback)."
  type        = number
  default     = 43200

  validation {
    condition     = var.review_sla_seconds >= 60 && var.review_sla_seconds <= 2592000
    error_message = "Entre 1 minuto y 30 días."
  }
}

variable "case_reviewers" {
  description = "Quién puede mandar la decisión al callback (workflows.callbacks.send): la consola de operaciones."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for m in var.case_reviewers : startswith(m, "serviceAccount:") || startswith(m, "group:")])
    error_message = "serviceAccount: (la consola) o group: (operaciones); nunca usuarios sueltos ni clientes."
  }
}

variable "model_armor_enabled" {
  description = "Plantilla de Model Armor del harness y plantillas de inspección de SDP. Sin coste en reposo: se paga lo evaluado (output guardrails_env)."
  type        = bool
  default     = false
}

variable "model_armor_floor_setting_enabled" {
  description = "Floor setting de Model Armor para Gemini en todo el proyecto (INSPECT_ONLY con logging). Cada llamada a Gemini consume tokens de Model Armor."
  type        = bool
  default     = false
}

# ---------- Presupuesto (budget.tf) ----------

variable "budget_email" {
  description = "Correo que recibe las alertas de presupuesto (no cortan el gasto)."
  type        = string
}

variable "budget_limit" {
  description = "Presupuesto mensual en la moneda de la cuenta de facturación (USD o EUR)."
  type        = number
  default     = 100
}

variable "budget_alert_thresholds" {
  description = "Porcentajes de gasto real que disparan alerta; además, previsión al 100 %."
  type        = list(number)
  default     = [50, 75, 100]

  validation {
    condition     = alltrue([for t in var.budget_alert_thresholds : t > 0 && t <= 1000])
    error_message = "Porcentajes entre 1 y 1000."
  }
}

# ---------- Identidades (identity.tf) ----------

variable "developer_members" {
  description = "Quién puede suplantar la cuenta de servicio de desarrollo desde local (user:correo)."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for m in var.developer_members : startswith(m, "user:") || startswith(m, "group:")])
    error_message = "Solo user:<correo> o group:<correo>: las personas suplantan, no se reparten claves."
  }
}

variable "ci_federation_enabled" {
  description = "Workload Identity Federation para GitHub Actions (gratis). La CI la usará en la fase 15."
  type        = bool
  default     = true
}

variable "github_repository" {
  description = "owner/repo que puede federarse."
  type        = string
  default     = "SergioVelmay/GenAI_PoCs"
}

# Los nombres se pueden renombrar o volver a registrar; los IDs numéricos, no. La condición del
# proveedor usa los IDs para que un repositorio homónimo creado tras un renombrado no herede acceso.
variable "github_repository_id" {
  description = "gh api repos/<owner>/<repo> --jq .id"
  type        = string
  default     = "1405357932"
}

variable "github_owner_id" {
  description = "gh api repos/<owner>/<repo> --jq .owner.id"
  type        = string
  default     = "42042494"
}

variable "ci_allowed_refs" {
  description = "Refs de Git cuyos workflows pueden obtener credenciales de GCP. Las PR no: código sin revisar."
  type        = list(string)
  default     = ["refs/heads/main"]
}

# ---------- Estado compartido del edge (memorystore.tf, 10.G2) ----------

variable "memorystore_enabled" {
  description = "Memorystore for Valkey (rate limiting y caché semántica) con su VPC y PSC. Coste por hora: efímero."
  type        = bool
  default     = false
}

variable "memorystore_node_type" {
  description = "SHARED_CORE_NANO (1,4 GB, sin SLA) para validar; CUSTOM_PICO o mayor si hace falta SLA."
  type        = string
  default     = "SHARED_CORE_NANO"
}

variable "memorystore_engine_version" {
  description = "Versión de Valkey: la misma que la imagen local valkey/valkey-bundle (9.0)."
  type        = string
  default     = "VALKEY_9_0"

  validation {
    condition     = startswith(var.memorystore_engine_version, "VALKEY_")
    error_message = "Memorystore for Valkey: VALKEY_8_0, VALKEY_9_0, VALKEY_9_1..."
  }
}

variable "memorystore_subnet_cidr" {
  description = "Rango de la subred del edge donde Memorystore crea su endpoint PSC."
  type        = string
  default     = "10.20.0.0/24"
}

variable "memorystore_zone_suffix" {
  description = "Zona de la instancia (sin réplicas, una sola zona): <región>-<sufijo>."
  type        = string
  default     = "a"
}

# ---------- Workers con autoescalado por backlog (queue_workers.tf, 10.G4) ----------

variable "queue_workers_enabled" {
  description = "Worker pool de triaje + CREMA (autoescalado por el backlog de Pub/Sub). Necesita queue_worker_image."
  type        = bool
  default     = false
}

variable "queue_worker_image" {
  description = "Imagen del orquestador (services/orchestrator/Dockerfile) en Artifact Registry (fase 15)."
  type        = string
  default     = ""
}

variable "queue_worker_max_instances" {
  description = "Techo de instancias: el que permite la cuota TPM del modelo (scripts/capacity_plan.py)."
  type        = number
  default     = 4
}

variable "queue_worker_messages_per_instance" {
  description = "Mensajes sin confirmar por instancia antes de añadir otra (threshold del escalador)."
  type        = number
  default     = 8
}

variable "crema_image" {
  description = "Imagen de CREMA publicada por Google (comprobar la etiqueta vigente antes del apply)."
  type        = string
  default     = "us-central1-docker.pkg.dev/cloud-run-oss-images/crema-v1/autoscaler:1.0"
}

# ---------- Edge tras un balanceador con Cloud Armor (edge_armor.tf, 10.G4) ----------

variable "edge_armor_enabled" {
  description = "Edge en Cloud Run tras un ALB externo con Cloud Armor. Coste fijo del balanceador: efímero."
  type        = bool
  default     = false
}

variable "edge_image" {
  description = "Imagen del edge (services/edge_api) en Artifact Registry (fase 15)."
  type        = string
  default     = ""
}

variable "edge_domain" {
  description = "Dominio del certificado gestionado (HTTPS). Vacío = HTTP en el 80, solo para validar."
  type        = string
  default     = ""
}

variable "edge_max_instances" {
  description = "Instancias máximas del edge en Cloud Run."
  type        = number
  default     = 10
}

variable "edge_ban_per_minute" {
  description = "Peticiones por minuto y IP a partir de las que Cloud Armor banea temporalmente."
  type        = number
  default     = 1200
}

variable "edge_ban_seconds" {
  description = "Duración del baneo temporal por IP."
  type        = number
  default     = 300
}

variable "edge_anonymous_per_minute" {
  description = "Tope por IP sin token (holgado: el edge aplica 5/min por usuario anónimo)."
  type        = number
  default     = 120
}

variable "edge_authenticated_per_minute" {
  description = "Tope por token (holgado: el edge aplica el límite fino por plan)."
  type        = number
  default     = 600
}

# ---------- Generador de carga (load_test.tf, 10.G5) ----------

variable "load_test_enabled" {
  description = "Cloud Run job de Locust contra el edge (pago solo mientras corre). Necesita load_test_image."
  type        = bool
  default     = false
}

variable "load_test_image" {
  description = "Imagen de loadtests/Dockerfile en Artifact Registry (fase 15)."
  type        = string
  default     = ""
}

variable "load_test_host" {
  description = "URL del edge a probar. Vacío = el edge tras Cloud Armor (edge_url) si edge_armor_enabled."
  type        = string
  default     = ""
}

variable "load_test_profile" {
  description = "Nombre del perfil del edge que se prueba (prefijo de los CSV): ingenuo, sin_cache, protegido."
  type        = string
  default     = "protegido"
}

variable "load_test_peak" {
  description = "Usuarios en el pico (LOAD_PEAK de la forma FestivalOnSale)."
  type        = number
  default     = 200
}

variable "load_test_cpu" {
  description = "vCPU del job (= procesos de Locust con --processes -1); la memoria, 1 GiB por vCPU."
  type        = number
  default     = 8

  validation {
    condition     = contains([1, 2, 4, 6, 8], var.load_test_cpu)
    error_message = "Cloud Run admite 1, 2, 4, 6 u 8 vCPU por instancia."
  }
}

variable "load_test_signing_key_secret" {
  description = "Secret de Secret Manager con la clave privada de los tokens de prueba (vacío = sin montar)."
  type        = string
  default     = ""
}
