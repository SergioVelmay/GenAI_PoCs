# Fase 9.G3: plan simulado (sin credenciales, sin red, sin coste) de los temas del orquestador:
# seis temas con su DLQ (case.reviews desde la 9.G5), exactly-once y ack deadline de 600 s en
# las suscripciones de los workers.
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
}

mock_provider "google-beta" {}

variables {
  project_id      = "genaipocs-test"
  billing_account = "000000-000000-000000"
  budget_email    = "alertas@genaipocs.example"
}

run "case_topics" {
  command = plan

  assert {
    condition = toset(keys(google_pubsub_subscription.cases_worker)) == toset([
      "support.emails", "case.triaged.refunds", "case.triaged.creative",
      "case.triaged.knowledge", "case.outcomes", "case.reviews",
    ])
    error_message = "Un tema por paso del flujo de casos, con los nombres lógicos de CaseTopics."
  }

  assert {
    condition     = alltrue([for s in google_pubsub_subscription.cases_worker : s.enable_exactly_once_delivery && s.ack_deadline_seconds == 600])
    error_message = "Los workers del orquestador usan exactly-once y el plazo máximo (turnos largos del LLM)."
  }

  assert {
    condition     = alltrue([for k, s in google_pubsub_subscription.cases_worker : s.name == "${k}.worker" && s.dead_letter_policy[0].max_delivery_attempts == 5])
    error_message = "Cada suscripción <tema>.worker tiene dead-letter policy con 5 entregas."
  }

  assert {
    condition     = alltrue([for k, s in google_pubsub_subscription.cases_dlq : s.name == "${k}.dlq" && s.expiration_policy[0].ttl == ""])
    error_message = "Cada DLQ conserva sus mensajes en su propia suscripción, que no caduca."
  }

  assert {
    condition     = !coalesce(google_pubsub_subscription.knowledge_worker.enable_exactly_once_delivery, false)
    error_message = "La indexación no lo necesita: el upsert es idempotente y exactly-once añade latencia."
  }
}
