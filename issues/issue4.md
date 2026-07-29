# Issue 4：整場活動的人臉分類工作台

狀態：第一階段已實作，合併、拆分與持久化待後續完成。

部署決策：主 Vercel 服務與本機 `tchop` 都以 `FACE_CLUSTERING_ENABLED=true` 預設啟用 InsightFace。Vercel 使用大型 Function 支援、CPU provider 與 instance-local 模型快取；模型失敗時降級為一般照片審核。這個開關遵循 `環境變數 -> config.json -> 程式預設值`。

建議先引用：

- `$implementation-planner`
- `$management-app-design-principles`
- `$frontend-micro-interactions`
- `$build-web-apps:frontend-app-builder`

這個需求不是單純把單張辨識結果排版得更漂亮，而是要把「整場活動」的處理流程升級成可管理的人臉分類工作台。核心不是照片本身，而是跨照片的人物群組、確認狀態、命名、合併、拆分與後續整理。
目前預期會直接使用 `tchop` 裡的 `insight` 模組，因此一開始輸出的群組很可能只有流水 ID，不會先有可讀名稱；前端必須負責補上命名與顯示語意。
這份計畫要對應既有的 `src/face` 功能來做，不是另起一套人臉系統。

## 需求重點

- 下方區塊要從「單張結果摘要」改成「整場活動的人臉分類工作台」
- 能看到整場活動的人臉群組，而不是只看單一照片的結果卡
- 每個群組要能檢視多張照片中的同一人物證據
- 需要有群組確認、待確認、加入既有人物、標記為不同人等操作
- 需要能對群組命名、加註記、追蹤操作紀錄
- 一開始若後端只給流水 ID，前端必須支援把群組或類別命名成可讀名稱
- 混淆矩陣與批次摘要可保留，但應該退到輔助摘要，不佔主工作區
- 舊有的下載、整理、finalize review 流程要保留相容性

## 建議方向

### 1. 主體從「照片」改成「人物群組」

這個功能的資料主體應該是 `face cluster`，不是單張圖片。
如果後端暫時只能輸出流水 ID，那前端就要把它視為「未命名群組」，並提供手動命名入口。

建議每個群組至少包含：

- `cluster_id`
- `display_name`
- `status`：未命名 / 待確認 / 已確認 / 已合併
- `face_count`
- `photo_count`
- `representative_faces`
- `evidence_photos`
- `notes`

這樣前端就能直接以「人」為單位工作，而不是一直在單張圖之間切換。

### 2. 後端先做群組聚合，再讓前端呈現

如果要讓整場活動的人臉分類真的可維護，後端最好提供一層群組化結果，而不是只回每張照片的局部偵測。

先可行的做法是：

- 保留每張照片的辨識結果
- 額外輸出臨時群組資料
- 讓前端把結果渲染成「群組清單 + 群組詳情」

長期再把真正的 embedding 聚類、合併、拆分、重算邏輯補完整。

### 2.1 對應 `src/face` 的現有功能分工

現有 `src/face` 已經有現成的拆法，這份工作台應該直接接上它，而不是重寫：

- `src/face/detector.py`：做人臉偵測，輸出臉框
- `src/face/annotation.py`：畫框、標註、後製圖
- `src/face/clustering.py`：做人臉群聚或相似度分組
- `src/face/models.py`：放群組、臉、證據圖等資料結構
- `src/face/pipeline.py`：把偵測、聚類、標註串成一條工作流

因此這個 issue 的後端目標不是「再做一次偵測」，而是：

- 讓 pipeline 可以吐出群組資料
- 讓 clustering 的結果可被前端顯示與命名
- 讓 annotation 的結果可當作群組證據圖
- 讓 models 的資料結構穩定支撐前後端傳輸

### 3. 前端下方整塊換成工作台

你截圖下方現在的區塊，最適合直接替換成三欄式工作台：

- 左欄：群組清單
- 中欄：群組詳情與證據照片
- 右欄：判定與操作面板

這種結構的好處是：

- 適合大量照片與大量人物
- 方便快速切換群組
- 可以同時放命名、確認、備註與操作紀錄
- 比單張結果卡更符合管理型系統的語意
- 群組名稱要可直接編輯，因為後端先給的可能只是 `cluster_001` 這種流水 ID

### 4. 保留現有 metrics，但降為輔助區

混淆矩陣、分類準確率、一致率這些資訊不應該消失，但位置應該下移。

建議保留成：

- 一場活動的分類摘要
- 群組確認統計
- AI 與使用者覆寫差異
- 匯出 JSON / CSV

