# Fase 6.G2: identidades del MCP Gateway en GCP. Sin coste fijo.
#
#   Azure                       AWS                               GCP
#   Entra ID (usuarios)         Cognito user pool                 Identity Platform
#   On-Behalf-Of                AgentCore Identity (RFC 8693)     signJwt de la cuenta del gateway
#
# GCP no tiene OBO hacia APIs propias: Google STS solo cambia tokens externos por access tokens
# de Google. El gateway firma un JWT por servidor con la clave gestionada por Google de su cuenta
# de servicio (sin claves que custodiar) y los servidores lo validan con su JWKS público.

# ---------- Cuenta de servicio del gateway (emisor de los tokens por servidor) ----------

resource "google_service_account" "mcp_gateway" {
  account_id   = "${local.name}-mcp-gw"
  display_name = "Genaipocs ${var.environment}: MCP Gateway"
  description  = "Firma con signJwt el token de cada servidor MCP (MCP__SIGNER_SERVICE_ACCOUNT)."

  depends_on = [google_project_service.enabled]
}

# Mínimo privilegio: solo signJwt. `roles/iam.serviceAccountTokenCreator` daría además
# getAccessToken, signBlob y getOpenIdToken, es decir, actuar como la cuenta en todo Google Cloud.
# Ojo: un rol propio borrado tarda 7 días en eliminarse y su ID no se reutiliza en 37.
resource "google_project_iam_custom_role" "mcp_jwt_signer" {
  role_id     = "mcpJwtSigner"
  title       = "Genaipocs: firmar JWT del MCP Gateway"
  description = "Solo iam.serviceAccounts.signJwt (fase 6.G2)."
  permissions = ["iam.serviceAccounts.signJwt"]
}

# La cuenta firma como sí misma (en Cloud Run, con su identidad adjunta; fase 15).
resource "google_service_account_iam_member" "mcp_gateway_self_sign" {
  service_account_id = google_service_account.mcp_gateway.name
  role               = google_project_iam_custom_role.mcp_jwt_signer.id
  member             = google_service_account.mcp_gateway.member
}

# En local, quien arranca el gateway la suplanta (GCP__IMPERSONATE_SERVICE_ACCOUNT con su correo)
# y prueba con sus permisos exactos: token de 1 h, auditado con el nombre de la persona.
resource "google_service_account_iam_member" "mcp_gateway_impersonation" {
  for_each = toset(var.developer_members)

  service_account_id = google_service_account.mcp_gateway.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = each.value
}

# ---------- Identity Platform: IdP de los clientes de Genaipocs (CIAM) ----------
# Equivalente de Entra External ID y de un user pool de Cognito. Emite ID tokens con
# iss = https://securetoken.google.com/<proyecto> y aud = <proyecto>, sin scopes; el
# `customer_id` lo pone el backend como custom claim (Admin SDK) al dar de alta al cliente.
#
# Cuidado: activar Identity Platform en un proyecto NO se deshace (no vuelve a Firebase Auth) y
# `terraform destroy` no lo apaga. Por eso va detrás de su variable. Nivel gratuito: 50 000 MAU
# con correo y contraseña.

resource "google_identity_platform_config" "customers" {
  count = var.identity_platform_enabled ? 1 : 0

  authorized_domains         = var.identity_platform_authorized_domains
  autodelete_anonymous_users = true

  sign_in {
    allow_duplicate_emails = false

    email {
      enabled           = true
      password_required = true
    }

    anonymous {
      enabled = false
    }
  }

  depends_on = [google_project_service.enabled]
}

# ---------- Fase 6.G3, carril A: Agent Gateway + Agent Registry ----------
# Equivalente de AgentCore Gateway: un punto de control gestionado entre los agentes y las tools.
# Diferencias que importan:
#   - Gobierna el tráfico de agentes que corren en Agent Runtime o GKE (Cloud Run, Limited
#     Preview); no es un endpoint MCP agregado al que se conecta cualquier cliente.
#   - Solo deja pasar a destinos REGISTRADOS en Agent Registry, y todo está denegado hasta que
#     una política de acceso de IAM (v3) da permiso al agente (principal de Agent Identity) por
#     servidor, método o tool. Esa política y el binding con un auth provider de Agent Identity
#     necesitan el agente desplegado: llegan en la 7.G2.
#   - Credenciales hacia la tool: auth providers de Agent Identity (API key, 2LO, 3LO). No hay
#     token exchange: nuestros servidores siguen recibiendo el JWT de `signJwt` (6.G2).
# Coste: no publicado (Limited Preview); se valida con agent_gateway_enabled solo durante la sesión.

locals {
  agent_gateway = var.agent_gateway_enabled ? 1 : 0
  # Toolspec de Agent Registry desde los contratos versionados de MCP: una sola fuente de verdad.
  mcp_toolspecs = {
    for server in keys(var.mcp_server_urls) : server => jsonencode({
      tools = [
        for tool in jsondecode(file("${path.module}/../../../services/mcp_servers/contracts/${server}.json")).tools : {
          name          = tool.name
          description   = tool.description
          inputSchema   = tool.inputSchema
          isReadOnly    = try(tool.annotations.readOnlyHint, false)
          isDestructive = try(tool.annotations.destructiveHint, true)
          isIdempotent  = try(tool.annotations.idempotentHint, false)
          isOpenWorld   = try(tool.annotations.openWorldHint, true)
        }
      ]
    })
  }
}

