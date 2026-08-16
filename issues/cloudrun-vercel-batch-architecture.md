# Vercel + Cloud Run 批次架構改造計畫

## 雙 GCP / 雙部署面總覽

這個專案目前不是單一 GCP 專案結構，而是兩套歷史脈絡並存：

- `vision-493709`：較早期的 Vercel / Firebase / OAuth / exports bucket / classifier 相關資源
- `photoidentifier-prod`：目前要補齊的正式 Cloud Run + Firestore 部署面

這不是單純的殘留垃圾，而是「舊有可運作路徑」與「新完整 GCP 部署路徑」同時存在。
後續所有部署、除錯、寄信、匯出、OAuth 設定，都必須先分清楚自己現在在碰哪一套。

### 專案級對照表

| 面向 | `vision-493709` | `photoidentifier-prod` | 目前用途 / 備註 |
| --- | --- | --- | --- |
| GCP project id | `vision-493709` | `photoidentifier-prod` | 兩個都仍在實際使用中 |
| 主要歷史角色 | Vercel 時期後端資源、Firebase / Firestore、暫存匯出 bucket、classifier 相依 | 新的正式 Cloud Run `photoidentifier` 執行面 | 現況是跨專案運作，不是純遷移完成狀態 |
| 主要 App 服務 | `photoclassifier` | `photoidentifier` | `photoidentifier` 仍會呼叫 `photoclassifier` |
| Cloud Run URL | `https://photoclassifier-dqmb7r4cla-de.a.run.app` | `https://photoidentifier-zn7y74nl2a-de.a.run.app` | `photoidentifier` 另有 Google-managed alias URL |
| OAuth / Google client 歷史綁定 | 舊本機 `.env` 與 Vercel 設定仍偏向這邊 | Cloud Run 正在改為這邊的 OAuth client / redirect | 最容易混淆的是 `GOOGLE_CLIENT_ID`、`GOOGLE_PROJECT_NUMBER`、`GOOGLE_REDIRECT_URI` |
| Firestore | 舊資料面與舊 service account 慣性使用 | Cloud Run 目前設定 `FIRESTORE_PROJECT_ID=photoidentifier-prod` | 現在已經不是全部都在 `vision-493709` |
| 暫存匯出 bucket | `gs://vision-493709-photoidentifier-exports` | `gs://photoidentifier-prod-exports` | Vercel 舊部署保留舊 bucket，Cloud Run 正式路線改用新 bucket |
| Gmail 通知前置 | 舊流程曾依賴 Vercel server-side secret | 現在 Cloud Run 需要自行具備 signed URL + Gmail 發信能力 | 這次補的是 `photoidentifier-prod` 這條路 |

### 執行服務 / Service Account 對照表

只列可公開識別資訊，不列 private key、secret 值。

| 類型 | 所屬 project | 名稱 / email | 目前用途 | 備註 |
| --- | --- | --- | --- | --- |
| Cloud Run service | `photoidentifier-prod` | `photoidentifier` | 正式 App 後端 | region `asia-east1` |
| Runtime service account | `photoidentifier-prod` | `photoidentifier-run@photoidentifier-prod.iam.gserviceaccount.com` | `photoidentifier` Cloud Run 執行身分 | 需讀 Secret Manager、存 Firestore、寫 exports bucket |
| Deploy service account | `photoidentifier-prod` | `github-actions-deploy@photoidentifier-prod.iam.gserviceaccount.com` | GitHub Actions / 部署身分 | 不應直接作為 runtime 使用 |
| Firebase admin service account | `photoidentifier-prod` | `firebase-adminsdk-fbsvc@photoidentifier-prod.iam.gserviceaccount.com` | Firebase admin 相關用途 | 不是本次批次匯出主角 |
| Default compute service account | `photoidentifier-prod` | `225874268617-compute@developer.gserviceaccount.com` | GCP 預設 compute identity | 不建議當正式 runtime 依賴 |
| Backend service account | `vision-493709` | `photoidentifier-backend@vision-493709.iam.gserviceaccount.com` | 舊 Vercel server-side Firestore / Storage 路徑 | 目前仍存在於舊部署脈絡 |
| Firestore service account | `vision-493709` | `photoidentifier-firestore@vision-493709.iam.gserviceaccount.com` | 舊 Firestore 回退 / 舊資料路徑 | 與新 Cloud Run runtime 要明確區分 |
| Classifier service | `vision-493709` | `photoclassifier` | 臉部偵測 / 分群運算 | `photoidentifier` 目前仍呼叫它 |

