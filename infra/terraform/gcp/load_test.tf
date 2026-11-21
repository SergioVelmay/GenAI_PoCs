# Fase 10.G5: generador de carga del edge en un Cloud Run job, con el mismo locustfile que en
# local (`loadtests/Dockerfile`). Desactivado por defecto (load_test_enabled).
#
#   Azure                          AWS                                   GCP
#   Azure Load Testing (gestionado, Distributed Load Testing on AWS      No hay servicio gestionado:
#   admite Locust y JMeter)        (solución con ECS) o Locust en ECS    Locust en un Cloud Run job
#
# Por qué un job con una máquina grande y no Locust distribuido: el modo distribuido necesita que
# los workers hablen con el maestro (ZeroMQ), y en Cloud Run no hay red entre instancias. Con
# `--processes -1`, el contenedor arranca un maestro y un worker por CPU (8 vCPU ≈ 8 generadores):
# lo que en local se quedaba corto por compartir CPU con el edge (sección 3 del doc). Para más,
# GKE Autopilot efímero con maestro y workers (la guía antigua de Google está obsoleta).
#
# Resultados: los CSV de cada ejecución en Cloud Storage (`<ejecución>/<perfil>_stats.csv`...), que
# `scripts/load_report.py` convierte en el mismo informe que en local.
#
# Coste: solo mientras corre (vCPU y memoria por segundo: ~0,05 $ por una prueba de 5 min con 8
# vCPU) + el bucket (céntimos). Lo caro es lo que se prueba (el edge y, si no es el simulado, el
# modelo): nunca contra un modelo real sin presupuesto acordado (scale.instructions.md).

locals {
  load_test = var.load_test_enabled && var.load_test_image != "" ? 1 : 0
  # Contra el edge tras Cloud Armor si está activo (prueba también el perímetro); si no, la URL dada.
  load_test_host = var.load_test_host != "" ? var.load_test_host : (
    local.edge_armor == 1 ? (
      local.edge_https == 1 ? "https://${var.edge_domain}" : "http://${google_compute_global_address.edge[0].address}"
    ) : ""
  )
}

resource "google_service_account" "load_test" {
  count = local.load_test

  account_id   = "${local.name}-load"
  display_name = "Genaipocs ${var.environment}: generador de carga"
  description  = "Cloud Run job de Locust: escribe sus resultados y lee la clave de firma de tokens de prueba."
}

resource "google_storage_bucket" "load_results" {
  count = local.load_test

  name                        = "${var.project_id}-load-results"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  labels                      = local.labels

  # Los CSV se descargan al terminar; no hace falta guardarlos más.
  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "load_results_writer" {
  count = local.load_test

  bucket = google_storage_bucket.load_results[0].name
  role   = "roles/storage.objectUser"
  member = google_service_account.load_test[0].member
}

# La clave privada de desarrollo con la que el locustfile firma los tokens de los usuarios de
# prueba (el edge confía en su pública). En Secret Manager, nunca en la imagen.
resource "google_secret_manager_secret_iam_member" "load_test_signing_key" {
  count = local.load_test == 1 && var.load_test_signing_key_secret != "" ? 1 : 0

  secret_id = var.load_test_signing_key_secret
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.load_test[0].member
}

resource "google_cloud_run_v2_job" "load_test" {
  count = local.load_test

  name                = "${local.name}-load"
  location            = var.region
  labels              = local.labels
  deletion_protection = false

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.load_test[0].email
      max_retries     = 0 # Una prueba de carga fallida no se repite sola.
      timeout         = "1800s"

      containers {
        image   = var.load_test_image
        command = ["sh", "-c"]
        # Una carpeta por ejecución: lanzar otra no pisa los resultados de la anterior.
        args = [join(" ", [
          "exec locust -f loadtests/locustfile.py --processes -1 --headless",
          "--host \"$LOAD_HOST\" --csv \"/results/$CLOUD_RUN_EXECUTION/$LOAD_PROFILE\"",
          "--only-summary",
        ])]

        resources {
          limits = {
            cpu    = tostring(var.load_test_cpu)
            memory = "${var.load_test_cpu}Gi"
          }
        }

        env {
          name  = "LOAD_HOST"
          value = local.load_test_host
        }
        env {
          name  = "LOAD_PEAK"
          value = tostring(var.load_test_peak)
        }
        env {
          name  = "LOAD_PROFILE"
          value = var.load_test_profile
        }
        env {
          name  = "EDGE__DEV_PRIVATE_KEY_FILE"
          value = "/run/secrets/genaipocs/signing-key.pem"
        }

        volume_mounts {
          name       = "results"
          mount_path = "/results"
        }

        dynamic "volume_mounts" {
          for_each = var.load_test_signing_key_secret != "" ? [1] : []
          content {
            name       = "signing-key"
            mount_path = "/run/secrets/genaipocs"
          }
        }
      }

      volumes {
        name = "results"
        gcs {
          bucket    = google_storage_bucket.load_results[0].name
          read_only = false
        }
      }

      dynamic "volumes" {
        for_each = var.load_test_signing_key_secret != "" ? [1] : []
        content {
          name = "signing-key"
          secret {
            secret = var.load_test_signing_key_secret
            items {
              path    = "signing-key.pem"
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition     = local.load_test_host != ""
      error_message = "load_test_enabled necesita load_test_host o el edge tras Cloud Armor (edge_armor_enabled)."
    }
  }

  depends_on = [google_storage_bucket_iam_member.load_results_writer]
}
