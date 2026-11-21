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
