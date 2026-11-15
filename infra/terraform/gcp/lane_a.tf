# Fase 3.G3: carril A de GCP, RAG Engine importando desde Cloud Storage. Equivale a la Bedrock
# Knowledge Base de aws/lane_a.tf y al indexer + skillset de AI Search de azure/lane_a.tf.
#
# Coste:
# - Bucket: ~0 $ (116 documentos pequeños). Siempre existe, para que la subida idempotente por MD5
#   sobreviva entre sesiones.
# - RAG Engine: el nivel Basic crea un Spanner Enterprise de 100 PU con backup que se factura POR
#   HORAS aunque nadie consulte. EFÍMERO (`rag_engine_enabled`): Basic durante la sesión y
#   `Unprovisioned` al terminar, que borra los datos y para la facturación (hay que reimportar en
#   la siguiente sesión, como al recrear AI Search).
# - Embeddings: los del modelo en cada importación (céntimos para este corpus).

locals {
  rag_acl_levels = ["public", "internal"]
  # Agente de servicio de RAG Engine: es quien lee el bucket al importar. Se crea con el primer
  # corpus, por eso sus permisos dependen de los corpus.
  rag_service_agent = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-vertex-rag.iam.gserviceaccount.com"
}

resource "google_storage_bucket" "rag_docs" {
  # Los nombres de bucket son globales: el ID de proyecto (único) los hace únicos.
  name     = "${var.project_id}-rag-docs"
  location = var.region
  labels   = local.labels

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  # Es una copia del corpus, regenerable con managed_ingestion.py: se puede borrar con datos.
  force_destroy = true

  depends_on = [google_project_service.enabled]
}

# Singleton por proyecto y región: siempre gestionado, para que apagar sea explícito y no dependa
# de que Terraform sepa "borrar" una configuración.
resource "google_vertex_ai_rag_engine_config" "this" {
  region = var.region

  rag_managed_db_config {
    dynamic "basic" {
      for_each = var.rag_engine_enabled ? [1] : []
      content {}
    }
    dynamic "unprovisioned" {
      for_each = var.rag_engine_enabled ? [] : [1]
      content {}
    }
  }

  depends_on = [google_project_service.enabled]
}

# Un corpus por nivel de ACL. El filtro por metadatos de RAG Engine está en preview (v1beta1), así
# que la ACL se garantiza separando los documentos, no filtrándolos (adapters/gcp_rag_engine.py).
resource "google_vertex_ai_rag_corpus" "knowledge" {
  for_each = var.rag_engine_enabled ? toset(local.rag_acl_levels) : toset([])

  region       = var.region
  display_name = "genaipocs-knowledge-${each.key}"
  description  = "Conocimiento de Genaipocs con ACL ${each.key} (carril A)"

  vector_db_config {
    # Búsqueda exacta (KNN): para ~150 chunks, un índice ANN no aporta nada.
    rag_managed_db {
      knn {}
    }
    rag_embedding_model_config {
      vertex_prediction_endpoint {
        endpoint = "projects/${var.project_id}/locations/${var.region}/publishers/google/models/${var.rag_embedding_model}"
      }
    }
  }

  depends_on = [google_vertex_ai_rag_engine_config.this]
}

resource "google_storage_bucket_iam_member" "rag_service_agent_read" {
  count = var.rag_engine_enabled ? 1 : 0

  bucket = google_storage_bucket.rag_docs.name
  role   = "roles/storage.objectViewer"
  member = local.rag_service_agent

  depends_on = [google_vertex_ai_rag_corpus.knowledge]
}

# ---------- Permisos de la app (managed_ingestion.py gcp) ----------

# Subir, listar y borrar objetos (la poda de lo que cambió de ACL o ya no existe).
resource "google_storage_bucket_iam_member" "app_rag_docs" {
  bucket = google_storage_bucket.rag_docs.name
  role   = "roles/storage.objectAdmin"
  member = google_service_account.app.member
}

# `import_files` comprueba que el bucket es del proyecto (contra el *bucket squatting*): necesita
# `storage.buckets.get` en el bucket y `resourcemanager.projects.get` en el proyecto. Sin ellos,
# el SDK no da un 403: dice que el bucket "no pertenece al proyecto".
resource "google_storage_bucket_iam_member" "app_rag_docs_bucket_get" {
  bucket = google_storage_bucket.rag_docs.name
  role   = "roles/storage.legacyBucketReader"
  member = google_service_account.app.member
}

resource "google_project_iam_member" "app_project_get" {
  count = var.rag_engine_enabled ? 1 : 0

  project = var.project_id
  role    = "roles/browser"
  member  = google_service_account.app.member
}
