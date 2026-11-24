variable "billing_account" {
  description = "Billing account that pays for the project (format XXXXXX-XXXXXX-XXXXXX)."
  type        = string
}

variable "project_prefix" {
  description = "Prefix for the new, dedicated project ID (a random suffix is appended)."
  type        = string
  default     = "jawab-b2036"
}

variable "region" {
  description = "Region for Cloud Run, Firestore and Artifact Registry. Kept next to the Live model."
  type        = string
  default     = "us-central1"
}

variable "image" {
  description = "Container image to deploy. Empty deploys a placeholder until the first build."
  type        = string
  default     = ""
}

variable "live_model" {
  type    = string
  default = "gemini-3.8-live"
}

variable "live_model_location" {
  type    = string
  default = "us-central1"
}

variable "text_model" {
  type    = string
  default = "gemini-3.5-flash-lite"
}

variable "budget_inr" {
  description = "Monthly budget (INR) for alert thresholds at 25/50/90/100%."
  type        = number
  default     = 3000
}

variable "github_repo" {
  description = "GitHub repository (owner/name) whose pushes to main may deploy."
  type        = string
  default     = "jayanth-mkv/jawab"
}
