# Checkpoint G→13.3: apply REAL del módulo de AWS contra el AWS Emulator oficial (coste 0).
#
#   docker compose --profile aws up -d
#   terraform test -test-directory=tests-emulator      (en infra/terraform/aws)
#
# terraform test guarda el estado en memoria y destruye al terminar: no toca terraform.tfstate.
# Va en un directorio aparte para que el `terraform test` de la CI (tests/, con mock_provider)
# no lo ejecute sin emulador. Cada `run` activa un módulo sobre el mismo estado: al pasar al
# siguiente, Terraform destruye el anterior, así que se prueban la creación y el borrado.
#
# Fuera de este test (huecos del emulador 1.0.569, ver docs/fase-13-g-checkpoint.md):
# - queue_workers: PutScalingPolicy con metric math devuelve 501 NotImplemented.
# - Targets del MCP gateway: el emulador hace el tool discovery contra la URL, como AWS, y aquí
#   no hay servidores MCP en HTTPS públicos (se prueba el gateway sin targets).

variables {
  budget_email = "alertas@genaipocs.example"
}

# Sustituye al provider del módulo. Tres redes de seguridad para que nada llegue a AWS:
# 1. Las claves son las del emulador (nuevas en cada arranque): AWS las rechazaría.
# 2. Proxy inexistente salvo para 127.0.0.1: un endpoint olvidado en `endpoints` falla en local
#    en vez de salir a internet (pasó con S3 Control, que el provider usa para las etiquetas de S3).
# 3. El primer `run` comprueba que la cuenta es la del emulador (000000000000).
provider "aws" {
  region            = "eu-west-1"
  access_key        = regex("aws_access_key_id = (\\S+)", file("../../../.aws-emulator/9297/emulator_credentials"))[0]
  secret_key        = regex("aws_secret_access_key = (\\S+)", file("../../../.aws-emulator/9297/emulator_credentials"))[0]
  custom_ca_bundle  = "../../../.aws-emulator/9297/ca.pem"
  s3_use_path_style = true
  http_proxy        = "http://127.0.0.1:9"
  https_proxy       = "http://127.0.0.1:9"
  no_proxy          = "127.0.0.1"
  max_retries       = 2
  # Sin el id de cuenta, el provider lee las etiquetas de los buckets con GetBucketTagging (S3) y
  # no con S3 Control, que antepone la cuenta al host (000000000000.127.0.0.1) y nunca llega al
  # emulador. Por eso no se usa `allowed_account_ids` (necesita el id); lo sustituye el assert.
  # Hacen falta las dos: la validación de credenciales también obtiene el id (sts:GetCallerIdentity).
  skip_requesting_account_id  = true
  skip_credentials_validation = true

  endpoints {
    applicationautoscaling = "https://127.0.0.1:9297"
    bedrock                = "https://127.0.0.1:9297"
    bedrockagent           = "https://127.0.0.1:9297"
    bedrockagentcore       = "https://127.0.0.1:9297"
    budgets                = "https://127.0.0.1:9297"
    cloudwatch             = "https://127.0.0.1:9297"
    ec2                    = "https://127.0.0.1:9297"
    ecs                    = "https://127.0.0.1:9297"
    iam                    = "https://127.0.0.1:9297"
    logs                   = "https://127.0.0.1:9297"
    s3                     = "https://127.0.0.1:9297"
    s3control              = "https://127.0.0.1:9297"
    s3vectors              = "https://127.0.0.1:9297"
    secretsmanager         = "https://127.0.0.1:9297"
    sns                    = "https://127.0.0.1:9297"
    sqs                    = "https://127.0.0.1:9297"
    sts                    = "https://127.0.0.1:9297"
    xray                   = "https://127.0.0.1:9297"
  }
}

run "base" {
  assert {
    condition     = data.aws_caller_identity.current.account_id == "000000000000"
    error_message = "No es la cuenta del emulador: las peticiones podrían estar llegando a AWS."
  }

  assert {
    condition     = aws_sqs_queue.knowledge.name == "knowledge-documents"
    error_message = "La cola de indexación lleva el nombre lógico del topic."
  }
}

