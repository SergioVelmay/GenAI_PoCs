# Pasada 5.P, carril A de evals en AWS: Bedrock Evaluations (rag_evals.bedrock_eval).
# Desactivado por defecto (bedrock_evals_enabled = false). Sin coste en reposo: el bucket guarda
# unos KB (dataset y resultados, que caducan) y el servicio solo cobra los tokens del evaluador.
# Outputs para BEDROCK_EVAL__BUCKET y BEDROCK_EVAL__ROLE_ARN.
#
# Equivalencias: en Azure, Foundry Evaluation corre en el proceso (sin recursos); en GCP, el
# Evaluation Service tampoco necesita nada (roles/aiplatform.user). AWS es el único que ejecuta la
# evaluación como un job asíncrono: necesita dónde leer y escribir y un rol que asumir.

locals {
  bedrock_evals = var.bedrock_evals_enabled ? 1 : 0
  # Perfil entre regiones (eu.<modelo>): el modelo base se invoca en las regiones del perfil.
  bedrock_evals_base_model = replace(var.bedrock_evals_evaluator_model_id, "/^(eu|us|apac|global)\\./", "")
}

resource "aws_s3_bucket" "evals" {
  count = local.bedrock_evals
  # Los nombres de bucket S3 son globales: se añade el id de cuenta.
  bucket = "${local.name}-evals-${data.aws_caller_identity.current.account_id}"
  # Datos regenerables (dataset de calibración y resultados): se pueden borrar con el bucket.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "evals" {
  count                   = local.bedrock_evals
  bucket                  = aws_s3_bucket.evals[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "evals" {
  count  = local.bedrock_evals
  bucket = aws_s3_bucket.evals[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Las respuestas del dataset y las explicaciones del juez caducan: no se acumulan datos.
resource "aws_s3_bucket_lifecycle_configuration" "evals" {
  count  = local.bedrock_evals
  bucket = aws_s3_bucket.evals[0].id
  rule {
    id     = "expire-evals"
    status = "Enabled"
    filter {
      prefix = ""
    }
    expiration {
      days = var.bedrock_evals_retention_days
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# ---------- Rol que asume Bedrock para leer el dataset, juzgar y escribir el resultado ----------

data "aws_iam_policy_document" "bedrock_evals_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }
    # Diputado confundido: solo jobs de evaluación de esta cuenta y región.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock:${var.region}:${data.aws_caller_identity.current.account_id}:evaluation-job/*"]
    }
  }
}

resource "aws_iam_role" "bedrock_evals" {
  count              = local.bedrock_evals
  name               = "${local.name}-bedrock-evals"
  assume_role_policy = data.aws_iam_policy_document.bedrock_evals_trust.json
}

# Mínimo privilegio: SOLO el evaluador (perfil y modelo base) y SOLO este bucket.
data "aws_iam_policy_document" "bedrock_evals" {
  count = local.bedrock_evals
  statement {
    sid = "Evaluator"
    # El servicio juzga por lotes: además de InvokeModel, crea y para invocaciones por lotes.
    actions = [
      "bedrock:InvokeModel",
      "bedrock:CreateModelInvocationJob",
      "bedrock:StopModelInvocationJob",
    ]
    resources = [
      "arn:aws:bedrock:${var.region}:${data.aws_caller_identity.current.account_id}:inference-profile/${var.bedrock_evals_evaluator_model_id}",
      "arn:aws:bedrock:*::foundation-model/${local.bedrock_evals_base_model}",
    ]
  }
  statement {
    sid       = "Bucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
    resources = [aws_s3_bucket.evals[0].arn]
  }
  statement {
    sid       = "Objects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.evals[0].arn}/*"]
  }
}

resource "aws_iam_role_policy" "bedrock_evals" {
  count  = local.bedrock_evals
  name   = "bedrock-evals"
  role   = aws_iam_role.bedrock_evals[0].id
  policy = data.aws_iam_policy_document.bedrock_evals[0].json
}
