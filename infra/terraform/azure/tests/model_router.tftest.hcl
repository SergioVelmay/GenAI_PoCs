# Pasada 2.P: plan simulado (sin credenciales, sin red, sin coste) del router gestionado de
# Foundry. `terraform validate` no evalúa los recursos con count = 0; este plan sí.
#
#   terraform test   (en infra/terraform/azure; lo ejecuta la CI)

mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id       = "00000000-0000-0000-0000-000000000001"
      object_id       = "00000000-0000-0000-0000-000000000002"
      subscription_id = "00000000-0000-0000-0000-000000000003"
    }
  }

  # azapi exige un id de ARM válido como `parent_id`.
  mock_resource "azurerm_cognitive_account" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/rg-genaipocs-test/providers/Microsoft.CognitiveServices/accounts/aif-test"
    }
  }
}

mock_provider "azapi" {}

variables {
  subscription_id = "00000000-0000-0000-0000-000000000003"
  budget_email    = "alertas@genaipocs.example"
  foundry_enabled = true
}

run "router_disabled_by_default" {
  command = plan

  assert {
    condition     = length(azapi_resource.model_router) == 0 && output.model_router_deployment == null
    error_message = "Sin model_router_enabled no se despliega el router."
  }
}

run "router_routes_across_the_same_models_as_the_own_router" {
  command = plan

  variables {
    model_router_enabled   = true
    foundry_deployment_sku = "DataZoneStandard"
  }

  assert {
    condition     = azapi_resource.model_router[0].body.properties.model.name == "model-router" && azapi_resource.model_router[0].body.properties.model.version == "2025-11-18"
    error_message = "El despliegue debe ser el model-router GA."
  }

  assert {
    condition     = azapi_resource.model_router[0].body.properties.routing.mode == "balanced" && [for m in azapi_resource.model_router[0].body.properties.routing.models : m.name] == ["gpt-4.1-nano", "gpt-4.1-mini"]
    error_message = "Modo balanced y el subconjunto de los modelos del routing propio."
  }

  assert {
    condition     = azapi_resource.model_router[0].body.sku.name == "DataZoneStandard" && output.model_router_deployment == "model-router"
    error_message = "El router usa el SKU de Foundry (Data Zone: datos en la UE) y su nombre sale como output."
  }
}

run "router_mode_is_validated" {
  command = plan

  variables {
    model_router_enabled = true
    model_router = {
      name           = "model-router"
      version        = "2025-11-18"
      mode           = "cheapest"
      capacity_k_tpm = 10
      models         = [{ name = "gpt-4.1-nano", version = "2025-04-14" }]
    }
  }

  expect_failures = [var.model_router]
}
