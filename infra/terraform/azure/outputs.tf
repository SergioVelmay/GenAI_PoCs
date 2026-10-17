output "search_endpoint" {
  description = "Valor para AZURE_SEARCH__ENDPOINT (estable; solo responde con search_enabled = true)."
  value       = "https://${local.search_name}.search.windows.net"
}

output "servicebus_namespace" {
  description = "Valor para SERVICE_BUS__FULLY_QUALIFIED_NAMESPACE."
  value       = "${azurerm_servicebus_namespace.main.name}.servicebus.windows.net"
}

output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "storage_resource_id" {
  description = "Valor para AZURE_MANAGED__STORAGE_RESOURCE_ID (identidad gestionada)."
  value       = azurerm_storage_account.docs.id
}

output "storage_account_url" {
  description = "Valor para AZURE_MANAGED__STORAGE_ACCOUNT_URL."
  value       = azurerm_storage_account.docs.primary_blob_endpoint
}

output "mcp_apim_urls" {
  description = "Endpoint MCP de cada servidor publicado en APIM (vacío si apim_enabled = false)."
  value = {
    for name, api in azapi_resource.mcp_server :
    name => "${azurerm_api_management.mcp[0].gateway_url}/headache-${name}/mcp"
  }
}

output "content_safety_endpoint" {
  description = "Valor para CONTENT_SAFETY__ENDPOINT (null si content_safety_enabled = false)."
  value       = var.content_safety_enabled ? azurerm_cognitive_account.content_safety[0].endpoint : null
}

# ---------- Fase 2: Azure AI Foundry (null si foundry_enabled = false) ----------

output "azure_openai_endpoint" {
  description = "Valor para AZURE_OPENAI__ENDPOINT (inferencia con Entra ID)."
  value       = var.foundry_enabled ? "https://${local.foundry_subdomain}.openai.azure.com/" : null
}

output "foundry_project_endpoint" {
  description = "Valor para FOUNDRY_IQ__PROJECT_ENDPOINT (agentes y knowledge bases)."
  value       = var.foundry_enabled ? "https://${local.foundry_subdomain}.services.ai.azure.com/api/projects/${azurerm_cognitive_account_project.main[0].name}" : null
}

output "foundry_project_resource_id" {
  description = "Valor para FOUNDRY_IQ__PROJECT_RESOURCE_ID."
  value       = var.foundry_enabled ? azurerm_cognitive_account_project.main[0].id : null
}

output "foundry_deployments" {
  description = "Nombres para AZURE_OPENAI__CHAT_DEPLOYMENT, __CHEAP_CHAT_DEPLOYMENT y __EMBEDDING_DEPLOYMENT."
  value       = { for k, m in var.foundry_models : k => m.name }
}