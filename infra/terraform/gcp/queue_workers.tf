# Fase 10.G4: workers de triaje en un worker pool de Cloud Run con autoescalado por el backlog de
# Pub/Sub. Desactivado por defecto (queue_workers_enabled). La imagen la publica la fase 15.
#
#   Azure                               AWS                                GCP
#   Container Apps + regla KEDA         ECS Fargate + target tracking      Worker pool + CREMA
#   `azure-servicebus` (KEDA gestionado) (mensajes visibles por tarea)     (KEDA `prometheus` como
#                                                                          servicio de Cloud Run)
#
# Los worker pools no tienen URL ni autoescalado propio (GA el 14-04-2026; la doc lo dice así
# aunque el provider exponga `scaling_mode = AUTOMATIC`). Los escala CREMA (Cloud Run External
# Metrics Autoscaling): KEDA empaquetado como servicio de Cloud Run que lee su configuración de
# Parameter Manager, consulta la métrica y cambia `manual_instance_count` por la Admin API.
#
# Métrica: el escalador `gcp-pubsub` de KEDA está obsoleto (usa MQL). Se usa `prometheus` contra
# la API de Managed Prometheus de Cloud Monitoring con **la misma consulta** que el adaptador
# local (`pubsub_backlog_promql()`, 10.G1; un test de Python comprueba que coinciden). Llega con
# 1-3 min de retraso: la ventana de bajada es larga para no apagar workers en mitad de un pico.
#
# Coste: el worker pool con 0 instancias no cuesta; cada instancia, vCPU y memoria por segundo
# mientras exista. CREMA es un servicio con CPU siempre asignada y una instancia fija (sondea cada
# 30 s): ~1 vCPU + 512 MiB todo el mes, del orden de 40-50 $/mes si se deja encendido. Efímero.

locals {
  queue_workers   = var.queue_workers_enabled && var.queue_worker_image != "" ? 1 : 0
  queue_worker    = "${local.name}-triage-worker"
  monitoring_prom = "https://monitoring.googleapis.com/v1/projects/${var.project_id}/location/global/prometheus"
  # Fuente única en Python: genaipocs_core.adapters.queue_metrics.pubsub_backlog_promql.
  queue_backlog_query = "sum(pubsub_googleapis_com:subscription_num_unacked_messages_by_region{monitored_resource=\"pubsub_subscription\",project_id=\"${var.project_id}\",subscription_id=\"support.emails.worker\"})"

  crema_config = yamlencode({
    apiVersion = "crema/v1"
    kind       = "CremaConfig"
    metadata   = { name = "${local.name}-queue-workers" }
    spec = {
      # Identidad del servicio de CREMA (lo que en GKE sería Workload Identity).
      triggerAuthentications = [{
        metadata = { name = "gcp-identity" }
        spec     = { podIdentity = { provider = "gcp" } }
      }]
      scaledObjects = [{
        # Cada cuánto consulta CREMA la métrica (la métrica cambia cada 60 s).
        pollingInterval = 30
        spec = {
          scaleTargetRef  = { name = "projects/${var.project_id}/locations/${var.region}/workerpools/${local.queue_worker}" }
          minReplicaCount = 0
          maxReplicaCount = var.queue_worker_max_instances
          triggers = [{
            type = "prometheus"
            metadata = {
              serverAddress = local.monitoring_prom
              query         = local.queue_backlog_query
              # Mensajes por instancia (AverageValue): la regla del dominio y de Azure/AWS.
              threshold           = tostring(var.queue_worker_messages_per_instance)
              activationThreshold = "0"
              # Un hueco en la métrica no es "cola vacía" (como el adaptador local).
              ignoreNullValues = "false"
            }
            authenticationRef = { name = "gcp-identity" }
          }]
          advanced = {
            horizontalPodAutoscalerConfig = {
              behavior = {
                # Subir rápido: +4 instancias por minuto como mucho (max_scale_up_step).
                scaleUp = {
                  stabilizationWindowSeconds = 0
                  policies                   = [{ type = "Pods", value = 4, periodSeconds = 60 }]
                }
                # Bajar despacio: la métrica llega con hasta 3 min de retraso.
                scaleDown = {
                  stabilizationWindowSeconds = 300
                  policies                   = [{ type = "Pods", value = 100, periodSeconds = 60 }]
                }
              }
            }
          }
        }
      }]
    }
  })
  # Versión por contenido: cambiar la política crea otra versión y CREMA se redespliega con ella.
  crema_config_version = substr(sha256(local.crema_config), 0, 12)
}

