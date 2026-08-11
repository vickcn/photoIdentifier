# 📸 AI 影像辨識與自動歸檔工具：使用說明

歡迎使用 **AI Photo Identifier**！本工具結合了最新的 AI 視覺辨識技術，幫助您快速分析照片中的「人臉」以及關鍵的「辨識證帶顏色」，並能自動根據安全性規則為您的照片進行分類。

---

## 🚀 快速開始：單張照片辨識

如果您只需要快速檢查一張照片，請使用首頁左側的 **「單張模式」**：

1.  **上傳照片**：點擊上傳區域或直接將照片拖入。
2.  **AI 分析**：程式會自動將照片送往 AI 進行掃描。
3.  **查看結果**：
    *   **視覺化框選**：照片上會出現紅色框（人臉）與青色框（名牌帶子）。
    *   **安全指標**：右側會顯示「✅ 適合公開」或「⚠️ 不建議公開」。
    *   **細節資訊**：顯示偵測到的人臉數量與帶子顏色。

### 本機如何開啟

如果你要在本機測試整個介面，先進到專案目錄，啟動 FastAPI：

```bash
source ~/tchop/bin/activate
python main.py
```

預設會開在 `http://localhost:6419`。如果要改 host 或 port，可先設定環境變數再啟動：

```bash
export HOST=127.0.0.1
export PORT=6419
python main.py
```

本機測試時，單張模式與批量模式都可直接在瀏覽器操作；若要驗證後端 API，也可以先跑測試：

```bash
source ~/tchop/bin/activate
python -m pytest
```

---

## 📂 大量處理：本機多檔上傳

如果您電腦裡有多張照片需要處理：

1. 切換至 **「批量處理」** 分頁。
2. 選擇 **「這台電腦」**。
3. 一次選取或拖入多張 JPG、PNG、WEBP 圖片。
4. 點擊開始辨識，系統會逐張顯示進度與標註結果。

系統預設最多接受 3 張、單檔 2MB、合計 4MB；實際限制以畫面顯示為準。超過限制時，請調整設定或改用雲端批次模式。上傳模式不接受伺服器本機路徑，也不會在部署端建立長期暫存資料夾。

如果同一份程式要同時支援 `Vercel` 與本機執行，建議把批次上傳限制與預設併發放在環境變數管理。讀取優先順序是：`環境變數 -> config.json -> 程式預設值`。

- `BATCH_UPLOAD_MAX_FILES`
- `BATCH_UPLOAD_MAX_FILE_MB`
- `BATCH_UPLOAD_MAX_TOTAL_MB`
- `BATCH_UPLOAD_CONCURRENCY`

目前預設值為：

- `BATCH_UPLOAD_MAX_FILES=3`
- `BATCH_UPLOAD_MAX_FILE_MB=2`
- `BATCH_UPLOAD_MAX_TOTAL_MB=4`
- `BATCH_UPLOAD_CONCURRENCY=1`

若在 `Vercel` 環境中執行，批次一次看幾張的上限會收斂到 `3`，避免同時打太多請求到下游辨識服務。

在 **「整場活動」** 模式下，右側有一個預設收合的 **進階分群設定**，可調整 DBSCAN 參數：

- `eps` 越大，越容易把兩張臉併成同一群
- `min_samples` 越大，分群會越保守

不特別調整時，前端會直接採用後端回傳的預設值。
目前預設為 `eps=0.9`、`min_samples=2`。

### 人臉分群執行環境

主 `photoIdentifier` 服務只負責呼叫獨立的 `classifier` API，不再內建 InsightFace、ONNX Runtime、OpenCV 或本地 embedding 分群。分群預設值由 `/api/config` 提供，部署時請確認以下伺服器端環境變數已設定：

- `FACE_CLUSTERING_ENABLED=true`
- `INSIGHT_API_URL`
- `INSIGHT_API_KEY`

若 classifier API 暫時不可用，系統會降級為一般照片公開性審核。

### 批次狀態持久化

批次工作流支援 Firestore 持久化；若要在雲端環境保存 session、照片結果、人物分群與關聯資料，請確認下列變數已設定：

- `BATCH_STATE_BACKEND=auto` 或 `firestore`
- `FIRESTORE_PROJECT_ID`
- `FIRESTORE_DATABASE`
- `PHOTOIDENTIFIER_BACKEND_SERVICE_ACCOUNT_JSON`（Vercel server-side 優先）
- `FIRESTORE_SERVICE_ACCOUNT_JSON`（舊值，保留作為遷移 fallback）

若未設定，系統會退回記憶體模式，不影響本機測試。

---

## ☁️ 雲端自動化：Google Drive 模式

專為遠端與自動化設計，無需佔用您的本機硬碟空間：

1.  切換至 **「批量處理」** 分頁並選擇 **「☁️ Google Drive」**。
2.  **連結帳號**：點擊連結按鈕並登入您的 Google 帳戶進行授權。
3.  **輸入 Folder ID**：
    *   打開您的雲端硬碟資料夾，複製網址列最後一段的字串（即 ID）。
    *   輸入 **來源資料夾 ID** 與 **輸出資料夾 ID**。
4.  **智慧處理**：點擊開始後，AI 會直接從雲端抓圖，標註完畢後**自動依照分類**存回您的雲端資料夾，不需要您再手動搬移。

如果雲端批次與本機批次都正常，但分群結果看起來不對，優先檢查 `eps` / `min_samples` 是否被前端改過，再確認 `INSIGHT_API_URL` 是否指到正確的 Cloud Run 服務。

### 相關設定與排錯

Google OAuth、Google Drive API、API key restriction、OAuth client、project number 這些設定都會影響登入與授權流程，請在部署前逐項確認。

若遇到登入失敗、Picker 403、redirect URI 不符、scope 或 session 不一致等問題，建議優先引用 `$google-api-session-patterns`，再依該 skill 的通則檢查：

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_API_KEY`
- `GOOGLE_PROJECT_NUMBER`
- `GOOGLE_REDIRECT_URI`
- `SESSION_SECRET`

如果是在 Vercel 上跑 Google Drive 模式，還要確認 `DRIVE_TOKEN_DIR`、`GOOGLE_CLOUD_PROJECT` 與平台上的 server-side env 是否一致。

---

## ⚖️ 安全辨識規則 (Moderation Rules)

本工具內建了一套嚴格的視覺審核邏輯：

*   **✅ 判定為「適合公開」**：
    *   有名牌帶子，且顏色判定為 **「青色 (Cyan/Teal)」**。
    *   完全沒有名牌帶子，且無其他違規內容。
*   **⚠️ 判定為「不建議公開」**：
    *   帶子顏色判定為 **「藍色 (Blue)」**。
    *   影像包含敏感內容（隱私資訊）。

---

## 💡 使用小撇步

*   **辨識框顏色**：
    *   🧱 **紅色框**：人臉偵測。
    *   💠 **青色框**：名牌/吊繩偵測。
*   **如何取得資料夾 ID？**：
    *   Google Drive 網址範例：`drive.google.com/drive/folders/1abc123...`
    *   中間那串 `1abc123...` 就是 ID。

如有任何使用上的異常，請確認您的網路連線是否正常，或檢查 OAuth 授權、Cloud Run 狀態與 Firestore / classifier 服務是否可用。
