# Fase 3, carril A en Azure: Blob Storage + vectorización integrada de AI Search.
# El índice, el skillset y el indexer no los gestiona azurerm: scripts/managed_ingestion.py.

resource "azurerm_storage_account" "docs" {
  name                            = "st${var.project}${random_string.suffix.result}"
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  # Claves de cuenta solo si el indexer no puede usar identidad gestionada (tier Free).
  shared_access_key_enabled = !local.search_managed_identity
  tags                      = local.tags

  blob_properties {
    # Soft delete: el indexer detecta los blobs borrados y elimina sus chunks del índice.
    delete_retention_policy {
      days = 7
    }
  }
}

resource "azurerm_storage_container" "docs" {
  name                  = "knowledge-docs"
  storage_account_id    = azurerm_storage_account.docs.id
  container_access_type = "private"
}

# Subida del corpus desde local con `az login`.
resource "azurerm_role_assignment" "me_blob" {
  scope                = azurerm_storage_account.docs.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

# ---------- Permisos de la identidad del servicio de AI Search (tier Basic o superior) ----------

resource "azurerm_role_assignment" "search_reads_blob" {
  count                = var.search_enabled && local.search_managed_identity ? 1 : 0
  scope                = azurerm_storage_account.docs.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_search_service.main[0].identity[0].principal_id
}

resource "azurerm_role_assignment" "search_calls_openai" {
  count                = var.search_enabled && local.search_managed_identity && (var.foundry_enabled || var.openai_resource_id != null) ? 1 : 0
  scope                = local.openai_resource_id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_search_service.main[0].identity[0].principal_id
}

# ---------- Fase 4, carril A: Foundry IQ + Agent Service ----------
# El agente invoca la knowledge base por MCP con la identidad gestionada del proyecto.

resource "azurerm_role_assignment" "foundry_project_reads_search" {
  count                = var.search_enabled && (var.foundry_enabled || var.foundry_project_principal_id != null) ? 1 : 0
  scope                = azurerm_search_service.main[0].id
  role_definition_name = "Search Index Data Reader"
  principal_id         = local.foundry_project_principal_id
}
