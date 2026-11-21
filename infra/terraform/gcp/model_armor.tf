# Fase 8.G3, guardrails gestionados en GCP: Model Armor (escudo) y Sensitive Data Protection
# (PII). Equivale a content_safety.tf (Azure) y guardrails.tf (AWS).
#
#   Azure                       AWS                          GCP
#   Content Safety (F0/S0)      Bedrock Guardrail + versión  Plantilla de Model Armor
#   Azure AI Language (PII)     sensitive_information_policy Plantillas de inspección de SDP
#   Filtro del despliegue       —                            Floor setting (todo el proyecto)
#
# Sin coste en reposo: plantillas y roles son gratis. Se paga lo evaluado: Model Armor, 2 M de
# tokens al mes gratis y 0,10 $ por millón después; SDP, 1 GB al mes gratis y 3 $/GB (mínimo de
# 1 KB por llamada). El harness los usa con GUARDRAILS__SHIELD=gcp y GUARDRAILS__PII=gcp
# (output guardrails_env).

locals {
  model_armor = var.model_armor_enabled ? 1 : 0
  floor       = var.model_armor_floor_setting_enabled ? 1 : 0
  # Plantillas de SDP y de Model Armor en la misma región (residencia de datos).
  dlp_parent = "projects/${var.project_id}/locations/${var.region}"

  # Los mismos infoTypes que `harness.pii_gcp.DEFAULT_INFO_TYPES` (y que Presidio).
  pii_info_types = [
    "PERSON_NAME",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD_NUMBER",
    "IBAN_CODE",
    "SPAIN_NIF_NUMBER",
    "SPAIN_DNI_NUMBER",
    "SPAIN_NIE_NUMBER",
    "IP_ADDRESS",
  ]
  # Lo que Model Armor bloquea por SDP: solo datos de pago, como Bedrock (BLOCK). El contacto lo
  # redacta el filtro de PII del harness, que corre después de las guardas de entrada.
  payment_info_types = ["CREDIT_CARD_NUMBER", "IBAN_CODE"]

  # RAI en HIGH (recomendación de Google: LOW_AND_ABOVE da falsos positivos); inyección en
  # MEDIUM_AND_ABOVE, la primera capa frente a ataques, como PROMPT_ATTACK HIGH en Bedrock.
  rai_filters = ["HATE_SPEECH", "HARASSMENT", "SEXUALLY_EXPLICIT", "DANGEROUS"]
}

# ---------- Permisos de la aplicación (siempre: sin coste) ----------

# `sanitizeUserPrompt` / `sanitizeModelResponse` (escudo `gcp`, 8.G1).
resource "google_project_iam_member" "app_model_armor" {
  project = var.project_id
  role    = "roles/modelarmor.user"
  member  = google_service_account.app.member
}

# `inspect_content` y uso de plantillas de inspección (detector `gcp`, 8.G2).
resource "google_project_iam_member" "app_dlp" {
  project = var.project_id
  role    = "roles/dlp.user"
  member  = google_service_account.app.member
}

# ---------- Sensitive Data Protection ----------

# GCP_SENSITIVE_DATA__INSPECT_TEMPLATE: el detector de PII del harness (8.G2).
resource "google_data_loss_prevention_inspect_template" "pii" {
  count = local.model_armor

  parent       = local.dlp_parent
  template_id  = "${local.name}-pii"
  display_name = "Genaipocs: PII del harness"
  description  = "Datos personales que se redactan antes de llegar al modelo."

  inspect_config {
    # LIKELY: con POSSIBLE, PERSON_NAME marca nombres de artistas y de eventos.
    min_likelihood = "LIKELY"
    include_quote  = false

    dynamic "info_types" {
      for_each = toset(local.pii_info_types)
      content {
        name = info_types.value
      }
    }
  }

  depends_on = [google_project_service.enabled]
}

# Filtro SDP avanzado de la plantilla de Model Armor: solo datos de pago.
resource "google_data_loss_prevention_inspect_template" "payment" {
  count = local.model_armor

  parent       = local.dlp_parent
  template_id  = "${local.name}-payment"
  display_name = "Genaipocs: datos de pago (Model Armor)"
  description  = "Tarjetas e IBAN: Model Armor bloquea el texto que los contenga."

  inspect_config {
    min_likelihood = "LIKELY"
    include_quote  = false

    dynamic "info_types" {
      for_each = toset(local.payment_info_types)
      content {
        name = info_types.value
      }
    }
  }

  depends_on = [google_project_service.enabled]
}

