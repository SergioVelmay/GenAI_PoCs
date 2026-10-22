# Fase 11.7: observabilidad en Azure (Application Insights sobre Log Analytics + alertas).
# Desactivado por defecto (observability_enabled). Coste: Log Analytics se paga por GB ingerido
# (~2,3 €/GB en Analytics; los primeros 5 GB/mes de la cuenta de facturación son gratis) y la
# retención de 30 días está incluida. El límite diario (observability_daily_cap_gb) corta la
# ingesta al llegar: es el freno ante un bucle que llene los logs. Las alertas de consulta
# programada cuestan ~1,5 $/mes cada una (evaluación cada 5-15 min).
#
# El código no cambia: el collector (infra/otel/collector-azure.yaml) exporta a Application
# Insights con el exporter `azuremonitor`, igual que en local exporta a Jaeger y Prometheus.
# Trazas → tabla `dependencies`/`requests`; métricas OTel → `customMetrics`; logs → `traces`.

locals {
  observability = var.observability_enabled ? 1 : 0
}

resource "azurerm_log_analytics_workspace" "main" {
  count               = local.observability
  name                = "log-${local.caf_suffix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30 # Mínimo (incluido en el precio de ingesta).
  daily_quota_gb      = var.observability_daily_cap_gb
  # Solo Entra ID: sin claves compartidas del workspace.
  local_authentication_enabled = false
  tags                         = local.tags
}

resource "azurerm_application_insights" "main" {
  count                = local.observability
  name                 = "appi-${local.caf_suffix}"
  location             = azurerm_resource_group.main.location
  resource_group_name  = azurerm_resource_group.main.name
  workspace_id         = azurerm_log_analytics_workspace.main[0].id
  application_type     = "other"
  retention_in_days    = 30
  daily_data_cap_in_gb = var.observability_daily_cap_gb
  # El muestreo se decide en el SDK (TELEMETRY__SAMPLE_RATIO, ParentBased): aquí no se recorta
  # otra vez, porque partiría trazas ya muestreadas de forma coherente.
  sampling_percentage = 100
  # Ingesta solo con Entra ID: el collector usa la identidad gestionada (rol Monitoring Metrics
  # Publisher); la connection string sola no basta para escribir.
  local_authentication_enabled = false
  tags                         = local.tags
}

# Escribir telemetría desde local con `az login` (en la nube, la identidad del collector, fase 14).
resource "azurerm_role_assignment" "me_appi_publisher" {
  count                = local.observability
  scope                = azurerm_application_insights.main[0].id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_monitor_action_group" "oncall" {
  count               = local.observability
  name                = "ag-${local.caf_suffix}"
  resource_group_name = azurerm_resource_group.main.name
  short_name          = "headache"
  tags                = local.tags

  email_receiver {
    name                    = "guardia"
    email_address           = var.budget_email
    use_common_alert_schema = true
  }
}

# Las mismas alertas que en Prometheus (domain/slo.py), en KQL. Dos de muestra: el resto sale del
# mismo catálogo cuando se despliegue (fase 14). Las métricas OTel llegan a `customMetrics` con
# los atributos en `customDimensions`.

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "llm_cost_runaway" {
  count                = local.observability
  name                 = "alert-llm-cost-runaway-${local.caf_suffix}"
  resource_group_name  = azurerm_resource_group.main.name
  location             = azurerm_resource_group.main.location
  description          = "Gasto en modelos al triple del presupuesto diario (docs/runbook-incidentes.md#coste)."
  scopes               = [azurerm_application_insights.main[0].id]
  severity             = 1
  evaluation_frequency = "PT5M"
  window_duration      = "PT15M"
  tags                 = local.tags

  criteria {
    query                   = <<-KQL
      customMetrics
      | where name == "headache.llm.cost"
      | summarize usd = sum(valueSum)
      | extend usd_per_day = usd * 96.0
    KQL
    time_aggregation_method = "Maximum"
    metric_measure_column   = "usd_per_day"
    operator                = "GreaterThan"
    threshold               = var.llm_daily_budget_usd * 3
  }

  action {
    action_groups = [azurerm_monitor_action_group.oncall[0].id]
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "llm_error_burn" {
  count                = local.observability
  name                 = "alert-llm-error-burn-${local.caf_suffix}"
  resource_group_name  = azurerm_resource_group.main.name
  location             = azurerm_resource_group.main.location
  description          = "SLO llm-calls (99 %) quemándose a 14,4x en 1 h (docs/runbook-incidentes.md#llm-calls)."
  scopes               = [azurerm_application_insights.main[0].id]
  severity             = 1
  evaluation_frequency = "PT5M"
  window_duration      = "PT1H"
  tags                 = local.tags

  criteria {
    # Los cortes del usuario (CancelledError) no son fallos del proveedor: igual que en local.
    query                   = <<-KQL
      customMetrics
      | where name == "gen_ai.client.operation.duration"
      | extend error_type = tostring(customDimensions["error.type"])
      | summarize total = sum(valueCount),
                  bad = sumif(valueCount, error_type != "" and error_type != "CancelledError")
      | extend error_ratio = iff(total == 0, 0.0, todouble(bad) / total)
    KQL
    time_aggregation_method = "Maximum"
    metric_measure_column   = "error_ratio"
    operator                = "GreaterThan"
    threshold               = 0.144 # 14,4 x (1 - 0,99)
  }

  action {
    action_groups = [azurerm_monitor_action_group.oncall[0].id]
  }
}
