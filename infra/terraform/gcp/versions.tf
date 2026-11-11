terraform {
  required_version = ">= 1.10"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.10"
    }
    # Recursos en preview (Agent Gateway, Agent Registry...) en fases posteriores de la pasada.
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 8.10"
    }
  }

  # Estado local de momento; en la fase 15 pasa a Cloud Storage (bloqueo nativo del backend gcs).
}

# Credenciales: ADC de usuario (`gcloud auth application-default login` con CLOUDSDK_CONFIG
# aislado) o la cuenta de servicio de la CI por Workload Identity Federation. Nunca claves JSON.
provider "google" {
  project = var.project_id
  region  = var.region

  # Con ADC de usuario, APIs como Billing Budgets exigen un proyecto de cuota explícito: sin
  # esto devuelven 403 aunque el usuario tenga permisos sobre la cuenta de facturación.
  billing_project       = var.project_id
  user_project_override = true

  default_labels = local.labels
}

provider "google-beta" {
  project               = var.project_id
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
  default_labels        = local.labels
}
