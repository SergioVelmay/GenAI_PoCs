# Fase 3.G1: cola de indexación en Pub/Sub (equivale a knowledge.documents de RabbitMQ, a la cola
# SQS de aws/main.tf y a la de Service Bus de azure/main.tf).
# Coste en reposo ~0 $: Pub/Sub se paga por volumen (primeros 10 GiB al mes gratis); temas y
# suscripciones vacíos no cuestan.
#
#   Azure Service Bus            AWS SQS                       GCP Pub/Sub
#   cola                         cola                          tema + suscripción (separados)
#   MaxDeliveryCount + $DLQ      redrive policy + cola DLQ     dead-letter policy + tema DLQ
#   abandon                      visibility timeout            nack + retry policy (backoff)
#
# Los nombres son los del adaptador (`adapters/pubsub.py`): Pub/Sub admite puntos, así que el tema
# se llama como el topic lógico y la suscripción de los workers es `<tema>.worker`. El proyecto ya
# aísla el entorno: no hace falta el prefijo `genaipocs-dev-`.

locals {
  knowledge_topic = "knowledge.documents"
  # Agente de servicio de Pub/Sub: es quien mueve los mensajes al dead-letter topic.
  pubsub_service_agent = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_topic" "knowledge" {
  name   = local.knowledge_topic
  labels = local.labels

  depends_on = [google_project_service.enabled]
}

resource "google_pubsub_topic" "knowledge_dlq" {
  name   = "${local.knowledge_topic}.dlq"
  labels = local.labels

  depends_on = [google_project_service.enabled]
}

# Un tema sin suscripciones descarta lo que recibe: la DLQ necesita la suya para conservar los
# mensajes muertos (en SQS y Service Bus, la DLQ ya es una cola).
resource "google_pubsub_subscription" "knowledge_dlq" {
  name   = "${local.knowledge_topic}.dlq"
  topic  = google_pubsub_topic.knowledge_dlq.id
  labels = local.labels

  message_retention_duration = "604800s" # 7 días, el máximo: tiempo para investigar y reprocesar.

  # Por defecto, una suscripción sin actividad se borra a los 31 días: "" = nunca caduca.
  expiration_policy {
    ttl = ""
  }
}

resource "google_pubsub_subscription" "knowledge_worker" {
  name   = "${local.knowledge_topic}.worker"
  topic  = google_pubsub_topic.knowledge.id
  labels = local.labels

  # Tiempo para procesar un mensaje antes de que se vuelva a entregar (como la visibility timeout).
  ack_deadline_seconds = 60

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.knowledge_dlq.id
    max_delivery_attempts = var.max_deliveries
  }

  # Backoff exponencial entre entregas tras un nack: el adaptador no espera, lo fija la nube.
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "300s"
  }

  expiration_policy {
    ttl = ""
  }
}

# ---------- Permisos del agente de servicio ----------
# Sin ellos, la dead-letter policy existe pero no funciona: Pub/Sub no puede publicar en la DLQ
# ni confirmar el original, y el mensaje sigue reintentándose (la consola lo avisa).

resource "google_pubsub_topic_iam_member" "dlq_service_agent" {
  topic  = google_pubsub_topic.knowledge_dlq.id
  role   = "roles/pubsub.publisher"
  member = local.pubsub_service_agent
}

resource "google_pubsub_subscription_iam_member" "worker_service_agent" {
  subscription = google_pubsub_subscription.knowledge_worker.id
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_service_agent
}

# ---------- Permisos mínimos de la aplicación (productor y worker del indexer) ----------
# Por recurso, no por proyecto. En la fase 15 se separarán productor y worker en dos identidades.

resource "google_pubsub_topic_iam_member" "app_publish" {
  topic  = google_pubsub_topic.knowledge.id
  role   = "roles/pubsub.publisher"
  member = google_service_account.app.member
}

# Errores permanentes: el adaptador los publica él mismo en la DLQ (no hay "rechazar a la DLQ").
resource "google_pubsub_topic_iam_member" "app_dead_letter" {
  topic  = google_pubsub_topic.knowledge_dlq.id
  role   = "roles/pubsub.publisher"
  member = google_service_account.app.member
}

resource "google_pubsub_subscription_iam_member" "app_consume" {
  subscription = google_pubsub_subscription.knowledge_worker.id
  role         = "roles/pubsub.subscriber"
  member       = google_service_account.app.member
}

# `pubsub.subscriptions.get`: el adaptador lee la dead-letter policy al suscribirse y se niega a
# consumir sin ella. `roles/pubsub.subscriber` no incluye ese permiso.
resource "google_pubsub_subscription_iam_member" "app_read_policy" {
  subscription = google_pubsub_subscription.knowledge_worker.id
  role         = "roles/pubsub.viewer"
  member       = google_service_account.app.member
}

