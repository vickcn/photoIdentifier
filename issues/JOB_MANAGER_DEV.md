# Job Manager 開發規格：批次狀態持久化與清理

狀態：草案，待實作。

建議先引用：

- `$management-app-design-principles`
- `$implementation-planner`

這份文件只處理第一階段：先把批次作業的狀態、人物群組、照片對應與匯出紀錄持久化，解掉 Cloud Run 多 instance 與重啟後狀態消失的問題。原圖與大檔存放策略，先不在這階段處理。

## 需求重點

- 批次作業不能只依賴程序內記憶體
- `session`、`job`、`face_clusters`、`photo_assignments` 需要可持久化
- Cloud Run 多 instance 之間要能一致讀寫同一份狀態
- 重新整理、重啟、切換 instance 後，結果仍可恢復
- 人物名稱、確認狀態、照片關聯、匯出結果都要保留
- 批次資料要有 TTL 與清理機制，避免無限成長

## 建議方向

### 1. 先把資料生命週期定下來

第一階段要管理的主體不是 UI，而是資料生命週期。

建議拆成以下幾層：

- `batch_sessions`：一場批次工作的主紀錄
- `batch_jobs`：執行與重試狀態
- `face_clusters`：人物主檔與命名
- `photo_items`：每張照片的處理結果
- `photo_assignments`：照片與人物的綁定關係
- `exports`：匯出紀錄

這樣可以避免把所有東西都塞進單一 session 物件，後面也比較好做索引與清理。

### 2. 建議的 collection / table 欄位

#### `batch_sessions`

作用：

- 承接一場批次工作的主狀態
- 讓工作可以跨 request、跨 instance 恢復

建議欄位：

- `session_id`
- `owner_id`
- `batch_mode`
- `status`
- `created_at`
- `updated_at`
- `completed_at`
- `expires_at`
- `face_cluster_eps`
- `face_cluster_min_samples`
- `result_count`

#### `batch_jobs`

作用：

- 記錄實際執行過程
- 支援重試、取消、追蹤錯誤

建議欄位：

- `job_id`
- `session_id`
- `status`
- `progress`
- `started_at`
- `finished_at`
- `error_message`
- `retry_count`
- `cancel_requested`
- `expires_at`

#### `face_clusters`

作用：

- 存人物主檔與群組結果
- 支援名稱編輯與確認狀態

建議欄位：

- `cluster_id`
- `session_id`
- `display_name`
- `status`
- `notes`
- `face_count`
- `photo_count`
- `updated_at`
- `expires_at`

#### `photo_items`

作用：

- 存每張照片的處理結果與公開判定

建議欄位：

- `photo_id`
- `session_id`
- `file_name`
- `public_decision`
- `face_count`
- `result_status`
- `result_summary`
- `updated_at`
- `expires_at`

#### `photo_assignments`

作用：

- 存每張照片對應到哪些人物
- 用來支援前端的照片列編輯與 JSON 匯出

建議欄位：

- `session_id`
- `photo_id`
- `cluster_ids`
- `updated_at`
- `updated_by`
- `expires_at`

#### `exports`

作用：

- 存匯出歷程
- 支援雲端模式與本地下載的對帳

建議欄位：

- `export_id`
- `session_id`
- `target`
- `file_name`
- `status`
- `created_at`
- `expires_at`

### 3. TTL 與清理策略

批次作業一定要有清理機制，否則資料會一直膨脹。

建議保留期：

- `batch_sessions`
  - 成功：7 到 14 天
  - 失敗：3 到 7 天
  - `processing` 但 stale：立即回收或 1 天內清掉
- `batch_jobs`
  - 保留 14 到 30 天
  - 若量大，超過期限只留摘要，不留完整 stage log
- `face_clusters`
  - 保留 7 到 30 天
  - 若還在編輯或尚未匯出，可延長到 30 天
- `photo_items`
  - 保留 7 到 30 天
- `photo_assignments`
  - 跟 `photo_items` 同壽命
- `exports`
  - DB 紀錄可留 30 到 90 天
  - 實體檔案本體另外設 TTL

### 4. 清理順序

刪除時建議按這個順序：

1. `photo_assignments`
2. `photo_items`
3. `face_clusters`
4. `batch_jobs`
5. `batch_sessions`

有實體檔案時，先刪檔案或確認已備份，再刪 DB 記錄。

### 5. 與 Cloud Run instance 的關係

這一階段要先解掉：

- 多 instance 下 session 不一致
- 重啟後資料消失
- 同一使用者跨頁籤/跨請求狀態丟失

因此 batch state 不應再只放在 `_batch_sessions` 這類程序內記憶體中，最多只保留短暫快取。

### 6. Firestore service account 分工

這個專案的 Firestore 連線憑證要和 Cloud Run 執行身分分開，不要共用同一把 key。

