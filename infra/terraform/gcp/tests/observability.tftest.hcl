# Fase 11.G1: plan simulado (sin credenciales, sin red, sin coste) de la observabilidad en GCP:
# desactivada por defecto; activada, los roles del collector en la cuenta de la aplicación, la
# retención del bucket _Default, las exclusiones de ruido y el canal de guardia.
#
#   terraform test   (en infra/terraform/gcp; lo ejecuta la CI)

mock_provider "google" {
  mock_data "google_project" {
    defaults = {
      number = "123456789012"
    }
  }

  # El provider valida el formato de los miembros de IAM: los valores aleatorios no pasan.
  mock_resource "google_service_account" {
    defaults = {
      email  = "genaipocs-dev-app@genaipocs-test.iam.gserviceaccount.com"
      member = "serviceAccount:genaipocs-dev-app@genaipocs-test.iam.gserviceaccount.com"
      name   = "projects/genaipocs-test/serviceAccounts/genaipocs-dev-app@genaipocs-test.iam.gserviceaccount.com"
    }
  }
}

mock_provider "google-beta" {}

variables {
  project_id      = "genaipocs-test"
  billing_account = "000000-000000-000000"
  budget_email    = "alertas@genaipocs.example"
}

run "disabled_by_default" {
  command = plan

  assert {
    condition = (
      length(google_project_iam_member.app_telemetry_writer) == 0
      && length(google_logging_project_bucket_config.default) == 0
      && length(google_logging_project_exclusion.noise) == 0
      && length(google_monitoring_notification_channel.oncall) == 0
      && length(google_monitoring_alert_policy.generated) == 0
    )
    error_message = "Sin observability_enabled no se crea nada de observabilidad."
  }

  assert {
    condition     = output.observability == null
    error_message = "Sin observabilidad, no hay variables del collector."
  }

  assert {
    condition     = contains(var.services, "telemetry.googleapis.com")
    error_message = "La Telemetry API (OTLP) se habilita siempre: habilitarla no cuesta."
  }
}

run "enabled_with_collector_roles_and_log_hygiene" {
  command = apply

  variables {
    observability_enabled = true
  }

  assert {
    condition = (
      google_project_iam_member.app_telemetry_writer[0].role == "roles/telemetry.writer"
      && google_project_iam_member.app_metric_writer[0].role == "roles/monitoring.metricWriter"
      && google_project_iam_member.app_telemetry_writer[0].member == "serviceAccount:genaipocs-dev-app@genaipocs-test.iam.gserviceaccount.com"
    )
    error_message = "El collector escribe con la cuenta de la aplicación: trazas y logs (Telemetry API) y métricas (Managed Prometheus)."
  }

  assert {
    condition = (
      google_logging_project_bucket_config.default[0].bucket_id == "_Default"
      && google_logging_project_bucket_config.default[0].retention_days == 30
      && google_logging_project_bucket_config.default[0].enable_analytics != true
    )
    error_message = "Retención de 30 días (incluida en el precio) y sin Log Analytics (no se puede desactivar)."
  }

  assert {
    condition = (
      length(google_logging_project_exclusion.noise) == 2
      && strcontains(google_logging_project_exclusion.noise["health-checks"].filter, "/(health|ready|ping)$")
      && google_logging_project_exclusion.noise["debug-logs"].filter == "severity<=DEBUG"
    )
    error_message = "Se excluyen las sondas de salud y los logs DEBUG."
  }

  assert {
    condition     = google_monitoring_notification_channel.oncall[0].labels.email_address == "alertas@genaipocs.example"
    error_message = "El canal de guardia avisa por correo."
  }

  assert {
    condition     = output.observability.collector_env.GOOGLE_CLOUD_PROJECT == "genaipocs-test"
    error_message = "El output da las variables del collector."
  }
}

run "generated_alert_policies" {
  command = apply

  variables {
    observability_enabled = true
  }

  assert {
    condition     = length(google_monitoring_alert_policy.generated) == length(jsondecode(file("alerts/alert_policies.json")).policies)
    error_message = "Una política por alerta generada (alerts/alert_policies.json)."
  }

  assert {
    condition = alltrue([
      for p in google_monitoring_alert_policy.generated : (
        !strcontains(p.conditions[0].condition_prometheus_query_language[0].query, "__PROJECT_ID__")
        && !strcontains(p.documentation[0].content, "__RUNBOOK_BASE__")
        && p.conditions[0].condition_prometheus_query_language[0].disable_metric_validation
      )
    ])
    error_message = "Marcadores sustituidos y sin validar métricas que aún no existen."
  }

  assert {
    condition = (
      google_monitoring_alert_policy.generated["ErrorBudgetBurn-llm-calls-1h"].severity == "CRITICAL"
      && google_monitoring_alert_policy.generated["ErrorBudgetBurn-llm-calls-3d"].severity == "WARNING"
      && google_monitoring_alert_policy.generated["ErrorBudgetBurn-llm-calls-3d"].conditions[0].condition_prometheus_query_language[0].duration == "0s"
      && google_monitoring_alert_policy.generated["ErrorBudgetBurn-llm-calls-3d"].conditions[0].condition_prometheus_query_language[0].evaluation_interval == "300s"
    )
    error_message = "Página = CRITICAL; consultas de más de 25 h cada 5 min y sin ventana de reintento."
  }

  assert {
    condition     = strcontains(google_monitoring_alert_policy.generated["PubSubBacklogGrowing"].conditions[0].condition_prometheus_query_language[0].query, "project_id=\"genaipocs-test\"")
    error_message = "El backlog de Pub/Sub consulta el proyecto real."
  }

  assert {
    condition     = alltrue([for p in google_monitoring_alert_policy.generated : tolist(p.notification_channels) == tolist([google_monitoring_notification_channel.oncall[0].id])])
    error_message = "Todas avisan al canal de guardia."
  }
}
