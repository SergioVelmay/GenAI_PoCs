# Guardarraíl de coste: presupuesto mensual del proyecto con alertas por correo.
# Igual que en AWS y Azure, avisa pero no corta el gasto.

resource "google_monitoring_notification_channel" "budget_email" {
  display_name = "${local.name}-budget"
  type         = "email"
  labels = {
    email_address = var.budget_email
  }

  depends_on = [google_project_service.enabled]
}

resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account
  display_name    = "${local.name}-monthly"

  budget_filter {
    projects = ["projects/${data.google_project.current.number}"]
    # Clave con la cuenta de prueba: por defecto el presupuesto mide el gasto NETO de créditos y,
    # mientras queden créditos, el gasto neto es 0 y ninguna alerta salta. Se mide el bruto.
    credit_types_treatment = "EXCLUDE_ALL_CREDITS"
    calendar_period        = "MONTH"
  }

  amount {
    specified_amount {
      # Sin currency_code: se usa la moneda de la cuenta de facturación (si no coincide, la API
      # rechaza el presupuesto).
      units = tostring(var.budget_limit)
    }
  }

  dynamic "threshold_rules" {
    for_each = toset(var.budget_alert_thresholds)
    content {
      threshold_percent = threshold_rules.value / 100
      spend_basis       = "CURRENT_SPEND"
    }
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = [google_monitoring_notification_channel.budget_email.id]
    # Además, los administradores de facturación reciben el correo por defecto.
    disable_default_iam_recipients = false
  }

  depends_on = [google_project_service.enabled]
}
