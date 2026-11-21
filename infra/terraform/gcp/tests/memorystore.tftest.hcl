# Fase 10.G2: plan simulado (sin credenciales, sin red, sin coste) de Memorystore for Valkey:
# desactivado por defecto; activado, una instancia sin clúster con IAM auth y TLS, su VPC con PSC
# y el rol de conexión para la cuenta de la aplicación.
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
    condition     = length(google_memorystore_instance.scale) == 0 && length(google_compute_network.scale) == 0
    error_message = "Sin memorystore_enabled no se crea ni la instancia ni la red (coste por hora)."
  }

  assert {
    condition     = length(output.scale_redis_env) == 0
    error_message = "Sin instancia, el edge sigue con su Redis local."
  }
}

run "enabled_with_iam_auth_and_tls" {
  command = apply

  variables {
    memorystore_enabled = true
  }

  assert {
    condition = (
      google_memorystore_instance.scale[0].mode == "CLUSTER_DISABLED"
      && google_memorystore_instance.scale[0].shard_count == 1
      && google_memorystore_instance.scale[0].replica_count == 0
      && google_memorystore_instance.scale[0].engine_version == "VALKEY_9_0"
      && google_memorystore_instance.scale[0].node_type == "SHARED_CORE_NANO"
    )
    error_message = "Una instancia pequeña y sin clúster, con el motor de la imagen local (Valkey 9.0)."
  }

  assert {
    condition = (
      google_memorystore_instance.scale[0].authorization_mode == "IAM_AUTH"
      && google_memorystore_instance.scale[0].transit_encryption_mode == "SERVER_AUTHENTICATION"
      && google_memorystore_instance.scale[0].deletion_protection_enabled == false
    )
    error_message = "IAM auth (sin contraseñas), TLS y efímera."
  }

  assert {
    condition     = google_network_connectivity_service_connection_policy.memorystore[0].service_class == "gcp-memorystore"
    error_message = "La política PSC es la que usa Memorystore para crear su endpoint."
  }

  assert {
    condition = (
      google_project_iam_member.app_memorystore[0].role == "roles/memorystore.dbConnectionUser"
      && google_project_iam_member.app_memorystore[0].member == "serviceAccount:genaipocs-dev-app@genaipocs-test.iam.gserviceaccount.com"
    )
    error_message = "La cuenta de la aplicación se conecta con su token (IAM auth)."
  }

  assert {
    condition = (
      output.scale_redis_env["SCALE_REDIS__AUTH"] == "gcp_iam"
      && output.scale_redis_env["SCALE_REDIS__TLS"] == "true"
      && output.scale_redis_env["SEMANTIC_CACHE__BACKEND"] == "redis"
    )
    error_message = "La salida lleva al edge a IAM auth, TLS y el backend compartido."
  }
}

run "rejects_a_non_valkey_engine" {
  command = plan

  variables {
    memorystore_enabled        = true
    memorystore_engine_version = "REDIS_7_2"
  }

  expect_failures = [var.memorystore_engine_version]
}
