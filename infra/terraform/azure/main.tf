# Fase 3, carril B en Azure: cola de indexación (Service Bus) + AI Search (tier Free).
# Coste en reposo ~0 €: AI Search Free y Service Bus Basic (pago por operación).
# Sin claves: autenticación local desactivada y permisos por RBAC (Entra ID).

locals {
  name = "${var.project}-${var.environment}"
  tags = {
    project     = var.project
    environment = var.environment
    managed_by  = "terraform"
  }
}

data "azurerm_client_config" "current" {}

# Los nombres de AI Search y Service Bus son globales: sufijo aleatorio estable.
resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

# ---------- Cola de indexación (equivale a knowledge.documents de RabbitMQ) ----------

resource "azurerm_servicebus_namespace" "main" {
  name                = "sb-${local.name}-${random_string.suffix.result}"
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

resource "azurerm_search_service" "main" {
  name                = "srch-${local.name}-${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "free" # 1 por suscripción, 50 MB, sin SLA: suficiente para la PoC.
  # Solo RBAC: sin claves de administración ni de consulta.
  local_authentication_enabled = false
  tags                         = local.tags
}

# ---------- Permisos para desarrollar en local con `az login` ----------
# En la nube estos roles se asignarán a la Managed Identity del worker (fase 14).

resource "azurerm_role_assignment" "me_search_data" {
  scope                = azurerm_search_service.main.id
  role_definition_name = "Search Index Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "me_search_service" {
  # Crear y actualizar índices (scripts/provision_search_index.py).
  scope                = azurerm_search_service.main.id
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

  notification {
    enabled        = true
    threshold      = 50
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_emails = [var.budget_email]
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Forecasted"
    contact_emails = [var.budget_email]
  }
}