# El agente de servicio de Model Armor ejecuta el filtro SDP avanzado: necesita usar y leer las
# plantillas. La documentación lo exige con las plantillas en otro proyecto; el codelab lo da
# también en el mismo (se comprueba en la nube si hace falta).
resource "google_project_service_identity" "model_armor" {
  count    = local.model_armor
  provider = google-beta

  project = var.project_id
  service = "modelarmor.googleapis.com"
}

resource "google_project_iam_member" "model_armor_dlp" {
  for_each = var.model_armor_enabled ? toset(["roles/dlp.user", "roles/dlp.reader"]) : toset([])

  project = var.project_id
  role    = each.value
  member  = google_project_service_identity.model_armor[0].member
}

# ---------- Plantilla de Model Armor (escudo del harness) ----------

resource "google_model_armor_template" "harness" {
  count = local.model_armor

  location    = var.region
  template_id = "${local.name}-harness"

  filter_config {
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "MEDIUM_AND_ABOVE"
    }

    rai_settings {
      dynamic "rai_filters" {
        for_each = toset(local.rai_filters)
        content {
          filter_type      = rai_filters.value
          confidence_level = "HIGH"
        }
      }
    }

    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }

    sdp_settings {
      advanced_config {
        inspect_template = "${local.dlp_parent}/inspectTemplates/${google_data_loss_prevention_inspect_template.payment[0].template_id}"
      }
    }
  }

  template_metadata {
    # Para las integraciones que lo apliquen (p. ej., Agent Gateway); con `sanitize*` decide el
    # harness.
    enforcement_type = "INSPECT_AND_BLOCK"
    # Un filtro saltado se ve en `invocation_result` y el escudo lo trata como no disponible.
    ignore_partial_invocation_failures = false
    # Los registros de `sanitize*` llevan el texto evaluado (PII): desactivados.
    log_sanitize_operations = false
    log_template_operations = true

    multi_language_detection {
      enable_multi_language_detection = true
    }

    # Versión estable de los filtros: un cambio de detector es una decisión, no una sorpresa.
    filter_version_selector {
      alias = "FILTER_VERSION_ALIAS_STABLE"
    }
  }

  depends_on = [
    google_project_service.enabled,
    google_project_iam_member.model_armor_dlp,
  ]
}

# ---------- Floor setting: red de seguridad de Gemini en todo el proyecto ----------
# Aplica Model Armor a todas las llamadas a `generateContent` del proyecto, también las que no
# pasan por el harness (notebooks, pruebas). Solo INSPECT_ONLY con logging: medir antes de
# bloquear (el bloqueo llegaría como BlockedReason.MODEL_ARMOR → provider_content_filter).
# Con coste por uso: cada llamada a Gemini consume tokens de Model Armor. Es un ajuste del
# proyecto, no un recurso: comprobar en la nube qué deja `destroy` (pendientes-cloud).

resource "google_project_service_identity" "agent_platform" {
  count    = local.floor
  provider = google-beta

  project = var.project_id
  service = "aiplatform.googleapis.com"
}

# El agente de servicio de Agent Platform llama a Model Armor en nombre de Gemini.
resource "google_project_iam_member" "agent_platform_model_armor" {
  count = local.floor

  project = var.project_id
  role    = "roles/modelarmor.user"
  member  = google_project_service_identity.agent_platform[0].member
}

resource "google_model_armor_floorsetting" "project" {
  count = local.floor

  parent   = "projects/${var.project_id}"
  location = "global" # Las floor settings solo se gestionan en el endpoint global.

  # El mínimo para cualquier plantilla del proyecto; la del harness lo cumple.
  enable_floor_setting_enforcement = true
  integrated_services              = ["AI_PLATFORM"]

  filter_config {
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "MEDIUM_AND_ABOVE"
    }

    rai_settings {
      dynamic "rai_filters" {
        for_each = toset(local.rai_filters)
        content {
          filter_type      = rai_filters.value
          confidence_level = "HIGH"
        }
      }
    }

    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }
  }

  ai_platform_floor_setting {
    inspect_only         = true
    enable_cloud_logging = true
  }

  floor_setting_metadata {
    multi_language_detection {
      enable_multi_language_detection = true
    }
  }

  depends_on = [
    google_project_service.enabled,
    google_project_iam_member.agent_platform_model_armor,
  ]
}
