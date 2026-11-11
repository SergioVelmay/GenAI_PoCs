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

output "enabled_services" {
  value = sort([for s in google_project_service.enabled : s.service])
}
