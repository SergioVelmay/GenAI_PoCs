variable "subscription_id" {
  description = "Suscripción de Azure (az account show --query id -o tsv)."
  type        = string
}

variable "location" {
  type    = string
  default = "westeurope"
}

variable "project" {
  type    = string
  default = "headache"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "max_deliveries" {
  description = "Entregas antes de mandar el mensaje a la DLQ (MaxDeliveryCount)."
  type        = number
  default     = 5
}

variable "search_sku" {
  description = "free (0 €, sin identidad gestionada) o basic (~70 €/mes, identidad gestionada)."
  type        = string
  default     = "free"
  validation {
    condition     = contains(["free", "basic"], var.search_sku)
    error_message = "search_sku debe ser free o basic."
  }
}

variable "openai_resource_id" {
  description = "Id del recurso de Azure OpenAI/Foundry con el despliegue de embeddings (fase 2)."
  type        = string
  default     = null
}

variable "foundry_project_principal_id" {
  description = "Object id de la identidad gestionada del proyecto de Foundry (carril A, fase 4)."
  type        = string
  default     = null
}

variable "budget_limit_eur" {
  type    = number
  default = 20
}

variable "budget_email" {
  description = "Correo que recibe la alerta de presupuesto."
  type        = string
}

variable "budget_start_date" {
  description = "Primer día del mes en que empieza el presupuesto (RFC 3339)."
  type        = string
  default     = "2026-10-01T00:00:00Z"
}

# ---------- Fase 6.4: API Management como MCP Gateway ----------

variable "apim_enabled" {
  description = "Crea APIM. Tiene coste fijo (Developer ~50 $/mes): solo con confirmación."
  type        = bool
  default     = false
}

variable "apim_sku" {
  description = "Tier con soporte MCP: Developer_1 (sin SLA) o BasicV2_1/StandardV2_1."
  type        = string
  default     = "Developer_1"
  validation {
    condition     = !startswith(var.apim_sku, "Consumption")
    error_message = "El tier Consumption no admite servidores MCP."
  }
}

variable "mcp_server_base_urls" {
  description = "URL base HTTPS de cada servidor (catalog, ticketing, refunds); APIM añade /mcp."
  type        = map(string)
  default     = {}
  validation {
    condition     = alltrue([for k, v in var.mcp_server_base_urls : contains(["catalog", "ticketing", "refunds"], k) && startswith(v, "https://")])
    error_message = "Claves catalog|ticketing|refunds y URLs https://."
  }
}

variable "mcp_oidc_config_url" {
  description = "OpenID configuration de Entra ID: https://login.microsoftonline.com/<tenant>/v2.0/.well-known/openid-configuration."
  type        = string
  default     = ""
}

variable "mcp_audience_template" {
  description = "Igual que MCP__AUDIENCE_TEMPLATE: App ID URI de cada servidor en Entra ID."
  type        = string
  default     = "api://headache-mcp/{server}"
}