它們應該服務工作台，不應該主導工作台。

### 5. 先做穩定重構，不急著一次完成完整聚類

最小可行方案可以先把資料整理成可操作群組，讓介面成立。

後續再補：

- 真正的跨照片 embedding 聚類
- 群組合併與拆分
- 代表臉挑選
- 群組層級的重算與同步

這樣可以避免一次把資料模型、聚類演算法、前端互動全部綁死。

### 5.1 `.npy` 暫存與清理策略

如果 `src/face` 或 `tchop insight` 會產生 embedding / clustering 用的 `.npy`，它們應該被視為「批次作業的中間產物」，不能當成長期資料。

建議策略：

- 每個批次獨立一個 `session_id` 目錄
- 目錄內用 `manifest.json` 記錄 `created_at`、`last_access_at`、`status`、`expires_at`
- `.npy` 只放在 `working/` 或 `cache/` 類型的暫存區，不進長期保存區
- 作業完成後若不再需要 embedding，就主動刪掉 `.npy`
- 清理器只刪超過 TTL 且沒有 `lock` 的 session 目錄
- `running`、`completed`、`exported`、`failed` 要有不同 TTL
- 使用 heartbeat 更新 `last_access_at`，避免長任務被誤刪
- Vercel 上不要依賴本機 `.npy` 長期存在，重要狀態要另外落地
- 這些清理參數應由 `config.json` 管理，並維持 `環境變數 -> config.json -> 程式預設值` 的讀取優先順序

清理規則可先定成：

- `running` 且長時間沒有 heartbeat：標記 stale
- `completed/exported` 超過保留期：刪除
- `failed` 超過短保留期：刪除
- 遇到 `.lock`：跳過
- `manifest.json` 缺失或不完整的孤兒資料夾：直接回收

建議可先放進 `config.json` 的參數：

- `cache_cleanup_enabled`
- `cache_cleanup_interval_minutes`
- `cache_retention_running_minutes`
- `cache_retention_completed_hours`
- `cache_retention_exported_hours`
- `cache_retention_failed_hours`
- `cache_stale_heartbeat_minutes`
- `cache_keep_lock_files`
- `cache_max_session_age_hours`

### 6. 介面微互動要明確

工作台會用到很多狀態切換，互動要清楚：

- 選中的群組要有明顯 selected state
- 待確認與已確認要有不同視覺語意
- 進行中要有局部 loading，不要整頁鎖死
- 群組切換要保留位置，不要每次重繪就跳回頂端
- 按鈕與標籤要維持管理工具的清楚層級，不要做成裝飾卡片

## 影響範圍

- `main.py`
- `photoIdentifier.py`
- `src/metrics.py`
- `src/face/*`
- `static/app.js`
- `static/app.css`
- `template/index.html`
- 匯出 JSON / CSV
- `finalize_review` 後續流程

## 推薦實作順序

1. 先定資料模型，明確區分 `photo result` 與 `face cluster`
2. 再讓後端輸出暫定群組資料，至少先有流水 ID 與群組證據，且接在 `src/face/pipeline.py` / `src/face/clustering.py`
3. 接著把前端下方區塊改成群組工作台，並先支援命名流水 ID
4. 再補合併、拆分、備註、操作紀錄
5. 再回頭調整 metrics 與下載格式

## 驗收條件

- [x] 批次完成後，下方優先顯示群組工作台，而不是舊的照片牆
- [x] 可以看到每個群組的證據照片
- [x] 可以切換群組並同步更新詳情
- [ ] 可以對群組做確認、待確認、加入既有人物、標記不同人
- [x] 如果群組一開始只有流水 ID，前端可以把它改成可讀名稱
- [x] 可以為群組命名與加註記
- [x] 原本的下載與 finalize review 保留相容
- [x] 手機版改為單欄工作台，避免三欄擠壓

## 風險

- 若沒有先定義群組資料模型，前後端會一起變成半成品
- 只改視覺、不改資料主體，後面很容易重工
- 群組化如果只做在前端，資料同步與匯出會變得脆弱
- 後端先只給流水 ID 沒問題，但前端一定要有命名層，否則工作台不可用
- 若一次塞太多操作，手機版會失去可用性

## 實作建議

先做最小可行版本：

1. 先把下方單張結果區換成群組工作台的骨架
2. 再補後端群組資料輸出
3. 先支援群組查看與命名
4. 再補確認、待確認、加入既有人物、標記不同人
5. 最後再做完整聚類與匯出同步
