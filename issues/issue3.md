# Issue 3：本機批次功能擴充為多檔上傳

狀態：已實作，待部署環境驗收。

建議先引用：

- `$implementation-planner`
- `$management-app-design-principles`

這個需求不是單純把「本機路徑」換成「上傳檔案」，而是要把批次處理的輸入生命週期重新整理成可部署、可清理、可追蹤的管理流程。

## 需求重點

- 不再接受本機路徑當作正式雲端入口參數
- 支援一次上傳多個檔案，但限制在小批次
- 上傳後可直接進入批次處理流程
- 不在部署端長期暫存上傳檔案
- 以「多檔總大小上限」與「單檔大小上限」控制輸入量，預設壓低到 Vercel 可承受範圍
- 超過上限時直接引導使用者改用雲端檔案模式

## 建議方向

### 1. 入口改成多檔上傳

正式入口應改為 multipart upload，而不是 `input_folder`。

建議形式：

- `files: list[UploadFile]`
- 另外保留 `concurrency`
- 另外保留 `skip_annotations`
- 另外保留 `collaborative_memory`

### 2. 不暫存到部署端，直接做輸入限制

這次的決策是：**不要把上傳檔案暫存到部署端**。

改成先檢查：

- 單檔大小
- 多檔總大小
- 檔案數量

任一條件超過上限，就直接拒絕並提示使用者改用自己的雲端檔案模式。

目前建議的保守預設是：

- 最多 3 張
- 單檔 2MB
- 總計 4MB

這樣可以保留多檔入口，同時避免 request body 太快撞到 Vercel 的 4.5MB 上限。

這樣可以避免：

- 干擾系統記憶體
- 造成部署端暫存清理問題
- 讓 Vercel 的 ephemeral filesystem 變成依賴點

### 3. 批次處理只處理允許範圍內的檔案

如果檔案沒有超過限制，就直接在一次請求內處理，不另外建立部署端暫存區。

真正需要保存的內容，仍然保留雲端模式：

- Google Drive
- 使用者自己的雲端儲存
- 之後若有需要再接外部物件儲存

### 4. 只對局部同步環節使用 threadpool

可以考慮把少數同步阻塞的小環節丟到 `threadpool` 或 `asyncio.to_thread`，例如：

- 圖片 resize
- 後製畫框
- 本地人臉偵測

用途只限於避免 event loop 被卡住，不把它當成主要加速手段。整體併發仍然要以部署端負荷為先，預設維持低併發，並先觀察單筆耗時與記憶體占用，再決定是否保留這種局部 offload。

### 5. 環境變數備忘

正式部署時，以下環境變數可覆蓋 `config.json`，讀取優先順序為 `環境變數 -> config.json -> 程式預設值`。

- `MAX_UPLOAD_SIZE_MB`：單張上傳上限，單圖模式共用
- `BATCH_UPLOAD_MAX_FILES`：批次最多檔案數
- `BATCH_UPLOAD_MAX_FILE_MB`：批次單檔大小上限
- `BATCH_UPLOAD_MAX_TOTAL_MB`：批次總大小上限
- `BATCH_UPLOAD_CONCURRENCY`：批次預設併發數
- `VERTEX_API_KEY`：Vertex AI 呼叫金鑰
- `SESSION_SECRET`：FastAPI session 加密金鑰
- `GOOGLE_CLIENT_ID`：Google OAuth client id
- `GOOGLE_CLIENT_SECRET`：Google OAuth client secret
- `GOOGLE_PROJECT_NUMBER`：Google Picker / Drive 相關識別值
- `GOOGLE_REDIRECT_URI`：OAuth callback URL
- `DRIVE_TOKEN_DIR`：本機 token 暫存目錄
- `GOOGLE_API_KEY`：Google Picker / API 啟用用 key

## 影響範圍

- `main.py`
- `photoIdentifier.py`
- `src/face/*`
- 前端上傳元件
- 輸入驗證 helper
- 文件與使用說明

## 驗收條件

- [x] 可一次上傳多個檔案
- [x] 正式前端入口不依賴本機路徑
- [x] 上傳前會檢查檔案數量、單檔與總大小上限
- [x] 超過上限會直接提示改用雲端模式
- [x] 後端以 NDJSON 逐筆回傳結果，不建立長期暫存資料夾
- [ ] Vercel 上可正常部署與完成實圖辨識

## 風險

- request body 太大
- 執行時間超時
- batch 任務同步回應過久

## 實作建議

先做最小可行版本：

1. 先新增多檔上傳 endpoint
2. 再做檔案大小與數量驗證
3. 超限時導向雲端模式說明
4. 最後再補前端批次上傳 UI
