# Fase 11.G1: observabilidad en GCP (Cloud Trace + Cloud Logging + Managed Prometheus + alertas).
# Desactivado por defecto (observability_enabled). Coste:
# - Trazas: Cloud Trace cobra por span ingerido (0,20 $/millón, los 2,5 M primeros al mes gratis).
# - Logs: 0,50 $/GiB ingerido (50 GiB/proyecto-mes gratis); retención de 30 días incluida.
# - Métricas: Managed Prometheus cobra por muestra ingerida ("Prometheus Samples Ingested").
# - Alertas: gratis hasta el 01-09-2027; después, 0,35 $/mes por métrica referenciada.
# El canal de notificación, los roles y las exclusiones no se facturan.
#
# El código no cambia: el collector (infra/otel/collector-gcp.yaml), como sidecar de Cloud Run con
# la cuenta de la aplicación, manda trazas y logs a la Telemetry API y métricas a Managed
# Prometheus. Las alertas, generadas desde domain/slo.py, llegan en la 11.G2.
#
#   Azure                          AWS                              GCP
#   Log Analytics (tope diario)    Log groups (retención)           Bucket _Default (retención) + exclusiones
#   App Insights (azuremonitor)    X-Ray por OTLP (SigV4)           Cloud Trace por la Telemetry API (OTLP)
#   Monitoring Metrics Publisher   Política IAM otel_collector      telemetry.writer + monitoring.metricWriter
#   Action group (email)           Tema SNS (email)                 Canal de notificación (email)

locals {
  observability = var.observability_enabled ? 1 : 0

  # Cada exclusión deja de ingerir (y de cobrar) lo que no se va a mirar nunca. Se aplican al
  # sink _Default: lo excluido no llega a ningún bucket.
  log_exclusions = {
    # Las sondas de Cloud Run y del balanceador llaman cada pocos segundos a cada réplica.
    health-checks = join(" AND ", [
      "resource.type=\"cloud_run_revision\"",
      "log_id(\"run.googleapis.com/requests\")",
      "httpRequest.requestUrl=~\"/(health|ready|ping)$\"",
    ])
    # Con TELEMETRY__LOG_LEVEL=DEBUG en una réplica para depurar, el volumen se multiplica: el
    # detalle se mira en la traza, no en los logs.
    debug-logs = "severity<=DEBUG"
  }
}

# ---------- Permisos del collector (cuenta de la aplicación) ----------

# Trazas y logs por OTLP a telemetry.googleapis.com (equivale a xray:PutSpans + logs:PutLogEvents).
resource "google_project_iam_member" "app_telemetry_writer" {
  count = local.observability

  project = var.project_id
  role    = "roles/telemetry.writer"
  member  = google_service_account.app.member
}

# Métricas a Managed Prometheus (exporter googlemanagedprometheus → monitoring.googleapis.com).
resource "google_project_iam_member" "app_metric_writer" {
  count = local.observability

  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = google_service_account.app.member
}

# ---------- Logs: retención y exclusiones ----------

# El bucket _Default existe siempre: Terraform solo fija su retención (30 días, los incluidos en el
# precio; más días se cobran) y no lo borra al destruir. Sin Log Analytics (`enable_analytics`):
# activarlo no se puede deshacer.
resource "google_logging_project_bucket_config" "default" {
  count = local.observability

  project        = var.project_id
  location       = "global"
  bucket_id      = "_Default"
  retention_days = var.log_retention_days
}

resource "google_logging_project_exclusion" "noise" {
  for_each = var.observability_enabled ? local.log_exclusions : {}

  name        = "${local.name}-${each.key}"
  description = "Fase 11.G1: no se ingiere (coste) lo que nadie va a mirar."
  filter      = each.value
}

# ---------- Aviso ----------

# Distinto del canal del presupuesto (budget.tf): las alertas de guardia pueden ir a otro sitio
# (un grupo, PagerDuty) sin tocar el aviso de facturación.
resource "google_monitoring_notification_channel" "oncall" {
  count = local.observability

  display_name = "${local.name}-oncall"
  type         = "email"
  labels = {
    email_address = var.budget_email
  }

  depends_on = [google_project_service.enabled]
}

# ---------- Alertas (11.G2): generadas desde domain/slo.py ----------
#
# alerts/alert_policies.json lo escribe `scripts/build_observability.py` (no editar a mano; la CI
# comprueba que está al día y pasa promtool a alerts/genaipocs.alerts.yml, la misma alerta en
# formato de Prometheus). Una política por alerta, con una sola condición PromQL: los SLI van
# expandidos dentro de la consulta porque Cloud Monitoring no evalúa recording rules.
#
#   Azure: 2 alertas KQL de muestra    AWS: 2 alarmas Metrics Insights    GCP: las 36, en PromQL
#
# Gratis hasta el 01-09-2027; después, 0,35 $/mes por métrica referenciada y 0,50 $ por millón de
# puntos devueltos (por eso 60 s de intervalo y no 30).

locals {
  alert_policies = var.observability_enabled ? jsondecode(file("${path.module}/alerts/alert_policies.json")).policies : {}
}

resource "google_monitoring_alert_policy" "generated" {
  for_each = local.alert_policies

  display_name          = "${local.name} · ${each.key}"
  combiner              = "OR"
  severity              = each.value.severity
  notification_channels = [google_monitoring_notification_channel.oncall[0].id]
  user_labels           = merge(local.labels, { owner = each.value.labels.owner })

  conditions {
    display_name = each.value.alert_rule

    condition_prometheus_query_language {
      query               = replace(each.value.query, "__PROJECT_ID__", var.project_id)
      duration            = each.value.duration
      evaluation_interval = each.value.evaluation_interval
      labels              = each.value.labels
      rule_group          = each.value.rule_group
      alert_rule          = each.value.alert_rule
      # Las métricas no existen hasta que el collector manda el primer punto: sin esto, el
      # apply falla en un proyecto recién creado.
      disable_metric_validation = true
    }
  }

  documentation {
    subject   = each.value.subject
    content   = replace(each.value.documentation, "__RUNBOOK_BASE__", var.runbook_base_url)
    mime_type = "text/markdown"
  }

  depends_on = [google_project_service.enabled]
}
