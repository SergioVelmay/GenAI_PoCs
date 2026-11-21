# Fase 7.G4, carril A en GCP: el agente en Agent Runtime y su memoria en Sessions + Memory Bank.
#
#   Azure                          AWS                                GCP
#   Foundry Agent Service          AgentCore Runtime                  Agent Runtime (contenedor propio)
#   conversations del servicio     AgentCore Memory (eventos)         Sessions (eventos con state_delta)
#   (memoria propia)               AgentCore Memory (registros)       Memory Bank (memories, scope)
#   identidad gestionada           rol de IAM del Runtime             Agent Identity (SPIFFE, sin cuenta)
#
# Dos instancias de Agent Runtime (recurso `reasoningEngines`), como en el código de la 7.G3:
#   - `memory`: *standalone*, sin código. Solo aloja Sessions y Memory Bank; no tiene coste por
#     hora (se paga por sesión, evento y memoria desde el 01-09-2026).
#   - `booking`: el contenedor del agente (sabor `adk`). Se paga por vCPU/GiB-hora activos
#     (~0,085 $/vCPU-h) con escalado a cero. Necesita la imagen en Artifact Registry (fase 15):
#     sin `agent_platform_runtime_image` no se crea, igual que AgentCore sin `container_uri`.

locals {
  agent_platform_memory  = var.agent_platform_enabled ? 1 : 0
  agent_platform_runtime = var.agent_platform_enabled && var.agent_platform_runtime_image != "" ? 1 : 0
  # La salida por Agent Gateway es regional: el agente va a la región del gateway (us-central1 o
  # europe-west1, no europe-west4). Sin gateway, a la región del resto del proyecto.
  agent_runtime_region = var.agent_gateway_enabled ? var.agent_gateway_region : var.region

  # Principal de Agent Identity: una identidad SPIFFE por agente, emitida por Google; no hay
  # cuenta de servicio que custodiar ni suplantar. El dominio de confianza depende de si el
  # proyecto cuelga de una organización. Redesplegar el agente cambia su id y, con él, el
  # principal: los permisos van a este recurso, no a mano.
  agent_trust_domain = (
    var.organization_id != ""
    ? "agents.global.org-${var.organization_id}.system.id.goog"
    : "agents.global.proj-${data.google_project.current.number}.system.id.goog"
  )
  agent_runtime_id = (
    local.agent_platform_runtime == 1
    ? regex("reasoningEngines/([^/]+)$", google_vertex_ai_reasoning_engine.booking[0].name)[0]
    : ""
  )
  agent_principal = "principal://${local.agent_trust_domain}/resources/aiplatform/projects/${data.google_project.current.number}/locations/${local.agent_runtime_region}/reasoningEngines/${local.agent_runtime_id}"
}

# ---------- Sessions + Memory Bank (GCP_AGENT_PLATFORM__MEMORY_ENGINE) ----------

resource "google_vertex_ai_reasoning_engine" "memory" {
  count = local.agent_platform_memory

  display_name = "${local.name}-booking-memory"
  description  = "Hilos (Sessions) y preferencias (Memory Bank) del Knowledge & Booking Agent. Sin código."
  region       = var.region

  context_spec {
    memory_bank_config {
      # `remember_preference` escribe directo (`memories.create` con `clave=valor`) y sobrescribe:
      # las revisiones solo duplicarían lo que ya cuenta el diario de tools.
      disable_memory_revisions = true

      ttl_config {
        default_ttl = "${var.agent_platform_memory_ttl_days * 86400}s"
      }

      # Contraste con la escritura directa: si algún día se usa `GenerateMemories`, el modelo
      # extrae solo estos temas (y no datos personales sueltos). No se usa en la 7.G.
      customization_configs {
        memory_topics {
          managed_memory_topic {
            managed_topic_enum = "USER_PREFERENCES"
          }
        }
        memory_topics {
          custom_memory_topic {
            label       = "booking_preferences"
            description = "Preferencias de reserva del cliente de Genaipocs: idioma, ciudad, categoría de evento y presupuesto."
          }
        }
      }
    }
  }

  depends_on = [google_project_service.enabled]
}

