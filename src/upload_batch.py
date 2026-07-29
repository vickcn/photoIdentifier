from dataclasses import dataclass

from fastapi import HTTPException, UploadFile


@dataclass(frozen=True)
class UploadedImage:
    filename: str
    content_type: str
    content: bytes


async def read_upload_batch(
    files: list[UploadFile],
    *,
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
) -> list[UploadedImage]:
    cloud_hint = "請減少檔案，或改用 Google 雲端資料夾模式。"
    if not files:
        raise HTTPException(status_code=400, detail="請至少選擇一張圖片")
    if len(files) > max_files:
        await _close_uploads(files)
        raise HTTPException(status_code=413, detail=f"一次最多上傳 {max_files} 張圖片。{cloud_hint}")

    uploaded_images: list[UploadedImage] = []
    total_bytes = 0
    try:
        for file in files:
            filename = file.filename or "未命名圖片"
            content_type = file.content_type or ""
            if not content_type.startswith("image/"):
                raise HTTPException(status_code=400, detail=f"{filename} 不是支援的圖片檔案")

            content = await file.read(max_file_bytes + 1)
            if not content:
                raise HTTPException(status_code=400, detail=f"{filename} 的內容為空")
            if len(content) > max_file_bytes:
                raise HTTPException(status_code=413, detail=f"{filename} 超過單檔大小限制。{cloud_hint}")

            total_bytes += len(content)
            if total_bytes > max_total_bytes:
                raise HTTPException(status_code=413, detail=f"上傳檔案總大小超過限制。{cloud_hint}")

            uploaded_images.append(UploadedImage(filename, content_type, content))
    finally:
        await _close_uploads(files)

    return uploaded_images


async def _close_uploads(files: list[UploadFile]) -> None:
    for file in files:
        await file.close()
