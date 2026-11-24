#!/usr/bin/env bash
# Manual deploy. Normally not needed: every push to main deploys through GitHub Actions (.github/workflows/ci.yml).
# Builds the current working tree with Cloud Build and rolls it out; Terraform keeps owning the rest of the service.
#   GCLOUD_ACCOUNT=you@example.com scripts/deploy.sh
# or, with a named gcloud configuration for the project:
#   CLOUDSDK_ACTIVE_CONFIG_NAME=jawab scripts/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

ACCOUNT="${GCLOUD_ACCOUNT:-$(gcloud config get-value account 2>/dev/null)}"
: "${ACCOUNT:?set GCLOUD_ACCOUNT (or a gcloud configuration) to the account that owns the project}"
export GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token --account="$ACCOUNT")"

PROJECT="$(terraform -chdir=infra output -raw project_id)"
REGION="$(terraform -chdir=infra output -raw region)"
REPO="$(terraform -chdir=infra output -raw image_repo)"
TAG="$(git rev-parse --short HEAD 2>/dev/null || echo manual)"
git diff --quiet 2>/dev/null || TAG="$TAG-dirty-$(date +%Y%m%d%H%M%S)"  # never reuse a tag for different code
IMAGE="$REPO/jawab:$TAG"

gcloud builds submit --tag "$IMAGE" --project "$PROJECT" --account "$ACCOUNT" --region "$REGION" --quiet
gcloud run services update jawab --image "$IMAGE" --region "$REGION" --project "$PROJECT" --account "$ACCOUNT" --quiet
echo "Deployed $IMAGE -> $(terraform -chdir=infra output -raw service_url)"
