import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Response, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.concurrency import run_in_threadpool
import base64
import shutil

from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
from pydantic import BaseModel, ValidationError
import os
import uuid

DEFAULT_MAX_UPLOAD_SIZE_MB = 25
CONFIG_PATH = Path(__file__).with_name("config.json")
logger = logging.getLogger(__name__)


def load_config() -> dict[str, Any]:
    config = {"max_upload_size_mb": DEFAULT_MAX_UPLOAD_SIZE_MB}
    if not CONFIG_PATH.exists():
        return config

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            raw_config = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.warning("config.json 讀取失敗，改用預設設定。")
        return config

    if not isinstance(raw_config, dict):
        logger.warning("config.json 格式錯誤（非物件），改用預設設定。")
        return config

    max_upload_size_mb = raw_config.get("max_upload_size_mb", DEFAULT_MAX_UPLOAD_SIZE_MB)
    try:
        max_upload_size_mb = int(max_upload_size_mb)
        if max_upload_size_mb <= 0:
            raise ValueError
    except (TypeError, ValueError):
        logger.warning("max_upload_size_mb 無效，改用預設 25MB。")
        max_upload_size_mb = DEFAULT_MAX_UPLOAD_SIZE_MB

    config["max_upload_size_mb"] = max_upload_size_mb
    config["host"] = str(raw_config.get("host", "0.0.0.0") or "0.0.0.0")
    config["port"] = int(raw_config.get("port", 8000) or 8000)
    return config


CONFIG = load_config()
MAX_UPLOAD_SIZE_MB = CONFIG["max_upload_size_mb"]
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024


# 1. 初始化 FastAPI 與靜態資源
app = FastAPI(title="照片審核系統 - 名牌帶子特化版")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="template")

# 新增 Session 支援，SECRET_KEY 可在 .env 設定
app.add_middleware(
    SessionMiddleware, 
    secret_key=os.environ.get("SESSION_SECRET", "photo-identifier-local-secret"),
    max_age=3600 * 24 * 7,
    same_site="lax",
    https_only=False
)

from src.google_usage import analyze_brand_strap_image, PhotoAnalysisResult
from src.google_auth import get_auth_url, exchange_code_for_token, load_user_credentials
from photoIdentifier import process_and_visualize_photo, batch_process_folder, batch_process_drive, batch_process_drive_stream

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})

@app.get("/api/config")
async def get_frontend_config():
    """提供前端啟動 Google Picker 所需的公開 ID (不含 Secret)"""
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    return {
        "google_client_id": client_id,
        "google_api_key": os.environ.get("GOOGLE_API_KEY", ""),
        "google_app_id": client_id.split("-")[0] if "-" in client_id else ""
    }

@app.get("/local_file/")
async def get_local_file(path: str):
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)


@app.post("/analyze/", response_model=PhotoAnalysisResult)
async def analyze_photo(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="請上傳圖片檔案")

    try:
        image_bytes = await file.read()
        if not image_bytes:
            raise HTTPException(status_code=400, detail="圖片內容為空")
        if len(image_bytes) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"圖片大小超過限制，最大為 {MAX_UPLOAD_SIZE_MB}MB",
            )

        b64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        return await analyze_brand_strap_image(b64_image, file.content_type)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected internal error handling request: %s", e)
        raise HTTPException(status_code=500, detail="圖片分析伺服器內部錯誤") from e


@app.post("/visualize/", response_class=Response)
async def visualize_photo(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="請上傳圖片檔案")

    try:
        image_bytes = await file.read()
        if not image_bytes:
            raise HTTPException(status_code=400, detail="圖片內容為空")
        if len(image_bytes) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"圖片大小超過限制，最大為 {MAX_UPLOAD_SIZE_MB}MB",
            )

        # 這裡改走最新封裝的流程，同時取得診斷與製圖！
        analysis_result, drawn_image_bytes = await process_and_visualize_photo(image_bytes, file.content_type)
        
        # 將畫好框的圖片以二進位返回，並標明 MIME 類型為 jpeg
        return Response(content=drawn_image_bytes, media_type="image/jpeg")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected internal error handling request: %s", e)
        raise HTTPException(status_code=500, detail="圖片製圖伺服器內部錯誤") from e


@app.post("/analyze_with_image/")
async def analyze_with_image(file: UploadFile = File(...)):
    """專門給單圖 UI 使用，回傳 JSON 結果，且夾帶畫好框的 base64 圖片供前端立即渲染"""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="請上傳圖片檔案")
    try:
        image_bytes = await file.read()
        if not image_bytes:
            raise HTTPException(status_code=400, detail="圖片內容為空")
        if len(image_bytes) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(status_code=413, detail="圖片大小超過限制")
        
        analysis_result, drawn_image_bytes = await process_and_visualize_photo(image_bytes, file.content_type)
        drawn_b64 = base64.b64encode(drawn_image_bytes).decode('utf-8')
        
        return {
            "analysis": analysis_result.model_dump(),
            "drawn_image_b64": drawn_b64
        }
    except Exception as e:
        logger.exception("analyze_with_image error: %s", e)
        raise HTTPException(status_code=500, detail="圖片辨識失敗") from e


class BatchRequest(BaseModel):
    input_folder: str
    output_folder: str
    concurrency: int = 3

@app.post("/batch/")
async def batch_visualize(req: BatchRequest):
    input_path = Path(req.input_folder)
    if not input_path.exists() or not input_path.is_dir():
        raise HTTPException(status_code=400, detail=f"資料夾不存在：{req.input_folder}")

    try:
        results = await batch_process_folder(
            input_dir=req.input_folder,
            output_dir=req.output_folder,
            concurrency=req.concurrency,
        )
        ok = [r for r in results if r["status"] == "ok"]
        err = [r for r in results if r["status"] == "error"]
        return {
            "total": len(results),
            "success": len(ok),
            "failed": len(err),
            "output_folder": req.output_folder,
            "results": results,
        }
    except Exception as e:
        logger.exception("Batch processing error: %s", e)
        raise HTTPException(status_code=500, detail="批量辨識失敗") from e


