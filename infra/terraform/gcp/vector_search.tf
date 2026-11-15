# Fase 3.G2: carril B de GCP con Agent Retrieval (antes Vector Search 2.0). Equivale al índice de
# AI Search (azure/main.tf) y al bucket + índice de S3 Vectors (aws/main.tf).
#
# Coste: EFÍMERA (`vector_search_enabled`), como AI Search. Agent Retrieval se factura por uso
# (almacenamiento por GiB-hora, lecturas y escrituras) y, si se crea un índice ANN, por capacidad
# de servicio. Aquí solo hay colección (búsqueda exacta, suficiente para ~150 chunks): el coste
# real se mide en la primera validación (docs/pendientes-cloud.md). Al terminar la sesión,
# `vector_search_enabled = false` borra la colección y sus datos; el ledger del indexer se olvida
# con el mismo procedimiento que AI Search.

resource "google_vector_search_collection" "knowledge" {
  count = var.vector_search_enabled ? 1 : 0

  collection_id = var.vector_search_collection
  location      = var.region
  display_name  = "Conocimiento de Headache (carril B)"
  labels        = local.labels

  # El vector lo calcula el indexer (gemini-embedding-001 a 1024, `RETRIEVAL_DOCUMENT`): sin
  # `vertex_embedding_config`, la colección no embebe por su cuenta (eso sería "carril A").
  vector_schema {
    field_name = "embedding"
    dense_vector {
      dimensions = var.embedding_dimensions
    }
  }

  # Mismos campos que el índice de AI Search (`adapters/gcp_vector_search.py`): los filtrables
  # como texto, el chunk como número y los metadatos completos en JSON para devolverlos con tipos.
  data_schema = jsonencode({
    type = "object"
    properties = merge(
      {
        chunk_id      = { type = "string" }
        text          = { type = "string" }
        title         = { type = "string" }
        chunk         = { type = "number" }
        metadata_json = { type = "string" }
      },
      { for field in local.vector_search_filterable : field => { type = "string" } },
    )
  })

  depends_on = [google_project_service.enabled]
}

locals {
  vector_search_filterable = ["document_id", "doc_type", "language", "category", "city", "venue_id", "event_id", "acl"]
}

# Escribir, borrar y buscar data objects (no crear ni borrar colecciones: eso es de Terraform).
# Agent Retrieval no tiene IAM por colección en el provider: el rol va a nivel de proyecto y
# solo mientras la colección existe.
resource "google_project_iam_member" "app_vector_search" {
  count = var.vector_search_enabled ? 1 : 0

  project = var.project_id
  role    = "roles/vectorsearch.dataObjectWriter"
  member  = google_service_account.app.member
}
