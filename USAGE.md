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

---

## 📂 大量處理：本機多檔上傳

如果您電腦裡有多張照片需要處理：

1. 切換至 **「批量處理」** 分頁。
2. 選擇 **「這台電腦」**。
3. 一次選取或拖入多張 JPG、PNG、WEBP 圖片。
4. 點擊開始辨識，系統會逐張顯示進度與標註結果。

系統預設最多接受 3 張、單檔 2MB、合計 4MB；實際限制以畫面顯示為準。超過限制時，請改用 Google 雲端資料夾模式。上傳模式不接受伺服器本機路徑，也不會在部署端建立長期暫存資料夾。

如果同一份程式要同時支援 `Vercel` 與本機執行，建議把批次上傳限制與預設併發放在環境變數管理。讀取優先順序是：`環境變數 -> config.json -> 程式預設值`。

- `BATCH_UPLOAD_MAX_FILES`
- `BATCH_UPLOAD_MAX_FILE_MB`
- `BATCH_UPLOAD_MAX_TOTAL_MB`
- `BATCH_UPLOAD_CONCURRENCY`

### 人臉分群執行環境

主 Vercel 服務預設設定 `FACE_CLUSTERING_ENABLED=true`，使用 CPU 執行 InsightFace。部署同時設定 `VERCEL_SUPPORT_LARGE_FUNCTIONS=1`，以容納 ONNX Runtime、OpenCV 與 `buffalo_l` 模型所需空間。模型初始化或推論失敗時，系統會降級為一般照片公開性審核。

本機需要完整的人臉分群時：

```bash
source ~/tchop/bin/activate
python -m pip install -r requirements.txt
export FACE_CLUSTERING_ENABLED=true
python main.py
```

設定優先順序同樣是 `環境變數 -> config.json -> 程式預設值`。Vercel 使用 `/tmp/insightface` 作為 instance-local 模型快取；冷 instance 可能需要重新下載模型，因此第一次分群會比後續請求慢。

---

## ☁️ 雲端自動化：Google Drive 模式

專為遠端與自動化設計，無需佔用您的本機硬碟空間：

1.  切換至 **「批量處理」** 分頁並選擇 **「☁️ Google Drive」**。
2.  **連結帳號**：點擊連結按鈕並登入您的 Google 帳戶進行授權。
3.  **輸入 Folder ID**：
    *   打開您的雲端硬碟資料夾，複製網址列最後一段的字串（即 ID）。
    *   輸入 **來源資料夾 ID** 與 **輸出資料夾 ID**。
4.  **智慧處理**：點擊開始後，AI 會直接從雲端抓圖，標註完畢後**自動依照分類**存回您的雲端資料夾，不需要您再手動搬移。

### 相關設定與排錯

Google OAuth、Google Picker、Google Drive API、API key restriction、OAuth client、project number 這些專有名詞可直接寫在專案文件中，方便排錯與溝通。

若遇到登入失敗、Picker 403、redirect URI 不符、scope 或 session 不一致等問題，建議優先引用 `$google-api-session-patterns`，再依該 skill 的通則檢查：

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_API_KEY`
- `GOOGLE_PROJECT_NUMBER`
- `GOOGLE_REDIRECT_URI`
- `SESSION_SECRET`

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

如有任何使用上的異常，請確認您的網路連線是否正常，或檢查 Google Drive 授權是否已過期。
