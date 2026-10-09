# Fase 6.4, carril A en Azure: API Management como MCP Gateway (servidores MCP "passthrough").
# Desactivado por defecto (apim_enabled = false). Coste: Developer ~50 $/mes y sin SLA; el tier
# Consumption no admite MCP. azurerm aún no modela MCP: azapi con la API 2025-09-01-preview.

locals {
  apim = var.apim_enabled ? 1 : 0
  mcp_scopes = {
    catalog   = "catalog:read"
    ticketing = "orders:read"
    refunds   = "refunds:write"
  }
  # APIM valida el token del servidor (su audience y scope) y lo reenvía sin cambios: es la
  # puerta del propio recurso, no un tercero. El servidor lo valida otra vez (defensa en profundidad).
  mcp_policy = {
    for name, scope in local.mcp_scopes : name => <<-XML
      <policies>
        <inbound>
          <base />
          <validate-jwt header-name="Authorization" require-scheme="Bearer" failed-validation-httpcode="401">
            <openid-config url="${var.mcp_oidc_config_url}" />
            <audiences>
              <audience>${replace(var.mcp_audience_template, "{server}", name)}</audience>
            </audiences>
            <required-claims>
              <claim name="scp" match="any" separator=" ">
                <value>${scope}</value>
              </claim>
            </required-claims>
          </validate-jwt>
          <rate-limit-by-key calls="60" renewal-period="60"
            counter-key="@(context.Request.Headers.GetValueOrDefault(&quot;Authorization&quot;,&quot;&quot;).AsJwt()?.Subject)" />
        </inbound>
        <backend>
          <forward-request />
        </backend>
        <outbound>
          <base />
        </outbound>
        <on-error>
          <base />
        </on-error>
      </policies>
    XML
  }
}

resource "azurerm_api_management" "mcp" {
  count               = local.apim
  name                = "apim-${local.name}-${random_string.suffix.result}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  publisher_name      = "Headache"
  publisher_email     = var.budget_email
  sku_name            = var.apim_sku
  tags                = local.tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azapi_resource" "mcp_server" {
  for_each  = var.apim_enabled ? var.mcp_server_base_urls : {}
  type      = "Microsoft.ApiManagement/service/apis@2025-09-01-preview"
  name      = "headache-${each.key}"
  parent_id = azurerm_api_management.mcp[0].id

  body = {
    properties = {
      type        = "mcp"
      displayName = "Headache ${each.key}"
      description = "Servidor MCP ${each.key} (FastMCP en Container Apps, fase 14)"
      path        = "headache-${each.key}"
      protocols   = ["https"]
      serviceUrl  = each.value
      # Sin claves de suscripción: la autorización es OAuth (validate-jwt).
      subscriptionRequired = false
      mcpProperties = {
        transportType = "streamable"
        endpoints     = [{ name = "message", uriTemplate = "/mcp" }]
      }
    }
  }
}

resource "azapi_resource" "mcp_policy" {
  for_each  = azapi_resource.mcp_server
  type      = "Microsoft.ApiManagement/service/apis/policies@2025-09-01-preview"
  name      = "policy"
  parent_id = each.value.id

  body = {
    properties = {
      format = "rawxml"
      value  = local.mcp_policy[each.key]
    }
  }
}