### 功能環節對照表

| 功能環節 | 目前主要落點 | 使用到的 project / service | 關鍵設定 | 風險 / 混淆點 |
| --- | --- | --- | --- | --- |
| 前端頁面 / 使用者互動 | Vercel 與 Cloud Run 兩邊都可能承載 | Vercel deployment、`photoidentifier` Cloud Run | `APP_BASE_URL`、`PUBLIC_APP_ORIGIN` | 如果 UI 入口與 OAuth redirect URI 不一致，登入會出問題 |
| Google OAuth 登入 | App server + Google OAuth client | 歷史上偏 `vision-493709`，Cloud Run 正在切往 `photoidentifier-prod` | `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`、`GOOGLE_PROJECT_NUMBER`、`GOOGLE_REDIRECT_URI` | 最容易出現「登入成功但 session / redirect 不對」 |
| Google Drive 存取 | App server | 跟 OAuth client 綁定 | Drive scopes、token session | 本機 `.env` 與 Cloud Run env 如果指向不同專案，會很難 debug |
| 批次工作控制平面 | `photoidentifier` | `photoidentifier-prod` | Cloud Run env、Firestore state store | 新正式流程主體在這裡 |
| 人臉分群運算 | `photoclassifier` | `vision-493709` Cloud Run | `INSIGHT_API_URL` 指向 classifier | 這條仍是跨服務、跨專案呼叫 |
| 批次狀態持久化 | `photoidentifier` | 目前 Cloud Run 指向 `photoidentifier-prod` Firestore | `BATCH_STATE_BACKEND`、`FIRESTORE_PROJECT_ID` | 舊文件與本機 env 仍常寫成 `vision-493709` |
| 暫存 ZIP 匯出 | `photoidentifier` 建立、GCS 保存 | Vercel 路線在 `vision-493709`；Cloud Run 路線在 `photoidentifier-prod` | Vercel:`vision-493709-photoidentifier-exports`；Cloud Run:`photoidentifier-prod-exports` | 兩套部署面各自用自己的 bucket |
| signed URL 下載 | `photoidentifier` | Cloud Run runtime SA + GCS bucket | `PHOTOIDENTIFIER_BACKEND_SERVICE_ACCOUNT_JSON` | 沒有 private key 時會在 signed URL 這一步失敗 |
| Gmail 發信通知 | `photoidentifier` | 使用登入者 OAuth token 呼叫 Gmail API | `gmail.send` scope、Gmail API enablement | 若前一步 signed URL 失敗，寄信不會發生 |
| 回顧辨識結果 | 前端 + 匯出 ZIP | ZIP 內 `workspace.json` / `result.json` / 原圖 | 匯出結構與 bbox metadata | 如果 ZIP 結構不一致，回顧功能就無法重建 |

### 環境變數責任面對照表

