# Fase 8.4, guardrails gestionados en AWS: Amazon Bedrock Guardrails (ApplyGuardrail).
# Desactivado por defecto (bedrock_guardrail_enabled = false). Sin coste en reposo: se paga por
# unidades de texto evaluadas (1000 caracteres) y política. El harness lo usa con
# GUARDRAILS__SHIELD=bedrock y BEDROCK_GUARDRAIL__GUARDRAIL_ID/VERSION (outputs).

locals {
  bedrock_guardrail = var.bedrock_guardrail_enabled ? 1 : 0
}

resource "aws_bedrock_guardrail" "harness" {
  count                     = local.bedrock_guardrail
  name                      = "${local.name}-harness"
  description               = "Prompt attacks, contenido dañino, temas vetados y PII del agente de Headache."
  blocked_input_messaging   = "No puedo ayudarte con esa petición."
  blocked_outputs_messaging = "No puedo darte esa respuesta. Un agente de Headache revisará tu caso."

  content_policy_config {
    # PROMPT_ATTACK solo se evalúa en la entrada (la salida debe ir a NONE).
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
    dynamic "filters_config" {
      for_each = toset(["HATE", "INSULTS", "SEXUAL", "VIOLENCE", "MISCONDUCT"])
      content {
        type            = filters_config.value
        input_strength  = "MEDIUM"
        output_strength = "MEDIUM"
      }
    }
  }

  topic_policy_config {
    topics_config {
      name       = "legal_advice"
      type       = "DENY"
      definition = "Asesoramiento legal o fiscal personalizado sobre reclamaciones o contratos."
      examples   = ["¿Puedo demandar al recinto por el retraso?", "¿Cómo declaro el reembolso a Hacienda?"]
    }
  }

  # Mismo criterio que el harness: el dato de contacto se anonimiza, el de pago se bloquea.
  sensitive_information_policy_config {
    dynamic "pii_entities_config" {
      for_each = toset(["EMAIL", "PHONE", "NAME"])
      content {
        type   = pii_entities_config.value
        action = "ANONYMIZE"
      }
    }
    dynamic "pii_entities_config" {
      for_each = toset(["CREDIT_DEBIT_CARD_NUMBER", "INTERNATIONAL_BANK_ACCOUNT_NUMBER", "PASSWORD"])
      content {
        type   = pii_entities_config.value
        action = "BLOCK"
      }
    }
  }
}

# Versión inmutable: producción apunta a una versión publicada, nunca a DRAFT.
resource "aws_bedrock_guardrail_version" "harness" {
  count         = local.bedrock_guardrail
  guardrail_arn = aws_bedrock_guardrail.harness[0].guardrail_arn
  description   = "Versión publicada por Terraform."
}
