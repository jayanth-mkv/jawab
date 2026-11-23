#!/usr/bin/env bash
# Build the image with Cloud Build and roll it out through Terraform.
#   GCLOUD_ACCOUNT=you@example.com scripts/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

ACCOUNT="${GCLOUD_ACCOUNT:?set GCLOUD_ACCOUNT to the gcloud account that owns the project}"
export GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token --account="$ACCOUNT")"

PROJECT="$(terraform -chdir=infra output -raw project_id)"
REPO="$(terraform -chdir=infra output -raw image_repo)"
IMAGE="$REPO/jawab:$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)"

gcloud builds submit --tag "$IMAGE" --project "$PROJECT" --account "$ACCOUNT" --region us-central1 --quiet
terraform -chdir=infra apply -input=false -auto-approve -var "image=$IMAGE"
echo "Deployed $IMAGE -> $(terraform -chdir=infra output -raw service_url)"
