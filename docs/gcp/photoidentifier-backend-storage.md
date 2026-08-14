# PhotoIdentifier Backend Storage

## Scope

This document provisions and verifies the backend service account and temporary exports bucket used by `photoIdentifier`.

- Project: `vision-493709`
- Backend service account: `photoidentifier-backend@vision-493709.iam.gserviceaccount.com`
- Temporary exports bucket: `gs://vision-493709-photoidentifier-exports`
- Region: `asia-east1`

The current rollback service account `photoidentifier-firestore@vision-493709.iam.gserviceaccount.com` stays in place and keeps its existing Firestore access.

## Why Bucket-Scoped `roles/storage.objectAdmin`

`photoIdentifier` needs to create ZIP files, upload them, read them back, and sometimes delete or rebuild them. Bucket-scoped `roles/storage.objectAdmin` matches that responsibility with one binding and avoids piecing together multiple object-level roles while still keeping scope limited to a single temporary exports bucket.

Do not grant project-level `roles/storage.admin`.

## Idempotent Provisioning CLI

Run from the repo root:

```bash
export PROJECT_ID="vision-493709"
export REGION="asia-east1"
export BACKEND_SA="photoidentifier-backend@${PROJECT_ID}.iam.gserviceaccount.com"
export EXPORTS_BUCKET="gs://vision-493709-photoidentifier-exports"
export LIFECYCLE_FILE="docs/gcp/photoidentifier-backend-exports-lifecycle.json"

gcloud config set project "$PROJECT_ID"
gcloud services enable storage.googleapis.com

if ! gcloud iam service-accounts describe "$BACKEND_SA" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create photoidentifier-backend \
    --project="$PROJECT_ID" \
    --display-name="PhotoIdentifier Backend" \
    --description="PhotoIdentifier Vercel backend access to Firestore and temporary exports bucket"
fi

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${BACKEND_SA}" \
  --role="roles/datastore.user" \
  --quiet

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
  --role="roles/storage.objectAdmin"

# 讓前端直接讀取短效 preview，並允許 canvas 使用圖片。
# 若正式網域不同，先調整 JSON 內的 origin。
gcloud storage buckets update "$EXPORTS_BUCKET" \
  --cors-file="docs/gcp/photoidentifier-preview-cors.json" \
  --project="$PROJECT_ID"
```

## Verification CLI

Check bucket metadata:

```bash
gcloud storage buckets describe gs://vision-493709-photoidentifier-exports \
  --project=vision-493709 \
  --format="yaml(name,location,uniformBucketLevelAccess,publicAccessPrevention,lifecycle,cors_config)"
```

Check bucket IAM and confirm there is no `allUsers` or `allAuthenticatedUsers` binding:

```bash
gcloud storage buckets get-iam-policy gs://vision-493709-photoidentifier-exports \
  --project=vision-493709 \
  --format="json(bindings)"
```

Check project-level Firestore role for the backend service account:

```bash
gcloud projects get-iam-policy vision-493709 \
  --flatten="bindings[].members" \
  --filter="bindings.members:photoidentifier-backend@vision-493709.iam.gserviceaccount.com" \
  --format="table(bindings.role,bindings.members)"
```

## Backend Credential For Vercel

Create a backend service-account key outside the repo, then use it for local validation and later Vercel server-side env import.

Example:

```bash
mkdir -p ~/.secr/gcp
gcloud iam service-accounts keys create \
  ~/.secr/gcp/photoidentifier-backend-vision-493709-YYYYMMDD.json \
  --iam-account="photoidentifier-backend@vision-493709.iam.gserviceaccount.com" \
  --project="vision-493709"
chmod 600 ~/.secr/gcp/photoidentifier-backend-vision-493709-YYYYMMDD.json
```

Do not store this JSON inside the repo.

## Vercel Migration Path

The backend now prefers:

1. `PHOTOIDENTIFIER_BACKEND_SERVICE_ACCOUNT_JSON`
2. `FIRESTORE_SERVICE_ACCOUNT_JSON`

That order enables a no-downtime migration.

Recommended sequence:

1. Deploy code containing the new env fallback.
2. Add `PHOTOIDENTIFIER_BACKEND_SERVICE_ACCOUNT_JSON` to Preview first.
3. Verify Preview can read/write Firestore and upload/delete temporary exports.
4. Add the same env to Production.
5. Redeploy Production and verify the export workflow.
6. Keep `FIRESTORE_SERVICE_ACCOUNT_JSON` and `photoidentifier-firestore@vision-493709.iam.gserviceaccount.com` in place for rollback until the new path has been stable long enough.

Example Vercel commands:

```bash
vercel env add PHOTOIDENTIFIER_BACKEND_SERVICE_ACCOUNT_JSON preview \
  < ~/.secr/gcp/photoidentifier-backend-vision-493709-YYYYMMDD.json

vercel env add PHOTOIDENTIFIER_BACKEND_SERVICE_ACCOUNT_JSON production \
  < ~/.secr/gcp/photoidentifier-backend-vision-493709-YYYYMMDD.json
```

Do not remove `FIRESTORE_SERVICE_ACCOUNT_JSON` during the first production cutover.
