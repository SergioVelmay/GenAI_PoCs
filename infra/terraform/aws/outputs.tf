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
