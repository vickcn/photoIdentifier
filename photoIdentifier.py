import base64
import asyncio
import mimetypes
import io
import json
import logging
from PIL import Image
from pathlib import Path
from typing import Tuple, List, Optional
from src.google_usage import analyze_brand_strap_image, PhotoAnalysisResult
from src.aoi import draw_bboxes_on_image
from src.insight_api_client import detect_normalized_bboxes
from src.upload_batch import UploadedImage

logger = logging.getLogger(__name__)

# 讀取 config.json 中的 request_timeout 設定
try:
    with open(Path(__file__).parent / "config.json", encoding="utf-8") as f:
        _cfg = json.load(f)
    REQUEST_TIMEOUT = int(_cfg.get("request_timeout", 600))
except Exception:
    REQUEST_TIMEOUT = 600

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

def resize_image_if_needed(image_bytes: bytes, max_size: int = 1600) -> bytes:
    """如果圖片太大的話，將長邊縮放至 max_size，節省傳輸頻寬與 AI 處理時間"""
    img = Image.open(io.BytesIO(image_bytes))
    w, h = img.size
    if max(w, h) <= max_size:
        return image_bytes
    
    # 計算縮放比例
    ratio = max_size / float(max(w, h))
    new_size = (int(w * ratio), int(h * ratio))
    img = img.resize(new_size, Image.Resampling.LANCZOS)
    
    out = io.BytesIO()
    # 轉為 RGB 存檔，確保與 AI 相容
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()

async def process_and_visualize_photo(
    image_bytes: bytes,
    content_type: str = "image/jpeg",
    color_rules: list | None = None,
    collaborative_memory: str | None = None,
    evaluate_public: bool = True,
) -> Tuple[PhotoAnalysisResult, bytes]:
    """
    整合 google_usage 跟 aoi 模組：
    1. 縮圖處理（防止 HTTP 400 並加速）
    2. 將圖片送往 Google Vertex AI 進行邏輯判斷與物件偵測
    3. 解析出 bbox 座標後，將邊界框畫上圖片
    """
    processed_image_bytes = await asyncio.to_thread(resize_image_if_needed, image_bytes)
    if not evaluate_public:
        return PhotoAnalysisResult(
            has_face=False,
            face_bboxes=[],
            face_confidences=[],
            has_brand_strap=False,
            strap_bboxes=[],
            strap_confidences=[],
            strap_color=None,
            is_safe_for_public=False,
            moderation_status="pending",
            moderation_reason="未執行可公開性判定",
            public_classification_performed=False,
        ), processed_image_bytes

    b64_image = base64.b64encode(processed_image_bytes).decode('utf-8')
    local_face_bboxes = await detect_normalized_bboxes(
        processed_image_bytes,
        content_type=content_type,
    )
    analysis_result = await analyze_brand_strap_image(
        b64_image,
        content_type,
        color_rules=color_rules,
        collaborative_memory=collaborative_memory,
        local_face_bboxes=local_face_bboxes,
    )
    
    drawn_image_bytes = await asyncio.to_thread(
        draw_bboxes_on_image,
        image_bytes=processed_image_bytes,
        face_bboxes=analysis_result.face_bboxes,
        strap_bboxes=analysis_result.strap_bboxes,
        strap_color=analysis_result.strap_color,
        face_confidences=analysis_result.face_confidences,
        strap_confidences=analysis_result.strap_confidences,
    )
    return analysis_result, drawn_image_bytes


