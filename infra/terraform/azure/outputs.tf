output "search_endpoint" {
  description = "Valor para AZURE_SEARCH__ENDPOINT."
  value       = "https://${azurerm_search_service.main.name}.search.windows.net"
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
