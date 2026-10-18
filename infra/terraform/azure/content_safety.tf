# Fase 8.4, guardrails gestionados en Azure: Azure AI Content Safety (Prompt Shields + análisis
# de texto). Desactivado por defecto (content_safety_enabled = false). Tier F0 gratuito con
# cuota mensual limitada; S0 se paga por cada 1000 registros de texto.
# Sin claves: autenticación local desactivada y rol "Cognitive Services User" para quien despliega.

locals {
  content_safety = var.content_safety_enabled ? 1 : 0
}

resource "azurerm_cognitive_account" "content_safety" {
  count                 = local.content_safety
  name                  = "cs-${local.caf_suffix}"
  resource_group_name   = azurerm_resource_group.main.name
  location              = azurerm_resource_group.main.location
  kind                  = "ContentSafety"
  sku_name              = var.content_safety_sku
  custom_subdomain_name = "cs-${local.caf_suffix}" # Exigido por Entra ID.
  local_auth_enabled    = false
  tags                  = local.tags
}

resource "azurerm_role_assignment" "me_content_safety" {
  count                = local.content_safety
  scope                = azurerm_cognitive_account.content_safety[0].id
  role_definition_name = "Cognitive Services User"
  principal_id         = data.azurerm_client_config.current.object_id
}
