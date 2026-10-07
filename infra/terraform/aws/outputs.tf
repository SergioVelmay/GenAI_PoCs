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
