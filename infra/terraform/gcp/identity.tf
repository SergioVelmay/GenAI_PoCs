# Identidades sin secretos.
#
#   Azure                          AWS                              GCP
#   Managed Identity               IAM Role de la tarea             Cuenta de servicio adjunta
#   az login + rol                 aws sso login + perfil           ADC de usuario + impersonation
#   OIDC → federated credential    OIDC → AssumeRoleWithWebIdentity Workload Identity Federation
#
# Ninguna cuenta de servicio tiene claves JSON: en local, la persona se autentica con su usuario
# y *suplanta* la cuenta (token de 1 h, auditado con su nombre); en la CI, GitHub presenta su
# token OIDC y STS lo cambia por uno de la cuenta de servicio.

# ---------- Cuenta de servicio de la aplicación (desarrollo) ----------
# Es la identidad con la que corren los adaptadores de GCP. Cada fase de la pasada le añade
# solo los roles que necesita (Gemini en la 2.G, Pub/Sub en la 3.G...): mínimo privilegio.

resource "google_service_account" "app" {
  account_id   = "${local.name}-app"
  display_name = "Headache ${var.environment}: aplicación"
  description  = "Identidad de los adaptadores de GCP. Sin claves: se suplanta (local) o se adjunta (nube)."

  depends_on = [google_project_service.enabled]
}

# Necesario para usar el proyecto como proyecto de cuota al llamar a las APIs.
resource "google_project_iam_member" "app_service_usage" {
  project = var.project_id
  role    = "roles/serviceusage.serviceUsageConsumer"
  member  = google_service_account.app.member
}

# Fase 2.G1: llamar a Gemini en Agent Platform (equivalente de "Cognitive Services OpenAI User"
# en Azure y de `bedrock:InvokeModel` en AWS). Incluye más permisos de Agent Platform de los que
# usa el chat; se acota con un rol propio si una fase posterior lo justifica.
resource "google_project_iam_member" "app_agent_platform" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = google_service_account.app.member
}

# Quién puede suplantarla desde local (GCP__IMPERSONATE_SERVICE_ACCOUNT).
resource "google_service_account_iam_member" "app_impersonation" {
  for_each = toset(var.developer_members)

  service_account_id = google_service_account.app.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = each.value
}

# ---------- Workload Identity Federation para GitHub Actions ----------

resource "google_service_account" "ci" {
  count = var.ci_federation_enabled ? 1 : 0

  account_id   = "${local.name}-ci"
  display_name = "Headache ${var.environment}: CI de GitHub Actions"
  description  = "Despliegues de la fase 15. Sin roles hasta entonces."

  depends_on = [google_project_service.enabled]
}

# Ojo: un pool borrado queda 30 días en papelera y su ID no se puede reutilizar mientras tanto.
resource "google_iam_workload_identity_pool" "github" {
  count = var.ci_federation_enabled ? 1 : 0

  workload_identity_pool_id = "${local.name}-github"
  display_name              = "GitHub Actions"
  description               = "Tokens OIDC de GitHub Actions de ${var.github_repository}"

  depends_on = [google_project_service.enabled]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count = var.ci_federation_enabled ? 1 : 0

  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions"
  display_name                       = "GitHub Actions"

  attribute_mapping = {
    "google.subject"                = "assertion.sub"
    "attribute.repository"          = "assertion.repository"
    "attribute.repository_id"       = "assertion.repository_id"
    "attribute.repository_owner_id" = "assertion.repository_owner_id"
    "attribute.ref"                 = "assertion.ref"
    "attribute.workflow_ref"        = "assertion.job_workflow_ref"
  }

  # Sin condición, cualquier repositorio de GitHub podría presentar un token válido a este pool.
  # IDs numéricos (inmutables) y solo las refs permitidas: una PR o una rama cualquiera no
  # consiguen credenciales aunque modifiquen el workflow.
  attribute_condition = join(" && ", [
    "assertion.repository_owner_id == '${var.github_owner_id}'",
    "assertion.repository_id == '${var.github_repository_id}'",
    "assertion.ref in [${join(", ", [for r in var.ci_allowed_refs : "'${r}'"])}]",
  ])

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "ci_federation" {
  count = var.ci_federation_enabled ? 1 : 0

  service_account_id = google_service_account.ci[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.repository_id/${var.github_repository_id}"
}