# ---------- El agente en Agent Runtime (GCP_AGENT_PLATFORM__RUNTIME_ENGINE) ----------

resource "google_vertex_ai_reasoning_engine" "booking" {
  count = local.agent_platform_runtime

  display_name = "${local.name}-booking"
  description  = "Knowledge & Booking Agent (sabor adk; contrato POST /api/reasoning_engine)."
  region       = local.agent_runtime_region

  spec {
    agent_framework = "custom"
    # Agent Identity en lugar de una cuenta de servicio (son excluyentes): el equivalente del rol
    # de IAM de AgentCore, pero con una identidad por agente que IAM y Agent Gateway reconocen.
    identity_type = "AGENT_IDENTITY"
    # Con contenedor propio, el servicio no descubre los métodos: se declaran desde el contrato
    # generado a partir de `QueryInput` (`python -m booking_agent.managed.agent_runtime`).
    class_methods = jsonencode(jsondecode(file("${path.module}/../../../services/booking_agent/contracts/agent_runtime.json")))

    container_spec {
      image_uri = var.agent_platform_runtime_image
      port      = 8080
    }

    deployment_spec {
      min_instances         = 0 # escala a cero: la primera petición paga el arranque en frío
      max_instances         = var.agent_platform_max_instances
      container_concurrency = 3 # 2 * vCPU + 1, lo recomendado
      resource_limits = {
        cpu    = "1"
        memory = "2Gi"
      }

      env {
        name  = "GCP__PROJECT"
        value = var.project_id
      }
      env {
        name  = "GCP__REGION"
        value = var.region
      }
      env {
        name  = "GCP_AGENT_PLATFORM__MEMORY_ENGINE"
        value = google_vertex_ai_reasoning_engine.memory[0].name
      }
      env {
        name  = "BOOKING_AGENT__FLAVOR"
        value = "adk"
      }

      # Toda la salida del agente pasa por Agent Gateway (6.G3) y la gobierna la política de
      # acceso de abajo: lo que no está permitido, se deniega.
      dynamic "agent_gateway_config" {
        for_each = var.agent_gateway_enabled ? [1] : []
        content {
          agent_to_anywhere_config {
            agent_gateway = google_network_services_agent_gateway.mcp[0].id
          }
        }
      }
    }
  }

  depends_on = [google_project_service.enabled]
}

# Permisos del agente (IAM clásico, de "allow"): Gemini y su memoria. `roles/aiplatform.user` es
# el rol predefinido más pequeño que cubre `endpoints.predict`, Sessions y Memory Bank; afinarlo
# con un rol propio queda para la fase 15, cuando se conozcan los permisos reales en uso.
resource "google_project_iam_member" "booking_agent_platform" {
  count = local.agent_platform_runtime

  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = local.agent_principal
}

# Quién puede invocar al agente (`aiplatform.reasoningEngines.query` sobre ESTE motor): solo el
# backend autenticado, nunca un cliente final. El agente confía en `input.user_id` (como
# AgentCore en `runtimeUserId`), así que este permiso es la frontera de identidad.
resource "google_vertex_ai_reasoning_engine_iam_member" "booking_invokers" {
  for_each = local.agent_platform_runtime == 1 ? toset(var.agent_platform_invokers) : toset([])

  region           = local.agent_runtime_region
  reasoning_engine = google_vertex_ai_reasoning_engine.booking[0].name
  role             = "roles/aiplatform.user"
  member           = each.value
}

