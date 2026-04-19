import base64
import asyncio
import mimetypes
import io
from pathlib import Path
from typing import Tuple, List, Optional
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from src.google_usage import analyze_brand_strap_image, PhotoAnalysisResult
from src.aoi import draw_bboxes_on_image

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

async def process_and_visualize_photo(image_bytes: bytes, content_type: str = "image/jpeg") -> Tuple[PhotoAnalysisResult, bytes]:
    """
    整合 google_usage 跟 aoi 模組：
    1. 將圖片送往 Google Vertex AI 進行邏輯判斷與物件偵測
    2. 解析出 bbox 座標後，送至 aoi 模組將邊界框畫上原圖
    3. 回傳 (分析診斷結果, 畫上框框的圖片 bytes)
    """
    b64_image = base64.b64encode(image_bytes).decode('utf-8')
    analysis_result = await analyze_brand_strap_image(b64_image, content_type)
    drawn_image_bytes = draw_bboxes_on_image(
        image_bytes=image_bytes,
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
                result, drawn_bytes = await process_and_visualize_photo(image_bytes, mime_type)
                
                out_file = output_path / f"annotated_{file.name}"
                out_file.write_bytes(drawn_bytes)
                
                return {
                    "file": file.name,
                    "original_path": str(file),
                    "output": str(out_file),
                    "has_face": result.has_face,
                    "face_count": len(result.face_bboxes),
                    "has_brand_strap": result.has_brand_strap,
                    "strap_color": result.strap_color,
                    "is_safe_for_public": result.is_safe_for_public,
                    "moderation_reason": result.moderation_reason,
                    "status": "ok",
                }
            except Exception as e:
                return {"file": file.name, "status": "error", "error": str(e)}

    tasks = [process_one(f) for f in image_files]
    results = await asyncio.gather(*tasks)
    return list(results)


async def batch_process_drive(
    folder_id: str,
    credentials,
    target_folder_id: Optional[str] = None,
    concurrency: int = 3,
) -> List[dict]:
    """
    從 Google Drive 批量取得圖片、辨識並回傳結果。
    如果 target_folder_id 有給，則會在內部自動建立 [Safe_Results] 與 [Unsafe_Results]
    並將標註圖分類存放。
    """
    drive_service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    
    # 1. 解析輸出資料夾（自動分類歸檔）
    safe_target_id = None
    unsafe_target_id = None

    if target_folder_id:
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
    q = f"'{folder_id}' in parents and (mimeType contains 'image/') and trashed = false"
    response = drive_service.files().list(
        q=q, fields="files(id, name, mimeType)", pageSize=100
    ).execute()
    files = response.get("files", [])
    
    if not files:
        return []

    semaphore = asyncio.Semaphore(concurrency)

    async def process_one_drive(file_item: dict) -> dict:
        async with semaphore:
            file_id = file_item["id"]
            file_name = file_item["name"]
            mime_type = file_item["mimeType"]
            
            try:
                # 3. 下載內容
                request = drive_service.files().get_media(fileId=file_id)
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = await asyncio.to_thread(downloader.next_chunk)
                
                image_bytes = fh.getvalue()
                
                # 4. 辨識與標註
                result, drawn_bytes = await process_and_visualize_photo(image_bytes, mime_type)
                
                output_link = None
                # 5. 上傳歸檔
                final_parent_id = safe_target_id if result.is_safe_for_public else unsafe_target_id
                
                if final_parent_id:
                    file_metadata = {
                        "name": f"annotated_{file_name}",
                        "parents": [final_parent_id]
                    }
                    media = MediaIoBaseUpload(
                        io.BytesIO(drawn_bytes), mimetype="image/jpeg", resumable=True
                    )
                    created_file = await asyncio.to_thread(
                        drive_service.files().create(
                            body=file_metadata, media_body=media, fields="id, webViewLink"
                        ).execute
                    )
                    output_link = created_file.get("webViewLink")

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
                    "moderation_reason": result.moderation_reason,
                    "status": "ok",
                }
            except Exception as e:
                return {"file": file_name, "status": "error", "error": str(e)}

    tasks = [process_one_drive(f) for f in files]
    results = await asyncio.gather(*tasks)
    return list(results)

