# Fase 10.6: workers de casos en Azure Container Apps con autoescalado por cola (KEDA gestionado).
# Desactivado por defecto (queue_workers_enabled). Coste: el entorno de consumo no cobra en
# reposo y, con min_replicas = 0, sin mensajes no hay réplicas ni coste de cómputo
# (~0,000024 €/vCPU-s mientras procesan). La imagen la publica la fase 14 (ACR + identidad).
#
# La regla equivale a la del controlador local (scripts/queue_autoscaler.py):
#   réplicas = ceil(mensajes / messageCount), entre min y max; a 0 tras cooldown_period.

locals {
  queue_workers = var.queue_workers_enabled ? 1 : 0
}

resource "azurerm_servicebus_queue" "support_emails" {
  count                                = local.queue_workers
  name                                 = "support.emails"
  namespace_id                         = azurerm_servicebus_namespace.main.id
  max_delivery_count                   = var.max_deliveries
  lock_duration                        = "PT5M" # Un caso con LLM tarda segundos o minutos.
  dead_lettering_on_message_expiration = true
}

resource "azurerm_container_app_environment" "workers" {
  count               = local.queue_workers
  name                = "cae-${local.caf_suffix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  # Sin Log Analytics (se cobra por GB ingerido): los logs, en la fase 11 con OpenTelemetry.
  tags = local.tags

  lifecycle {
    precondition {
      condition     = var.queue_worker_image != ""
      error_message = "queue_workers_enabled exige queue_worker_image (imagen del orquestador)."
    }
  }
}

resource "azurerm_container_app" "triage_worker" {
  count                        = local.queue_workers
  name                         = "ca-triage-${local.caf_suffix}"
  container_app_environment_id = azurerm_container_app_environment.workers[0].id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  tags                         = local.tags

  # Identidad gestionada: la usan el worker (recibir y publicar) y la regla de escalado.
  identity {
    type = "SystemAssigned"
  }

  template {
    min_replicas = 0
    # Techo útil = la cuota del modelo (scripts/capacity_plan.py): más réplicas solo dan 429.
    max_replicas                = var.queue_worker_max_replicas
    polling_interval_in_seconds = 15
    cooldown_period_in_seconds  = 120

    container {
      name    = "triage"
      image   = var.queue_worker_image
      cpu     = 0.5
      memory  = "1Gi"
      command = ["python", "-m", "orchestrator.worker"]
      args    = ["triage", "relay"]

      env {
        name  = "ORCHESTRATOR__BROKER"
        value = "servicebus"
      }
      env {
        name  = "SERVICE_BUS__FULLY_QUALIFIED_NAMESPACE"
        value = "${azurerm_servicebus_namespace.main.name}.servicebus.windows.net"
      }
    }

    custom_scale_rule {
      name             = "support-emails-backlog"
      custom_rule_type = "azure-servicebus"
      metadata = {
        queueName              = azurerm_servicebus_queue.support_emails[0].name
        namespace              = azurerm_servicebus_namespace.main.name
        messageCount           = tostring(var.queue_worker_messages_per_replica)
        activationMessageCount = "0"
      }
      # Sin connection strings (local_auth desactivado): KEDA lee la cola con la identidad.
      identity_id = "system"
    }
  }
}

# El worker recibe y publica en el namespace (triaje → case.triaged.*).
resource "azurerm_role_assignment" "triage_worker_receiver" {
  count                = local.queue_workers
  scope                = azurerm_servicebus_namespace.main.id
  role_definition_name = "Azure Service Bus Data Receiver"
  principal_id         = azurerm_container_app.triage_worker[0].identity[0].principal_id
}

resource "azurerm_role_assignment" "triage_worker_sender" {
  count                = local.queue_workers
  scope                = azurerm_servicebus_namespace.main.id
  role_definition_name = "Azure Service Bus Data Sender"
  principal_id         = azurerm_container_app.triage_worker[0].identity[0].principal_id
}

# El escalador lee la longitud de la cola (propiedades de runtime): exige el rol Owner, acotado
# a esa cola y no a todo el namespace.
resource "azurerm_role_assignment" "triage_scaler_reads_queue" {
  count                = local.queue_workers
  scope                = azurerm_servicebus_queue.support_emails[0].id
  role_definition_name = "Azure Service Bus Data Owner"
  principal_id         = azurerm_container_app.triage_worker[0].identity[0].principal_id
}
