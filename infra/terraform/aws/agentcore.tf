# Fase 7.4, carril A en AWS: el agente en AgentCore Runtime y su memoria en AgentCore Memory.
# Desactivado por defecto (agentcore_enabled = false). Se paga por uso (vCPU/GB-hora activos del
# Runtime, eventos y registros de Memory). El Runtime necesita la imagen ARM64 en ECR (fase 13):
# sin `agentcore_container_uri` solo se crea la memoria.

locals {
  agentcore        = var.agentcore_enabled ? 1 : 0
  agentcore_runtime = var.agentcore_enabled && var.agentcore_container_uri != "" ? 1 : 0
  # Memory y Runtime solo admiten [a-zA-Z][a-zA-Z0-9_]*.
  agentcore_name = replace("${local.name}_booking", "-", "_")
}

resource "aws_bedrockagentcore_memory" "booking" {
  count                 = local.agentcore
  name                  = local.agentcore_name
  description           = "Hilos (eventos) y preferencias (registros) del Knowledge & Booking Agent."
  event_expiry_duration = var.agentcore_event_expiry_days
}

data "aws_iam_policy_document" "agentcore_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "agentcore_runtime" {
  count              = local.agentcore_runtime
  name               = "${local.name}-agentcore-runtime"
  assume_role_policy = data.aws_iam_policy_document.agentcore_trust.json
}

# Mínimo privilegio: imagen, logs, modelos de Bedrock y SOLO esta memoria.
data "aws_iam_policy_document" "agentcore_runtime" {
  count = local.agentcore_runtime
  statement {
    sid       = "PullImage"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/bedrock-agentcore/*"]
  }
  statement {
    sid       = "Models"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = ["*"]
  }
  statement {
    sid = "Memory"
    actions = [
      "bedrock-agentcore:CreateEvent",
      "bedrock-agentcore:ListEvents",
      "bedrock-agentcore:BatchCreateMemoryRecords",
      "bedrock-agentcore:ListMemoryRecords",
    ]
    resources = [aws_bedrockagentcore_memory.booking[0].arn]
  }
}

resource "aws_iam_role_policy" "agentcore_runtime" {
  count  = local.agentcore_runtime
  role   = aws_iam_role.agentcore_runtime[0].id
  policy = data.aws_iam_policy_document.agentcore_runtime[0].json
}

resource "aws_bedrockagentcore_agent_runtime" "booking" {
  count              = local.agentcore_runtime
  agent_runtime_name = local.agentcore_name
  description        = "Knowledge & Booking Agent (contrato HTTP: /invocations y /ping)."
  role_arn           = aws_iam_role.agentcore_runtime[0].arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = var.agentcore_container_uri
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  environment_variables = {
    AGENTCORE__MEMORY_ID = aws_bedrockagentcore_memory.booking[0].id
    BOOKING_AGENT__FLAVOR = var.agentcore_flavor
  }

  depends_on = [aws_iam_role_policy.agentcore_runtime]
}
