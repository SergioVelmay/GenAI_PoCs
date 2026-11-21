# Fase 10.G5: plan simulado (sin credenciales, sin red, sin coste) del generador de carga: un
# Cloud Run job de Locust con un proceso por vCPU, resultados en Cloud Storage y, por defecto,
# contra el edge tras Cloud Armor.
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
      email  = "genaipocs-dev-load@genaipocs-test.iam.gserviceaccount.com"
      member = "serviceAccount:genaipocs-dev-load@genaipocs-test.iam.gserviceaccount.com"
      name   = "projects/genaipocs-test/serviceAccounts/genaipocs-dev-load@genaipocs-test.iam.gserviceaccount.com"
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
    condition     = length(google_cloud_run_v2_job.load_test) == 0 && length(google_storage_bucket.load_results) == 0
    error_message = "Sin load_test_enabled no hay job ni bucket."
  }
}

run "needs_a_target" {
  command = plan

  variables {
    load_test_enabled = true
    load_test_image   = "europe-west4-docker.pkg.dev/genaipocs-test/load/genaipocs-load:test"
  }

  expect_failures = [google_cloud_run_v2_job.load_test]
}

run "against_the_edge_behind_armor" {
  command = apply

  variables {
    load_test_enabled            = true
    load_test_image              = "europe-west4-docker.pkg.dev/genaipocs-test/load/genaipocs-load:test"
    load_test_signing_key_secret = "edge-dev-signing-key"
    edge_armor_enabled           = true
    edge_image                   = "europe-west4-docker.pkg.dev/genaipocs-test/edge/edge-api:test"
  }

  assert {
    condition = contains([
      for e in google_cloud_run_v2_job.load_test[0].template[0].template[0].containers[0].env : e.value if e.name == "LOAD_HOST"
    ], "http://203.0.113.10")
    error_message = "Sin load_test_host, la carga va al edge tras Cloud Armor (prueba también el perímetro)."
  }

  assert {
    condition = (
      strcontains(google_cloud_run_v2_job.load_test[0].template[0].template[0].containers[0].args[0], "--processes -1")
      && google_cloud_run_v2_job.load_test[0].template[0].template[0].containers[0].resources[0].limits["cpu"] == "8"
      && google_cloud_run_v2_job.load_test[0].template[0].task_count == 1
    )
    error_message = "Una tarea con 8 vCPU y un proceso de Locust por vCPU (sin red entre instancias)."
  }

  assert {
    condition     = google_cloud_run_v2_job.load_test[0].template[0].template[0].max_retries == 0
    error_message = "Una prueba de carga fallida no se repite sola (cada intento cuesta)."
  }

  assert {
    condition     = length(google_secret_manager_secret_iam_member.load_test_signing_key) == 1
    error_message = "La clave de los tokens de prueba se lee de Secret Manager, no va en la imagen."
  }

  assert {
    condition     = google_storage_bucket.load_results[0].public_access_prevention == "enforced"
    error_message = "Los resultados no son públicos."
  }
}