| 變數 | 歷史主要來源 | 現在較應該由誰主導 | 用途 | 備註 |
| --- | --- | --- | --- | --- |
| `GOOGLE_CLIENT_ID` | Vercel / 舊 `.env` | 依實際對外登入入口決定，但要全域一致 | Google OAuth client id | 不能一套 local、一套 Cloud Run、另一套 Vercel |
| `GOOGLE_PROJECT_NUMBER` | 舊 `vision-493709` 設定 | 應與目前 OAuth client 所屬 project 對齊 | Picker / OAuth 識別資訊 | 常跟 project id 混淆 |
| `GOOGLE_REDIRECT_URI` | 舊 Vercel / local 為主 | 要跟實際對外 URL 完全一致 | OAuth callback | 不能混用兩個 Cloud Run URL |
| `GOOGLE_CLOUD_PROJECT` | 本機常是 `vision-493709` | Cloud Run `photoidentifier` 應是 `photoidentifier-prod` | App 預設 GCP project | 要和 runtime 身分一致 |
| `FIRESTORE_PROJECT_ID` | 舊案多半是 `vision-493709` | Cloud Run 現在是 `photoidentifier-prod` | Firestore 狀態保存 | 現在這個已經在新 project |
| `PHOTOIDENTIFIER_EXPORTS_BUCKET` | 舊 bucket | 依部署面決定 | 暫存 ZIP 保存位置 | Vercel 保留舊 bucket，Cloud Run 改用 `photoidentifier-prod-exports` |
| `PHOTOIDENTIFIER_BACKEND_SERVICE_ACCOUNT_JSON` | Vercel server-side secret | Cloud Run runtime secret 也需要 | Storage signed URL / server-side GCP auth | 這次寄信問題就是補這個 |
| `FIRESTORE_SERVICE_ACCOUNT_JSON` | 舊 Vercel / rollback 路徑 | 僅作舊路徑或回退用途 | 舊 Firestore service identity | 應避免與新版 backend JSON 混為一談 |
| `INSIGHT_API_URL` | 舊 classifier URL | 目前仍要指向 `photoclassifier` | 人臉偵測 / 分群 API | 表示架構仍是雙服務 |

### 目前已確認的真實狀態

| 項目 | 目前狀態 |
| --- | --- |
| `photoidentifier` Cloud Run 所在 project | `photoidentifier-prod` |
| `photoidentifier` runtime SA | `photoidentifier-run@photoidentifier-prod.iam.gserviceaccount.com` |
| `photoidentifier` Firestore target | `photoidentifier-prod` |
| `photoidentifier` exports bucket | Cloud Run:`photoidentifier-prod-exports`；Vercel:`vision-493709-photoidentifier-exports` |
| `photoclassifier` URL | `https://photoclassifier-dqmb7r4cla-de.a.run.app` |
| 舊本機 `.env` 預設 project | `vision-493709` |
| 新 Cloud Run 已建立 backend SA secret 名稱 | `PHOTOIDENTIFIER_BACKEND_SERVICE_ACCOUNT_JSON` |
| 本次缺口 | OAuth / local env 對齊，以及是否同步把本機 `.env` 切到 `photoidentifier-prod` |

### 現階段建議的架構解讀

| 類別 | 建議解讀 |
| --- | --- |
| `vision-493709` | 舊資源母體，短期仍承擔 classifier、舊 bucket、部分 OAuth / Vercel 脈絡 |
| `photoidentifier-prod` | 新正式 App 後端 project，現在已承擔 Cloud Run app、Firestore、runtime secret 與 Cloud Run exports bucket |
| 是否已完全遷移 | 否，目前是跨專案組合態 |
| 是否必須立刻合併成單一 project | 不一定，但必須把責任邊界寫清楚並固定 |
| 近期最重要工作 | 讓 `photoidentifier-prod` 的 OAuth / local env 也完全對齊，避免再混用舊 project 設定 |

### 正式邊界決議

| 部署面 | project | exports bucket | 說明 |
| --- | --- | --- | --- |
| Vercel 舊部署 | `vision-493709` 脈絡 | `gs://vision-493709-photoidentifier-exports` | 保留既有 bucket，不強迫一起搬遷 |
| Cloud Run 正式部署 | `photoidentifier-prod` | `gs://photoidentifier-prod-exports` | 自成一套，不再依賴舊 bucket |
| Classifier | `vision-493709` | 不適用 | 可繼續獨立存在，供 `photoidentifier` 呼叫 |

狀態：第一階段核心實作完成，持續補強非同步控制平面與持久化。

建議先引用：

- `$implementation-planner`
- `$management-app-design-principles`
- `$session-reverse-engineer`

這份文件記錄的是第二階段架構調整：把前端與 identifier 維持在 Vercel 的短請求模式，把耗時的人臉分群與批次運算搬到 Cloud Run，避免單一 request 撐過 300 秒，同時保留 200 張以上的工作能力。

## 目標與成功條件