# ---------- Workers ----------

resource "google_cloud_run_v2_worker_pool" "triage" {
  count = local.queue_workers

  name                = local.queue_worker
  location            = var.region
  labels              = local.labels
  deletion_protection = false

  # Arranca en 0 y lo mueve CREMA: Terraform no debe devolverlo a 0 en cada apply.
  scaling {
    scaling_mode          = "MANUAL"
    manual_instance_count = 0
  }

  template {
    # La identidad de la aplicación ya publica y consume los temas del orquestador (pubsub.tf).
    service_account = google_service_account.app.email

    containers {
      image   = var.queue_worker_image
      command = ["python", "-m", "orchestrator.worker"]
      # Como en Azure y AWS: triaje + relay del outbox (la base de datos llega en la fase 15).
      args = ["triage", "relay"]

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      env {
        name  = "ORCHESTRATOR__BROKER"
        value = "pubsub"
      }
      env {
        name  = "GCP__PROJECT"
        value = var.project_id
      }
      env {
        name  = "LLM_PROVIDER"
        value = "gcp"
      }
    }
  }

  lifecycle {
    ignore_changes = [scaling[0].manual_instance_count]
  }
}

# ---------- CREMA ----------

resource "google_service_account" "crema" {
  count = local.queue_workers

  account_id   = "${local.name}-crema"
  display_name = "Genaipocs ${var.environment}: autoescalador CREMA"
  description  = "Lee el backlog de Pub/Sub en Cloud Monitoring y ajusta las instancias del worker pool."
}

resource "google_parameter_manager_parameter" "crema" {
  count = local.queue_workers

  parameter_id = "${local.name}-crema"
  format       = "YAML"
  labels       = local.labels

  depends_on = [google_project_service.enabled]
}

resource "google_parameter_manager_parameter_version" "crema" {
  count = local.queue_workers

  parameter            = google_parameter_manager_parameter.crema[0].id
  parameter_version_id = local.crema_config_version
  parameter_data       = local.crema_config
}

# Mínimo privilegio: leer su configuración y la métrica, publicar las métricas del escalador y
# cambiar solo este worker pool (actuando como su identidad).
resource "google_project_iam_member" "crema" {
  for_each = local.queue_workers == 1 ? toset([
    "roles/parametermanager.parameterViewer",
    "roles/monitoring.viewer",
    "roles/monitoring.metricWriter",
  ]) : toset([])

  project = var.project_id
  role    = each.key
  member  = google_service_account.crema[0].member
}

resource "google_cloud_run_v2_worker_pool_iam_member" "crema_developer" {
  count = local.queue_workers

  name     = google_cloud_run_v2_worker_pool.triage[0].name
  location = var.region
  role     = "roles/run.developer"
  member   = google_service_account.crema[0].member
}

resource "google_service_account_iam_member" "crema_acts_as_app" {
  count = local.queue_workers

  service_account_id = google_service_account.app.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.crema[0].member
}

resource "google_cloud_run_v2_service" "crema" {
  count = local.queue_workers

  name                = "${local.name}-crema"
  location            = var.region
  labels              = merge(local.labels, { created-by = "crema" })
  deletion_protection = false
  # No recibe tráfico: sondea por su cuenta (pollingInterval).
  ingress              = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  invoker_iam_disabled = false

  template {
    service_account = google_service_account.crema[0].email

    scaling {
      min_instance_count = 1
      max_instance_count = 1
    }

    containers {
      image = var.crema_image

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = false # --no-cpu-throttling: sondea aunque no haya peticiones.
      }

      env {
        name  = "CREMA_CONFIG"
        value = "projects/${var.project_id}/locations/global/parameters/${google_parameter_manager_parameter.crema[0].parameter_id}/versions/${local.crema_config_version}"
      }
      env {
        name  = "OUTPUT_SCALER_METRICS"
        value = "True"
      }
    }
  }

  depends_on = [
    google_parameter_manager_parameter_version.crema,
    google_project_iam_member.crema,
    google_cloud_run_v2_worker_pool_iam_member.crema_developer,
  ]
}