resource "google_agent_registry_service" "mcp" {
  for_each = var.agent_gateway_enabled ? var.mcp_server_urls : {}

  location     = var.agent_gateway_region
  service_id   = "genaipocs-${each.key}"
  display_name = "Genaipocs ${each.key}"
  description  = "Servidor MCP ${each.key} de Genaipocs (FastMCP en Cloud Run, fase 15)."

  interfaces {
    protocol_binding = "JSONRPC"
    url              = each.value
  }

  # Registro manual: Agent Registry no inspecciona el servidor (salvo Cloud Run con
  # --functional-type=mcp-server), así que las tools van declaradas.
  mcp_server_spec {
    type    = "TOOL_SPEC"
    content = local.mcp_toolspecs[each.key]
  }

  depends_on = [google_project_service.enabled]
}

resource "google_network_services_agent_gateway" "mcp" {
  count = local.agent_gateway

  name        = "${local.name}-agw"
  location    = var.agent_gateway_region
  description = "Salida gobernada de los agentes de Genaipocs hacia sus servidores MCP."
  # Sin `protocols`: el provider 8.11 lo marca obsoleto (el codelab aún lo usa).
  registries = ["//agentregistry.googleapis.com/projects/${var.project_id}/locations/${var.agent_gateway_region}"]

  google_managed {
    governed_access_path = "AGENT_TO_ANYWHERE"
  }

  depends_on = [google_agent_registry_service.mcp]
}

# ---------- Fase 6.G3, carril A: Apigee (equivalente de API Management) ----------
# Un proxy por servidor generado desde su contrato (`python -m genaipocs_mcp.apigee`): ParsePayload
# del JSON-RPC, VerifyJWT del token del servidor, lista de tools permitidas y SpikeArrest por
# cliente. Organización de EVALUACIÓN: gratis 60 días, sin SLA y no apta para producción; de pago
# por uso, ~365 $/mes por entorno Base más las llamadas. Sin VPC peering (PSC): exponerla pide un
# balanceador con un NEG de PSC (con coste por regla de reenvío).
# Crear la organización y la instancia tarda del orden de una hora.

locals {
  apigee = var.apigee_enabled ? 1 : 0
}

resource "google_apigee_organization" "mcp" {
  count = local.apigee

  project_id          = var.project_id
  analytics_region    = var.region
  runtime_type        = "CLOUD"
  billing_type        = "EVALUATION"
  disable_vpc_peering = true
  description         = "Genaipocs: proxies de los servidores MCP (fase 6.G3)."

  depends_on = [google_project_service.enabled]
}

resource "google_apigee_instance" "mcp" {
  count = local.apigee

  name     = "${local.name}-mcp"
  location = var.region
  org_id   = google_apigee_organization.mcp[0].id
}

resource "google_apigee_environment" "mcp" {
  count = local.apigee

  name         = "mcp"
  display_name = "MCP"
  description  = "Proxies de los servidores MCP."
  org_id       = google_apigee_organization.mcp[0].id
}

resource "google_apigee_instance_attachment" "mcp" {
  count = local.apigee

  instance_id = google_apigee_instance.mcp[0].id
  environment = google_apigee_environment.mcp[0].name
}

resource "google_apigee_envgroup" "mcp" {
  count = local.apigee

  name      = "mcp"
  hostnames = var.apigee_hostnames
  org_id    = google_apigee_organization.mcp[0].id
}

resource "google_apigee_envgroup_attachment" "mcp" {
  count = local.apigee

  envgroup_id = google_apigee_envgroup.mcp[0].id
  environment = google_apigee_environment.mcp[0].name
}

# El bundle no lleva URLs: apunta a un TargetServer con el nombre del proxy.
resource "google_apigee_target_server" "mcp" {
  for_each = var.apigee_enabled ? var.mcp_server_urls : {}

  name     = "genaipocs-${each.key}"
  env_id   = google_apigee_environment.mcp[0].id
  host     = regex("^https://([^/:]+)", each.value)[0]
  port     = 443
  protocol = "HTTP"

  s_sl_info {
    enabled = true
  }
}

resource "google_apigee_api" "mcp" {
  for_each = var.apigee_enabled ? var.mcp_server_urls : {}

  name           = "genaipocs-${each.key}"
  org_id         = google_apigee_organization.mcp[0].name
  config_bundle  = "${var.apigee_bundle_dir}/genaipocs-${each.key}.zip"
  detect_md5hash = filemd5("${var.apigee_bundle_dir}/genaipocs-${each.key}.zip")
}

resource "google_apigee_api_deployment" "mcp" {
  for_each = google_apigee_api.mcp

  org_id      = google_apigee_organization.mcp[0].name
  environment = google_apigee_environment.mcp[0].name
  proxy_id    = each.value.name
  revision    = each.value.latest_revision_id

  depends_on = [google_apigee_instance_attachment.mcp, google_apigee_target_server.mcp]
}
