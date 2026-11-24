# Jawab on Google Cloud: an isolated project, least-privilege runtime identity,
# Firestore, Secret Manager, Artifact Registry and a single-instance Cloud Run service,
# guarded by a billing budget alert.

terraform {
  required_version = ">= 1.9"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

provider "google" {
  region = var.region
}

# Budgets API calls must name a quota project; attribute them to the new project.
provider "google" {
  alias                 = "billing"
  region                = var.region
  user_project_override = true
  billing_project       = local.project_id
}

resource "random_id" "suffix" {
  byte_length = 2
}

locals {
  project_id = "${var.project_prefix}-${random_id.suffix.hex}"
  service    = "jawab"
  image      = var.image != "" ? var.image : "us-docker.pkg.dev/cloudrun/container/hello"
  public_url = "https://${local.service}-${google_project.this.number}.${var.region}.run.app"
  labels     = { app = "jawab", event = "build2036", managed-by = "terraform" }
}

# ------------------------------------------------------------------ project

resource "google_project" "this" {
  project_id      = local.project_id
  name            = "Jawab Build2036"
  billing_account = var.billing_account
  labels          = local.labels
  deletion_policy = "DELETE"
}

resource "google_project_service" "apis" {
  for_each = toset([
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudbuild.googleapis.com",
    "firestore.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sts.googleapis.com",
  ])
  project            = google_project.this.project_id
  service            = each.value
  disable_on_destroy = false
}

# ------------------------------------------------------------------ budget guard

resource "google_billing_budget" "guard" {
  provider        = google.billing
  billing_account = var.billing_account
  display_name    = "jawab-build2036-guard"

  budget_filter {
    projects = ["projects/${google_project.this.number}"]
  }
  amount {
    specified_amount {
      currency_code = "INR"
      units         = tostring(var.budget_inr)
    }
  }
  dynamic "threshold_rules" {
    for_each = [0.25, 0.5, 0.9, 1.0]
    content {
      threshold_percent = threshold_rules.value
    }
  }
  depends_on = [google_project_service.apis]
}

# ------------------------------------------------------------------ storage and images

resource "google_firestore_database" "default" {
  project         = google_project.this.project_id
  name            = "(default)"
  location_id     = var.region
  type            = "FIRESTORE_NATIVE"
  deletion_policy = "DELETE"
  depends_on      = [google_project_service.apis]
}

resource "google_artifact_registry_repository" "images" {
  project       = google_project.this.project_id
  location      = var.region
  repository_id = "jawab"
  format        = "DOCKER"
  labels        = local.labels

  cleanup_policies {
    id     = "keep-last-5"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }
  depends_on = [google_project_service.apis]
}

# ------------------------------------------------------------------ secrets

resource "random_password" "access_code" {
  length  = 10
  special = false
  upper   = false
}

resource "google_secret_manager_secret" "access_code" {
  project   = google_project.this.project_id
  secret_id = "jawab-access-code"
  labels    = local.labels
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "access_code" {
  secret      = google_secret_manager_secret.access_code.id
  secret_data = random_password.access_code.result
}

# ------------------------------------------------------------------ runtime identity (least privilege)

resource "google_service_account" "runtime" {
  project      = google_project.this.project_id
  account_id   = "jawab-runtime"
  display_name = "Jawab Cloud Run runtime"
}

resource "google_project_iam_member" "runtime" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/datastore.user",
    "roles/logging.logWriter",
  ])
  project = google_project.this.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_reads_code" {
  project   = google_project.this.project_id
  secret_id = google_secret_manager_secret.access_code.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

# Cloud Build's default identity pushes images; grant it only what the build needs.
resource "google_project_iam_member" "build" {
  for_each = toset([
    "roles/artifactregistry.writer",
    "roles/logging.logWriter",
    "roles/storage.objectViewer",
  ])
  project = google_project.this.project_id
  role    = each.value
  member  = "serviceAccount:${google_project.this.number}-compute@developer.gserviceaccount.com"
}

# ------------------------------------------------------------------ Cloud Run

resource "google_cloud_run_v2_service" "jawab" {
  project             = google_project.this.project_id
  name                = local.service
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false
  labels              = local.labels

  template {
    service_account                  = google_service_account.runtime.email
    timeout                          = "3600s" # long-lived WebSocket voice sessions
    session_affinity                 = true
    max_instance_request_concurrency = 40

    scaling {
      min_instance_count = 0
      max_instance_count = 1 # single instance: in-process event bus, bounded spend
    }

    containers {
      image = local.image
      ports {
        container_port = 8080
      }
      resources {
        limits            = { cpu = "1", memory = "1Gi" }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = google_project.this.project_id
      }
      env {
        name  = "STORE"
        value = "firestore"
      }
      env {
        name  = "LIVE_MODEL"
        value = var.live_model
      }
      env {
        name  = "LIVE_MODEL_LOCATION"
        value = var.live_model_location
      }
      env {
        name  = "TEXT_MODEL"
        value = var.text_model
      }
      env {
        name  = "PUBLIC_BASE_URL"
        value = local.public_url
      }
      env {
        name = "ACCESS_CODE"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.access_code.secret_id
            version = "latest"
          }
        }
      }
      startup_probe {
        http_get {
          path = "/health"
        }
        period_seconds    = 3
        failure_threshold = 20
      }
    }
  }

  # The image is rolled out by CI (or scripts/deploy.sh) with `gcloud run services update --image`;
  # Terraform owns everything else about the service.
  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }

  depends_on = [
    google_project_service.apis,
    google_project_iam_member.runtime,
    google_secret_manager_secret_iam_member.runtime_reads_code,
    google_secret_manager_secret_version.access_code,
    google_firestore_database.default,
  ]
}

# Public demo (judges need access); the app itself gates calls and owner actions with the access code.
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = google_project.this.project_id
  location = var.region
  name     = google_cloud_run_v2_service.jawab.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ------------------------------------------------------------------ continuous deployment from GitHub (keyless)

# GitHub Actions exchanges its OIDC token for short-lived credentials; only pushes to main of this repo qualify.
resource "google_iam_workload_identity_pool" "github" {
  project                   = google_project.this.project_id
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = google_project.this.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  attribute_condition = "assertion.repository == \"${var.github_repo}\" && assertion.ref == \"refs/heads/main\""
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "deployer" {
  project      = google_project.this.project_id
  account_id   = "jawab-deployer"
  display_name = "Jawab CI deployer (GitHub Actions)"
}

resource "google_service_account_iam_member" "deployer_from_github" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

# Push images, roll out new revisions of this one service, and run them as the runtime identity.
resource "google_artifact_registry_repository_iam_member" "deployer_pushes" {
  project    = google_project.this.project_id
  location   = var.region
  repository = google_artifact_registry_repository.images.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_cloud_run_v2_service_iam_member" "deployer_deploys" {
  project  = google_project.this.project_id
  location = var.region
  name     = google_cloud_run_v2_service.jawab.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "deployer_acts_as_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