# Antes que AgentCore: el run que sigue a Memory necesitaría `refresh = false` (ListTagsForResource
# de AgentCore Control devuelve 501 en el emulador).
run "bedrock_evals" {
  variables {
    bedrock_evals_enabled = true
  }

  assert {
    condition     = output.bedrock_evals_bucket == "genaipocs-dev-evals-000000000000"
    error_message = "El bucket de evaluaciones lleva el id de cuenta (los nombres de S3 son globales)."
  }

  assert {
    condition     = output.bedrock_evals_role_arn == aws_iam_role.bedrock_evals[0].arn
    error_message = "El rol de servicio del job se publica para BEDROCK_EVAL__ROLE_ARN."
  }

  assert {
    condition     = strcontains(aws_iam_role.bedrock_evals[0].assume_role_policy, "evaluation-job/*")
    error_message = "La confianza se limita a jobs de evaluación de esta cuenta (diputado confundido)."
  }

  assert {
    condition = (
      strcontains(aws_iam_role_policy.bedrock_evals[0].policy, "inference-profile/eu.anthropic.claude-sonnet-4-6")
      && strcontains(aws_iam_role_policy.bedrock_evals[0].policy, "foundation-model/anthropic.claude-sonnet-4-6")
    )
    error_message = "El rol solo invoca el evaluador: su perfil entre regiones y el modelo base."
  }

  assert {
    condition     = aws_s3_bucket_lifecycle_configuration.evals[0].rule[0].expiration[0].days == 30
    error_message = "Datasets y resultados caducan."
  }
}

run "agents_guardrail_observability" {
  variables {
    agentcore_enabled         = true
    agentcore_container_uri   = "000000000000.dkr.ecr.eu-west-1.amazonaws.com/genaipocs-booking-agent:emulator"
    bedrock_guardrail_enabled = true
    observability_enabled     = true
  }

  assert {
    condition     = aws_bedrockagentcore_memory.booking[0].name == "genaipocs_dev_booking"
    error_message = "Memory solo admite [a-zA-Z][a-zA-Z0-9_]*: el nombre sale de local.agentcore_name."
  }

  assert {
    condition     = output.agentcore_runtime_arn != null
    error_message = "Con imagen, el Runtime se crea y publica su ARN."
  }

  assert {
    condition     = aws_bedrock_guardrail_version.harness[0].version == "1"
    error_message = "El harness fija la primera versión publicada del guardrail."
  }

  assert {
    condition     = length(aws_cloudwatch_metric_alarm.llm_cost_runaway) + length(aws_cloudwatch_metric_alarm.outbox_backlog) == 2
    error_message = "Alarma de coste de modelos y de backlog del outbox."
  }
}

run "mcp_gateway_without_targets" {
  # Sin refresh: ListTagsForResource de AgentCore Control (lo llama la lectura de Memory, Runtime
  # y Gateway) devuelve 501 en el emulador. Crear y borrar sí funcionan.
  plan_options {
    refresh = false
  }

  variables {
    mcp_gateway_enabled     = true
    mcp_oidc_discovery_url  = "https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_EXAMPLE/.well-known/openid-configuration"
    mcp_oauth_client_id     = "genaipocs-gateway"
    mcp_oauth_client_secret = "solo-para-el-emulador"
  }

  # El enum TOKEN_EXCHANGE (On-Behalf-Of) que el provider no documenta: la API lo acepta.
  assert {
    condition     = aws_bedrockagentcore_oauth2_credential_provider.mcp[0].credential_provider_arn != ""
    error_message = "El proveedor OAuth con token exchange debe crearse."
  }

  assert {
    condition     = length(aws_bedrockagentcore_memory.booking) == 0
    error_message = "Al pasar de run, el módulo anterior se destruye (prueba el borrado)."
  }
}
