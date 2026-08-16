#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-photoidentifier-prod}"
REGION="${REGION:-asia-east1}"
SERVICE_NAME="${SERVICE_NAME:-photoidentifier}"
RUNTIME_SA="${RUNTIME_SA:-photoidentifier-run@${PROJECT_ID}.iam.gserviceaccount.com}"
REPOSITORY="${REPOSITORY:-photoidentifier}"
IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d-%H%M%S)}"
IMAGE="${IMAGE:-${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/${SERVICE_NAME}:${IMAGE_TAG}}"
ENABLE_GCLOUD_APIS="${ENABLE_GCLOUD_APIS:-false}"
SKIP_IMAGE_BUILD="${SKIP_IMAGE_BUILD:-false}"

: "${GOOGLE_CLIENT_ID:?Export GOOGLE_CLIENT_ID before deploying}"
: "${GOOGLE_REDIRECT_URI:?Export GOOGLE_REDIRECT_URI before deploying}"
: "${INSIGHT_API_URL:?Export INSIGHT_API_URL before deploying}"

gcloud config set project "${PROJECT_ID}"
if [[ "${ENABLE_GCLOUD_APIS}" == "true" ]]; then
  gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
fi

gcloud artifacts repositories describe "${REPOSITORY}" \
  --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1 || \
gcloud artifacts repositories create "${REPOSITORY}" \
  --repository-format=docker --location="${REGION}" \
  --description="PhotoIdentifier Cloud Run images" --project="${PROJECT_ID}"

if [[ "${SKIP_IMAGE_BUILD}" != "true" ]]; then
  gcloud builds submit --project="${PROJECT_ID}" --tag="${IMAGE}" .
fi

gcloud run deploy "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="${RUNTIME_SA}" \
  --allow-unauthenticated \
  --port=8080 \
  --memory=2Gi \
  --cpu=2 \
  --timeout=900 \
  --concurrency=1 \
  --min=0 \
  --max=3 \
  --set-env-vars="GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID},GOOGLE_PROJECT_NUMBER=${GOOGLE_PROJECT_NUMBER:-225874268617},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_REDIRECT_URI=${GOOGLE_REDIRECT_URI},APP_BASE_URL=${APP_BASE_URL:-},PUBLIC_APP_ORIGIN=${PUBLIC_APP_ORIGIN:-},INSIGHT_API_URL=${INSIGHT_API_URL},PHOTOIDENTIFIER_EXPORTS_BUCKET=${PHOTOIDENTIFIER_EXPORTS_BUCKET:-photoidentifier-prod-exports},FIRESTORE_PROJECT_ID=${FIRESTORE_PROJECT_ID:-${PROJECT_ID}},FIRESTORE_DATABASE=${FIRESTORE_DATABASE:-(default)},BATCH_STATE_BACKEND=firestore,FACE_CLUSTERING_ENABLED=${FACE_CLUSTERING_ENABLED:-true},PREVIEW_SIGNED_URL_TTL_MINUTES=${PREVIEW_SIGNED_URL_TTL_MINUTES:-1440},EXPORT_SIGNED_URL_TTL_MINUTES=${EXPORT_SIGNED_URL_TTL_MINUTES:-60}" \
  --set-secrets="SESSION_SECRET=SESSION_SECRET:latest,GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,INSIGHT_API_KEY=INSIGHT_API_KEY:latest,VERTEX_API_KEY=VERTEX_API_KEY:latest"

gcloud run services describe "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" --region="${REGION}" \
  --format='value(status.url)'
