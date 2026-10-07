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
