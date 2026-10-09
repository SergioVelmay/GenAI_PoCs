# Fase 6.4, carril A en AWS: AgentCore Gateway como MCP Gateway delante de los tres servidores.
# Desactivado por defecto (mcp_gateway_enabled = false). Coste en reposo ~0 $: se paga por
# llamada. Requiere los servidores MCP publicados por HTTPS (fase 13) y un IdP OIDC (Cognito).

locals {
  mcp_gateway = var.mcp_gateway_enabled ? 1 : 0
  mcp_scopes = {
    catalog   = ["catalog:read"]
    ticketing = ["orders:read"]
    refunds   = ["refunds:write"]
  }
}

data "aws_iam_policy_document" "mcp_gateway_trust" {
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

resource "aws_iam_role" "mcp_gateway" {
  count              = local.mcp_gateway
  name               = "${local.name}-mcp-gateway"
  assume_role_policy = data.aws_iam_policy_document.mcp_gateway_trust.json
}

# Lo mínimo para el token exchange: token de workload, token OAuth del vault y su secreto.
data "aws_iam_policy_document" "mcp_gateway" {
  statement {
    sid = "TokenExchange"
    actions = [
      "bedrock-agentcore:GetWorkloadAccessToken",
      "bedrock-agentcore:GetResourceOauth2Token",
    ]
    resources = ["*"]
  }
  statement {
    sid       = "OAuthClientSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:bedrock-agentcore*"]
  }
}

resource "aws_iam_role_policy" "mcp_gateway" {
  count  = local.mcp_gateway
  role   = aws_iam_role.mcp_gateway[0].id
  policy = data.aws_iam_policy_document.mcp_gateway.json
}

# Cliente OAuth del gateway en el IdP. On-Behalf-Of (RFC 8693): cambia el token del usuario por
# uno con el audience y el scope de cada servidor. Sin passthrough del token de entrada.
resource "aws_bedrockagentcore_oauth2_credential_provider" "mcp" {
  count                      = local.mcp_gateway
  name                       = "${local.name}-mcp-obo"
  credential_provider_vendor = "CustomOauth2"

  oauth2_provider_config {
    custom_oauth2_provider_config {
      client_id_wo                  = var.mcp_oauth_client_id
      client_secret_wo              = var.mcp_oauth_client_secret
      client_credentials_wo_version = var.mcp_oauth_credentials_version

      oauth_discovery {
        discovery_url = var.mcp_oidc_discovery_url
      }

      on_behalf_of_token_exchange_config {
        grant_type = "TOKEN_EXCHANGE"
        token_exchange_grant_type_config {
          actor_token_content = "NONE"
        }
      }
    }
  }
}

resource "aws_bedrockagentcore_gateway" "mcp" {
  count           = local.mcp_gateway
  name            = "${local.name}-mcp"
  description     = "MCP Gateway de Headache: catalog, ticketing y refunds"
  role_arn        = aws_iam_role.mcp_gateway[0].arn
  protocol_type   = "MCP"
  authorizer_type = "CUSTOM_JWT"

  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url    = var.mcp_oidc_discovery_url
      allowed_audience = [var.mcp_gateway_audience]
      allowed_scopes   = sort(distinct(flatten(values(local.mcp_scopes))))
    }
  }

  protocol_configuration {
    mcp {
      instructions       = "Catálogo, pedidos y reembolsos del cliente autenticado de Headache."
      supported_versions = ["2025-06-18", "2025-11-25"]
    }
  }
}

resource "aws_bedrockagentcore_gateway_target" "mcp" {
  for_each = var.mcp_gateway_enabled ? var.mcp_server_urls : {}

  name               = each.key
  gateway_identifier = aws_bedrockagentcore_gateway.mcp[0].gateway_id
  description        = "Servidor MCP ${each.key} (FastMCP en ECS, fase 13)"

  credential_provider_configuration {
    oauth {
      provider_arn = aws_bedrockagentcore_oauth2_credential_provider.mcp[0].credential_provider_arn
      scopes       = local.mcp_scopes[each.key]
      grant_type   = "TOKEN_EXCHANGE"
    }
  }

  target_configuration {
    mcp {
      mcp_server {
        endpoint = each.value
        # DYNAMIC: tools descubiertas en cada llamada con el token del usuario (OBO sin 2LO).
        listing_mode = "DYNAMIC"
      }
    }
  }
}