# ==================================================================================================
# Fase 9.G3: flujo asíncrono de casos del orquestador (ORCHESTRATOR__BROKER=pubsub).
#
#   support.emails → triaje → case.triaged.<especialista> → especialista → case.outcomes
#   case.reviews (9.G5): decisión de la persona o SLA vencido, desde Workflows → rol `review`
#
# Mismo patrón que la cola de indexación (tema, DLQ con su suscripción, `<tema>.worker`), con dos
# diferencias:
# - **Exactly-once delivery** en las suscripciones de los workers: tras un `ack` correcto no hay
#   reentregas (cada triaje o especialista es una llamada al LLM, con coste). No elimina los
#   duplicados al publicar (otro `message_id`): la deduplicación de verdad sigue en el CaseStore.
#   Solo con *pull* y en una región; añade latencia.
# - **Ack deadline de 600 s** (el máximo): un turno del agente con tools puede pasar del minuto, y
#   el adaptador no renueva el plazo. Con exactly-once, un `ack` fuera de plazo falla y el
#   mensaje vuelve (lo absorbe el CaseStore, pero se paga otra vez el modelo).
#
# `case.outcomes` no tiene consumidor en el orquestador: su suscripción `.worker` la leerán las
# notificaciones y la espera humana de Workflows (9.G5). Sin suscripción, el mensaje se perdería.
# ==================================================================================================

locals {
  case_topics = toset([
    "support.emails",
    "case.triaged.refunds",
    "case.triaged.creative",
    "case.triaged.knowledge",
    "case.outcomes",
    # 9.G5: decisiones de revisión (o SLA vencido) que publica Workflows; las aplica el rol
    # `review` del worker.
    "case.reviews",
  ])
}

resource "google_pubsub_topic" "cases" {
  for_each = local.case_topics

  name   = each.key
  labels = local.labels

  depends_on = [google_project_service.enabled]
}

resource "google_pubsub_topic" "cases_dlq" {
  for_each = local.case_topics

  name   = "${each.key}.dlq"
  labels = local.labels

  depends_on = [google_project_service.enabled]
}

resource "google_pubsub_subscription" "cases_dlq" {
  for_each = local.case_topics

  name   = "${each.key}.dlq"
  topic  = google_pubsub_topic.cases_dlq[each.key].id
  labels = local.labels

  message_retention_duration = "604800s"

  expiration_policy {
    ttl = ""
  }
}

resource "google_pubsub_subscription" "cases_worker" {
  for_each = local.case_topics

  name   = "${each.key}.worker"
  topic  = google_pubsub_topic.cases[each.key].id
  labels = local.labels

  ack_deadline_seconds         = 600
  enable_exactly_once_delivery = true

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.cases_dlq[each.key].id
    max_delivery_attempts = var.max_deliveries
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "300s"
  }

  expiration_policy {
    ttl = ""
  }
}

resource "google_pubsub_topic_iam_member" "cases_dlq_service_agent" {
  for_each = local.case_topics

  topic  = google_pubsub_topic.cases_dlq[each.key].id
  role   = "roles/pubsub.publisher"
  member = local.pubsub_service_agent
}

resource "google_pubsub_subscription_iam_member" "cases_worker_service_agent" {
  for_each = local.case_topics

  subscription = google_pubsub_subscription.cases_worker[each.key].id
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_service_agent
}

# La aplicación publica en todos (el relay del outbox publica traspasos y resultados; la entrada,
# los emails), escribe sus errores permanentes en las DLQ y consume las suscripciones `.worker`.
resource "google_pubsub_topic_iam_member" "cases_app_publish" {
  for_each = local.case_topics

  topic  = google_pubsub_topic.cases[each.key].id
  role   = "roles/pubsub.publisher"
  member = google_service_account.app.member
}

resource "google_pubsub_topic_iam_member" "cases_app_dead_letter" {
  for_each = local.case_topics

  topic  = google_pubsub_topic.cases_dlq[each.key].id
  role   = "roles/pubsub.publisher"
  member = google_service_account.app.member
}

resource "google_pubsub_subscription_iam_member" "cases_app_consume" {
  for_each = local.case_topics

  subscription = google_pubsub_subscription.cases_worker[each.key].id
  role         = "roles/pubsub.subscriber"
  member       = google_service_account.app.member
}

resource "google_pubsub_subscription_iam_member" "cases_app_read_policy" {
  for_each = local.case_topics

  subscription = google_pubsub_subscription.cases_worker[each.key].id
  role         = "roles/pubsub.viewer"
  member       = google_service_account.app.member
}