class DriveBatchRequest(BaseModel):
    folder_id: str
    target_folder_id: Optional[str] = None
    concurrency: int = 3

@app.post("/batch_drive/")
async def batch_visualize_drive(req: DriveBatchRequest, request: Request):
    """雲端硬碟批量處理入口 (舊 - 一次性回傳)"""
    user_key = request.session.get("user_key")
    if not user_key:
        raise HTTPException(status_code=401, detail="尚未登入 Google 帳號")
    
    try:
        creds = load_user_credentials(user_key)
        results = await batch_process_drive(
            folder_id=req.folder_id,
            credentials=creds,
            target_folder_id=req.target_folder_id,
            concurrency=req.concurrency
        )
        
        success_count = sum(1 for r in results if r.get("status") == "ok")
        failed_count = len(results) - success_count
        
        return {
            "status": "success",
            "results": results,
            "success": success_count,
            "failed": failed_count
        }
    except Exception as e:
        logger.exception("Drive batch error: %s", e)
        if "找不到使用者憑證" in str(e):
             raise HTTPException(status_code=401, detail="Google 授權已失效，請重新連結。")
        raise HTTPException(status_code=500, detail=f"雲端批量處理失敗: {str(e)}")

@app.post("/batch_drive_stream/")
async def batch_visualize_drive_stream(req: DriveBatchRequest, request: Request):
    """雲端硬碟批量處理入口 (新 - 串流即時回傳進度)"""
    user_key = request.session.get("user_key")
    if not user_key:
        raise HTTPException(status_code=401, detail="尚未登入 Google 帳號")

    try:
        creds = load_user_credentials(user_key)
        
        async def event_generator():
            try:
                # 這裡調用剛才在 photoIdentifier.py 寫好的產生器
                async for chunk in batch_process_drive_stream(
                    folder_id=req.folder_id,
                    credentials=creds,
                    target_folder_id=req.target_folder_id,
                    concurrency=req.concurrency
                ):
                    # 每一筆結果都轉成 JSON 並加上換行符號推播出去
                    yield json.dumps(chunk, ensure_ascii=False) + "\n"
            except Exception as inner_e:
                yield json.dumps({"status": "error", "error": f"串流中斷: {str(inner_e)}"}, ensure_ascii=False) + "\n"

        return StreamingResponse(event_generator(), media_type="application/x-ndjson")

    except Exception as e:
        logger.exception("Drive batch stream error: %s", e)
        if "找不到使用者憑證" in str(e):
             raise HTTPException(status_code=401, detail="Google 授權已失效，請重新連結。")
        raise HTTPException(status_code=500, detail=f"啟動串流處理失敗: {str(e)}")


@app.get("/auth/google")
def google_auth(request: Request):
    try:
        user_key = request.session.get("user_key")
        if not user_key:
            user_key = uuid.uuid4().hex
            request.session["user_key"] = user_key

        auth_url, state, code_verifier = get_auth_url()
        request.session["oauth_state"] = state
        request.session["oauth_user_key"] = user_key
        if code_verifier:
            request.session["oauth_code_verifier"] = code_verifier
        
        return RedirectResponse(url=auth_url)
    except Exception as e:
        logger.exception("Auth URL Error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/auth/callback")
def google_auth_callback(request: Request, code: str, state: str):
    try:
        expected_state = request.session.get("oauth_state")
        if not expected_state or state != expected_state:
            raise HTTPException(status_code=400, detail="Invalid OAuth state")
        
        user_key = request.session.get("oauth_user_key")
        if not user_key:
            raise HTTPException(status_code=401, detail="Missing session user_key")
            
        code_verifier = request.session.get("oauth_code_verifier")
        
        exchange_code_for_token(code=code, user_key=user_key, state=state, code_verifier=code_verifier)
        
        request.session.pop("oauth_state", None)
        request.session.pop("oauth_code_verifier", None)
        
        # 授權成功後，導向回前端並帶上成功標記
        return RedirectResponse(url="/?auth=success")
    except Exception as e:
        logger.exception("Auth Callback Error")
        raise HTTPException(status_code=500, detail=str(e))


class OrganizeRequest(BaseModel):
    results: list[dict]
    safe_folder: str
    unsafe_folder: str

@app.post("/organize_batch/")
async def organize_batch(req: OrganizeRequest):
    safe_path = Path(req.safe_folder)
    unsafe_path = Path(req.unsafe_folder)
    safe_path.mkdir(parents=True, exist_ok=True)
    unsafe_path.mkdir(parents=True, exist_ok=True)

    moved_count = 0
    errors = []
    
    for res in req.results:
        if res.get("status") != "ok":
            continue
            
        orig_path = res.get("original_path")
        is_safe = res.get("is_safe_for_public")
        
        if not orig_path:
            continue
            
        src = Path(orig_path)
        if not src.exists():
            errors.append(f"Source file missing: {src}")
            continue

        dest_dir = safe_path if is_safe else unsafe_path
        dest = dest_dir / src.name
        try:
            shutil.copy2(src, dest)
            moved_count += 1
        except Exception as e:
            errors.append(f"Failed to copy {src.name}: {e}")

    return {
        "message": f"成功分類複製了 {moved_count} 個檔案。",
        "moved": moved_count,
        "errors": errors
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=CONFIG.get("host", "0.0.0.0"),
        port=CONFIG.get("port", 8000),
        reload=True,
    )
