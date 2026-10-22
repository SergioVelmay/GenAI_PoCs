# Fase 11.7: observabilidad en AWS (X-Ray con Transaction Search + CloudWatch + alarmas).
# Desactivado por defecto (observability_enabled). Coste: CloudWatch Logs ~0,57 $/GB ingerido y
# ~0,03 $/GB-mes almacenado (retención de 14 días); métricas personalizadas EMF 0,30 $/métrica-mes
# (cada combinación de dimensiones es una métrica: por eso el collector las limita); trazas de
# X-Ray por Transaction Search se cobran como logs (grupo aws/spans) y el 1 % se indexa gratis.
# Las alarmas, 0,10 $/mes (estándar) o 0,30 $/mes (Metrics Insights).
#
# El código no cambia: el collector ADOT (infra/otel/collector-aws.yaml) envía las trazas por
# OTLP al endpoint de X-Ray y las métricas como EMF a CloudWatch Logs.

locals {
  observability = var.observability_enabled ? 1 : 0
  metrics_ns    = "Headache/GenAI"
}

resource "aws_cloudwatch_log_group" "app" {
  count             = local.observability
  name              = "/headache/${var.environment}/app"
  retention_in_days = 14 # Los logs JSON llevan trace_id: se salta de un error a su traza.
}

# El exporter awsemf escribe aquí y CloudWatch extrae las métricas (Embedded Metric Format).
resource "aws_cloudwatch_log_group" "metrics" {
  count             = local.observability
  name              = "/headache/${var.environment}/metrics"
  retention_in_days = 3 # Solo hacen falta hasta que CloudWatch extrae la métrica.
}

# Vista de las trazas con error (equivale a filtrar `error=true` en Jaeger). Sin X-Ray Insights:
# la detección de anomalías tiene coste y aquí la hacen las alarmas de SLO.
resource "aws_xray_group" "errors" {
  count             = local.observability
  group_name        = "${local.name}-errors"
  filter_expression = "fault = true OR error = true"

  insights_configuration {
    insights_enabled      = false
    notifications_enabled = false
  }
}

# Permisos mínimos del collector ADOT (sidecar en ECS o DaemonSet en EKS, fase 13).
data "aws_iam_policy_document" "otel_collector" {
  statement {
    sid = "Traces"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "xray:PutSpans",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
    ]
    resources = ["*"] # X-Ray no admite permisos por recurso en estas acciones.
  }

  statement {
    sid       = "LogsAndEmfMetrics"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = [for g in concat(aws_cloudwatch_log_group.app, aws_cloudwatch_log_group.metrics) : "${g.arn}:*"]
  }
}

resource "aws_iam_policy" "otel_collector" {
  count  = local.observability
  name   = "${local.name}-otel-collector"
  policy = data.aws_iam_policy_document.otel_collector.json
}

resource "aws_sns_topic" "alerts" {
  count = local.observability
  name  = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count     = local.observability
  topic_arn = aws_sns_topic.alerts[0].arn
  protocol  = "email"
  endpoint  = var.budget_email
}

# Las mismas alertas que en Prometheus (domain/slo.py). Dos de muestra con CloudWatch Metrics
# Insights (SQL sobre las métricas EMF): suman todas las series sin depender de las dimensiones.

resource "aws_cloudwatch_metric_alarm" "llm_cost_runaway" {
  count               = local.observability
  alarm_name          = "${local.name}-llm-cost-runaway"
  alarm_description   = "Gasto en modelos al triple del presupuesto diario (docs/runbook-incidentes.md#coste)."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  # 15 min de gasto proyectado a un día: x96.
  threshold          = var.llm_daily_budget_usd * 3 / 96
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.alerts[0].arn]

  metric_query {
    id          = "cost"
    label       = "Coste del modelo en 15 min (USD)"
    expression  = "SELECT SUM(\"headache.llm.cost\") FROM \"${local.metrics_ns}\""
    period      = 900
    return_data = true
  }
}

resource "aws_cloudwatch_metric_alarm" "outbox_backlog" {
  count               = local.observability
  alarm_name          = "${local.name}-outbox-backlog"
  alarm_description   = "Eventos atascados en el outbox: los casos no avanzan (docs/runbook-incidentes.md#outbox)."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 100
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts[0].arn]

  metric_query {
    id          = "backlog"
    label       = "Eventos sin publicar"
    expression  = "SELECT MAX(\"headache.outbox.backlog\") FROM \"${local.metrics_ns}\""
    period      = 300
    return_data = true
  }
}
