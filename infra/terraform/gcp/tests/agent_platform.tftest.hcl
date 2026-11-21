# Fase 7.G4: `terraform validate` no evalúa los recursos con count = 0. Este test hace un `plan`
# con proveedores simulados (sin credenciales, sin red, sin coste) y todo activado, y comprueba
# las decisiones que importan: identidad del agente, región, contrato y política por tool.
#
#   terraform test   (en infra/terraform/gcp; lo ejecuta la CI)

mock_provider "google" {
  mock_data "google_project" {
    defaults = {
      number = "123456789012"
    }
  }

  # El provider valida el formato de los miembros de IAM: los valores aleatorios no pasan.
  mock_resource "google_service_account" {
    defaults = {
      email  = "genaipocs-dev-test@genaipocs-test.iam.gserviceaccount.com"
      member = "serviceAccount:genaipocs-dev-test@genaipocs-test.iam.gserviceaccount.com"
      name   = "projects/genaipocs-test/serviceAccounts/genaipocs-dev-test@genaipocs-test.iam.gserviceaccount.com"
    }
  }

  mock_resource "google_vertex_ai_reasoning_engine" {
    defaults = {
      name = "projects/123456789012/locations/europe-west1/reasoningEngines/987"
    }
  }

  mock_resource "google_network_services_agent_gateway" {
    defaults = {
      id = "projects/genaipocs-test/locations/europe-west1/agentGateways/genaipocs-dev-agw"
    }
  }
}

mock_provider "google-beta" {}

override_resource {
  target = google_agent_registry_service.mcp["catalog"]
  values = {
    registry_resource = "projects/123456789012/locations/europe-west1/mcpServers/catalog"
  }
}

override_resource {
  target = google_agent_registry_service.mcp["refunds"]
  values = {
    registry_resource = "projects/123456789012/locations/europe-west1/mcpServers/refunds"
  }
}

variables {
  project_id      = "genaipocs-test"
  billing_account = "000000-000000-000000"
  budget_email    = "alertas@genaipocs.example"
  mcp_server_urls = {
    catalog = "https://catalog.genaipocs.example/mcp"
    refunds = "https://refunds.genaipocs.example/mcp"
  }
}

run "disabled_by_default" {
  command = plan

  variables {
    mcp_server_urls = {}
  }

  assert {
    condition     = length(google_vertex_ai_reasoning_engine.memory) == 0 && length(google_vertex_ai_reasoning_engine.booking) == 0
    error_message = "Sin agent_platform_enabled no se crea nada con coste."
  }
}

run "memory_only_without_image" {
  command = plan

  variables {
    agent_platform_enabled = true
  }

  assert {
    condition     = length(google_vertex_ai_reasoning_engine.memory) == 1 && length(google_vertex_ai_reasoning_engine.booking) == 0
    error_message = "Sin imagen solo se crea el motor standalone (como AgentCore sin container_uri)."
  }

  assert {
    condition     = google_vertex_ai_reasoning_engine.memory[0].context_spec[0].memory_bank_config[0].ttl_config[0].default_ttl == "2592000s"
    error_message = "TTL de 30 días por defecto."
  }
}

run "agent_behind_the_gateway" {
  command = apply # con mocks: sin nube; hace falta para leer los valores calculados

  variables {
    agent_platform_enabled       = true
    agent_platform_runtime_image = "europe-west1-docker.pkg.dev/genaipocs-test/agents/booking:1"
    agent_gateway_enabled        = true
    agent_platform_mcp_tools     = { refunds = [] }
  }

  assert {
    condition     = google_vertex_ai_reasoning_engine.booking[0].region == "europe-west1"
    error_message = "Con Agent Gateway, el agente va a la región del gateway."
  }

  assert {
    condition     = google_vertex_ai_reasoning_engine.booking[0].spec[0].identity_type == "AGENT_IDENTITY"
    error_message = "El agente usa Agent Identity, no una cuenta de servicio."
  }

  assert {
    condition     = jsondecode(google_vertex_ai_reasoning_engine.booking[0].spec[0].class_methods)[0].name == "query"
    error_message = "class_methods sale del contrato del contenedor."
  }

  assert {
    condition     = output.agent_identity.principal == "principal://agents.global.proj-123456789012.system.id.goog/resources/aiplatform/projects/123456789012/locations/europe-west1/reasoningEngines/987"
    error_message = "Principal de un proyecto sin organización."
  }

  assert {
    condition     = google_project_iam_member.booking_agent_platform[0].member == output.agent_identity.principal
    error_message = "Los permisos van al principal del agente."
  }

  assert {
    condition = (
      length(google_iam_project_access_policy.booking_agent[0].details[0].rules) == 3 &&
      alltrue([for r in google_iam_project_access_policy.booking_agent[0].details[0].rules : r.effect == "ALLOW"])
    )
    error_message = "Una regla por servidor más la de las APIs de Google."
  }

  assert {
    condition = anytrue([
      for r in google_iam_project_access_policy.booking_agent[0].details[0].rules :
      strcontains(one(r.conditions).expression, "mcpServers/catalog") &&
      strcontains(one(r.conditions).expression, "[\"\",\"get_event\",\"list_sessions\",\"search_events\"]")
    ])
    error_message = "catalog: handshake ('') y sus tres tools del contrato."
  }

  assert {
    condition = anytrue([
      for r in google_iam_project_access_policy.booking_agent[0].details[0].rules :
      strcontains(one(r.conditions).expression, "mcpServers/refunds") &&
      strcontains(one(r.conditions).expression, "in [\"\"]")
    ])
    error_message = "refunds acotado a ninguna tool: solo el handshake."
  }

  assert {
    condition     = google_iam_projects_policy_binding.booking_agent[0].target[0].resource == "//cloudresourcemanager.googleapis.com/projects/genaipocs-test"
    error_message = "La política se enlaza al proyecto."
  }
}

run "organization_trust_domain" {
  command = apply

  variables {
    agent_platform_enabled       = true
    agent_platform_runtime_image = "europe-west4-docker.pkg.dev/genaipocs-test/agents/booking:1"
    organization_id              = "555"
  }

  assert {
    condition     = startswith(output.agent_identity.principal, "principal://agents.global.org-555.system.id.goog/") && strcontains(output.agent_identity.principal, "/locations/europe-west4/")
    error_message = "Con organización, dominio org-<id>; sin gateway, la región del proyecto."
  }

  assert {
    condition     = length(google_iam_project_access_policy.booking_agent) == 0
    error_message = "Sin Agent Gateway no hay política de acceso."
  }
}

run "unknown_tool_is_rejected" {
  command = plan

  variables {
    agent_platform_enabled       = true
    agent_platform_runtime_image = "europe-west1-docker.pkg.dev/genaipocs-test/agents/booking:1"
    agent_gateway_enabled        = true
    agent_platform_mcp_tools     = { refunds = ["delete_everything"] }
  }

  expect_failures = [google_iam_project_access_policy.booking_agent]
}