- 使用者可以一次選很多張照片
- Vercel 只負責建立工作、回傳 `session_id` / `job_id`、輪詢進度、接收最終結果
- Cloud Run 負責真正的批次運算與人臉分群
- 單一 Vercel request 不需要等待完整批次完成，因此不會卡在 300 秒限制
- classifier 可以分批收檔，但最後仍能做全域一致的分群或等價的合併流程
- 前端可以穩定顯示進度、可取消、可重試、可恢復

## 現有系統判讀

- `photoIdentifier` 已經有批次上傳、輪詢進度、取消、Cloud Run 呼叫 classifier 的基礎
- 目前的分批流程已能避開單次 20 張限制，但如果每批獨立 DBSCAN，再硬合併，人物一致性仍有限
- `Cloud Run` 適合長時間運算、排隊、狀態保存與多 instance 擴展
- `Vercel` 適合短生命週期的控制平面，不適合長時間同步等待
- 目前最需要補的是 classifier 的工作模型，而不是再把 Vercel request 撐長

## 影響範圍

- Vercel / identifier 的批次 API
- Cloud Run / classifier 的 job API
- 進度輪詢與取消流程
- 批次狀態持久化
- 前端等待視窗、進度顯示、取消按鈕
- 測試與部署環境變數

## 建議方案

### 方案 A：最小可行

- classifier 維持現有 API
- identifier 在 client 端分批送出
- 每批各自分群後做結果合併

優點：

- 改動小
- 風險低
- 可以快速避開單次負載與部分時間限制

缺點：

- 不是真正全域一致分群
- 同一個人跨批次容易出現群組切分

### 方案 B：穩定重構

- classifier 改成「先收 batch，最後統一分群」的 job model
- 每批先存 `embedding` / `face metadata`
- 所有批次收完後，再一次做全域 `fit_predict`
- identifier 只負責送件、輪詢、取消、接收結果

優點：

- 保留 Vercel 短請求特性
- 全域分群一致性最好
- 之後要接 `BigQuery` / `Firestore` 也比較順

缺點：

- classifier 要補工作狀態、持久化與排隊
- 需要改動較多

### 方案 C：長期架構

- classifier 專注產 embedding 與臉框資訊
- embedding 進分析層
- 標籤與工作狀態進 Firestore
- 後續分類、聚合、再訓練分離處理

優點：

- 擴充性最好
- 最符合後續資料治理

缺點：

- 需要更多資料層設計
- 不適合一次推完

建議採用：`方案 B`，並保留往 `方案 C` 演進的結構。

## Python Integration Design

### 既有入口

- `main.py`
- `src/insight_api_client.py`
- `src/batch_state_store.py`
- `static/js/app.js`
- `tests/test_batch_upload_api.py`
- `tests/test_insight_api_client.py`

### 推薦整合方式

- 用 service layer 包住 classifier job orchestration
- 用 adapter 將「分批送件」與「全域結果合併」藏在 client 端
- 保留現有 route，不要把 orchestration 直接塞進 route handler
- 讓前端只看 job / session 狀態，不直接處理分批細節

### 檔案級設計表

| 檔案 | 動作 | 原因 | 預期變更 |
| --- | --- | --- | --- |
| `src/insight_api_client.py` | 修改 | 增加分批 job、統一 progress、結果合併 | 新增 job chunking、job merge、batch progress aggregation |
| `main.py` | 修改 | 接住 job state、輪詢、取消、完成狀態 | 讓批次流程不再依賴單次同步分群 |
| `src/batch_state_store.py` | 修改 | 保存 job / batch state | 增加 job 列表、chunk index、last status、cancel state |
| `static/js/app.js` | 修改 | 顯示輪詢進度與取消 | 進度視窗要能顯示 queued / running / success / failed / cancelled |
| `tests/test_insight_api_client.py` | 修改 | 驗證分批送件與合併 | 新增 200 張、20 張一批的回歸測試 |
| `tests/test_batch_upload_api.py` | 修改 | 驗證 API 不超時與狀態回傳 | 新增 job mode / cancel / progress cases |

## 實作步驟

### 1. 定義 classifier job 模型

