# MEMORY

## 目前進度

- Firebase 專案已初始化並綁定到 `photoidentifier-prod`。
- Firestore 已完成 `firebase init` 設定。
- Firestore database location 設為 `asia-east1`。
- 已產生並寫入以下檔案：
  - `firebase.json`
  - `.firebaserc`
  - `firestore.rules`
  - `firestore.indexes.json`
- 已執行 `python tmp_dev/init_firestore.py`，Firestore 初始化成功。
- 已建立專用 Firestore service account：`photoidentifier-firestore@vision-493709.iam.gserviceaccount.com`。
- 已完成第一階段 Firestore 持久化骨架：
  - `batch_sessions`
  - `batch_jobs`
  - `face_clusters`
  - `photo_items`
  - `photo_assignments`
  - `exports`
- `photo_items.result_summary` 已改為 Firestore-safe 的扁平/JSON 字串儲存，避免 nested entity 寫入錯誤。
- `batch_sessions` 查詢已加入無索引 fallback，避免本機測試或尚未建立複合索引時直接失敗。
- `photoclassifier` Cloud Run 已調整為 `maxScale=3`、`containerConcurrency=1`，以降低 burst 造成的 429。
- `photoIdentifier` 批次預設併發已降為 `1`，Vercel 環境上限收斂到 `3`。
- `USAGE.md` 已更新到目前現況：
  - 本機預設埠號 `6419`
  - Firestore 批次持久化說明
  - 獨立 classifier API 與 Vercel server-side env 的設定說明
- 前端批量模式只保留本機瀏覽器上傳，不再提供伺服器本機資料夾路徑輸入。
- 人臉分群與可公開性判定已拆成可獨立執行的功能；預設只開人臉分群，`可公開性判定` 預設關閉。
- 可公開性判定的前端設定已暫時註解，包括相關選項、顏色規則區塊與 `就這樣，整理好` 按鈕；後續再決定是否恢復。
- 雲端模式在分群後會提供獨立的 `儲存辨識結果` 按鈕；若已有輸出區就直接存入，沒有輸出區則先開 Picker 選資料夾再立即執行。

## 已確認的測試資料

- `user_id`: `test-user-001`
- `photo_id`: `Dm2R0rP7YFvEcHOzsqbf`
- `person_id`: `JcDRNaNKcxxNAiWDxLrV`
- `face_id`: `aZd4PsD5eDC05NErSIZb`
- `job_id`: `5BJ5fbMNWXPEqBpPypty`

## 目前的技術方向

- 原圖不由系統長期保存，圖片仍由使用者自己的雲端位置管理。
- 系統優先保存圖片 metadata、face records、人物群組與後續辨識/分群所需索引。
- 持久化資料以可查詢、扁平欄位為主；大塊巢狀結果只在必要時用 JSON 字串保留。
- batch / Face workspace 的讀取流程要能在本機記憶體、Firestore 與無索引 fallback 三種狀態下都可用。
- 前端 / 後端 / 部署環境的批次預設值要一致，避免 UI 可選值高於 runtime cap。
- Vercel 環境只放 server-side env；`INSIGHT_API_KEY`、Firestore service account JSON 這類值不可進前端 bundle。
- `photoclassifier` 只負責獨立人臉辨識 API；`photoIdentifier` 以呼叫 API 與工作流整合為主。
- batch 的本機上傳與雲端 Drive 工作流要維持分開，下載結果與儲存到 Drive 也要分開，不要把兩者綁成同一個動作。
- Drive 模式的儲存流程要支援「已有目標資料夾直接存」與「先 Picker 選目標再存」兩條路徑。
- 前端文案要用有溫度、但不失準確的方式來設計；延續過序的書寫風格；避免過度冰冷、機械或像系統錯誤訊息的語氣。

## 待辦

- 圖片登記流程與寫入條件仍可再細化，但已可先用人物關聯與匯出 JSON 運作。
- 若之後要提高吞吐量，先調上游 fan-out，再考慮放寬雲端服務 cap。

# GCP 部署準則

- 部署位置與業務邏輯要分離，程式不要硬寫特定網域。
- 一律用 env 注入 `APP_BASE_URL`、`API_BASE_URL`、`PUBLIC_APP_ORIGIN` 這類值。
- 同一份 code 要能在本機、Vercel、Cloud Run 共用。

- GCP 的 project、billing、IAM、Secret Manager、Artifact Registry、Cloud Run 要分開看。
- 先確認資源歸屬，再做跨服務串接。
- 不要把部署流程、runtime secret、OAuth 憑證混在一起。

- 部署用憑證與 runtime 憑證必須分開。
- CI/CD 只負責 build / deploy。
- 服務執行時使用的 service account 只負責 runtime 存取。

- OAuth redirect URI 必須和實際上線網域完全一致。
- 切換部署平台時，先更新 OAuth 設定，再切流量。
- 不要使用「看起來差不多」的網址。

- 先驗證 staging，再動 production。
- 先跑通登入、讀寫、下載、CORS、signed URL，再切正式環境。
- 不要直接把 production traffic 移到未驗證的 deployment。

- 敏感資訊只放 Secret Manager 或等價機制。
- 不要把 private key、client secret、service account JSON 寫進 repo、log 或測試輸出。
- 需要輪替時，優先用 secret version，不要改程式碼。

- 先確認服務實際使用的 service account。
- 不要憑印象補 IAM。
- Cloud Run 的 runtime identity 要以實際設定為準，不以預期為準。

- IAM 一律最小權限。
- bucket 權限只給 bucket scope，不要升到 project level。
- 需要上傳、讀取、刪除，就只給對應最小角色。

- API 啟用、billing、service account、IAM、secret binding 要先於部署完成。
- 如果 deploy 失敗，先查 API 是否已開、billing 是否已接。
- 不要先跑部署再回頭補權限。

- 設定項要集中管理，不要散落。
- OAuth origins、CORS allowlist、bucket 名稱、signed URL TTL、base URL 盡量集中在少數幾個位置。
- 避免 Vercel、一份 GCP、一份 local 互相不一致。

- 每次改動都要能回答三個問題：
  - 現在誰在跑？
  - 它能存取什麼？
  - 失敗時會落回哪裡？

- 驗證順序由外到內。
- 先確認 domain、redirect URI、CORS。
- 再確認 auth、secret、IAM。
- 最後確認業務功能與資料結果。

- 不要硬綁特定平台網域。
- 不要混用部署憑證與 runtime 憑證。
- 不要把測試環境設定直接搬到 production。
- 不要在未驗證前切 production 流量。
- 不要用 project-level 大權限去偷跑。
