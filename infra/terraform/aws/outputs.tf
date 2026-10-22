output "sqs_queue_url" {
  value = aws_sqs_queue.knowledge.url
}

output "sqs_dlq_url" {
  value = aws_sqs_queue.knowledge_dlq.url
}

output "vector_bucket_name" {
  description = "Valor para S3_VECTORS__BUCKET_NAME."
  value       = aws_s3vectors_vector_bucket.knowledge.vector_bucket_name
}

output "vector_index_arn" {
  value = aws_s3vectors_index.knowledge.index_arn
}

output "indexer_worker_policy_arn" {
  value = aws_iam_policy.indexer_worker.arn
}

output "account_id" {
  value = data.aws_caller_identity.current.account_id
}

output "mcp_gateway_url" {
  description = "Valor para MCP__GATEWAY_URL (vacío si el gateway está desactivado)."
  value       = one(aws_bedrockagentcore_gateway.mcp[*].gateway_url)
}

output "agentcore_memory_id" {
  description = "Valor para AGENTCORE__MEMORY_ID (vacío si AgentCore está desactivado)."
  value       = one(aws_bedrockagentcore_memory.booking[*].id)
}

output "agentcore_runtime_arn" {
  description = "Valor para AGENTCORE__RUNTIME_ARN (vacío sin imagen)."
  value       = one(aws_bedrockagentcore_agent_runtime.booking[*].agent_runtime_arn)
}

output "kb_docs_bucket" {
  description = "Valor para BEDROCK_KB__DOCS_BUCKET."
  value       = aws_s3_bucket.docs.bucket
}

output "knowledge_base_id" {
  description = "Valor para BEDROCK_KB__KNOWLEDGE_BASE_ID."
  value       = aws_bedrockagent_knowledge_base.main.id
}

output "kb_data_source_id" {
  description = "Valor para BEDROCK_KB__DATA_SOURCE_ID."
  value       = aws_bedrockagent_data_source.docs.data_source_id
}

output "observability" {
  description = "Grupos de logs y política del collector ADOT (null si observability_enabled = false)."
  value = var.observability_enabled ? {
    app_log_group         = aws_cloudwatch_log_group.app[0].name
    metrics_log_group     = aws_cloudwatch_log_group.metrics[0].name
    collector_policy_arn  = aws_iam_policy.otel_collector[0].arn
    alerts_topic_arn      = aws_sns_topic.alerts[0].arn
    cloudwatch_metrics_ns = local.metrics_ns
  } : null
}

output "bedrock_guardrail" {
  description = "BEDROCK_GUARDRAIL__GUARDRAIL_ID y __VERSION (null si está desactivado)."
  value = var.bedrock_guardrail_enabled ? {
    guardrail_id = aws_bedrock_guardrail.harness[0].guardrail_id
    version      = aws_bedrock_guardrail_version.harness[0].version
  } : null
}
