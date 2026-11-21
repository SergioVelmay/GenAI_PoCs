# Fase 9.G5: plan simulado (sin credenciales, sin red, sin coste) de la espera humana con SLA:
# desactivada por defecto; activada, el workflow con el YAML del repo, su SLA y sus temas, el
# disparador de Eventarc sobre case.outcomes y los revisores como únicos que mandan el callback.
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
      email  = "genaipocs-dev-wf-review@genaipocs-test.iam.gserviceaccount.com"
      member = "serviceAccount:genaipocs-dev-wf-review@genaipocs-test.iam.gserviceaccount.com"
      name   = "projects/genaipocs-test/serviceAccounts/genaipocs-dev-wf-review@genaipocs-test.iam.gserviceaccount.com"
      id     = "projects/genaipocs-test/serviceAccounts/genaipocs-dev-wf-review@genaipocs-test.iam.gserviceaccount.com"
    }
  }

  mock_resource "google_pubsub_topic" {
    defaults = {
      id = "projects/genaipocs-test/topics/mock"
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
    condition     = length(google_workflows_workflow.case_review) == 0 && length(google_eventarc_trigger.case_outcomes) == 0 && length(google_pubsub_topic.review_requests) == 0
    error_message = "Sin case_review_workflow_enabled no se crea nada."
  }
}

run "review_with_sla" {
  command = apply # con mocks: sin nube; hace falta para leer los valores calculados

  variables {
    case_review_workflow_enabled = true
    review_sla_seconds           = 7200
    case_reviewers               = ["group:operaciones@genaipocs.example"]
  }

  assert {
    condition     = google_workflows_workflow.case_review[0].source_contents == file("${path.module}/../../workflows/case_review.yaml")
    error_message = "El workflow desplegado es el YAML versionado en infra/workflows."
  }

  assert {
    condition     = google_workflows_workflow.case_review[0].user_env_vars["REVIEWS_TOPIC"] == "case.reviews" && google_workflows_workflow.case_review[0].user_env_vars["REQUESTS_TOPIC"] == "case.review_requests" && google_workflows_workflow.case_review[0].user_env_vars["REVIEW_SLA_SECONDS"] == "7200"
    error_message = "Temas y SLA llegan al workflow por user_env_vars."
  }

  assert {
    condition     = google_workflows_workflow.case_review[0].call_log_level == "LOG_ERRORS_ONLY"
    error_message = "Los logs de llamadas no guardan el borrador (datos del cliente)."
  }

  assert {
    condition     = [for m in google_eventarc_trigger.case_outcomes[0].matching_criteria : m.value] == ["google.cloud.pubsub.topic.v1.messagePublished"]
    error_message = "El disparador escucha los mensajes publicados en case.outcomes."
  }

  assert {
    condition     = [for m in google_project_iam_member.case_reviewers : m.role] == ["roles/workflows.invoker"]
    error_message = "Solo los revisores declarados pueden mandar la decisión al callback."
  }
}
