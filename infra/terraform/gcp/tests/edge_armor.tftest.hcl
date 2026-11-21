# Fase 10.G4: plan simulado (sin credenciales, sin red, sin coste) del edge tras un balanceador
# con Cloud Armor: desactivado por defecto; con imagen, el servicio solo admite tráfico del
# balanceador y la política tiene baneo por IP y topes por IP (sin token) y por token.
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
      email  = "genaipocs-dev-app@genaipocs-test.iam.gserviceaccount.com"
      member = "serviceAccount:genaipocs-dev-app@genaipocs-test.iam.gserviceaccount.com"
      name   = "projects/genaipocs-test/serviceAccounts/genaipocs-dev-app@genaipocs-test.iam.gserviceaccount.com"
    }
  }

  mock_resource "google_compute_global_address" {
    defaults = {
      address = "203.0.113.10"
    }
  }
}

mock_provider "google-beta" {}

variables {
  project_id      = "genaipocs-test"
  billing_account = "000000-000000-000000"
  budget_email    = "alertas@genaipocs.example"
}

run "disabled_by_default" {
  command = plan

  assert {
    condition     = length(google_compute_security_policy.edge) == 0 && length(google_compute_global_forwarding_rule.edge) == 0
    error_message = "Sin edge_armor_enabled no hay balanceador (coste fijo) ni política."
  }

  assert {
    condition     = output.edge_url == null
    error_message = "Sin balanceador no hay URL pública."
  }
}

run "edge_behind_armor_over_http_without_domain" {
  command = apply

  variables {
    edge_armor_enabled = true
    edge_image         = "europe-west4-docker.pkg.dev/genaipocs-test/edge/edge-api:test"
  }

  assert {
    condition     = google_cloud_run_v2_service.edge[0].ingress == "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
    error_message = "Solo tráfico del balanceador: la URL run.app no se salta Cloud Armor."
  }

  assert {
    condition     = google_compute_backend_service.edge[0].security_policy == google_compute_security_policy.edge[0].id
    error_message = "La política de Cloud Armor va en el backend del edge."
  }

  assert {
    condition = (
      length([for r in google_compute_security_policy.edge[0].rule : r if r.action == "rate_based_ban"]) == 1
      && length([for r in google_compute_security_policy.edge[0].rule : r if r.action == "throttle"]) == 2
    )
    error_message = "Un baneo temporal por IP y dos topes (sin token por IP, con token por cabecera)."
  }

  assert {
    condition = alltrue([
      for r in google_compute_security_policy.edge[0].rule :
      r.rate_limit_options[0].exceed_action == "deny(429)" if r.action != "allow"
    ])
    error_message = "Se responde 429 (con reintento posible), como el rate limit del edge."
  }

  assert {
    condition = length([
      for r in google_compute_security_policy.edge[0].rule : r
      if r.action == "throttle" && r.rate_limit_options[0].enforce_on_key == "HTTP_HEADER"
      && r.rate_limit_options[0].enforce_on_key_name == "Authorization"
    ]) == 1
    error_message = "Con token, el tope es por token y no por IP (muchos usuarios tras un NAT)."
  }

  assert {
    condition     = length(google_compute_target_http_proxy.edge) == 1 && length(google_compute_managed_ssl_certificate.edge) == 0
    error_message = "Sin dominio: HTTP en el 80 para validar, sin certificado."
  }

  assert {
    condition     = output.edge_url == "http://203.0.113.10"
    error_message = "La URL es la IP del balanceador."
  }
}

run "https_with_a_domain" {
  command = plan

  variables {
    edge_armor_enabled = true
    edge_image         = "europe-west4-docker.pkg.dev/genaipocs-test/edge/edge-api:test"
    edge_domain        = "edge.genaipocs.example"
  }

  assert {
    condition = (
      length(google_compute_managed_ssl_certificate.edge) == 1
      && length(google_compute_target_http_proxy.edge) == 0
      && google_compute_global_forwarding_rule.edge[0].port_range == "443"
    )
    error_message = "Con dominio: certificado gestionado y HTTPS en el 443."
  }
}
