# Fase 8.G3: plan simulado (sin credenciales, sin red, sin coste) de los guardrails de GCP con
# todo activado: región de las plantillas, filtros, SDP solo para datos de pago y floor setting
# en INSPECT_ONLY.
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

mock_provider "google-beta" {
  mock_resource "google_project_service_identity" {
    defaults = {
      email  = "service-123456789012@gcp-sa-modelarmor.iam.gserviceaccount.com"
      member = "serviceAccount:service-123456789012@gcp-sa-modelarmor.iam.gserviceaccount.com"
    }
  }
}

variables {
  project_id      = "genaipocs-test"
  billing_account = "000000-000000-000000"
  budget_email    = "alertas@genaipocs.example"
}

run "disabled_by_default" {
  command = plan

  assert {
    condition     = length(google_model_armor_template.harness) == 0 && length(google_data_loss_prevention_inspect_template.pii) == 0 && length(google_model_armor_floorsetting.project) == 0
    error_message = "Sin model_armor_enabled no se crea ninguna plantilla ni se toca el proyecto."
  }

  assert {
    condition     = google_project_iam_member.app_model_armor.role == "roles/modelarmor.user" && google_project_iam_member.app_dlp.role == "roles/dlp.user"
    error_message = "Los roles de la aplicación no tienen coste: siempre."
  }
}

run "harness_templates" {
  command = apply # con mocks: sin nube; hace falta para leer los valores calculados

  variables {
    model_armor_enabled = true
  }

  assert {
    condition     = google_model_armor_template.harness[0].location == "europe-west4" && startswith(google_data_loss_prevention_inspect_template.payment[0].parent, "projects/genaipocs-test/locations/europe-west4")
    error_message = "Plantillas de Model Armor y de SDP en la región del proyecto."
  }

  assert {
    condition     = google_model_armor_template.harness[0].filter_config[0].sdp_settings[0].advanced_config[0].inspect_template == "projects/genaipocs-test/locations/europe-west4/inspectTemplates/genaipocs-dev-payment"
    error_message = "El SDP avanzado de Model Armor usa la plantilla de datos de pago."
  }

  assert {
    condition     = toset([for t in google_data_loss_prevention_inspect_template.payment[0].inspect_config[0].info_types : t.name]) == toset(["CREDIT_CARD_NUMBER", "IBAN_CODE"])
    error_message = "Model Armor solo bloquea datos de pago; el contacto lo redacta el harness."
  }

  assert {
    condition     = length(google_data_loss_prevention_inspect_template.pii[0].inspect_config[0].info_types) == 9 && !google_data_loss_prevention_inspect_template.pii[0].inspect_config[0].include_quote
    error_message = "Plantilla de PII con los infoTypes del detector y sin devolver el dato."
  }

  assert {
    condition = (
      google_model_armor_template.harness[0].filter_config[0].pi_and_jailbreak_filter_settings[0].confidence_level == "MEDIUM_AND_ABOVE" &&
      alltrue([for f in google_model_armor_template.harness[0].filter_config[0].rai_settings[0].rai_filters : f.confidence_level == "HIGH"])
    )
    error_message = "Inyección en MEDIUM_AND_ABOVE y RAI en HIGH."
  }

  assert {
    condition     = !google_model_armor_template.harness[0].template_metadata[0].log_sanitize_operations && !google_model_armor_template.harness[0].template_metadata[0].ignore_partial_invocation_failures
    error_message = "Sin registros con el texto evaluado y sin ocultar filtros saltados."
  }

  assert {
    condition     = output.guardrails_env.GCP_MODEL_ARMOR__TEMPLATE == "genaipocs-dev-harness" && output.guardrails_env.GCP_SENSITIVE_DATA__INSPECT_TEMPLATE == "genaipocs-dev-pii"
    error_message = "guardrails_env da los ids que espera el .env."
  }

  assert {
    condition     = length(google_project_iam_member.model_armor_dlp) == 2 && length(google_model_armor_floorsetting.project) == 0
    error_message = "El agente de servicio de Model Armor lee las plantillas; sin floor setting por defecto."
  }
}

run "floor_setting_inspect_only" {
  command = apply

  variables {
    model_armor_floor_setting_enabled = true
  }

  assert {
    condition     = google_model_armor_floorsetting.project[0].location == "global" && google_model_armor_floorsetting.project[0].integrated_services == tolist(["AI_PLATFORM"])
    error_message = "Floor setting global aplicada a Agent Platform."
  }

  assert {
    condition     = google_model_armor_floorsetting.project[0].ai_platform_floor_setting[0].inspect_only && !coalesce(google_model_armor_floorsetting.project[0].ai_platform_floor_setting[0].inspect_and_block, false)
    error_message = "Primero medir (INSPECT_ONLY), no bloquear."
  }

  assert {
    condition     = google_project_iam_member.agent_platform_model_armor[0].role == "roles/modelarmor.user"
    error_message = "El agente de servicio de Agent Platform llama a Model Armor."
  }
}
