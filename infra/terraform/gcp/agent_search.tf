# Fase 4.G3: carril A de GCP con Agent Search (antes Vertex AI Search; API Discovery Engine).
# Equivale a Foundry IQ sobre AI Search (azure) y a Bedrock KB con RetrieveAndGenerate (aws).
#
# Coste: sin coste por hora. Enterprise cobra por consulta (~4 $ / 1000; las respuestas
# generativas básicas van incluidas) y el índice es gratis hasta 10 GiB al mes. Aun así va detrás
# de `agent_search_enabled = false`: al terminar la sesión se borra y se reimporta en la
# siguiente (`managed_ingestion.py gcp-search --apply`, reconciliación FULL).
#
# Los documentos son los HTML que sube `managed_ingestion.py gcp-search` al mismo bucket que RAG
# Engine (`lane_a.tf`), con el prefijo `agent-search/`.

locals {
  # Agente de servicio de Discovery Engine: es quien lee el bucket al importar.
  discovery_engine_service_agent = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-discoveryengine.iam.gserviceaccount.com"
}

# Fuerza que exista el agente de servicio antes de darle permisos (se crea al primer uso).
resource "google_project_service_identity" "discovery_engine" {
  count    = var.agent_search_enabled ? 1 : 0
  provider = google-beta

  service = "discoveryengine.googleapis.com"

  depends_on = [google_project_service.enabled]
}

resource "google_discovery_engine_data_store" "knowledge" {
  count = var.agent_search_enabled ? 1 : 0

  data_store_id     = "knowledge"
  display_name      = "Conocimiento de Genaipocs (carril A, Agent Search)"
  location          = var.agent_search_location
  industry_vertical = "GENERIC"
  content_config    = "CONTENT_REQUIRED"
  solution_types    = ["SOLUTION_TYPE_SEARCH"]
  # El esquema lo gestiona `google_discovery_engine_schema.knowledge` (campos filtrables).
  skip_default_schema_creation = true

  # Troceo para RAG (modo CHUNKS de `search` y fragmentos de `answer_query`). El *layout parser*
  # admite HTML pero no TXT ni Markdown: por eso la ingesta sube HTML. 300 tokens, como el troceo
  # de RAG Engine y de Bedrock KB. No se puede cambiar tras crear el data store.
  document_processing_config {
    chunking_config {
      layout_based_chunking_config {
        chunk_size                = 300
        include_ancestor_headings = true
      }
    }
    default_parsing_config {
      layout_parsing_config {}
    }
  }

  depends_on = [google_project_service.enabled]
}

# `structData` de cada documento (`gcp_agent_search.struct_data`): la ACL y los filtros del dominio
# son `indexable` para poder filtrar en el servidor (`acl: ANY("public")`) y `retrievable` para que
# vuelvan con cada fragmento y la app pueda comprobarlos.
resource "google_discovery_engine_schema" "knowledge" {
  count = var.agent_search_enabled ? 1 : 0

  location      = var.agent_search_location
  data_store_id = google_discovery_engine_data_store.knowledge[0].data_store_id
  schema_id     = "default_schema"
  json_schema = jsonencode({
    "$schema" = "https://json-schema.org/draft/2020-12/schema"
    type      = "object"
    properties = merge(
      {
        title = {
          type               = "string"
          keyPropertyMapping = "title"
          retrievable        = true
        }
      },
      {
        for field in ["acl", "document_id", "doc_type", "language", "category", "city", "event_id"] :
        field => {
          type        = "string"
          indexable   = true
          retrievable = true
        }
      },
    )
  })
}

# Motor (app) de búsqueda: Enterprise con el complemento LLM (respuestas generadas con citas).
resource "google_discovery_engine_search_engine" "knowledge" {
  count = var.agent_search_enabled ? 1 : 0

  engine_id         = "knowledge-search"
  collection_id     = "default_collection"
  location          = var.agent_search_location
  display_name      = "Conocimiento de Genaipocs"
  industry_vertical = "GENERIC"
  data_store_ids    = [google_discovery_engine_data_store.knowledge[0].data_store_id]

  search_engine_config {
    search_tier    = "SEARCH_TIER_ENTERPRISE"
    search_add_ons = ["SEARCH_ADD_ON_LLM"]
  }

  depends_on = [google_discovery_engine_schema.knowledge]
}

# ---------- Permisos ----------

# El agente de servicio de Discovery Engine lee los HTML y el manifiesto al importar.
resource "google_storage_bucket_iam_member" "discovery_engine_read" {
  count = var.agent_search_enabled ? 1 : 0

  bucket = google_storage_bucket.rag_docs.name
  role   = "roles/storage.objectViewer"
  member = local.discovery_engine_service_agent

  depends_on = [google_project_service_identity.discovery_engine]
}

# La app importa documentos (`documents.import`). Buscar y responder ya lo cubre
# `roles/discoveryengine.viewer` (identity.tf, 4.G1). Solo mientras existe el data store.
resource "google_project_iam_member" "app_agent_search_import" {
  count = var.agent_search_enabled ? 1 : 0

  project = var.project_id
  role    = "roles/discoveryengine.editor"
  member  = google_service_account.app.member
}
