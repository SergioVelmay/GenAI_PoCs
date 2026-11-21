# Fase 10.G4: plan simulado (sin credenciales, sin red, sin coste) del worker pool de triaje con
# CREMA: desactivado por defecto; con imagen, un worker pool que arranca en 0 y que Terraform no
# vuelve a poner a 0, y CREMA con el escalador `prometheus` sobre el backlog de Pub/Sub.
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
      email  = "genaipocs-dev-crema@genaipocs-test.iam.gserviceaccount.com"
      member = "serviceAccount:genaipocs-dev-crema@genaipocs-test.iam.gserviceaccount.com"
      name   = "projects/genaipocs-test/serviceAccounts/genaipocs-dev-crema@genaipocs-test.iam.gserviceaccount.com"
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
    condition     = length(google_cloud_run_v2_worker_pool.triage) == 0 && length(google_cloud_run_v2_service.crema) == 0
    error_message = "Sin queue_workers_enabled no hay worker pool ni CREMA (CREMA tiene coste fijo)."
  }
}

run "enabled_without_image_creates_nothing" {
  command = plan

  variables {
    queue_workers_enabled = true
  }

  assert {
    condition     = length(google_cloud_run_v2_worker_pool.triage) == 0
    error_message = "Sin imagen del orquestador no se despliega nada."
  }
}

run "worker_pool_scaled_by_crema" {
  command = apply

  variables {
    queue_workers_enabled = true
    queue_worker_image    = "europe-west4-docker.pkg.dev/genaipocs-test/agents/orchestrator:test"
  }

  assert {
    condition = (
      google_cloud_run_v2_worker_pool.triage[0].scaling[0].scaling_mode == "MANUAL"
      && google_cloud_run_v2_worker_pool.triage[0].scaling[0].manual_instance_count == 0
    )
    error_message = "El worker pool arranca en 0 y en modo manual: lo mueve CREMA, no Cloud Run."
  }

  assert {
    condition     = google_cloud_run_v2_worker_pool.triage[0].template[0].containers[0].args == tolist(["triage", "relay"])
    error_message = "Mismo rol que en Azure y AWS: triaje + relay del outbox."
  }

  assert {
    condition     = strcontains(google_parameter_manager_parameter_version.crema[0].parameter_data, "\"type\": \"prometheus\"")
    error_message = "CREMA usa el escalador prometheus (gcp-pubsub está obsoleto en KEDA 2.21)."
  }

  assert {
    condition     = strcontains(google_parameter_manager_parameter_version.crema[0].parameter_data, "subscription_num_unacked_messages_by_region")
    error_message = "La métrica es la del adaptador local (pubsub_backlog_promql)."
  }

  assert {
    condition     = strcontains(google_parameter_manager_parameter_version.crema[0].parameter_data, "workerpools/genaipocs-dev-triage-worker")
    error_message = "CREMA apunta al worker pool de triaje."
  }

  assert {
    condition = (
      google_cloud_run_v2_service.crema[0].template[0].containers[0].resources[0].cpu_idle == false
      && google_cloud_run_v2_service.crema[0].template[0].scaling[0].min_instance_count == 1
      && google_cloud_run_v2_service.crema[0].ingress == "INGRESS_TRAFFIC_INTERNAL_ONLY"
    )
    error_message = "CREMA sondea siempre (CPU asignada, una instancia) y no recibe tráfico."
  }

  assert {
    condition = (
      google_cloud_run_v2_worker_pool_iam_member.crema_developer[0].role == "roles/run.developer"
      && contains(keys(google_project_iam_member.crema), "roles/monitoring.viewer")
      && !contains(keys(google_project_iam_member.crema), "roles/run.admin")
    )
    error_message = "CREMA lee la métrica y solo puede cambiar este worker pool."
  }

  assert {
    condition     = output.queue_workers.backlog_promql == "sum(pubsub_googleapis_com:subscription_num_unacked_messages_by_region{monitored_resource=\"pubsub_subscription\",project_id=\"genaipocs-test\",subscription_id=\"support.emails.worker\"})"
    error_message = "La consulta coincide con pubsub_backlog_promql('genaipocs-test', 'support.emails.worker')."
  }
}