async def batch_process_folder(
    input_dir: str,
    output_dir: str,
    concurrency: int = 3,
    color_rules: list | None = None,
    collaborative_memory: str | None = None,
    evaluate_public: bool = True,
) -> list[dict]:
    """
    批量掃描資料夾內所有圖檔，並行辨識後將後製圖存至 output_dir
    回傳每張圖的摘要清單
    """
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    image_files = [f for f in input_path.iterdir() if f.suffix.lower() in IMAGE_SUFFIXES]
    if not image_files:
        return []

    semaphore = asyncio.Semaphore(concurrency)

    async def process_one(file: Path) -> dict:
        async with semaphore:
            try:
                image_bytes = file.read_bytes()
                mime_type = mimetypes.guess_type(str(file))[0] or "image/jpeg"
                result, drawn_bytes = await process_and_visualize_photo(
                    image_bytes,
                    mime_type,
                    color_rules=color_rules,
                    collaborative_memory=collaborative_memory,
                    evaluate_public=evaluate_public,
                )
                out_file = output_path / f"annotated_{file.name}"
                out_file.write_bytes(drawn_bytes)

                ai_decision = "safe" if result.moderation_status == "public" else "unsafe" if result.moderation_status == "private" else "pending"

                return {
                    "file": file.name,
                    "original_path": str(file),
                    "output": str(out_file),
                    "has_face": result.has_face,
                    "face_count": len(result.face_bboxes),
                    "has_brand_strap": result.has_brand_strap,
                    "strap_color": result.strap_color,
                    "is_safe_for_public": result.is_safe_for_public,
                    "moderation_status": result.moderation_status,
                    "moderation_reason": result.moderation_reason,
                    "public_classification_performed": result.public_classification_performed,
                    "ai_decision": ai_decision,
                    "status": "ok",
                }
            except Exception as e:
                return {"file": file.name, "status": "error", "error": str(e)}

    tasks = [process_one(f) for f in image_files]
    results = await asyncio.gather(*tasks)
    return list(results)


async def batch_process_uploads_stream(
    images: list[UploadedImage],
    concurrency: int = 3,
    color_rules: list | None = None,
    collaborative_memory: str | None = None,
    evaluate_public: bool = True,
):
    """辨識已驗證的上傳圖片，依完成順序逐筆回傳 NDJSON 所需資料。"""
    semaphore = asyncio.Semaphore(concurrency)

    async def process_one(image: UploadedImage) -> dict:
        async with semaphore:
            try:
                result, drawn_bytes = await process_and_visualize_photo(
                    image.content,
                    image.content_type,
                    color_rules=color_rules,
                    collaborative_memory=collaborative_memory,
                    evaluate_public=evaluate_public,
                )
                return {
                    "status": "ok",
                    "file_name": image.filename,
                    "result": result.model_dump(),
                    "original_image_b64": base64.b64encode(image.content).decode("ascii"),
                    "drawn_image_b64": base64.b64encode(drawn_bytes).decode("ascii"),
                }
            except Exception as exc:
                logger.exception("Upload batch item failed file=%s error=%s", image.filename, exc)
                return {"status": "error", "file_name": image.filename, "error": str(exc)}

    tasks = [asyncio.create_task(process_one(image)) for image in images]
    total = len(tasks)
    for index, task in enumerate(asyncio.as_completed(tasks), start=1):
        yield {**await task, "index": index, "total": total}


