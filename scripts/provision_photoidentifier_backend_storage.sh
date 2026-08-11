#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-vision-493709}"
REGION="${REGION:-asia-east1}"
BACKEND_SA_NAME="${BACKEND_SA_NAME:-photoidentifier-backend}"
BACKEND_SA="${BACKEND_SA:-${BACKEND_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com}"
EXPORTS_BUCKET="${EXPORTS_BUCKET:-gs://vision-493709-photoidentifier-exports}"
LIFECYCLE_FILE="${LIFECYCLE_FILE:-docs/gcp/photoidentifier-backend-exports-lifecycle.json}"

if [[ ! -f "$LIFECYCLE_FILE" ]]; then
  echo "Missing lifecycle file: $LIFECYCLE_FILE" >&2
  exit 1
fi

gcloud config set project "$PROJECT_ID"
gcloud services enable storage.googleapis.com

if ! gcloud iam service-accounts describe "$BACKEND_SA" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$BACKEND_SA_NAME" \
    --project="$PROJECT_ID" \
    --display-name="PhotoIdentifier Backend" \
    --description="PhotoIdentifier Vercel backend access to Firestore and temporary exports bucket"
fi

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${BACKEND_SA}" \
  --role="roles/datastore.user" \
  --quiet >/dev/null

if gcloud storage buckets describe "$EXPORTS_BUCKET" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud storage buckets update "$EXPORTS_BUCKET" \
    --project="$PROJECT_ID" \
    --uniform-bucket-level-access \
    --public-access-prevention \
    --lifecycle-file="$LIFECYCLE_FILE"
else
  gcloud storage buckets create "$EXPORTS_BUCKET" \
    --project="$PROJECT_ID" \
    --location="$REGION" \
    --uniform-bucket-level-access \
    --public-access-prevention \
    --lifecycle-file="$LIFECYCLE_FILE"
fi

gcloud storage buckets add-iam-policy-binding "$EXPORTS_BUCKET" \
  --member="serviceAccount:${BACKEND_SA}" \
  --role="roles/storage.objectAdmin" >/dev/null

gcloud storage buckets describe "$EXPORTS_BUCKET" \
  --project="$PROJECT_ID" \
  --format="table(name,location,publicAccessPrevention)"
