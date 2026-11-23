output "project_id" {
  value = google_project.this.project_id
}

output "region" {
  value = var.region
}

output "service_url" {
  value = google_cloud_run_v2_service.jawab.uri
}

output "image_repo" {
  value = "${var.region}-docker.pkg.dev/${google_project.this.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "access_code" {
  value     = random_password.access_code.result
  sensitive = true
}
