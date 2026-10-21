variable "region" {
  description = "Región de AWS (la misma que Bedrock en la fase 2)."
  type        = string
  default     = "eu-west-1"
}

variable "profile" {
  description = "Perfil de AWS CLI (SSO). null = cadena de credenciales por defecto (OIDC en CI)."
  type        = string
  default     = null
}

variable "project" {
  type    = string
  default = "headache"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "embedding_dimensions" {
  description = "Debe coincidir con EMBEDDING_DIMENSIONS del código (Titan v2 a 1024)."
  type        = number
  default     = 1024
}

variable "max_deliveries" {
  description = "Entregas antes de mandar el mensaje a la DLQ (maxReceiveCount)."
  type        = number
  default     = 5
}

variable "budget_limit_usd" {
  type    = number
  default = 20
}

variable "budget_email" {
  description = "Correo que recibe la alerta de presupuesto."
  type        = string
}

# ---------- Fase 6.4: AgentCore Gateway (MCP) ----------

variable "agentcore_enabled" {
  description = "Fase 7.4: crea AgentCore Memory y, si hay imagen, AgentCore Runtime."
  type        = bool
  default     = false
}

variable "bedrock_guardrail_enabled" {
  description = "Fase 8.4: crea el guardrail de Bedrock del harness (pago por uso)."
  type        = bool
  default     = false
}

variable "agentcore_container_uri" {
  description = "Imagen ARM64 del agente en ECR (fase 13). Vacío = sin Runtime."
  type        = string
  default     = ""
}

variable "agentcore_flavor" {
  description = "Sabor que corre dentro del contenedor del Runtime."
  type        = string
  default     = "strands"

  validation {
    condition     = contains(["native", "langgraph", "llamaindex", "maf", "strands"], var.agentcore_flavor)
    error_message = "Sabor de framework desconocido (los gestionados no se alojan a sí mismos)."
  }
}

variable "agentcore_event_expiry_days" {
  description = "Retención de los eventos de memoria a corto plazo (días, 7-365)."
  type        = number
  default     = 30
}

variable "mcp_gateway_enabled" {
  description = "Crea el MCP Gateway. false hasta tener los servidores MCP en HTTPS (fase 13)."
  type        = bool
  default     = false
}

variable "mcp_server_urls" {
  description = "Endpoint HTTPS /mcp de cada servidor (catalog, ticketing, refunds)."
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for k, v in var.mcp_server_urls : contains(["catalog", "ticketing", "refunds"], k) && startswith(v, "https://")])
    error_message = "Claves catalog|ticketing|refunds y URLs https://."
  }
}

variable "mcp_oidc_discovery_url" {
  description = "OpenID configuration del IdP (Cognito user pool): .../.well-known/openid-configuration."
  type        = string
  default     = ""
}

variable "mcp_gateway_audience" {
  description = "Audience que deben traer los tokens de los agentes (MCP__AUDIENCE_TEMPLATE)."
  type        = string
  default     = "api://headache-mcp/gateway"
}

variable "mcp_oauth_client_id" {
  description = "Cliente OAuth del gateway en el IdP (write-only: no queda en el estado)."
  type        = string
  default     = null
  ephemeral   = true
}

variable "mcp_oauth_client_secret" {
  type      = string
  default   = null
  sensitive = true
  ephemeral = true
}

variable "mcp_oauth_credentials_version" {
  description = "Incrementar para rotar el cliente OAuth (argumentos write-only)."
  type        = number
  default     = 1
}

variable "queue_workers_enabled" {
  description = "Fase 10.6: workers de casos en ECS Fargate con autoescalado por la cola SQS support-emails."
  type        = bool
  default     = false
}

variable "queue_worker_image" {
  description = "Imagen del orquestador (services/orchestrator/Dockerfile) en ECR (fase 13)."
  type        = string
  default     = ""
}

variable "queue_worker_max_tasks" {
  description = "Techo de tareas: el que permite la cuota del modelo (scripts/capacity_plan.py)."
  type        = number
  default     = 4
}

variable "queue_worker_messages_per_task" {
  description = "Objetivo de la política: mensajes visibles por tarea en marcha (backlog per task)."
  type        = number
  default     = 8
}
