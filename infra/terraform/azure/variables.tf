variable "subscription_id" {
  description = "Suscripción de Azure (az account show --query id -o tsv)."
  type        = string
}

variable "location" {
  description = "Región de la UE con más modelos y cuota en Foundry."
  type        = string
  default     = "swedencentral"
  validation {
    # Debe tener abreviatura en local.region_abbr (convención de nombres CAF).
    condition     = contains(["swedencentral", "westeurope", "northeurope", "spaincentral", "francecentral"], var.location)
    error_message = "Región sin abreviatura CAF: añádela a local.region_abbr en main.tf."
  }
}

variable "instance" {
  description = "Número de instancia de la convención CAF (001, 002...). Cambiarlo recrea los recursos con nombre."
  type        = string
  default     = "001"
  validation {
    condition     = can(regex("^[0-9]{3}$", var.instance))
    error_message = "instance debe tener 3 dígitos (p. ej. 001)."
  }
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

variable "search_enabled" {
  description = "Crea AI Search. Basic tiene coste fijo (~70 €/mes): encenderlo solo en las sesiones de trabajo."
  type        = bool
  default     = false
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
  description = "Id de un recurso de Foundry externo con el despliegue de embeddings. Se ignora si foundry_enabled = true."
  type        = string
  default     = null
}

variable "foundry_project_principal_id" {
  description = "Object id de la identidad de un proyecto de Foundry externo (carril A, fase 4). Se ignora si foundry_enabled = true."
  type        = string
  default     = null
}

# ---------- Fase 2: Azure AI Foundry (foundry.tf) ----------

variable "foundry_enabled" {
  description = "Crea la cuenta AI Services, el proyecto y los despliegues de modelos. Sin coste fijo (pago por token)."
  type        = bool
  default     = false
}

variable "foundry_deployment_sku" {
  description = "GlobalStandard (más cuota y modelos), DataZoneStandard (procesamiento dentro de la UE) o Standard (región)."
  type        = string
  default     = "GlobalStandard"
  validation {
    condition     = contains(["GlobalStandard", "DataZoneStandard", "Standard"], var.foundry_deployment_sku)
    error_message = "foundry_deployment_sku debe ser GlobalStandard, DataZoneStandard o Standard."
  }
}

variable "foundry_models" {
  description = "Despliegues. `name` debe coincidir con AZURE_OPENAI__*_DEPLOYMENT; capacidad en miles de TPM."
  type = object({
    chat       = object({ name = string, model = string, version = string, capacity_k_tpm = number })
    cheap_chat = object({ name = string, model = string, version = string, capacity_k_tpm = number })
    embedding  = object({ name = string, model = string, version = string, capacity_k_tpm = number })
  })
  default = {
    chat       = { name = "gpt-4.1-mini", model = "gpt-4.1-mini", version = "2025-04-14", capacity_k_tpm = 50 }
    cheap_chat = { name = "gpt-4.1-nano", model = "gpt-4.1-nano", version = "2025-04-14", capacity_k_tpm = 50 }
    embedding  = { name = "text-embedding-3-small", model = "text-embedding-3-small", version = "1", capacity_k_tpm = 50 }
  }
}

variable "budget_limit_eur" {
  description = "Presupuesto mensual del grupo de recursos (solo avisa, no corta el gasto)."
  type        = number
  default     = 100
}

variable "budget_alert_thresholds" {
  description = "Porcentajes de gasto real que envían alerta (además de la previsión al 100 %)."
  type        = list(number)
  default     = [50, 75, 100]
  validation {
    # Azure admite 5 notificaciones por presupuesto; una es la de previsión.
    condition     = length(var.budget_alert_thresholds) <= 4 && alltrue([for t in var.budget_alert_thresholds : t > 0 && t <= 1000])
    error_message = "Hasta 4 umbrales, entre 1 y 1000 (%)."
  }
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

variable "content_safety_enabled" {
  description = "Fase 8.4: crea Azure AI Content Safety para el harness."
  type        = bool
  default     = false
}

variable "content_safety_sku" {
  description = "F0 (gratuito, cuota limitada) o S0 (pago por uso)."
  type        = string
  default     = "F0"
}
