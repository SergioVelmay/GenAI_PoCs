output "project_number" {
  value = data.google_project.current.number
}

output "app_service_account" {
  description = "GCP__IMPERSONATE_SERVICE_ACCOUNT en el .env local."
  value       = google_service_account.app.email
}

output "ci_service_account" {
  description = "service_account de google-github-actions/auth (fase 15)."
  value       = var.ci_federation_enabled ? google_service_account.ci[0].email : null
}

output "ci_workload_identity_provider" {
  description = "workload_identity_provider de google-github-actions/auth (fase 15)."
  value       = var.ci_federation_enabled ? google_iam_workload_identity_pool_provider.github[0].name : null
}

output "knowledge_topic" {
  description = "INDEXER__TOPIC con INDEXER__BROKER=pubsub (la suscripción es <tema>.worker)."
  value       = google_pubsub_topic.knowledge.name
}

output "case_topics" {
  description = "Temas del flujo de casos con ORCHESTRATOR__BROKER=pubsub (suscripciones <tema>.worker con exactly-once)."
  value       = sort([for t in google_pubsub_topic.cases : t.name])
}

output "a2a_env" {
  description = "Variables del orquestador para llamar a los especialistas en Cloud Run (vacío sin a2a_enabled)."
  value = length(local.a2a_urls) == 0 ? {} : {
    A2A__IDENTITY = "gcp"
    A2A__URLS     = jsonencode(local.a2a_urls)
  }
}

output "case_review_workflow" {
  description = "Workflow de la espera humana con SLA (null sin case_review_workflow_enabled)."
  value       = var.case_review_workflow_enabled ? google_workflows_workflow.case_review[0].name : null
}

output "enabled_services" {
  value = sort([for s in google_project_service.enabled : s.service])
}

output "vector_search_collection" {
  description = "GCP_VECTOR_SEARCH__COLLECTION (null mientras vector_search_enabled = false)."
  value       = var.vector_search_enabled ? google_vector_search_collection.knowledge[0].collection_id : null
}

output "rag_docs_bucket" {
  description = "GCP_RAG_ENGINE__BUCKET."
  value       = google_storage_bucket.rag_docs.name
}

output "mcp_gateway_service_account" {
  description = "MCP__SIGNER_SERVICE_ACCOUNT del gateway y MCP__ISSUER de los servidores (6.G2)."
  value       = google_service_account.mcp_gateway.email
}

output "mcp_env" {
  description = "Variables de los servidores MCP y del gateway con MCP__EXCHANGER=gcp (6.G2)."
  value = {
    MCP__ISSUER                       = google_service_account.mcp_gateway.email
    MCP__JWKS_URI                     = "https://www.googleapis.com/service_accounts/v1/metadata/jwk/${google_service_account.mcp_gateway.email}"
    MCP__AUTHORIZATION_SERVER         = "https://securetoken.google.com/${var.project_id}"
    MCP__GATEWAY_AUTH__ISSUER         = "https://securetoken.google.com/${var.project_id}"
    MCP__GATEWAY_AUTH__JWKS_URI       = "https://www.googleapis.com/service_accounts/v1/metadata/jwk/securetoken@system.gserviceaccount.com"
    MCP__GATEWAY_AUTH__AUDIENCE       = var.project_id
    MCP__GATEWAY_AUTH__REQUIRE_SCOPES = "false"
    MCP__GATEWAY_AUTH__CUSTOMER_CLAIM = "customer_id"
    MCP__EXCHANGER                    = "gcp"
    MCP__SIGNER_SERVICE_ACCOUNT       = google_service_account.mcp_gateway.email
  }
}

output "agent_gateway" {
  description = "Agent Gateway de salida (null mientras agent_gateway_enabled = false)."
  value       = var.agent_gateway_enabled ? google_network_services_agent_gateway.mcp[0].id : null
}

output "agent_platform_env" {
  description = "GCP_AGENT_PLATFORM__MEMORY_ENGINE y RUNTIME_ENGINE del .env (vacías mientras agent_platform_enabled = false)."
  value = {
    GCP_AGENT_PLATFORM__MEMORY_ENGINE  = one(google_vertex_ai_reasoning_engine.memory[*].name)
    GCP_AGENT_PLATFORM__RUNTIME_ENGINE = one(google_vertex_ai_reasoning_engine.booking[*].name)
  }
}

output "agent_identity" {
  description = "Principal de Agent Identity del agente: el calculado (el de los permisos) y el que informa el servicio; deben coincidir."
  value = local.agent_platform_runtime == 1 ? {
    principal          = local.agent_principal
    effective_identity = google_vertex_ai_reasoning_engine.booking[0].spec[0].effective_identity
  } : null
}

output "apigee_instance" {
  description = "Host y service attachment de la instancia de Apigee (null mientras apigee_enabled = false)."
  value = var.apigee_enabled ? {
    host               = google_apigee_instance.mcp[0].host
    service_attachment = google_apigee_instance.mcp[0].service_attachment
  } : null
}

