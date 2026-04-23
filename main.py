import json
import logging
from pathlib import Path
from typing import Any, Optional
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Response, Request
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
from src.google_auth import get_auth_url, exchange_code_for_token, load_user_credentials, token_store, DEFAULT_SCOPES
from src.metrics import compute_batch_metrics, collect_changed_files, compute_analysis_stats, format_metrics_for_export
from photoIdentifier import process_and_visualize_photo, batch_process_folder, batch_process_drive, batch_process_drive_stream

# Session storage for batch operations
_batch_sessions: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Vercel /tmp 是 instance-local 的，不跨請求共享。
# 解法：OAuth credentials 同時備份在加密的 session cookie，
#       load 時先嘗試 /tmp，若失效再從 session 重建。
# ---------------------------------------------------------------------------

def _save_creds_to_session(request: Request, creds) -> None:
    """把 credentials 序列化後存入 session（去除 client_secret，從 env 補回）。"""
    import json as _json
    data = _json.loads(creds.to_json())
    data.pop("client_secret", None)
    data.pop("client_id", None)
    request.session["drive_credentials"] = data


def _load_creds_from_session(request: Request):
    """從 session 重建 Credentials；失敗回傳 None。"""
    from google.oauth2.credentials import Credentials as _Creds
    data = request.session.get("drive_credentials")
    if not data:
        return None
    data = dict(data)
    data["client_id"]     = os.environ.get("GOOGLE_CLIENT_ID", "")
    data["client_secret"] = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    try:
        return _Creds.from_authorized_user_info(data, scopes=DEFAULT_SCOPES)
    except Exception:
        return None


def get_drive_credentials(request: Request):
    """
    取得 Drive OAuth credentials：
    1. 先嘗試本機 /tmp（本地開發 / 同 instance 命中快取）
    2. /tmp 找不到時從 session 重建（Vercel 跨 instance 情境）
    3. 重建後寫回 /tmp 供同 instance 後續請求使用
    4. 每次成功都將最新狀態同步回 session
    """
    from google.auth.transport.requests import Request as GoogleRequest

    user_key = request.session.get("user_key")
    if not user_key:
        raise HTTPException(status_code=401, detail="尚未登入 Google 帳號")

    creds = None
    try:
        creds = load_user_credentials(user_key)
    except Exception as e:
        logger.warning(f"本地憑證載入失敗，嘗試從 session 重建: {e}")
        creds = None

    if creds is None:
        creds = _load_creds_from_session(request)
        if creds is None:
            raise HTTPException(status_code=401, detail="Google 授權已失效，請重新連結。")
        
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(GoogleRequest())
            except Exception as e:
                logger.error(f"憑證刷新失敗: {e}")
                raise HTTPException(status_code=401, detail="Google 授權已過期且無法自動刷新，請重新登入。")
        
        try:
            token_store.save(user_key, creds)
        except Exception:
            pass # 可能是唯讀環境，不影響本次執行

    try:
        _save_creds_to_session(request, creds)
    except Exception:
        pass
        
    return creds

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

@app.get("/api/user/me")
async def get_current_user(request: Request):
    """取得目前登入的 Google 帳號資訊"""
    try:
        creds = get_drive_credentials(request)
    except Exception:
        # 任何原因導致無法取得憑證都視為未登入
        return {"logged_in": False}
    
    from googleapiclient.discovery import build
    try:
        # 使用 oauth2 service 取得使用者資訊
        service = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        userinfo = service.userinfo().get().execute()
        return {
            "logged_in": True,
            "email": userinfo.get("email"),
            "name": userinfo.get("name"),
            "picture": userinfo.get("picture")
        }
    except Exception as e:
        logger.error(f"取得使用者資訊失敗: {e}")
        # 如果憑證還在但 API 呼叫失敗，通常也是授權有問題
        return {"logged_in": False, "error": str(e)}

@app.get("/auth/logout")
async def google_logout(request: Request):
    """清除 Google 登入 Session"""
    request.session.clear()
    return RedirectResponse(url="/")

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
async def analyze_with_image(
    file: UploadFile = File(...),
    color_rules_json: Optional[str] = Form(None),
):
    """專門給單圖 UI 使用，回傳 JSON 結果，且夾帶畫好框的 base64 圖片供前端立即渲染"""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="請上傳圖片檔案")
    try:
        image_bytes = await file.read()
        if not image_bytes:
            raise HTTPException(status_code=400, detail="圖片內容為空")
        if len(image_bytes) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(status_code=413, detail="圖片大小超過限制")

        color_rules = json.loads(color_rules_json) if color_rules_json else None
        analysis_result, drawn_image_bytes = await process_and_visualize_photo(
            image_bytes, file.content_type, color_rules=color_rules
        )
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
    concurrency: int = 3
    color_rules: Optional[list] = None
    session_id: Optional[str] = None

