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
