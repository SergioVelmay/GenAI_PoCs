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