output "agent_search_engine" {
  description = "GCP_AGENT_SEARCH__ENGINE (null mientras agent_search_enabled = false)."
  value       = var.agent_search_enabled ? google_discovery_engine_search_engine.knowledge[0].engine_id : null
}

output "rag_corpora" {
  description = "GCP_RAG_ENGINE__PUBLIC_CORPUS e INTERNAL_CORPUS ({} mientras rag_engine_enabled = false)."
  value       = { for acl, corpus in google_vertex_ai_rag_corpus.knowledge : acl => corpus.name }
}

output "guardrails_env" {
  description = "Plantillas del harness con GUARDRAILS__SHIELD=gcp y GUARDRAILS__PII=gcp (vacías mientras model_armor_enabled = false)."
  value = {
    GCP_MODEL_ARMOR__TEMPLATE            = one(google_model_armor_template.harness[*].template_id)
    GCP_MODEL_ARMOR__LOCATION            = one(google_model_armor_template.harness[*].location)
    GCP_SENSITIVE_DATA__INSPECT_TEMPLATE = one(google_data_loss_prevention_inspect_template.pii[*].template_id)
  }
}

output "scale_redis_env" {
  description = "Variables del edge para usar Memorystore (vacío sin memorystore_enabled). La CA, en memorystore_server_ca."
  value = var.memorystore_enabled ? {
    SCALE_REDIS__HOST = try(google_memorystore_instance.scale[0].endpoints[0].connections[0].psc_auto_connection[0].ip_address, "")
    SCALE_REDIS__PORT = tostring(try(google_memorystore_instance.scale[0].endpoints[0].connections[0].psc_auto_connection[0].port, 6379))
    SCALE_REDIS__AUTH = "gcp_iam"
    SCALE_REDIS__TLS  = "true"
    # Guardar la salida memorystore_server_ca en un fichero y apuntar aquí su ruta.
    SCALE_REDIS__TLS_CA_FILE = "memorystore-server-ca.pem"
    RATE_LIMIT__BACKEND      = "redis"
    SEMANTIC_CACHE__BACKEND  = "redis"
  } : {}
}

output "memorystore_server_ca" {
  description = "CA del servidor de Memorystore en PEM (pública): terraform output -raw memorystore_server_ca > memorystore-server-ca.pem"
  value       = var.memorystore_enabled ? join("\n", flatten([for ca in try(google_memorystore_instance.scale[0].managed_server_ca[0].ca_certs, []) : ca.certificates])) : ""
}

output "queue_workers" {
  description = "Worker pool de triaje, servicio de CREMA y su configuración (null sin queue_workers_enabled)."
  value = local.queue_workers == 1 ? {
    worker_pool    = google_cloud_run_v2_worker_pool.triage[0].name
    crema_service  = google_cloud_run_v2_service.crema[0].name
    crema_config   = google_parameter_manager_parameter_version.crema[0].id
    backlog_promql = local.queue_backlog_query
  } : null
}

output "edge_url" {
  description = "URL pública del edge tras el balanceador con Cloud Armor (null sin edge_armor_enabled)."
  value = local.edge_armor == 1 ? (
    local.edge_https == 1 ? "https://${var.edge_domain}" : "http://${google_compute_global_address.edge[0].address}"
  ) : null
}

output "load_test" {
  description = "Cómo lanzar la prueba de carga y bajar los CSV (null sin load_test_enabled)."
  value = local.load_test == 1 ? {
    run      = "gcloud run jobs execute ${google_cloud_run_v2_job.load_test[0].name} --region ${var.region} --wait"
    results  = "gcloud storage cp -r gs://${google_storage_bucket.load_results[0].name}/<ejecución> evals/reports/load/gcp/"
    host     = local.load_test_host
    peak     = var.load_test_peak
    profile  = var.load_test_profile
    vcpu     = var.load_test_cpu
    report   = "uv run python scripts/load_report.py evals/reports/load/gcp/<ejecución>/${var.load_test_profile} --out evals/reports/load/gcp/README.md"
    see_also = "docs/fase-10-escala.md (10.G5)"
  } : null
}

output "observability" {
  description = "Variables del collector de GCP y canal de guardia (null si observability_enabled = false)."
  value = var.observability_enabled ? {
    collector_config     = "infra/otel/collector-gcp.yaml"
    collector_env        = { GOOGLE_CLOUD_PROJECT = var.project_id, ENVIRONMENT = var.environment }
    collector_identity   = google_service_account.app.email
    oncall_channel       = google_monitoring_notification_channel.oncall[0].id
    log_retention_days   = var.log_retention_days
    log_exclusions       = keys(local.log_exclusions)
    trace_explorer       = "https://console.cloud.google.com/traces/explorer?project=${var.project_id}"
    prometheus_query_api = "https://monitoring.googleapis.com/v1/projects/${var.project_id}/location/global/prometheus"
  } : null
}
