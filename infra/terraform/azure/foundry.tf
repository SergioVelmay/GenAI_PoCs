# Fase 2 (y 4/7): Azure AI Foundry como código. Cuenta AI Services + proyecto + despliegues.
# Desactivado por defecto (foundry_enabled = false). Sin coste fijo: los despliegues Standard /
# GlobalStandard / DataZoneStandard se pagan por token; la cuota (TPM) solo limita, no se cobra.
# Sin claves: autenticación local desactivada y roles de Entra ID para quien despliega.
# Antes del plan: comprobar disponibilidad y cuota de cada modelo en la región elegida
#   az cognitiveservices model list --location <región> -o table
#   az cognitiveservices usage list --location <región> -o table

locals {
  foundry           = var.foundry_enabled ? 1 : 0
  foundry_subdomain = "aif-${local.caf_suffix}"
  # Si Foundry vive en este módulo, sus ids sustituyen a las variables de entrada.
  openai_resource_id           = var.foundry_enabled ? azurerm_cognitive_account.foundry[0].id : var.openai_resource_id
  foundry_project_principal_id = var.foundry_enabled ? azurerm_cognitive_account_project.main[0].identity[0].principal_id : var.foundry_project_principal_id
}

resource "azurerm_cognitive_account" "foundry" {
  count                      = local.foundry
  name                       = local.foundry_subdomain
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  kind                       = "AIServices"
  sku_name                   = "S0"
  custom_subdomain_name      = local.foundry_subdomain # Exigido por Entra ID y por los proyectos.
  project_management_enabled = true                    # Foundry "nuevo": proyectos sin hub.
  local_auth_enabled         = false
  tags                       = local.tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_cognitive_account_project" "main" {
  count                = local.foundry
  name                 = var.project
  cognitive_account_id = azurerm_cognitive_account.foundry[0].id
  location             = azurerm_resource_group.main.location
  display_name         = "Headache"
  description          = "Agentes y knowledge bases de Headache (GenAI_PoCs)."
  tags                 = local.tags

  # Identidad del proyecto: la usa Foundry Agent Service para leer AI Search (Foundry IQ).
  identity {
    type = "SystemAssigned"
  }
}

# Proyecto y despliegues, en serie: Azure rechaza operaciones concurrentes sobre la misma cuenta
# (409 "Another operation is in progress"), y el proyecto también es una operación sobre ella.
resource "azurerm_cognitive_deployment" "chat" {
  count                = local.foundry
  name                 = var.foundry_models.chat.name
  cognitive_account_id = azurerm_cognitive_account.foundry[0].id

  model {
    format  = "OpenAI"
    name    = var.foundry_models.chat.model
    version = var.foundry_models.chat.version
  }

  sku {
    name     = var.foundry_deployment_sku
    capacity = var.foundry_models.chat.capacity_k_tpm
  }

  depends_on = [azurerm_cognitive_account_project.main]
}

resource "azurerm_cognitive_deployment" "cheap_chat" {
  count                = local.foundry
  name                 = var.foundry_models.cheap_chat.name
  cognitive_account_id = azurerm_cognitive_account.foundry[0].id

  model {
    format  = "OpenAI"
    name    = var.foundry_models.cheap_chat.model
    version = var.foundry_models.cheap_chat.version
  }

  sku {
    name     = var.foundry_deployment_sku
    capacity = var.foundry_models.cheap_chat.capacity_k_tpm
  }

  depends_on = [azurerm_cognitive_deployment.chat]
}

resource "azurerm_cognitive_deployment" "embedding" {
  count                = local.foundry
  name                 = var.foundry_models.embedding.name
  cognitive_account_id = azurerm_cognitive_account.foundry[0].id

  model {
    format  = "OpenAI"
    name    = var.foundry_models.embedding.model
    version = var.foundry_models.embedding.version
  }

  sku {
    name     = var.foundry_deployment_sku
    capacity = var.foundry_models.embedding.capacity_k_tpm
  }

  depends_on = [azurerm_cognitive_deployment.cheap_chat]
}

resource "azurerm_cognitive_deployment" "judge" {
  count                = local.foundry
  name                 = var.foundry_models.judge.name
  cognitive_account_id = azurerm_cognitive_account.foundry[0].id

  model {
    format  = "OpenAI"
    name    = var.foundry_models.judge.model
    version = var.foundry_models.judge.version
  }

  sku {
    name     = var.foundry_deployment_sku
    capacity = var.foundry_models.judge.capacity_k_tpm
  }

  depends_on = [azurerm_cognitive_deployment.embedding]
}

# ---------- Permisos para desarrollar en local con `az login` ----------

resource "azurerm_role_assignment" "me_foundry_openai" {
  # Inferencia (chat y embeddings) desde el gateway, el indexer y los scripts.
  count                = local.foundry
  scope                = azurerm_cognitive_account.foundry[0].id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "me_foundry_project" {
  # Plano de datos del proyecto: agentes, conversaciones y knowledge bases (fases 4 y 7).
  count                = local.foundry
  scope                = azurerm_cognitive_account_project.main[0].id
  role_definition_name = "Foundry User" # Antes "Azure AI User" (renombrado por Microsoft).
  principal_id         = data.azurerm_client_config.current.object_id
}
