# Fase 9.G4: especialistas como servicios A2A en Cloud Run, un servicio por subagente (equivale
# a los contenedores del perfil `agents` de docker-compose, a Container Apps en Azure y a AgentCore
# Runtime con protocolo A2A o ECS en AWS).
#
#   Azure                          AWS                              GCP
#   Entra ID: app roles + MI       Cognito client credentials, IAM  ID token de Google (OIDC)
#   audience = api://<app>         audience por resource server     custom audience por servicio
#   rol asignado al llamante       scope del token                  roles/run.invoker + lista
#
# Dos capas de autorización: Cloud Run comprueba el ID token y `roles/run.invoker` ANTES de llegar
# al contenedor (solo la cuenta del orquestador), y el especialista lo vuelve a verificar contra el
# JWKS de Google (`A2A__IDENTITY=gcp`: `aud` propio y `email` en `A2A__ALLOWED_CALLERS`). El
# token viaja en `Authorization`: con `X-Serverless-Authorization`, Cloud Run le quita la firma.
#
# Coste: pago por petición (CPU solo durante las peticiones) y escala a cero: ~0 $ en reposo. Sin
# `a2a_image` no se crea nada. Arrancar de verdad necesita además lo que el especialista usa en
# local (PostgreSQL de las aprobaciones, API de ticketing, retrieval): llega en la fase 15 con
# Cloud SQL y Direct VPC egress; por eso aquí solo van las variables de identidad y de modelo.

locals {
  a2a_specialists = var.a2a_enabled && var.a2a_image != "" ? toset(["refunds", "creative", "knowledge"]) : toset([])
  # URL determinista de Cloud Run: la Agent Card la anuncia y el orquestador la usa (A2A__URLS).
  a2a_urls = { for s in local.a2a_specialists : s => "https://${local.name}-a2a-${s}-${data.google_project.current.number}.${var.region}.run.app" }
}

# Una identidad por especialista: mínimo privilegio y trazas de auditoría separadas.
resource "google_service_account" "a2a" {
  for_each = local.a2a_specialists

  account_id   = "${local.name}-a2a-${each.key}"
  display_name = "Genaipocs ${var.environment}: especialista ${each.key} (A2A)"
  description  = "Identidad del contenedor A2A del especialista ${each.key} (9.G4)."

  depends_on = [google_project_service.enabled]
}

# Gemini (LLM_PROVIDER=gcp). El resto de permisos (Cloud SQL, retrieval) llegan en la fase 15.
resource "google_project_iam_member" "a2a_agent_platform" {
  for_each = local.a2a_specialists

  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = google_service_account.a2a[each.key].member
}

resource "google_cloud_run_v2_service" "a2a" {
  for_each = local.a2a_specialists

  name     = "${local.name}-a2a-${each.key}"
  location = var.region
  labels   = merge(local.labels, { specialist = each.key })

  # Público en red pero con IAM: sin `roles/run.invoker`, 403 antes de llegar al contenedor. En
  # la fase 15, INGRESS_TRAFFIC_INTERNAL_ONLY con el orquestador en la misma VPC.
  ingress              = "INGRESS_TRAFFIC_ALL"
  invoker_iam_disabled = false
  # El audience que pide el orquestador (A2A__AUDIENCE_TEMPLATE), además de la URL run.app.
  custom_audiences    = ["api://genaipocs-agents/${each.key}"]
  deletion_protection = false

  template {
    service_account                  = google_service_account.a2a[each.key].email
    max_instance_request_concurrency = 4 # Cada caso es una o varias llamadas al LLM.
    timeout                          = "300s"

    scaling {
      min_instance_count = 0
      max_instance_count = var.a2a_max_instances
    }

    containers {
      image = var.a2a_image
      # El ENTRYPOINT del Dockerfile ya trae `orchestrator.serve --host 0.0.0.0 --port 8000`.
      args = [each.key, "--public-url", local.a2a_urls[each.key]]

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle = true # CPU solo durante las peticiones (facturación por petición).
      }

      env {
        name  = "A2A__IDENTITY"
        value = "gcp"
      }
      env {
        name  = "A2A__ALLOWED_CALLERS"
        value = jsonencode([google_service_account.app.email])
      }
      env {
        name  = "LLM_PROVIDER"
        value = "gcp"
      }
      env {
        name  = "GCP__PROJECT"
        value = var.project_id
      }
      env {
        name  = "GCP__REGION"
        value = var.region
      }

      startup_probe {
        http_get {
          path = "/health"
          port = 8000
        }
      }
    }
  }

  depends_on = [google_project_iam_member.a2a_agent_platform]
}

# Solo el orquestador (la cuenta de la aplicación) invoca a los especialistas.
resource "google_cloud_run_v2_service_iam_member" "a2a_invoker" {
  for_each = local.a2a_specialists

  name     = google_cloud_run_v2_service.a2a[each.key].name
  location = var.region
  role     = "roles/run.invoker"
  member   = google_service_account.app.member
}