async def batch_process_drive(
    folder_id: str,
    credentials,
    target_folder_id: Optional[str] = None,
    concurrency: int = 3,
    collaborative_memory: str | None = None,
    evaluate_public: bool = True,
) -> List[dict]:
    """
    從 Google Drive 批量取得圖片、辨識並回傳結果。
    如果 target_folder_id 有給，則會在內部自動建立 [Safe_Results] 與 [Unsafe_Results]
    並將標註圖分類存放。
    """
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload

    drive_service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    
    # 1. 解析輸出資料夾（自動分類歸檔）
    safe_target_id = None
    unsafe_target_id = None

    if target_folder_id and evaluate_public:
        def get_or_create_subfolder(name: str, parent_id: str):
            q = f"name = '{name}' and '{parent_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            res = drive_service.files().list(q=q, fields="files(id)").execute()
            items = res.get("files", [])
            if items:
                return items[0]["id"]
            # 建立
            meta = {
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id]
            }
            folder = drive_service.files().create(body=meta, fields="id").execute()
            return folder.get("id")

        safe_target_id = await asyncio.to_thread(get_or_create_subfolder, "Safe_Results", target_folder_id)
        unsafe_target_id = await asyncio.to_thread(get_or_create_subfolder, "Unsafe_Results", target_folder_id)

    # 2. 列出來源資料夾內圖片
    q = f"'{folder_id}' in parents and trashed = false"
    # First get ALL files to see what is really there
    test_response = drive_service.files().list(
        q=q, fields="files(id, name, mimeType)", pageSize=100
    ).execute()
    all_files = test_response.get("files", [])
    print(f"[DEBUG] Found {len(all_files)} total items in folder {folder_id} (ignoring mimeType filter).")
    for f in all_files:
        print(f"   -> {f['name']} ({f['mimeType']})")

    # Now filter to just images manually or via query
    files = [f for f in all_files if 'image/' in f['mimeType']]
    print(f"[DEBUG] After filtering for images, {len(files)} items remain.")
    
    if not files:
        return []

    semaphore = asyncio.Semaphore(concurrency)

    import httpx
    download_headers = {"Authorization": f"Bearer {credentials.token}"}
    download_client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)

    async def process_one_drive(file_item: dict) -> dict:
        async with semaphore:
            file_id = file_item["id"]
            file_name = file_item["name"]
            mime_type = file_item["mimeType"]
            
            try:
                # 3. 下載內容 (純非同步 HTTP 請求，完全避免 httplib2 阻塞)
                url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
                resp = await download_client.get(url, headers=download_headers)
                if resp.status_code != 200:
                    raise Exception(f"Drive Download Failed: HTTP {resp.status_code}")
                image_bytes = resp.content
                
                # 4. 辨識與標註
                result, drawn_bytes = await process_and_visualize_photo(
                    image_bytes,
                    mime_type,
                    evaluate_public=evaluate_public,
                )
                
                output_link = None
                # 5. 上傳歸檔
                final_parent_id = (
                    safe_target_id if result.is_safe_for_public else unsafe_target_id
                ) if evaluate_public else None
                
                if final_parent_id:
                    def upload():
                        import google.oauth2.credentials
                        # 複製憑證，確保在 Thread 池中獨立運行不互相干擾
                        local_creds = google.oauth2.credentials.Credentials(
                            token=credentials.token,
                            refresh_token=credentials.refresh_token,
                            token_uri=credentials.token_uri,
                            client_id=credentials.client_id,
                            client_secret=credentials.client_secret,
                            scopes=credentials.scopes
                        )
                        local_drive = build("drive", "v3", credentials=local_creds, cache_discovery=False)
                        
                        file_metadata = {
                            "name": f"annotated_{file_name}",
                            "parents": [final_parent_id]
                        }
                        media = MediaIoBaseUpload(
                            io.BytesIO(drawn_bytes), mimetype="image/jpeg", resumable=True
                        )
                        return local_drive.files().create(
                            body=file_metadata, media_body=media, fields="id, webViewLink"
                        ).execute()

                    created_file = await asyncio.to_thread(upload)
                    output_link = created_file.get("webViewLink")

                # 計算 ai_decision
                ai_decision = "safe" if result.moderation_status == "public" else "unsafe" if result.moderation_status == "private" else "pending"

                # 因為是雲端檔案，回傳時 B64 以便前端預覽
                return {
                    "file": file_name,
                    "drive_id": file_id,
                    "output_b64": base64.b64encode(drawn_bytes).decode('utf-8'),
                    "output_url": output_link,
                    "has_face": result.has_face,
                    "face_count": len(result.face_bboxes),
                    "has_brand_strap": result.has_brand_strap,
                    "strap_color": result.strap_color,
                    "is_safe_for_public": result.is_safe_for_public,
                    "moderation_status": result.moderation_status,
                    "moderation_reason": result.moderation_reason,
                    "public_classification_performed": result.public_classification_performed,
                    "original_image_b64": base64.b64encode(image_bytes).decode('utf-8'),
                    "ai_decision": ai_decision,
                    "status": "ok",
                }
            except Exception as e:
                print(f"[ERROR] Failed to process {file_name}: {repr(e)}")
                return {"file": file_name, "status": "error", "error": str(e)}

    try:
        tasks = [process_one_drive(f) for f in files]
        results = await asyncio.gather(*tasks)
        return results
    finally:
        await download_client.aclose()


async def list_drive_image_files(folder_id: str, credentials) -> list[dict]:
    from googleapiclient.discovery import build

    drive_service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    q = f"'{folder_id}' in parents and trashed = false"
    res = drive_service.files().list(q=q, fields="files(id, name, mimeType)", pageSize=1000).execute()
    all_files = res.get("files", [])
    return [f for f in all_files if "image/" in f.get("mimeType", "")]