# ---------- Heredado de la 6.G3: política de acceso de IAM v3 en Agent Gateway ----------
# Con Agent Gateway, todo está denegado hasta que una política de ACCESO (IAM v3, distinta de las
# de allow) da `egressViaIAP` al principal del agente. La condición mira el destino tal como lo
# resuelve Agent Registry: servidor y tool. Es el equivalente de la lista de tools por target de
# AgentCore Gateway y de la `allowlist` de Apigee, pero aplicado a la identidad del agente.
#
# Requisitos que no comprueba `validate` (pendientes-cloud): la restricción de organización
# `iam.managed.disableAccessPolicyBindings` sin aplicar y el gateway en modo ENFORCED (o en
# DRY_RUN para probar sin cortar).
#
# Fuera a propósito: el binding de Agent Registry con un auth provider de Agent Identity (API key,
# 2LO o 3LO). Es para APIs de terceros; nuestros servidores MCP validan el JWT por servidor de
# `signJwt` (6.G2), que lleva el cliente de Genaipocs y no lo emite ningún auth provider.

locals {
  agent_access_policy = local.agent_platform_runtime == 1 && var.agent_gateway_enabled ? 1 : 0
  # Tools permitidas por servidor: las del contrato, o la lista acotada de la variable.
  contract_tools = {
    for server in keys(var.mcp_server_urls) : server => [
      for tool in jsondecode(file("${path.module}/../../../services/mcp_servers/contracts/${server}.json")).tools : tool.name
    ]
  }
  agent_allowed_tools = {
    for server, tools in local.contract_tools : server => lookup(var.agent_platform_mcp_tools, server, tools)
  }
  # Hosts sin registrar a los que sale el agente: Gemini (global), Sessions y Memory Bank
  # (regional) y la telemetría. Todo lo demás se deniega.
  agent_egress_hosts = distinct(concat(
    ["aiplatform.googleapis.com", "${var.region}-aiplatform.googleapis.com", "telemetry.googleapis.com"],
    var.agent_platform_egress_hosts,
  ))
}

resource "google_iam_project_access_policy" "booking_agent" {
  count = local.agent_access_policy

  location         = "global"
  access_policy_id = "${local.name}-booking-egress"
  display_name     = "Salida del booking agent por Agent Gateway"

  details {
    # Una regla por servidor. `''` deja pasar `initialize` y `tools/list`, que no llevan tool: si
    # el gateway lo resolviera de otro modo, el handshake falla cerrado (se ve en la validación).
    dynamic "rules" {
      for_each = local.agent_allowed_tools
      content {
        description = "MCP ${rules.key}: solo las tools ${join(", ", rules.value)}."
        effect      = "ALLOW"
        principals  = [local.agent_principal]
        operation {
          permissions = ["iap.googleapis.com/resources.egressViaIAP"]
        }
        conditions {
          service = "iap.googleapis.com"
          expression = join(" && ", [
            "destination.is_registered == true",
            "destination.agent_registry.mcp_server.name == '${google_agent_registry_service.mcp[rules.key].registry_resource}'",
            "destination.agent_registry.mcp_server.tool.name in ${jsonencode(concat([""], rules.value))}",
          ])
        }
      }
    }

    rules {
      description = "APIs de Google que usa el agente (sin registrar en Agent Registry)."
      effect      = "ALLOW"
      principals  = [local.agent_principal]
      operation {
        permissions = ["iap.googleapis.com/resources.egressViaIAP"]
      }
      conditions {
        service    = "iap.googleapis.com"
        expression = "destination.is_registered == false && destination.unregistered.host in ${jsonencode(local.agent_egress_hosts)}"
      }
    }
  }

  lifecycle {
    precondition {
      condition = alltrue([
        for server, tools in var.agent_platform_mcp_tools :
        contains(keys(local.contract_tools), server) && length(setsubtract(tools, local.contract_tools[server])) == 0
      ])
      error_message = "agent_platform_mcp_tools: servidor sin URL o tool que no está en su contrato."
    }
  }
}

resource "google_iam_projects_policy_binding" "booking_agent" {
  count = local.agent_access_policy

  location          = "global"
  policy_binding_id = "${local.name}-booking-egress"
  display_name      = "Salida del booking agent"
  policy_kind       = "ACCESS"
  policy            = google_iam_project_access_policy.booking_agent[0].name

  target {
    resource = "//cloudresourcemanager.googleapis.com/projects/${var.project_id}"
  }
}
