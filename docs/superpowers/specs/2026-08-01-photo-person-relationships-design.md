# 照片與人物關聯紀錄設計

## 目標

批次辨識完成後，使用者可從「人物角度」或「照片角度」檢視同一批人臉分群結果、修正人物名稱與照片人物關聯，最後下載 JSON 紀錄。Google Drive 模式若指定輸出資料夾，下載時還要在該資料夾新增一份不覆蓋舊檔的時間戳 JSON。

## 管理物件與生命週期

本功能管理三種物件：

- 人物群組：由 InsightFace 分群產生，以 `cluster_id` 作為批次內穩定識別碼；使用者可修改 `display_name`、確認狀態與備註。
- 照片：沿用批次結果的 `file_name`、Drive `drive_id` 與照片公開判定。
- 照片人物關聯：以 `file_name` 對應一組 `cluster_id`；初始值由分群證據自動建立，使用者可在照片小視窗勾選或取消。

生命週期為：

```text
辨識完成 -> 自動建立關聯草稿 -> 人物改名／關聯校正 -> 本機匯出 -> 選配 Drive 備份
```

目前批次 session 為記憶體內暫存，因此編輯結果以當次頁面與匯出 JSON 為正式紀錄，不新增長期資料庫。

## 介面設計

批次結果上方新增主要檢視切換：

- `人物角度`：預設檢視。顯示人物折疊列表；列上顯示最新名稱、照片數與人臉框數。展開後保留九宮格人臉框、照片判定、人物名稱／狀態／備註編輯。
- `照片角度`：顯示照片折疊列表；列上顯示檔名、公開判定與已登記人物數。展開後顯示縮圖、公開判定、目前人物標籤與「編輯人物」按鈕。

兩種列表都預設折疊，展開狀態各自保存在 JavaScript `Set`，切換角度或重新渲染時不會互相污染。

「編輯人物」開啟單一共用 modal：

- 列出本批次所有人物群組。
- 每列顯示 `display_name` 與 `cluster_id`。
- 目前已綁定人物預先勾選。
- 儲存後更新該照片的 `cluster_id` 集合並局部重繪。
- 人物名稱不複製進關聯狀態；畫面永遠從人物群組讀取最新名稱。

## 關聯初始化與一致性

前端收到 `face_clusters` 後，走訪每個群組的 `evidence_photos`，依 `file_name` 建立初始關聯：

```text
photoPeople[file_name] = Set(cluster_id, ...)
```

同一人物在同一照片出現多個人臉框時，關聯只保留一次。使用者手動修改後，後續畫面重繪不得用自動偵測結果覆蓋人工結果；只有新批次開始時才重建關聯草稿。

人物改名透過既有 `PATCH /face_clusters/{session_id}/{cluster_id}` 儲存。照片關聯在匯出前保留於前端草稿，JSON 生成時依 `cluster_id` 展開最新 `display_name`。

## JSON 格式

本機下載與 Drive 備份使用同一份 JSON 字串，避免兩份內容不同：

```json
{
  "exported_at": "2026-08-01T07:30:00.000Z",
  "session_id": "batch-session-id",
  "batch_mode": "drive",
  "people": [
    {
      "cluster_id": "cluster_001",
      "display_name": "王小明",
      "status": "confirmed",
      "notes": "講師"
    }
  ],
  "photos": [
    {
      "file_name": "photo-01.jpg",
      "drive_id": "drive-file-id",
      "public_decision": "safe",
      "people": [
        {
          "cluster_id": "cluster_001",
          "display_name": "王小明"
        }
      ]
    }
  ]
}
```

既有詳細辨識 `results` 與 `face_clusters` 可繼續保留在匯出檔，新增的 `people`、`photos` 作為較穩定且容易被其他系統讀取的關聯索引。匯出前必須移除 `original_image_b64`、`drawn_image_b64` 與人物證據中的 `image_b64`；保留 bbox 與檔名，但不把照片內容嵌入 JSON。

## Drive 匯出 API

新增資源導向端點：

```text
POST /batch_exports/drive
```

請求包含：

```json
{
  "session_id": "batch-session-id",
  "target_folder_id": "drive-folder-id",
  "document": {}
}
```

後端必須：

- 驗證 Google 登入與 Drive credentials。
- 用 `_owned_batch_session` 驗證 session 屬於目前瀏覽器使用者。
- 驗證 `target_folder_id` 非空、文件的 `session_id` 與請求一致、JSON 可序列化。
- 拒絕超過 10 MB 的 JSON 文件，避免無界限的請求與 Drive 上傳。
- 以 `photo_people_YYYYMMDD_HHMMSS.json` 新增至指定資料夾，不搜尋或覆蓋舊檔。
- 回傳 `file_id`、`file_name` 與狀態；錯誤需提供可操作訊息。

前端下載順序：先產生單一 JSON，再立即下載到本機；若 `batchMode === "drive"` 且已填輸出資料夾，接著呼叫 Drive 匯出 API。Drive 失敗不能撤銷本機下載，只顯示「本機已下載，但雲端備份失敗」。

## 錯誤與邊界

- 尚未產生人物分群：照片角度仍可檢視判定，但人物清單為空，modal 顯示「未偵測到人物」。
- 照片沒有綁定人物：JSON 的 `people` 為空陣列，這是有效狀態。
- 人物名稱同步失敗：保留當次瀏覽器草稿並明確提示；匯出使用畫面上的最新名稱。
- Drive 未登入或授權失效：本機下載照常完成，Drive API 回傳 401。
- Drive 寫入失敗：回報原因，不重複自動送出，避免建立多份不明確檔案。
- 相同檔名：現有分群與前端皆以 `file_name` 關聯；本次不擴張識別模型。Drive 模式匯出同時保留 `drive_id`，後續可再升級為複合鍵。

## 測試

後端自動測試：

- 未登入時拒絕 Drive 匯出。
- 非 session 擁有者看不到或匯出不了該批次。
- 成功時以時間戳檔名、指定 parent 與 `application/json` 建立檔案。
- Drive 建立失敗時回傳可理解的錯誤。

前端靜態與人工驗證：

- 人物／照片角度切換正確，兩邊預設折疊。
- 人物改名後，照片列與 modal 立即顯示新名稱。
- 自動關聯去重，人工勾選結果在重繪後保留。
- 點照片列可看到公開判定；modal 可用鍵盤關閉與儲存。
- 本機模式只下載本機；Drive 無輸出區只下載本機；Drive 有輸出區同時本機下載與雲端新增。
- JSON 中每張照片的人物 ID 與名稱一致。
- 桌面與手機寬度下列表、modal 與按鈕皆可操作。

## 不在本次範圍

- 跨 session 的人物主檔或永久人物資料庫。
- 跨活動辨識同一自然人。
- 將照片人物關聯逐筆持久化到資料庫。
- 覆蓋或更新既有 Drive JSON。
- 修改 InsightFace 分群演算法。
