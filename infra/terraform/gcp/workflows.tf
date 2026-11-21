# Fase 9.G5: espera humana con SLA en Workflows (`infra/workflows/case_review.yaml`).
#
#   Azure                               AWS                                  GCP
#   Durable Functions                   Step Functions                       Workflows
#   WaitForExternalEvent + timer        .waitForTaskToken + TimeoutSeconds   await_callback + timeout
#   Event Grid / Service Bus trigger    EventBridge rule                     Eventarc (Pub/Sub)
#
# Cada resultado publicado en `case.outcomes` arranca una ejecución (Eventarc crea su propia
# suscripción al tema); si el caso no está pendiente de aprobación, termina en el primer paso.
# Si lo está, crea un callback, avisa en `case.review_requests` y espera la decisión hasta
# `review_sla_seconds`; publica la decisión o `sla_expired` en `case.reviews`, que aplica el rol
# `review` del worker. Workflows no decide nada: el dominio valida la decisión y decide qué
# significa que venza el plazo (caducar la aprobación y escalar el caso).
#
# Coste: Workflows se paga por paso (5.000 internos gratis al mes; luego ~0,01 $ por 1.000) y
# una ejecución que espera no consume pasos: ~0 $ en reposo. Eventarc con fuente Pub/Sub, por
# mensaje. Desactivado por defecto (`case_review_workflow_enabled`).

locals {
  case_review = var.case_review_workflow_enabled ? 1 : 0
}

# Avisos para operaciones: borrador pendiente + URL del callback. Los lee la consola (`.ops`).
resource "google_pubsub_topic" "review_requests" {
  count = local.case_review

  name   = "case.review_requests"
  labels = local.labels

  depends_on = [google_project_service.enabled]
}

resource "google_pubsub_subscription" "review_requests_ops" {
  count = local.case_review

  name   = "case.review_requests.ops"
  topic  = google_pubsub_topic.review_requests[0].id
  labels = local.labels

  message_retention_duration = "604800s"

  expiration_policy {
    ttl = ""
  }
}

# Identidad del workflow y del disparador: publicar en dos temas e invocar el workflow.
resource "google_service_account" "case_review" {
  count = local.case_review

  account_id   = "${local.name}-wf-review"
  display_name = "Genaipocs ${var.environment}: espera de revisión (Workflows)"
  description  = "Ejecuta case_review y recibe los eventos de case.outcomes por Eventarc (9.G5)."

  depends_on = [google_project_service.enabled]
}

resource "google_pubsub_topic_iam_member" "case_review_publish_reviews" {
  count = local.case_review

  topic  = google_pubsub_topic.cases["case.reviews"].id
  role   = "roles/pubsub.publisher"
  member = google_service_account.case_review[0].member
}

resource "google_pubsub_topic_iam_member" "case_review_publish_requests" {
  count = local.case_review

  topic  = google_pubsub_topic.review_requests[0].id
  role   = "roles/pubsub.publisher"
  member = google_service_account.case_review[0].member
}

resource "google_project_iam_member" "case_review_logs" {
  count = local.case_review

  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = google_service_account.case_review[0].member
}

resource "google_workflows_workflow" "case_review" {
  count = local.case_review

  name            = "${local.name}-case-review"
  region          = var.region
  description     = "Espera humana con SLA de los casos pendientes de aprobación (9.G5)."
  service_account = google_service_account.case_review[0].id
  labels          = local.labels
  # Sin datos personales en los logs de llamadas: el borrador no se registra.
  call_log_level      = "LOG_ERRORS_ONLY"
  deletion_protection = false

  source_contents = file("${path.module}/../../workflows/case_review.yaml")

  user_env_vars = {
    REVIEWS_TOPIC      = google_pubsub_topic.cases["case.reviews"].name
    REQUESTS_TOPIC     = google_pubsub_topic.review_requests[0].name
    REVIEW_SLA_SECONDS = tostring(var.review_sla_seconds)
  }
}

# El disparador invoca el workflow con su cuenta: necesita `workflows.invoker`.
resource "google_project_iam_member" "case_review_trigger_invoker" {
  count = local.case_review

  project = var.project_id
  role    = "roles/workflows.invoker"
  member  = google_service_account.case_review[0].member
}

resource "google_eventarc_trigger" "case_outcomes" {
  count = local.case_review

  name            = "${local.name}-case-outcomes"
  location        = var.region
  service_account = google_service_account.case_review[0].email
  labels          = local.labels

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.pubsub.topic.v1.messagePublished"
  }

  transport {
    pubsub {
      topic = google_pubsub_topic.cases["case.outcomes"].id
    }
  }

  destination {
    workflow = google_workflows_workflow.case_review[0].id
  }

  depends_on = [google_project_iam_member.case_review_trigger_invoker]
}

# Quién decide: `workflows.callbacks.send` viene en roles/workflows.invoker, a nivel de proyecto
# (no hay IAM por workflow en el provider). En la fase 15, un rol propio solo con ese permiso.
resource "google_project_iam_member" "case_reviewers" {
  for_each = var.case_review_workflow_enabled ? toset(var.case_reviewers) : toset([])

  project = var.project_id
  role    = "roles/workflows.invoker"
  member  = each.value
}