@app.post("/batch/")
async def batch_visualize(req: BatchRequest):
    input_path = Path(req.input_folder)
    if not input_path.exists() or not input_path.is_dir():
        raise HTTPException(status_code=400, detail=f"資料夾不存在：{req.input_folder}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_folder = str(input_path / f"review_temp_{ts}")

    # 生成或使用提供的 session_id
    session_id = req.session_id or str(uuid.uuid4())
    start_time = datetime.now()

    # 初始化 session storage
    _batch_sessions[session_id] = {
        "session_id": session_id,
        "batch_mode": "local",
        "start_time": start_time.isoformat(),
        "end_time": None,
        "results": [],
        "processing_info": {
            "input_folder": req.input_folder,
            "concurrency": req.concurrency,
        },
        "completed": False
    }

    try:
        results = await batch_process_folder(
            input_dir=req.input_folder,
            output_dir=temp_folder,
            concurrency=req.concurrency,
            color_rules=req.color_rules,
        )
        ok = [r for r in results if r["status"] == "ok"]
        err = [r for r in results if r["status"] == "error"]

        # 儲存結果到 session
        _batch_sessions[session_id]["results"] = results
        _batch_sessions[session_id]["end_time"] = datetime.now().isoformat()
        _batch_sessions[session_id]["completed"] = True

        return {
            "session_id": session_id,
            "total": len(results),
            "success": len(ok),
            "failed": len(err),
            "temp_folder": temp_folder,
            "results": results,
        }
    except Exception as e:
        logger.exception("Batch processing error: %s", e)
        _batch_sessions[session_id]["end_time"] = datetime.now().isoformat()
        raise HTTPException(status_code=500, detail="批量辨識失敗") from e


class DriveBatchRequest(BaseModel):
    folder_id: str
    target_folder_id: Optional[str] = None
    concurrency: int = 3
    color_rules: Optional[list] = None
    session_id: Optional[str] = None

@app.post("/batch_drive/")
async def batch_visualize_drive(req: DriveBatchRequest, request: Request):
    """雲端硬碟批量處理入口 (舊 - 一次性回傳)"""
    user_key = request.session.get("user_key")
    if not user_key:
        raise HTTPException(status_code=401, detail="尚未登入 Google 帳號")
    
    try:
        creds = get_drive_credentials(request)
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
        creds = get_drive_credentials(request)

        # 生成或使用提供的 session_id
        session_id = req.session_id or str(uuid.uuid4())
        start_time = datetime.now()

        # 初始化 session storage
        _batch_sessions[session_id] = {
            "session_id": session_id,
            "batch_mode": "drive",
            "start_time": start_time.isoformat(),
            "end_time": None,
            "results": [],
            "processing_info": {
                "folder_id": req.folder_id,
                "concurrency": req.concurrency,
            },
            "completed": False
        }

        async def event_generator():
            try:
                # 這裡調用剛才在 photoIdentifier.py 寫好的產生器
                async for chunk in batch_process_drive_stream(
                    folder_id=req.folder_id,
                    credentials=creds,
                    target_folder_id=req.target_folder_id,
                    concurrency=req.concurrency,
                    color_rules=req.color_rules,
                ):
                    # 儲存結果到 session
                    if chunk.get("status") == "ok":
                        _batch_sessions[session_id]["results"].append(chunk)

                    # 每一筆結果都轉成 JSON 並加上換行符號推播出去
                    chunk_with_session = {**chunk, "session_id": session_id}
                    yield json.dumps(chunk_with_session, ensure_ascii=False) + "\n"

                # 標記完成
                _batch_sessions[session_id]["end_time"] = datetime.now().isoformat()
                _batch_sessions[session_id]["completed"] = True
                yield json.dumps({
                    "status": "completed",
                    "session_id": session_id,
                    "message": f"批次處理完成，共 {len(_batch_sessions[session_id]['results'])} 個結果"
                }, ensure_ascii=False) + "\n"

            except Exception as inner_e:
                _batch_sessions[session_id]["end_time"] = datetime.now().isoformat()
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
        
        creds = exchange_code_for_token(code=code, user_key=user_key, state=state, code_verifier=code_verifier)

        # 同步備份到 session，供 Vercel /tmp 失效時使用
        _save_creds_to_session(request, creds)

        request.session.pop("oauth_state", None)
        request.session.pop("oauth_code_verifier", None)

        # 授權成功後，導向回前端並帶上成功標記
        return RedirectResponse(url="/?auth=success")
    except Exception as e:
        logger.exception("Auth Callback Error")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/auth/access_token")
def get_access_token(request: Request):
    """回傳目前 session 使用者的 OAuth access token，供前端 Picker 使用。"""
    user_key = request.session.get("user_key")
    if not user_key:
        raise HTTPException(status_code=401, detail="尚未登入")
    try:
        creds = get_drive_credentials(request)
        return {"access_token": creds.token}
    except Exception:
        raise HTTPException(status_code=401, detail="尚未授權或憑證已失效")


class OrganizeRequest(BaseModel):
    results: list[dict]
    safe_folder: str
    unsafe_folder: str
    pending_folder: Optional[str] = None  # 待人員判定資料夾（選填）

@app.post("/organize_batch/")
async def organize_batch(req: OrganizeRequest):
    safe_path = Path(req.safe_folder)
    unsafe_path = Path(req.unsafe_folder)
    safe_path.mkdir(parents=True, exist_ok=True)
    unsafe_path.mkdir(parents=True, exist_ok=True)

    pending_path: Optional[Path] = None
    if req.pending_folder:
        pending_path = Path(req.pending_folder)
        pending_path.mkdir(parents=True, exist_ok=True)

    moved_count = 0
    errors = []

    for res in req.results:
        if res.get("status") != "ok":
            continue

        orig_path = res.get("original_path")
        if not orig_path:
            continue

        src = Path(orig_path)
        if not src.exists():
            errors.append(f"Source file missing: {src}")
            continue

        moderation_status = res.get("moderation_status", "")
        is_safe = res.get("is_safe_for_public")

        if moderation_status == "public" or (not moderation_status and is_safe):
            dest_dir = safe_path
        elif moderation_status == "pending" and pending_path:
            dest_dir = pending_path
        else:
            dest_dir = unsafe_path

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


@app.get("/review_temp_folders/")
async def list_review_temp_folders(input_folder: str):
    input_path = Path(input_folder)
    if not input_path.exists() or not input_path.is_dir():
        raise HTTPException(status_code=400, detail="資料夾不存在")
    folders = []
    for d in sorted(input_path.iterdir(), reverse=True):
        if d.is_dir() and d.name.startswith("review_temp_"):
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            folders.append({
                "name": d.name,
                "path": str(d),
                "size_mb": round(size / 1024 / 1024, 2),
            })
    return {"folders": folders}


class DeleteTempFolderRequest(BaseModel):
    input_folder: str
    folder_name: str

@app.post("/delete_review_temp/")
async def delete_review_temp(req: DeleteTempFolderRequest):
    if not req.folder_name.startswith("review_temp_"):
        raise HTTPException(status_code=400, detail="只能刪除 review_temp_ 開頭的資料夾")
    folder_path = Path(req.input_folder) / req.folder_name
    if not folder_path.exists():
        raise HTTPException(status_code=404, detail="暫存資料夾不存在")
    try:
        shutil.rmtree(folder_path)
        return {"message": f"已刪除：{req.folder_name}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刪除失敗：{e}") from e


class FinalizeReviewRequest(BaseModel):
    decisions: list[dict]  # [{file_name, drive_id, user_decision: "safe"|"unsafe"}, ...]
    target_folder_id: str


class BatchSummaryRequest(BaseModel):
    session_id: str


@app.post("/batch_summary/")
async def get_batch_summary(req: BatchSummaryRequest):
    """獲取批次處理的綜合指標與混淆矩陣"""
    session_id = req.session_id
    if session_id not in _batch_sessions:
        raise HTTPException(status_code=404, detail=f"找不到會話 {session_id}")

    session_data = _batch_sessions[session_id]
    results = session_data.get("results", [])

    if not results:
        return {"error": "尚無結果"}

    try:
        start_time = datetime.fromisoformat(session_data.get("start_time", datetime.now().isoformat()))
        end_time = datetime.fromisoformat(session_data.get("end_time", datetime.now().isoformat()))
        batch_mode = session_data.get("batch_mode", "local")
        processing_info = session_data.get("processing_info", {})

        metrics = compute_batch_metrics(results, start_time, end_time, batch_mode, session_id, processing_info)
        stats = compute_analysis_stats(results)
        changed_files = collect_changed_files(results, session_id)

        return {
            "session_id": session_id,
            "metrics": metrics,
            "analysis_stats": stats,
            "changed_files": changed_files
        }
    except Exception as e:
        logger.exception("Failed to compute batch summary: %s", e)
        raise HTTPException(status_code=500, detail=f"計算指標失敗: {str(e)}")


@app.post("/batch_summary_export/")
async def export_batch_summary(req: BatchSummaryRequest):
    """匯出批次指標為 JSON 格式"""
    session_id = req.session_id
    if session_id not in _batch_sessions:
        raise HTTPException(status_code=404, detail=f"找不到會話 {session_id}")

    session_data = _batch_sessions[session_id]
    results = session_data.get("results", [])

    if not results:
        raise HTTPException(status_code=400, detail="尚無結果可匯出")

    try:
        start_time = datetime.fromisoformat(session_data.get("start_time", datetime.now().isoformat()))
        end_time = datetime.fromisoformat(session_data.get("end_time", datetime.now().isoformat()))
        batch_mode = session_data.get("batch_mode", "local")
        processing_info = session_data.get("processing_info", {})

        metrics = compute_batch_metrics(results, start_time, end_time, batch_mode, session_id, processing_info)
        stats = compute_analysis_stats(results)

        json_content = format_metrics_for_export(metrics, stats)

        return Response(
            content=json_content,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=batch_summary_{session_id}.json"}
        )
    except Exception as e:
        logger.exception("Failed to export batch summary: %s", e)
        raise HTTPException(status_code=500, detail=f"匯出失敗: {str(e)}")


@app.get("/batch_sessions/")
async def list_batch_sessions():
    """列出所有活躍的批次會話"""
    sessions = []
    for session_id, session_data in _batch_sessions.items():
        sessions.append({
            "session_id": session_id,
            "batch_mode": session_data.get("batch_mode"),
            "start_time": session_data.get("start_time"),
            "result_count": len(session_data.get("results", [])),
            "status": "processing" if not session_data.get("completed") else "completed"
        })
    return {"sessions": sessions}

@app.post("/finalize_review/")
async def finalize_review(req: FinalizeReviewRequest, request: Request):
    """根據使用者最終裁決，將 Drive 原始檔案搬移到 Safe/Unsafe 子資料夾"""
    user_key = request.session.get("user_key")
    if not user_key:
        raise HTTPException(status_code=401, detail="尚未登入 Google 帳號")

    try:
        creds = get_drive_credentials(request)
        from googleapiclient.discovery import build as gbuild
        drive_service = gbuild("drive", "v3", credentials=creds, cache_discovery=False)

        # 建立或取得 Safe / Unsafe 子資料夾
        def get_or_create_subfolder(name: str, parent_id: str):
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

        safe_folder_id = await run_in_threadpool(get_or_create_subfolder, "Safe_Results", req.target_folder_id)
        unsafe_folder_id = await run_in_threadpool(get_or_create_subfolder, "Unsafe_Results", req.target_folder_id)
        pending_folder_id = await run_in_threadpool(get_or_create_subfolder, "Pending_Results", req.target_folder_id)

        copied_count = 0
        errors = []

        for item in req.decisions:
            file_id = item.get("drive_id")
            file_name = item.get("file_name", "unknown")
            decision = item.get("user_decision", "safe")

            if not file_id:
                errors.append(f"{file_name}: 缺少 drive_id")
                continue

            if decision == "safe":
                target_parent = safe_folder_id
            elif decision == "pending":
                target_parent = pending_folder_id
            else:
                target_parent = unsafe_folder_id

            try:
                # 複製檔案到目標資料夾（原檔留在原處）
                await run_in_threadpool(
                    lambda fid=file_id, tp=target_parent, fn=file_name: drive_service.files().copy(
                        fileId=fid,
                        body={"name": fn, "parents": [tp]},
                        fields="id"
                    ).execute()
                )
                copied_count += 1
            except Exception as e:
                errors.append(f"{file_name}: {str(e)}")

        return {
            "message": f"成功複製歸檔 {copied_count} 個檔案到 Drive。",
            "moved": copied_count,
            "errors": errors
        }

    except Exception as e:
        logger.exception("Finalize review error: %s", e)
        if "找不到使用者憑證" in str(e):
            raise HTTPException(status_code=401, detail="Google 授權已失效，請重新連結。")
        raise HTTPException(status_code=500, detail=f"歸檔失敗: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=CONFIG.get("host", "0.0.0.0"),
        port=CONFIG.get("port", 8000),
        reload=True,
    )
