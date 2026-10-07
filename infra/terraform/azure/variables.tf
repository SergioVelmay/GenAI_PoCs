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
