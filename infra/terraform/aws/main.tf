# Fase 3, carril B en AWS: cola de indexación + almacén vectorial S3 Vectors.
# Coste en reposo ~0 $: SQS y S3 Vectors se pagan por uso; no hay capacidad reservada.

locals {
  name = "${var.project}-${var.environment}"
}

data "aws_caller_identity" "current" {}

# ---------- Cola de indexación (equivale a knowledge.documents de RabbitMQ) ----------

resource "aws_sqs_queue" "knowledge_dlq" {
  name                      = "${local.name}-knowledge-documents-dlq"
  message_retention_seconds = 1209600 # 14 días: tiempo para investigar y reprocesar.
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "knowledge" {
  name                       = "${local.name}-knowledge-documents"
  visibility_timeout_seconds = 60
  receive_wait_time_seconds  = 10 # Long polling por defecto: menos ReceiveMessage vacíos.
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.knowledge_dlq.arn
    maxReceiveCount     = var.max_deliveries
  })
}

# Solo la cola principal puede enviar mensajes a esta DLQ.
resource "aws_sqs_queue_redrive_allow_policy" "knowledge_dlq" {
  queue_url = aws_sqs_queue.knowledge_dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.knowledge.arn]
  })
}

# ---------- Almacén vectorial (carril B) ----------

resource "aws_s3vectors_vector_bucket" "knowledge" {
  vector_bucket_name = "${local.name}-knowledge-vectors"
}

resource "aws_s3vectors_index" "knowledge" {
  vector_bucket_name = aws_s3vectors_vector_bucket.knowledge.vector_bucket_name
  index_name         = "knowledge"
  data_type          = "float32"
  dimension          = var.embedding_dimensions
  distance_metric    = "cosine"

  # El texto del chunk no cabe en los 2 KB filtrables: se guarda como no filtrable.
  metadata_configuration {
    non_filterable_metadata_keys = ["text"]
  }
}

# ---------- Permisos mínimos del worker del indexer ----------
# Se adjuntará al task role de ECS en la fase 13; aquí queda definida y revisable.

data "aws_iam_policy_document" "indexer_worker" {
  statement {
    sid = "ConsumeQueue"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueUrl",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.knowledge.arn]
  }

  statement {
    sid       = "DeadLetterPermanentErrors"
    actions   = ["sqs:SendMessage", "sqs:GetQueueUrl"]
    resources = [aws_sqs_queue.knowledge_dlq.arn]
  }

  statement {
    sid       = "WriteVectors"
    actions   = ["s3vectors:PutVectors", "s3vectors:DeleteVectors", "s3vectors:QueryVectors", "s3vectors:GetVectors"]
    resources = [aws_s3vectors_index.knowledge.index_arn]
  }

  statement {
    sid       = "Embeddings"
    actions   = ["bedrock:InvokeModel"]
    resources = ["arn:aws:bedrock:${var.region}::foundation-model/amazon.titan-embed-text-v2:0"]
  }
}

resource "aws_iam_policy" "indexer_worker" {
  name   = "${local.name}-indexer-worker"
  policy = data.aws_iam_policy_document.indexer_worker.json
}

# ---------- Guardarraíl de coste ----------

resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }
}