async def process_drive_file_item(
    file_item: dict,
    *,
    index: int,
    total: int,
    credentials,
    color_rules: list | None = None,
    collaborative_memory: str | None = None,
    evaluate_public: bool = True,
) -> dict:
    import httpx

    file_id = file_item["id"]
    file_name = file_item["name"]
    mime_type = file_item["mimeType"]
    try:
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
        headers = {"Authorization": f"Bearer {credentials.token}"}
        async with httpx.AsyncClient(timeout=float(REQUEST_TIMEOUT), follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            raise Exception(f"Drive Download Failed: HTTP {resp.status_code}")
        image_bytes = resp.content

        analysis, drawn_bytes = await asyncio.wait_for(
            process_and_visualize_photo(
                image_bytes,
                mime_type,
                color_rules=color_rules,
                collaborative_memory=collaborative_memory,
                evaluate_public=evaluate_public,
            ),
            timeout=float(REQUEST_TIMEOUT),
        )

        preview_io = io.BytesIO()
        with Image.open(io.BytesIO(drawn_bytes)) as img:
            img.thumbnail((800, 800))
            img.save(preview_io, format="JPEG", quality=75)
        preview_b64 = base64.b64encode(preview_io.getvalue()).decode("utf-8")

        orig_io = io.BytesIO()
        with Image.open(io.BytesIO(image_bytes)) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.thumbnail((800, 800))
            img.save(orig_io, format="JPEG", quality=75)
        orig_b64 = base64.b64encode(orig_io.getvalue()).decode("utf-8")

        ai_decision = "safe" if analysis.moderation_status == "public" else "unsafe" if analysis.moderation_status == "private" else "pending"
        result_dict = analysis.model_dump()
        result_dict["ai_decision"] = ai_decision

        return {
            "status": "ok",
            "index": index + 1,
            "total": total,
            "file_name": file_name,
            "drive_id": file_id,
            "result": result_dict,
            "drawn_image_b64": preview_b64,
            "original_image_b64": orig_b64,
        }
    except Exception as e:
        print(f"[ERROR] Stream failed for {file_name}: {repr(e)}")
        return {
            "status": "error",
            "index": index + 1,
            "total": total,
            "file_name": file_name,
            "drive_id": file_id,
            "error": str(e),
        }


async def batch_process_drive_stream(
    folder_id: str,
    credentials,
    target_folder_id: str = None,
    concurrency: int = 3,
    color_rules: list | None = None,
    collaborative_memory: str | None = None,
    evaluate_public: bool = True,
):
    """
    與 batch_process_drive 類似，但這是一個 Async Generator，會逐一 yield 每張圖的結果。
    用於實現即時進度條推送。
    """
    from googleapiclient.discovery import build
    drive_service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    
    # 1. 建立分類資料夾
    safe_target_id = None
    unsafe_target_id = None
    if target_folder_id:
        def get_or_create_subfolder(name, parent_id):
            q = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed = false"
            res = drive_service.files().list(q=q, fields="files(id)").execute()
            items = res.get("files", [])
            if items:
                return items[0]["id"]
            meta = {
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id]
            }
            folder = drive_service.files().create(body=meta, fields="id").execute()
            return folder.get("id")

        safe_target_id = await asyncio.to_thread(get_or_create_subfolder, "Safe_Results", target_folder_id)
        unsafe_target_id = await asyncio.to_thread(get_or_create_subfolder, "Unsafe_Results", target_folder_id)

    # 2. 列出圖片
    files = await list_drive_image_files(folder_id, credentials)
    
    total = len(files)
    if total == 0:
        yield {"type": "info", "message": "沒有找到任何圖片"}
        return

    semaphore = asyncio.Semaphore(concurrency)

    async def process_task(file_item, index):
        async with semaphore:
            return await process_drive_file_item(
                file_item,
                index=index,
                total=total,
                credentials=credentials,
                color_rules=color_rules,
                collaborative_memory=collaborative_memory,
                evaluate_public=evaluate_public,
            )

    # 使用 as_completed 讓完成的結果立即傳出
    pending_tasks = [process_task(f, i) for i, f in enumerate(files)]
    for finished_task in asyncio.as_completed(pending_tasks):
        result = await finished_task
        yield result
