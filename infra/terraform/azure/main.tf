# Fase 3, carril B en Azure: cola de indexación (Service Bus) + AI Search.
# Coste en reposo ~0 €: Service Bus Basic (pago por operación) y AI Search apagado
# (search_enabled = false). AI Search Basic (~70 €/mes) solo existe mientras se usa.
# Sin claves: autenticación local desactivada y permisos por RBAC (Entra ID).

locals {
  name = "${var.project}-${var.environment}"
  tags = {
    project     = var.project
    environment = var.environment
    managed_by  = "terraform"
  }
  search_managed_identity = var.search_sku != "free"
  search                  = var.search_enabled ? 1 : 0

  # Convención de nombres del Cloud Adoption Framework: <tipo>-<carga>-<entorno>-<región>-<instancia>.
  # Varios nombres son globales en Azure (forman parte de una URL pública): comprobar que están
  # libres antes del apply. Storage no admite guiones y tiene un máximo de 24 caracteres.
  region_abbr = {
    swedencentral = "swc"
    westeurope    = "weu"
    northeurope   = "neu"
    spaincentral  = "spc"
    francecentral = "frc"
  }
  caf_suffix = "${local.name}-${local.region_abbr[var.location]}-${var.instance}"
  # AI Search puede vivir en otra región (ver var.search_location): su nombre lleva la suya.
  # Estable: al recrear AI Search, el endpoint no cambia.
  search_name  = "srch-${local.name}-${local.region_abbr[var.search_location]}-${var.instance}"
  storage_name = replace("st${local.caf_suffix}", "-", "")
}

data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

# ---------- Cola de indexación (equivale a knowledge.documents de RabbitMQ) ----------

resource "azurerm_servicebus_namespace" "main" {
  name                = "sb-${local.caf_suffix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic" # Colas sí; topics, sesiones y duplicados exigen Standard.
  local_auth_enabled  = false   # Nada de connection strings con SAS: solo Entra ID.
  minimum_tls_version = "1.2"
  tags                = local.tags
}

resource "azurerm_servicebus_queue" "knowledge" {
  name                                 = "knowledge.documents"
  namespace_id                         = azurerm_servicebus_namespace.main.id
  max_delivery_count                   = var.max_deliveries
  lock_duration                        = "PT1M"
  dead_lettering_on_message_expiration = true
}

# ---------- Almacén vectorial (carril B) ----------
# Efímero: se crea para las sesiones de trabajo y se borra al terminar (search_enabled).
# Al recrearlo hay que volver a crear el índice e indexar (ver docs/pendientes-cloud.md).

resource "azurerm_search_service" "main" {
  count               = local.search
  name                = local.search_name
  resource_group_name = azurerm_resource_group.main.name
  location            = var.search_location
  sku                 = var.search_sku
  # Plan gratuito del Semantic Ranker (1000 consultas/mes): lo usan el carril B y Foundry IQ.
  # azurerm no admite fijarlo en el tier Free.
  semantic_search_sku = var.search_sku == "free" ? null : "free"
  # Solo RBAC: sin claves de administración ni de consulta.
  local_authentication_enabled = false
  tags                         = local.tags

  # Identidad gestionada para que el indexer (carril A) lea Blob y llame a Azure OpenAI.
  # El tier Free no la admite: ahí el carril A tiene que usar claves.
  dynamic "identity" {
    for_each = local.search_managed_identity ? [1] : []
    content {
      type = "SystemAssigned"
    }
  }
}

# ---------- Permisos para desarrollar en local con `az login` ----------
# En la nube estos roles se asignarán a la Managed Identity del worker (fase 14).

resource "azurerm_role_assignment" "me_search_data" {
  count                = local.search
  scope                = azurerm_search_service.main[0].id
  role_definition_name = "Search Index Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "me_search_service" {
  # Crear y actualizar índices (scripts/provision_search_index.py).
  count                = local.search
  scope                = azurerm_search_service.main[0].id
  role_definition_name = "Search Service Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "me_servicebus" {
  scope                = azurerm_servicebus_namespace.main.id
  role_definition_name = "Azure Service Bus Data Owner"
  principal_id         = data.azurerm_client_config.current.object_id
}

# ---------- Guardarraíl de coste ----------

resource "azurerm_consumption_budget_resource_group" "monthly" {
  name              = "budget-${local.name}"
  resource_group_id = azurerm_resource_group.main.id
  amount            = var.budget_limit_eur
  time_grain        = "Monthly"

  time_period {
    start_date = var.budget_start_date
  }

  # Gasto real (50 %, 75 %, 100 % por defecto) + previsión de superar el límite a fin de mes.
  dynamic "notification" {
    for_each = toset(var.budget_alert_thresholds)
    content {
      enabled        = true
      threshold      = notification.value
      operator       = "GreaterThan"
      threshold_type = "Actual"
      contact_emails = [var.budget_email]
    }
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Forecasted"
    contact_emails = [var.budget_email]
  }
}