目前已完成：Classifier 接受一個完整邏輯工作，服務內部以 `INSIGHT_PROCESS_BATCH_SIZE`
分批偵測，全部 embedding 收齊後才做一次全域 `fit_predict`。這避免跨批次人物被錯誤拆分，
且不把圖片寫入持久化儲存。

- 收檔時建立 `batch_job`
- job 內記錄：
  - `session_id`
  - `job_id`
  - `status`
  - `batch_index`
  - `batch_size`
  - `total_files`
  - `started_at`
  - `finished_at`
  - `error_message`
- 每批只處理固定數量照片，避免一次打進 classifier 太多檔案

### 2. 讓 classifier 支援分批收件

目前已完成服務內部分批處理與全域分群；Identifier 不再把每個 20 張區塊建立成獨立分群工作。

- identifier 端將照片切成多批
- 每批送到 classifier
- classifier 回傳該批結果後，identifier 只更新狀態，不阻塞整體 request
- 若 classifier 支援持久化，優先保存每批 embedding 與 face metadata

### 3. 加入全域完成條件

- 全部批次送完後，才進行最終聚合
- 若 classifier 仍是分批 DBSCAN，至少要把批次結果保留，避免前一批資料遺失
- 進度回報要能反映整體完成率，而不是單一批次完成率

### 4. 前端改成輪詢式等待

目前已完成既有等待視窗的狀態、進度、佇列位置與取消回饋；Classifier 內部批次狀態也會轉成
較容易理解的「正在分批辨識」文案。

- 提交後立即拿到 `session_id`
- 等候視窗顯示：
  - 已處理幾張
  - 目前狀態
  - 前面還有幾個工作
  - 可取消
- 不要讓 UI 直接等同步 response 完成

### 5. 補清理與失敗處理

- `queued` 超時要自動取消
- `running` 超時要自動標失敗
- `cancelled` 要能明確回報，不留殭屍工作
- 進度查詢要能列出前面卡住的是哪個 job

### 6. 對齊部署變數

- `Vercel` 只保留控制平面必要變數
- `Cloud Run` 保留 classifier 所需的 API key 與佇列參數
- batch size、total max files、concurrency 要分成：
  - 使用者一次可準備的總量
  - 系統每批送件大小
  - 每階段可併行處理量

## 測試與驗收

- `pytest tests/test_insight_api_client.py tests/test_batch_upload_api.py tests/test_upload_batch.py`
- `vercel build`
- classifier job API 測試：
  - 單批成功
  - 多批成功
  - 中途取消
  - queued timeout
  - running timeout
- 前端驗收：
  - 200 張上傳後仍會進入等待視窗
  - 等待視窗可取消
  - 完成後可顯示總進度與結果

## 風險與注意事項

- 如果 classifier 還是每批獨立 DBSCAN，跨批次一致性會有限
- 如果 Cloud Run 只做同步處理，仍會遇到 request timeout
- 如果 Vercel 仍直接等待完成，300 秒限制還是會碰到
- 如果 job state 不持久化，重啟後會失去排隊與進度
- 如果批次設定只改名字不改語意，之後還是會混淆「總量」和「每批大小」

## 可交給 Cursor / Codex 的 Prompt

請先讀 `main.py`、`src/insight_api_client.py`、`src/batch_state_store.py`、`static/js/app.js`、`tests/test_batch_upload_api.py`、`tests/test_insight_api_client.py`。

目標是把批次工作改成非同步 job 模型：Vercel 只負責送出與輪詢，Cloud Run classifier 負責分批收件與最後聚合。不要讓單一 request 撐過 300 秒。保留現有 API 盡量相容，但要把「總可準備張數」和「每批送件張數」分開命名，避免再混淆。

實作順序：

1. 先補 classifier job 模型與分批聚合邏輯。
2. 再把 identifier 的批次流程改成送件後立即回 session。
3. 然後更新前端等候視窗與取消按鈕。
4. 最後補測試、回歸檢查與部署變數對齊。

驗收標準：

- 200 張以上可正常送件
- Vercel 不需要等待整批結果才回應
- 前端可輪詢進度、可取消、可回報失敗
- 測試通過，且 `vercel build` 成功
