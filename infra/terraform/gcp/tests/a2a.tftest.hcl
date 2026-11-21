# Fase 9.G4: plan simulado (sin credenciales, sin red, sin coste) de los especialistas A2A en
# Cloud Run: desactivados por defecto; con imagen, tres servicios con custom audience, escala a
# cero, identidad propia, ID tokens de Google y solo la cuenta del orquestador como invoker.
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
      email  = "genaipocs-dev-app@genaipocs-test.iam.gserviceaccount.com"
      member = "serviceAccount:genaipocs-dev-app@genaipocs-test.iam.gserviceaccount.com"
      name   = "projects/genaipocs-test/serviceAccounts/genaipocs-dev-app@genaipocs-test.iam.gserviceaccount.com"
    }
  }
}

mock_provider "google-beta" {}

variables {
  project_id      = "genaipocs-test"
  billing_account = "000000-000000-000000"
  budget_email    = "alertas@genaipocs.example"
}

run "disabled_by_default" {
  command = plan

  assert {
    condition     = length(google_cloud_run_v2_service.a2a) == 0 && length(google_service_account.a2a) == 0
    error_message = "Sin a2a_enabled no se crea ningún servicio ni identidad."
  }
}

run "enabled_without_image_creates_nothing" {
  command = plan

  variables {
    a2a_enabled = true
  }

  assert {
    condition     = length(google_cloud_run_v2_service.a2a) == 0
    error_message = "Sin imagen en Artifact Registry no hay nada que desplegar."
  }
}

run "specialists_on_cloud_run" {
  command = apply # con mocks: sin nube; hace falta para leer los valores calculados

  variables {
    a2a_enabled = true
    a2a_image   = "europe-west4-docker.pkg.dev/genaipocs-test/agents/orchestrator:test"
  }

  assert {
    condition     = toset(keys(google_cloud_run_v2_service.a2a)) == toset(["refunds", "creative", "knowledge"])
    error_message = "Un servicio por especialista."
  }

  assert {
    condition     = alltrue([for k, s in google_cloud_run_v2_service.a2a : length(s.custom_audiences) == 1 && contains(s.custom_audiences, "api://genaipocs-agents/${k}")])
    error_message = "Cada servicio acepta el audience que pide el orquestador para ese especialista."
  }

  assert {
    condition     = alltrue([for s in google_cloud_run_v2_service.a2a : !s.invoker_iam_disabled && s.template[0].scaling[0].min_instance_count == 0])
    error_message = "IAM obligatorio y escala a cero."
  }

  assert {
    condition     = alltrue([for k, s in google_cloud_run_v2_service.a2a : contains(s.template[0].containers[0].args, k) && contains([for e in s.template[0].containers[0].env : "${e.name}=${e.value}"], "A2A__IDENTITY=gcp")])
    error_message = "Cada contenedor arranca su especialista con ID tokens de Google."
  }

  assert {
    condition     = alltrue([for i in google_cloud_run_v2_service_iam_member.a2a_invoker : i.role == "roles/run.invoker" && i.member == "serviceAccount:genaipocs-dev-app@genaipocs-test.iam.gserviceaccount.com"])
    error_message = "Solo la cuenta del orquestador invoca a los especialistas."
  }

  assert {
    condition     = output.a2a_env.A2A__IDENTITY == "gcp" && jsondecode(output.a2a_env.A2A__URLS).refunds == "https://genaipocs-dev-a2a-refunds-123456789012.europe-west4.run.app"
    error_message = "El output da al orquestador las URL deterministas de Cloud Run."
  }
}
