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
  default = "headache"
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
    "aiplatform.googleapis.com",
    "cloudtrace.googleapis.com",
    "discoveryengine.googleapis.com",
    "dlp.googleapis.com",
    "logging.googleapis.com",
    "modelarmor.googleapis.com",
    "monitoring.googleapis.com",
    "pubsub.googleapis.com",
    "storage.googleapis.com",
  ]
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
