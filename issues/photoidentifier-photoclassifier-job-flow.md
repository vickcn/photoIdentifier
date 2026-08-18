# `photoIdentifier` 批次與匯出生命週期整理

狀態：先記錄邊界與清理策略，不改程式。

這份只描述 `photoIdentifier` 這個 repo 要負責的部分。`photoclassifier` 的 job queue、Firestore job state、stale job 判定，請看對應的本地 repo 文件：`../photoclassifier/issues/photoidentifier-photoclassifier-job-flow.md`。
如果之後要做「依使用者 / 人物回收 embedding 來訓練 DBSCAN 或其他模型」，請先看 `issues/todo-embedding_storage_plan.md`；那份文件處理的是分析資料底座，不是這份的 session / export 清理。

## 這個 repo 要管的東西

- 使用者登入與 session
- 批次結果的 Firestore state
- 匯出 ZIP 與 signed URL
- 暫存 export bucket 與 lifecycle
- 本機或 Vercel 的 `/tmp` / 暫存檔清理
- 前端回顧結果所需的 session / export 查詢

## 目前已確認的清理面

| 層級 | 現況 | 建議 |
| --- | --- | --- |
| GCS export bucket | `photoidentifier-prod-exports` 與 `vision-493709-photoidentifier-exports` 都有 `age: 1` lifecycle | 維持 1 天自動刪除 |
| signed URL | preview / download URL 都是短效 | 維持短效，不把 URL 當長期保存 |
| Firestore `batch_sessions` | 程式有 `expires_at`，但 TTL policy 還沒真正開啟 | 啟用 Firestore TTL，讓過期文件可自動刪除 |
| `_batch_sessions` 記憶體 dict | 會保存 session，完成後沒有看到主動 eviction | 增加完成後 / 過期後的清理或壓縮策略 |
| `/tmp/drive_tokens` | 只有 token 檔案存在時才會留著 | 依登入 / logout 主動刪除，不能只靠 instance 回收 |
| `review_temp_*` | 目前有手動刪除 API | 若要長時間跑服務，要補背景清理或 TTL |

## TTL / 超時建議

| 對象 | 建議值 | 理由 |
| --- | --- | --- |
| `batch_sessions` active | `7 天` | 讓使用者回頭補看、補下載、補操作 |
| `batch_sessions` completed / failed / cancelled | `30 天` | 留足回顧與除錯時間 |
| export 物件 | `1 天` | ZIP 暫存空間不應長期保留 |
| signed URL | `60 分鐘` 到 `24 小時` | URL 不應成為長期存取手段 |
| `_batch_sessions` 記憶體 session | `completed` 後 `15 分鐘`，`processing` 最後更新 `2 小時` | 降低 RAM 累積風險 |
| `/tmp/drive_tokens` | 登出即刪，或 idle 後清理 | 避免 session / token 一直堆著 |

## 建議變數

| 變數 | 建議值 | 用途 |
| --- | --- | --- |
| `BATCH_STATE_BACKEND` | `firestore` | 啟用 durable batch session state |
| `FIRESTORE_PROJECT_ID` | `photoidentifier-prod` | Cloud Run 正式環境使用的 Firestore project |
| `FIRESTORE_DATABASE` | `(default)` | Firestore database 名稱 |
| `PHOTOIDENTIFIER_EXPORTS_BUCKET` | `photoidentifier-prod-exports` | Cloud Run 暫存 ZIP bucket |
| `PREVIEW_SIGNED_URL_TTL_MINUTES` | `1440` | preview URL 約 24 小時 |
| `EXPORT_SIGNED_URL_TTL_MINUTES` | `60` | 匯出下載 URL 約 1 小時 |
| `PHOTOIDENTIFIER_BACKEND_SERVICE_ACCOUNT_JSON` | Secret | 產生 signed URL 與 GCS / Firestore server-side auth |

## 後續程式參數建議

這些目前不一定都已經做成 env，但建議最後落成可調參數：

| 參數 | 建議值 | 用途 |
| --- | --- | --- |
| `BATCH_SESSION_ACTIVE_TTL_HOURS` | `2` | `_batch_sessions` 中 processing session 的記憶體保留時間 |
| `BATCH_SESSION_TERMINAL_TTL_MINUTES` | `15` | `_batch_sessions` 中 completed / failed session 的記憶體保留時間 |
| `DRIVE_TOKEN_IDLE_TTL_HOURS` | `24` | `/tmp/drive_tokens` 閒置 token 清理門檻 |
| `REVIEW_TEMP_TTL_HOURS` | `24` | `review_temp_*` 自動清理門檻 |
| `FIRESTORE_ACTIVE_TTL_DAYS` | `7` | batch session active 文件 TTL |
| `FIRESTORE_HISTORY_TTL_DAYS` | `30` | completed / export 文件 TTL |

## 依賴的本地 repo

- `../photoclassifier`：只提供人臉偵測 / 分群 job 服務，不承擔 `photoIdentifier` 的 session 與匯出清理。
- `../photoclassifier/issues/`：那邊要記錄 classifier job queue 的 TTL 與 Firestore job state。

## 需要開的 GCP 清理

- Firestore TTL 要真的啟用，不能只寫 `expires_at`
- GCS bucket lifecycle 要保留
- 如果之後新增更多 session collection，就一律用同樣的 TTL 規則