建議分工：

- `photoclassifier-run@vision-493709.iam.gserviceaccount.com`
  - 保留給 Cloud Run 執行辨識服務
  - 不拿來當外部直連 Firestore 的私鑰
- `photoidentifier-firestore@vision-493709.iam.gserviceaccount.com`
  - 專門給 Vercel 或本機直連 Firestore
  - 權限只給 `roles/datastore.user`
  - 若之後需要輪替或撤權，只影響資料層，不影響辨識服務本體

實作上，後端可優先用 Cloud Run 的預設身分；若在 Vercel 或本機需要直連 Firestore，才把專用 service account JSON 放進 `FIRESTORE_SERVICE_ACCOUNT_JSON`。

### 6.1 專用 Firestore service account 建立流程

目前規劃的專用帳號：

- `photoidentifier-firestore@vision-493709.iam.gserviceaccount.com`

建議 CLI 流程：

1. 建立 service account
2. 指派 `roles/datastore.user`
3. 匯出 JSON key
4. 列出 JSON key

指令如下：

```bash
gcloud iam service-accounts create photoidentifier-firestore \
  --display-name="PhotoIdentifier Firestore Access" \
  --project vision-493709
```

```bash
gcloud projects add-iam-policy-binding vision-493709 \
  --member="serviceAccount:photoidentifier-firestore@vision-493709.iam.gserviceaccount.com" \
  --role="roles/datastore.user"
```

```bash
gcloud iam service-accounts keys create photoidentifier-firestore-key.json \
  --iam-account="photoidentifier-firestore@vision-493709.iam.gserviceaccount.com" \
  --project vision-493709
```

```bash
gcloud iam service-accounts keys list \
  --iam-account="photoidentifier-firestore@vision-493709.iam.gserviceaccount.com"
```

### 6.2 本機與 Vercel 的接法

本機或 Vercel 若要直連 Firestore，使用 `FIRESTORE_SERVICE_ACCOUNT_JSON`。

本機 `.env` 建議至少包含：

- `BATCH_STATE_BACKEND=firestore`
- `FIRESTORE_PROJECT_ID=vision-493709`
- `FIRESTORE_SERVICE_ACCOUNT_JSON=<單行 JSON>`

若已有 `photoidentifier-firestore-key.json`，可先把它壓成單行：

```bash
python - <<'PY'
import json
from pathlib import Path
p = Path("photoidentifier-firestore-key.json")
print(json.dumps(json.loads(p.read_text())))
PY
```

Vercel 端則把同一份單行 JSON 放進：

- `FIRESTORE_SERVICE_ACCOUNT_JSON`

Cloud Run 則優先使用執行中的 service account，不需要額外下載這把 key。

部署注意：

- `google-cloud-firestore` 套件本身不算大，通常不是 bundle 超限的主要來源
- 真正要注意的是額外依賴增加、冷啟動時間變長，以及 `FIRESTORE_SERVICE_ACCOUNT_JSON` 的環境變數管理
- 若 Vercel 只需要呼叫後端 API，不直接讀寫 Firestore，就不必把這把 key 放進前端部署
- 若未來 bundle 接近上限，優先把 Firestore 存取留在 Cloud Run 後端，Vercel 只保留 API 呼叫

### 7. 暫不處理的事項

第一階段先不處理：

- 原圖長期存放策略
- 大型物件儲存方案
- 圖片縮圖/CDN 策略
- 使用者歷史庫與跨 session 長期查詢

這些要等狀態模型穩了再談。

## 影響範圍

- `main.py`
- `photoIdentifier.py`
- `src/insight_api_client.py`
- `static/app.js`
- `static/app.css`
- `template/index.html`
- Firebase DB schema / rules
- 匯出 JSON 流程
- 批次清理工作

## 驗收條件

- [ ] 批次作業可跨 request 查回同一份狀態
- [ ] Cloud Run 多 instance 不會讓 session 消失
- [ ] 人物名稱與照片對應可持久化
- [ ] 匯出紀錄可追蹤
- [ ] 批次資料有 TTL，且能自動清理
- [ ] stale / orphan job 有回收機制
- [ ] 這一階段不依賴原圖長期存放

## 風險

- 若只做前端快取，instance 問題不會消失
- 若把圖片 base64 一起塞進 DB，成本會很快升高
- 若沒有 `session_id` 與 `owner_id` 約束，會出現跨使用者資料混淆
- 若清理策略沒有 TTL 與 stale 判定，批次資料會一直累積

## 實作建議

先做最小可行版本：

1. 先定義 `batch_sessions` 與 `face_clusters` 的持久化結構
2. 再把 `photo_assignments` 與 `exports` 補上
3. 接著把現有 in-memory state 逐步改成讀寫持久層
4. 最後再補 TTL 清理與 stale recovery
