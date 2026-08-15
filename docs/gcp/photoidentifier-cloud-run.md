# PhotoIdentifier Cloud Run 部署

正式 GCP 專案是 `photoidentifier-prod`，區域是 `asia-east1`，runtime service account 是：

`photoidentifier-run@photoidentifier-prod.iam.gserviceaccount.com`

## 前置條件

- Firestore `(default)` 已在 `asia-east1` 建立。
- Secret Manager 已建立 `SESSION_SECRET`、`GOOGLE_CLIENT_SECRET`、`INSIGHT_API_KEY`、`VERTEX_API_KEY`。
- `photoidentifier-run` 已有 `roles/datastore.user`。
- 既有 `vision-493709` 的 temporary exports bucket 需授予此 SA bucket-scoped `roles/storage.objectAdmin`。
- `run.googleapis.com`、`cloudbuild.googleapis.com`、`artifactregistry.googleapis.com`、`secretmanager.googleapis.com` 已在 `photoidentifier-prod` 先行啟用。

## 一次性 IAM

```bash
gcloud storage buckets add-iam-policy-binding \
  gs://vision-493709-photoidentifier-exports \
  --member=serviceAccount:photoidentifier-run@photoidentifier-prod.iam.gserviceaccount.com \
  --role=roles/storage.objectAdmin

for secret_name in SESSION_SECRET GOOGLE_CLIENT_SECRET INSIGHT_API_KEY VERTEX_API_KEY; do
  gcloud secrets add-iam-policy-binding "${secret_name}" \
    --project=photoidentifier-prod \
    --member=serviceAccount:photoidentifier-run@photoidentifier-prod.iam.gserviceaccount.com \
    --role=roles/secretmanager.secretAccessor
done
```

`roles/storage.objectAdmin` 的 scope 只有 `vision-493709-photoidentifier-exports`，沒有授予 project-level Storage Admin。

## 初次部署

不要把 `.env`、service-account JSON 或 private key 放進映像檔。先在目前 shell 匯出非機密設定：

```bash
export GOOGLE_CLIENT_ID='...'
export GOOGLE_PROJECT_NUMBER='225874268617'
export GOOGLE_REDIRECT_URI='https://<正式網域>/auth/callback'
export INSIGHT_API_URL='https://photoclassifier-dqmb7r4cla-de.a.run.app'
export APP_BASE_URL='https://<正式網域>'
export PUBLIC_APP_ORIGIN='https://<正式網域>'
```

執行：

```bash
./scripts/deploy_photoidentifier_cloud_run.sh
```

若尚未有正式網域，可先使用 Cloud Run URL 部署，取得 URL 後再把該 URL 設為 `GOOGLE_REDIRECT_URI`、`APP_BASE_URL`、`PUBLIC_APP_ORIGIN` 重部署。Google OAuth client 必須同時加入完全相同的 callback URL。

如果你要在人工 provisioning 時順手啟用 API，可以先加：

```bash
ENABLE_GCLOUD_APIS=true ./scripts/deploy_photoidentifier_cloud_run.sh
```

但 GitHub Actions 的正式 deploy 不應該依賴這一步。

## 驗證

```bash
SERVICE_URL="$(gcloud run services describe photoidentifier \
  --project=photoidentifier-prod --region=asia-east1 --format='value(status.url)')"

curl -fsS "${SERVICE_URL}/api/config"
gcloud run services describe photoidentifier \
  --project=photoidentifier-prod --region=asia-east1 \
  --format='yaml(status.url,spec.template.spec.serviceAccountName)'
```

確認 Cloud Run logs 沒有 `Firestore batch state disabled`、Secret Manager permission denied 或啟動例外，再測試登入、Drive Picker、批次辨識與 GCS preview。

## Vercel 保留

這個部署不會修改 Vercel env，也不會刪除 `FIRESTORE_SERVICE_ACCOUNT_JSON`。Vercel 與 Cloud Run 使用同一份程式碼，但各自設定 `APP_BASE_URL`、`PUBLIC_APP_ORIGIN`、`GOOGLE_REDIRECT_URI`。切換正式網域前，先完成 Cloud Run 的健康檢查與 OAuth callback 驗證。

## GitHub Actions 自動部署

repo 已新增：

- [deploy-cloud-run.yml](/Users/kexuen/projects/photoIdentifier/.github/workflows/deploy-cloud-run.yml)
- [verify.yml](/Users/kexuen/projects/photoIdentifier/.github/workflows/verify.yml)

設計是：

- `push` 到 `master` 時，Vercel 繼續用原本的 Git 自動部署。
- 同一次 `push` 會由 GitHub Actions 部署 Cloud Run。
- `pull_request` 時只跑驗證，不碰 production deploy。

### GitHub 端還需要手動補的設定

GitHub repository `Settings -> Secrets and variables -> Actions` 需要補：

Secrets:

- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_DEPLOY_SERVICE_ACCOUNT`

Variables:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_REDIRECT_URI`
- `APP_BASE_URL`
- `PUBLIC_APP_ORIGIN`
- `INSIGHT_API_URL`

建議目前沒有自訂網域時先用 Cloud Run URL：

- `APP_BASE_URL=https://photoidentifier-225874268617.asia-east1.run.app`
- `PUBLIC_APP_ORIGIN=https://photoidentifier-225874268617.asia-east1.run.app`
- `GOOGLE_REDIRECT_URI=https://photoidentifier-225874268617.asia-east1.run.app/auth/callback`

目前這個 repo 對應的實際值是：

- `GCP_WORKLOAD_IDENTITY_PROVIDER=projects/225874268617/locations/global/workloadIdentityPools/github-actions/providers/github`
- `GCP_DEPLOY_SERVICE_ACCOUNT=github-actions-deploy@photoidentifier-prod.iam.gserviceaccount.com`

### OIDC / deploy service account

workflow 採用 GitHub OIDC，不用 service account key JSON。

`GCP_DEPLOY_SERVICE_ACCOUNT` 應是專門給 GitHub Actions impersonate 的 deploy 身分，不要直接用 runtime SA。這個 deploy SA 至少要能：

- build image
- push Artifact Registry
- deploy Cloud Run
- impersonate runtime service account if deploy policy需要

如果你還沒建立 GitHub OIDC provider 與 deploy SA，這部分還要補一次性 GCP 設定，之後 workflow 才能真正自動跑通。
