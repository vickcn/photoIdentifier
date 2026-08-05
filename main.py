from dotenv import load_dotenv

load_dotenv()

import json
import logging
from pathlib import Path
from typing import Any, Literal, Optional
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

from src.insight_api_client import (
    DEFAULT_CLUSTER_EPS,
    DEFAULT_CLUSTER_MIN_SAMPLES,
    cluster_batch_results,
    detect_normalized_bboxes,
)
from src.batch_state_store import create_batch_state_store

DEFAULT_MAX_UPLOAD_SIZE_MB = 25
DEFAULT_BATCH_UPLOAD_MAX_FILES = 3
DEFAULT_BATCH_UPLOAD_MAX_FILE_MB = 2
DEFAULT_BATCH_UPLOAD_MAX_TOTAL_MB = 4
DEFAULT_BATCH_UPLOAD_CONCURRENCY = 1
DEFAULT_BATCH_DOWNLOAD_MAX_MB = 8
DEFAULT_FACE_CLUSTERING_ENABLED = True
FACE_CLUSTER_EPS_MIN = 0.05
FACE_CLUSTER_EPS_MAX = 1.5
IS_VERCEL = os.getenv("VERCEL") == "1"
CONFIG_BATCH_UPLOAD_MAX_FILES_CAP = 20 if IS_VERCEL else None
CONFIG_BATCH_UPLOAD_CONCURRENCY_CAP = 3 if IS_VERCEL else None
CONFIG_PATH = Path(__file__).with_name("config.json")
logger = logging.getLogger(__name__)


def _read_positive_int(
    raw_value: Any,
    *,
    key_name: str,
    default: int,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    try:
        value = int(raw_value)
        if value < minimum:
            raise ValueError
        if maximum is not None and value > maximum:
            raise ValueError
        return value
    except (TypeError, ValueError):
        if maximum is None:
            logger.warning("%s 無效，改用預設值 %s。", key_name, default)
        else:
            logger.warning("%s 無效，改用預設值 %s（允許範圍 %s-%s）。", key_name, default, minimum, maximum)
        return default


def _read_bool(raw_value: Any, *, key_name: str, default: bool) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    logger.warning("%s 無效，改用預設值 %s。", key_name, default)
    return default


def _read_face_cluster_params(raw_eps: Any, raw_min_samples: Any) -> tuple[float, int]:
    try:
        eps = float(raw_eps)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="分群 eps 必須是數字") from exc
    if not FACE_CLUSTER_EPS_MIN <= eps <= FACE_CLUSTER_EPS_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"分群 eps 必須介於 {FACE_CLUSTER_EPS_MIN} 到 {FACE_CLUSTER_EPS_MAX}",
        )

    try:
        min_samples = int(raw_min_samples)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="分群 min_samples 必須是整數") from exc
    if not 1 <= min_samples <= BATCH_UPLOAD_MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"分群 min_samples 必須介於 1 到 {BATCH_UPLOAD_MAX_FILES}",
        )
    return eps, min_samples


def _validate_processing_scope(run_public_classification: bool, run_face_clustering: bool) -> None:
    if not run_public_classification and not run_face_clustering:
        raise HTTPException(status_code=400, detail="至少選擇一項：可公開性判定或人臉分群")


def _skip_session_face_clustering(session_id: str) -> dict[str, Any]:
    session = _batch_sessions[session_id]
    session["face_clusters"] = []
    session["face_clustering"] = {
        "available": False,
        "reason": "not_requested",
        "cluster_count": 0,
        "message": "本次未執行人臉分群。",
    }
    return session["face_clustering"]


def load_config() -> dict[str, Any]:
    config = {
        "max_upload_size_mb": DEFAULT_MAX_UPLOAD_SIZE_MB,
        "batch_upload_max_files": DEFAULT_BATCH_UPLOAD_MAX_FILES,
        "batch_upload_max_file_mb": DEFAULT_BATCH_UPLOAD_MAX_FILE_MB,
        "batch_upload_max_total_mb": DEFAULT_BATCH_UPLOAD_MAX_TOTAL_MB,
        "batch_upload_concurrency": DEFAULT_BATCH_UPLOAD_CONCURRENCY,
        "batch_download_max_mb": DEFAULT_BATCH_DOWNLOAD_MAX_MB,
        "face_clustering_enabled": DEFAULT_FACE_CLUSTERING_ENABLED,
    }
    raw_config: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                loaded_config = json.load(f)
        except (OSError, json.JSONDecodeError):
            logger.warning("config.json 讀取失敗，改用預設設定。")
            loaded_config = {}

        if isinstance(loaded_config, dict):
            raw_config = loaded_config
        else:
            logger.warning("config.json 格式錯誤（非物件），改用預設設定。")

    config["max_upload_size_mb"] = _read_positive_int(
        os.environ.get("MAX_UPLOAD_SIZE_MB", raw_config.get("max_upload_size_mb", DEFAULT_MAX_UPLOAD_SIZE_MB)),
        key_name="MAX_UPLOAD_SIZE_MB",
        default=DEFAULT_MAX_UPLOAD_SIZE_MB,
    )
    config["face_clustering_enabled"] = _read_bool(
        os.environ.get(
            "FACE_CLUSTERING_ENABLED",
            raw_config.get("face_clustering_enabled", DEFAULT_FACE_CLUSTERING_ENABLED),
        ),
        key_name="FACE_CLUSTERING_ENABLED",
        default=DEFAULT_FACE_CLUSTERING_ENABLED,
    )
    for key, env_key, default, minimum, maximum in (
        ("batch_upload_max_files", "BATCH_UPLOAD_MAX_FILES", DEFAULT_BATCH_UPLOAD_MAX_FILES, 1, CONFIG_BATCH_UPLOAD_MAX_FILES_CAP),
        ("batch_upload_max_file_mb", "BATCH_UPLOAD_MAX_FILE_MB", DEFAULT_BATCH_UPLOAD_MAX_FILE_MB, 1, 20),
        ("batch_upload_max_total_mb", "BATCH_UPLOAD_MAX_TOTAL_MB", DEFAULT_BATCH_UPLOAD_MAX_TOTAL_MB, 1, 100),
        ("batch_upload_concurrency", "BATCH_UPLOAD_CONCURRENCY", DEFAULT_BATCH_UPLOAD_CONCURRENCY, 1, CONFIG_BATCH_UPLOAD_CONCURRENCY_CAP),
        ("batch_download_max_mb", "BATCH_DOWNLOAD_MAX_MB", DEFAULT_BATCH_DOWNLOAD_MAX_MB, 1, 100),
    ):
        config[key] = _read_positive_int(
            os.environ.get(env_key, raw_config.get(key, default)),
            key_name=env_key,
            default=default,
            minimum=minimum,
            maximum=maximum,
        )
    config["host"] = str(raw_config.get("host", "0.0.0.0") or "0.0.0.0")
    config["port"] = _read_positive_int(
        raw_config.get("port", 8000) or 8000,
        key_name="port",
        default=8000,
        minimum=1,
        maximum=65535,
    )
    return config


CONFIG = load_config()
MAX_UPLOAD_SIZE_MB = CONFIG["max_upload_size_mb"]
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
BATCH_UPLOAD_MAX_FILES = CONFIG["batch_upload_max_files"]
BATCH_UPLOAD_MAX_FILE_MB = CONFIG["batch_upload_max_file_mb"]
BATCH_UPLOAD_MAX_TOTAL_MB = CONFIG["batch_upload_max_total_mb"]
BATCH_UPLOAD_CONCURRENCY = CONFIG["batch_upload_concurrency"]
BATCH_DOWNLOAD_MAX_MB = CONFIG["batch_download_max_mb"]
FACE_CLUSTERING_ENABLED = CONFIG["face_clustering_enabled"]
BATCH_UPLOAD_MAX_FILE_BYTES = BATCH_UPLOAD_MAX_FILE_MB * 1024 * 1024
BATCH_UPLOAD_MAX_TOTAL_BYTES = BATCH_UPLOAD_MAX_TOTAL_MB * 1024 * 1024


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
from photoIdentifier import process_and_visualize_photo, batch_process_folder, batch_process_drive, batch_process_drive_stream, batch_process_uploads_stream
from src.upload_batch import read_upload_batch

# Session storage for batch operations
_batch_sessions: dict[str, dict] = {}
_active_batch_owners: dict[str, str] = {}
batch_state_store = create_batch_state_store()


def _get_client_id(request: Request) -> str:
    client_id = request.session.get("client_id")
    if not client_id:
        client_id = str(uuid.uuid4())
        request.session["client_id"] = client_id
    return client_id


def _acquire_batch_slot(request: Request, session_id: str) -> str:
    owner_id = _get_client_id(request)
    active_session_id = _active_batch_owners.get(owner_id)
    if active_session_id:
        raise HTTPException(
            status_code=409,
            detail="另一個頁籤正在處理照片，請等它完成後再試。",
        )

    existing = _batch_sessions.get(session_id)
    if existing is not None:
        if existing.get("owner_id") != owner_id:
            raise HTTPException(status_code=404, detail="找不到這場活動的辨識紀錄")
        raise HTTPException(status_code=409, detail="這場活動已經建立，請勿重複送出。")

    _active_batch_owners[owner_id] = session_id
    return owner_id


def _release_batch_slot(owner_id: str, session_id: str) -> None:
    if _active_batch_owners.get(owner_id) == session_id:
        _active_batch_owners.pop(owner_id, None)


def _owned_batch_session(request: Request, session_id: str) -> dict[str, Any]:
    session = _batch_sessions.get(session_id)
    if session is None or session.get("owner_id") != _get_client_id(request):
        # Deliberately hide whether another user's session exists.
        raise HTTPException(status_code=404, detail="找不到這場活動的辨識紀錄")
    return session


async def _owned_batch_session_async(request: Request, session_id: str) -> dict[str, Any]:
    owner_id = _get_client_id(request)
    session = _batch_sessions.get(session_id)
    if session is not None and session.get("owner_id") == owner_id:
        return session
    if batch_state_store.enabled:
        stored_session = await batch_state_store.get_session(owner_id, session_id)
        if stored_session is not None:
            _batch_sessions[session_id] = stored_session
            return stored_session
    raise HTTPException(status_code=404, detail="找不到這場活動的辨識紀錄")


async def _persist_session_created(session: dict[str, Any]) -> None:
    if not batch_state_store.enabled:
        return
    try:
        await batch_state_store.create_session(session)
    except Exception:
        logger.exception("Failed to persist batch session=%s", session.get("session_id"))


async def _persist_photo_result(session_id: str, owner_id: str, result: dict[str, Any]) -> None:
    if not batch_state_store.enabled:
        return
    try:
        await batch_state_store.add_photo_result(session_id, owner_id, result)
    except Exception:
        logger.exception("Failed to persist photo result session=%s", session_id)


async def _persist_session_update(session_id: str, updates: dict[str, Any]) -> None:
    if not batch_state_store.enabled:
        return
    try:
        await batch_state_store.update_session(session_id, updates)
    except Exception:
        logger.exception("Failed to persist batch session update=%s", session_id)


async def _classify_session_faces(session_id: str) -> dict[str, Any]:
    session = _batch_sessions[session_id]
    if not FACE_CLUSTERING_ENABLED:
        session["face_clusters"] = []
        session["face_clustering"] = {
            "available": False,
            "reason": "disabled",
            "cluster_count": 0,
            "message": "此部署環境未啟用人臉分群。",
        }
        return session["face_clustering"]
    try:
        processing_info = session.get("processing_info", {})
        eps = float(processing_info.get("face_cluster_eps", DEFAULT_CLUSTER_EPS))
        min_samples = int(processing_info.get("face_cluster_min_samples", DEFAULT_CLUSTER_MIN_SAMPLES))
        clusters = await cluster_batch_results(session.get("results", []), eps=eps, min_samples=min_samples)
        session["face_clusters"] = clusters
        if batch_state_store.enabled:
            try:
                await batch_state_store.save_face_clusters(session_id, session["owner_id"], clusters)
            except Exception:
                logger.exception("Failed to persist face clusters session=%s", session_id)
        session["face_clustering"] = {
            "available": True,
            "cluster_count": len(clusters),
            "eps": eps,
            "min_samples": min_samples,
        }
    except Exception as exc:
        logger.warning("Face clustering unavailable session=%s error=%s", session_id, exc)
        session["face_clusters"] = []
        session["face_clustering"] = {
            "available": False,
            "cluster_count": 0,
            "message": "人臉分類服務目前無法使用，請檢查 classifier API。",
        }
    return session["face_clustering"]


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
    project_number = os.environ.get("GOOGLE_PROJECT_NUMBER", "")
    return {
        "google_client_id": client_id,
        "google_api_key": os.environ.get("GOOGLE_API_KEY", ""),
        "google_app_id": project_number or client_id,
        "batch_upload_max_files": BATCH_UPLOAD_MAX_FILES,
        "batch_upload_max_file_mb": BATCH_UPLOAD_MAX_FILE_MB,
        "batch_upload_max_total_mb": BATCH_UPLOAD_MAX_TOTAL_MB,
        "batch_upload_concurrency": BATCH_UPLOAD_CONCURRENCY,
        "batch_download_max_mb": BATCH_DOWNLOAD_MAX_MB,
        "face_clustering_enabled": FACE_CLUSTERING_ENABLED,
        "face_cluster_default_eps": DEFAULT_CLUSTER_EPS,
        "face_cluster_default_min_samples": DEFAULT_CLUSTER_MIN_SAMPLES,
        "face_cluster_eps_min": FACE_CLUSTER_EPS_MIN,
        "face_cluster_eps_max": FACE_CLUSTER_EPS_MAX,
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
async def analyze_photo(file: UploadFile = File(...), collaborative_memory: str = Form(None)):
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

        local_face_bboxes = await detect_normalized_bboxes(
            image_bytes,
            file.filename or "image.jpg",
            file.content_type,
        )
        return await analyze_brand_strap_image(
            b64_image,
            file.content_type,
            collaborative_memory=collaborative_memory,
            local_face_bboxes=local_face_bboxes,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected internal error handling request: %s", e)
        raise HTTPException(status_code=500, detail="圖片分析伺服器內部錯誤") from e


@app.post("/visualize/", response_class=Response)
async def visualize_photo(file: UploadFile = File(...), collaborative_memory: str = Form(None)):
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
        analysis_result, drawn_image_bytes = await process_and_visualize_photo(image_bytes, file.content_type, collaborative_memory=collaborative_memory)
        
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
    collaborative_memory: Optional[str] = Form(None),
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
            image_bytes, file.content_type, color_rules=color_rules, collaborative_memory=collaborative_memory
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
    concurrency: int = BATCH_UPLOAD_CONCURRENCY
    color_rules: Optional[list] = None
    session_id: Optional[str] = None
    collaborative_memory: Optional[str] = None
    face_cluster_eps: float = DEFAULT_CLUSTER_EPS
    face_cluster_min_samples: int = DEFAULT_CLUSTER_MIN_SAMPLES
    run_public_classification: bool = False
    run_face_clustering: bool = True


@app.post("/batch_upload_stream/")
async def batch_upload_stream(
    request: Request,
    files: list[UploadFile] = File(...),
    concurrency: int = Form(BATCH_UPLOAD_CONCURRENCY),
    color_rules_json: Optional[str] = Form(None),
    session_id: Optional[str] = Form(None),
    collaborative_memory: Optional[str] = Form(None),
    face_cluster_eps: float = Form(DEFAULT_CLUSTER_EPS),
    face_cluster_min_samples: int = Form(DEFAULT_CLUSTER_MIN_SAMPLES),
    run_public_classification: bool = Form(False),
    run_face_clustering: bool = Form(True),
):
    _validate_processing_scope(run_public_classification, run_face_clustering)
    if concurrency < 1:
        raise HTTPException(status_code=400, detail="一次處理張數必須至少為 1")
    if IS_VERCEL and concurrency > 3:
        raise HTTPException(status_code=400, detail="Vercel 環境下一次處理張數必須介於 1 到 3")

    try:
        color_rules = json.loads(color_rules_json) if color_rules_json else None
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="顏色規則格式錯誤") from exc
    if color_rules is not None and not isinstance(color_rules, list):
        raise HTTPException(status_code=400, detail="顏色規則必須是陣列")
    if run_face_clustering:
        face_cluster_eps, face_cluster_min_samples = _read_face_cluster_params(
            face_cluster_eps,
            face_cluster_min_samples,
        )
    else:
        face_cluster_eps, face_cluster_min_samples = DEFAULT_CLUSTER_EPS, DEFAULT_CLUSTER_MIN_SAMPLES

    current_session_id = session_id or str(uuid.uuid4())
    owner_id = _acquire_batch_slot(request, current_session_id)
    try:
        images = await read_upload_batch(
            files,
            max_files=BATCH_UPLOAD_MAX_FILES,
            max_file_bytes=BATCH_UPLOAD_MAX_FILE_BYTES,
            max_total_bytes=BATCH_UPLOAD_MAX_TOTAL_BYTES,
        )
    except Exception:
        _release_batch_slot(owner_id, current_session_id)
        raise
    start_time = datetime.now()
    _batch_sessions[current_session_id] = {
        "session_id": current_session_id,
        "owner_id": owner_id,
        "batch_mode": "upload",
        "start_time": start_time.isoformat(),
        "end_time": None,
        "results": [],
        "processing_info": {
            "file_count": len(images),
            "concurrency": concurrency,
            "face_cluster_eps": face_cluster_eps,
            "face_cluster_min_samples": face_cluster_min_samples,
            "run_public_classification": run_public_classification,
            "run_face_clustering": run_face_clustering,
        },
        "completed": False,
    }
    await _persist_session_created(_batch_sessions[current_session_id])

    async def event_generator():
        try:
            async for chunk in batch_process_uploads_stream(
                images,
                concurrency=concurrency,
                color_rules=color_rules,
                collaborative_memory=collaborative_memory,
                evaluate_public=run_public_classification,
            ):
                if chunk.get("status") == "ok":
                    _batch_sessions[current_session_id]["results"].append(chunk)
                    await _persist_photo_result(current_session_id, owner_id, chunk)
                yield json.dumps({**chunk, "session_id": current_session_id}, ensure_ascii=False) + "\n"

            _batch_sessions[current_session_id]["end_time"] = datetime.now().isoformat()
            _batch_sessions[current_session_id]["completed"] = True
            face_clustering = (
                await _classify_session_faces(current_session_id)
                if run_face_clustering
                else _skip_session_face_clustering(current_session_id)
            )
            await _persist_session_update(
                current_session_id,
                {
                    "status": "completed",
                    "completed_at": _batch_sessions[current_session_id]["end_time"],
                    "result_count": len(_batch_sessions[current_session_id]["results"]),
                    "face_clustering": face_clustering,
                },
            )
            yield json.dumps(
                {
                    "status": "completed",
                    "session_id": current_session_id,
                    "message": f"批次處理完成，共 {len(images)} 張圖片",
                    "face_clustering": face_clustering,
                    "face_clusters": _batch_sessions[current_session_id]["face_clusters"],
                },
                ensure_ascii=False,
            ) + "\n"
        except Exception as exc:
            _batch_sessions[current_session_id]["end_time"] = datetime.now().isoformat()
            await _persist_session_update(
                current_session_id,
                {
                    "status": "failed",
                    "completed_at": _batch_sessions[current_session_id]["end_time"],
                    "error_message": "批次處理中斷",
                },
            )
            logger.exception("Upload batch stream error: %s", exc)
            yield json.dumps(
                {"status": "error", "session_id": current_session_id, "error": "批次處理中斷"},
                ensure_ascii=False,
            ) + "\n"
        finally:
            _release_batch_slot(owner_id, current_session_id)

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")

@app.post("/batch/")
async def batch_visualize(req: BatchRequest, request: Request):
    _validate_processing_scope(req.run_public_classification, req.run_face_clustering)
    input_path = Path(req.input_folder)
    if not input_path.exists() or not input_path.is_dir():
        raise HTTPException(status_code=400, detail=f"資料夾不存在：{req.input_folder}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_folder = str(input_path / f"review_temp_{ts}")
    if req.run_face_clustering:
        face_cluster_eps, face_cluster_min_samples = _read_face_cluster_params(
            req.face_cluster_eps,
            req.face_cluster_min_samples,
        )
    else:
        face_cluster_eps, face_cluster_min_samples = DEFAULT_CLUSTER_EPS, DEFAULT_CLUSTER_MIN_SAMPLES

    # 生成或使用提供的 session_id
    session_id = req.session_id or str(uuid.uuid4())
    owner_id = _acquire_batch_slot(request, session_id)
    start_time = datetime.now()

    # 初始化 session storage
    _batch_sessions[session_id] = {
        "session_id": session_id,
        "owner_id": owner_id,
        "batch_mode": "local",
        "start_time": start_time.isoformat(),
        "end_time": None,
        "results": [],
        "processing_info": {
            "input_folder": req.input_folder,
            "concurrency": req.concurrency,
            "face_cluster_eps": face_cluster_eps,
            "face_cluster_min_samples": face_cluster_min_samples,
            "run_public_classification": req.run_public_classification,
            "run_face_clustering": req.run_face_clustering,
        },
        "completed": False
    }
    await _persist_session_created(_batch_sessions[session_id])

    try:
        results = await batch_process_folder(
            input_dir=req.input_folder,
            output_dir=temp_folder,
            concurrency=req.concurrency,
            color_rules=req.color_rules,
            collaborative_memory=req.collaborative_memory,
            evaluate_public=req.run_public_classification,
        )
        ok = [r for r in results if r["status"] == "ok"]
        err = [r for r in results if r["status"] == "error"]

        # 儲存結果到 session
        _batch_sessions[session_id]["results"] = results
        for result in results:
            if result.get("status") == "ok":
                await _persist_photo_result(session_id, owner_id, result)
        _batch_sessions[session_id]["end_time"] = datetime.now().isoformat()
        _batch_sessions[session_id]["completed"] = True
        face_clustering = (
            await _classify_session_faces(session_id)
            if req.run_face_clustering
            else _skip_session_face_clustering(session_id)
        )
        await _persist_session_update(
            session_id,
            {
                "status": "completed",
                "completed_at": _batch_sessions[session_id]["end_time"],
                "result_count": len(results),
                "face_clustering": face_clustering,
            },
        )

        return {
            "session_id": session_id,
            "total": len(results),
            "success": len(ok),
            "failed": len(err),
            "temp_folder": temp_folder,
            "results": results,
            "face_clustering": face_clustering,
            "face_clusters": _batch_sessions[session_id]["face_clusters"],
        }
    except Exception as e:
        logger.exception("Batch processing error: %s", e)
        _batch_sessions[session_id]["end_time"] = datetime.now().isoformat()
        await _persist_session_update(
            session_id,
            {
                "status": "failed",
                "completed_at": _batch_sessions[session_id]["end_time"],
                "error_message": "批量辨識失敗",
            },
        )
        raise HTTPException(status_code=500, detail="批量辨識失敗") from e
    finally:
        _release_batch_slot(owner_id, session_id)


class DriveBatchRequest(BaseModel):
    folder_id: str
    target_folder_id: Optional[str] = None
    concurrency: int = 3
    color_rules: Optional[list] = None
    session_id: Optional[str] = None
    collaborative_memory: Optional[str] = None
    face_cluster_eps: float = DEFAULT_CLUSTER_EPS
    face_cluster_min_samples: int = DEFAULT_CLUSTER_MIN_SAMPLES
    run_public_classification: bool = False
    run_face_clustering: bool = True

@app.post("/batch_drive/")
async def batch_visualize_drive(req: DriveBatchRequest, request: Request):
    """雲端硬碟批量處理入口 (舊 - 一次性回傳)"""
    _validate_processing_scope(req.run_public_classification, req.run_face_clustering)
    user_key = request.session.get("user_key")
    if not user_key:
        raise HTTPException(status_code=401, detail="尚未登入 Google 帳號")
    
    try:
        creds = get_drive_credentials(request)
        if req.run_face_clustering:
            face_cluster_eps, face_cluster_min_samples = _read_face_cluster_params(
                req.face_cluster_eps,
                req.face_cluster_min_samples,
            )
        else:
            face_cluster_eps, face_cluster_min_samples = DEFAULT_CLUSTER_EPS, DEFAULT_CLUSTER_MIN_SAMPLES
        results = await batch_process_drive(
            folder_id=req.folder_id,
            credentials=creds,
            target_folder_id=req.target_folder_id,
            concurrency=req.concurrency,
            evaluate_public=req.run_public_classification,
        )
        
        success_count = sum(1 for r in results if r.get("status") == "ok")
        failed_count = len(results) - success_count
        if req.run_face_clustering and FACE_CLUSTERING_ENABLED:
            face_clusters = await cluster_batch_results(
                results,
                eps=face_cluster_eps,
                min_samples=face_cluster_min_samples,
            )
            face_clustering = {"available": True, "cluster_count": len(face_clusters)}
        elif req.run_face_clustering:
            face_clusters = []
            face_clustering = {"available": False, "reason": "disabled", "cluster_count": 0}
        else:
            face_clusters = []
            face_clustering = {"available": False, "reason": "not_requested", "cluster_count": 0}
        
        return {
            "status": "success",
            "results": results,
            "success": success_count,
            "failed": failed_count,
            "face_clustering": face_clustering,
            "face_clusters": face_clusters,
        }
    except Exception as e:
        logger.exception("Drive batch error: %s", e)
        if "找不到使用者憑證" in str(e):
             raise HTTPException(status_code=401, detail="Google 授權已失效，請重新連結。")
        raise HTTPException(status_code=500, detail=f"雲端批量處理失敗: {str(e)}")

@app.post("/batch_drive_stream/")
async def batch_visualize_drive_stream(req: DriveBatchRequest, request: Request):
    """雲端硬碟批量處理入口 (新 - 串流即時回傳進度)"""
    _validate_processing_scope(req.run_public_classification, req.run_face_clustering)
    user_key = request.session.get("user_key")
    if not user_key:
        raise HTTPException(status_code=401, detail="尚未登入 Google 帳號")

    try:
        creds = get_drive_credentials(request)
        if req.run_face_clustering:
            face_cluster_eps, face_cluster_min_samples = _read_face_cluster_params(
                req.face_cluster_eps,
                req.face_cluster_min_samples,
            )
        else:
            face_cluster_eps, face_cluster_min_samples = DEFAULT_CLUSTER_EPS, DEFAULT_CLUSTER_MIN_SAMPLES

        # 1. 獲取協作記憶：優先使用請求提供的，再從遠端讀取
        collaborative_memory = req.collaborative_memory

        if not collaborative_memory and req.run_public_classification:
            # 嘗試從 Google Drive 讀取
            try:
                from googleapiclient.discovery import build
                drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
                q = f"name = '.photoidentifier_memory.md' and '{req.folder_id}' in parents and trashed = false"
                res = drive_service.files().list(q=q, fields="files(id)").execute()
                files = res.get("files", [])

                if files:
                    file_id = files[0]["id"]
                    import httpx
                    url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
                    headers = {"Authorization": f"Bearer {creds.token}"}
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        resp = await client.get(url, headers=headers)
                        if resp.status_code == 200:
                            content = resp.text
                            if len(content) > 1000:
                                content = content[:1000]
                            collaborative_memory = content
            except Exception as e:
                logger.warning(f"無法獲取協作記憶文件: {e}")

        # 生成或使用提供的 session_id
        session_id = req.session_id or str(uuid.uuid4())
        owner_id = _acquire_batch_slot(request, session_id)
        start_time = datetime.now()

        # 初始化 session storage
        _batch_sessions[session_id] = {
            "session_id": session_id,
            "owner_id": owner_id,
            "batch_mode": "drive",
            "start_time": start_time.isoformat(),
            "end_time": None,
            "results": [],
            "processing_info": {
                "folder_id": req.folder_id,
                "concurrency": req.concurrency,
                "face_cluster_eps": face_cluster_eps,
                "face_cluster_min_samples": face_cluster_min_samples,
                "run_public_classification": req.run_public_classification,
                "run_face_clustering": req.run_face_clustering,
            },
            "completed": False
        }
        await _persist_session_created(_batch_sessions[session_id])

        async def event_generator():
            try:
                # 這裡調用剛才在 photoIdentifier.py 寫好的產生器
                async for chunk in batch_process_drive_stream(
                    folder_id=req.folder_id,
                    credentials=creds,
                    target_folder_id=req.target_folder_id,
                    concurrency=req.concurrency,
                    color_rules=req.color_rules,
                    collaborative_memory=collaborative_memory,
                    evaluate_public=req.run_public_classification,
                ):
                    # 儲存結果到 session
                    if chunk.get("status") == "ok":
                        _batch_sessions[session_id]["results"].append(chunk)
                        await _persist_photo_result(session_id, owner_id, chunk)

                    # 每一筆結果都轉成 JSON 並加上換行符號推播出去
                    chunk_with_session = {**chunk, "session_id": session_id}
                    yield json.dumps(chunk_with_session, ensure_ascii=False) + "\n"

                # 標記完成
                _batch_sessions[session_id]["end_time"] = datetime.now().isoformat()
                _batch_sessions[session_id]["completed"] = True
                face_clustering = (
                    await _classify_session_faces(session_id)
                    if req.run_face_clustering
                    else _skip_session_face_clustering(session_id)
                )
                await _persist_session_update(
                    session_id,
                    {
                        "status": "completed",
                        "completed_at": _batch_sessions[session_id]["end_time"],
                        "result_count": len(_batch_sessions[session_id]["results"]),
                        "face_clustering": face_clustering,
                    },
                )
                yield json.dumps({
                    "status": "completed",
                    "session_id": session_id,
                    "message": f"批次處理完成，共 {len(_batch_sessions[session_id]['results'])} 個結果",
                    "face_clustering": face_clustering,
                    "face_clusters": _batch_sessions[session_id]["face_clusters"],
                }, ensure_ascii=False) + "\n"

            except Exception as inner_e:
                _batch_sessions[session_id]["end_time"] = datetime.now().isoformat()
                await _persist_session_update(
                    session_id,
                    {
                        "status": "failed",
                        "completed_at": _batch_sessions[session_id]["end_time"],
                        "error_message": f"串流中斷: {str(inner_e)}",
                    },
                )
                yield json.dumps({"status": "error", "error": f"串流中斷: {str(inner_e)}"}, ensure_ascii=False) + "\n"
            finally:
                _release_batch_slot(owner_id, session_id)

        return StreamingResponse(event_generator(), media_type="application/x-ndjson")

    except Exception as e:
        if "owner_id" in locals() and "session_id" in locals():
            _release_batch_slot(owner_id, session_id)
        logger.exception("Drive batch stream error: %s", e)
        if "找不到使用者憑證" in str(e):
             raise HTTPException(status_code=401, detail="Google 授權已失效，請重新連結。")
        raise HTTPException(status_code=500, detail=f"啟動串流處理失敗: {str(e)}")


@app.get("/drive/collaborative_memory/get/")
async def get_collaborative_memory(folder_id: str, request: Request):
    """獲取指定文件夾的協作記憶文件內容"""
    try:
        creds = get_drive_credentials(request)
        from googleapiclient.discovery import build
        drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

        # 查找 .photoidentifier_memory.md 文件
        q = f"name = '.photoidentifier_memory.md' and '{folder_id}' in parents and trashed = false"
        res = drive_service.files().list(q=q, fields="files(id)").execute()
        files = res.get("files", [])

        if not files:
            return {"content": "", "exists": False}

        # 下載文件內容
        file_id = files[0]["id"]
        import httpx
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
        headers = {"Authorization": f"Bearer {creds.token}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                raise Exception(f"下載失敗: HTTP {resp.status_code}")
            content = resp.text

        return {"content": content, "exists": True}

    except Exception as e:
        logger.exception("獲取協作記憶文件失敗: %s", e)
        raise HTTPException(status_code=500, detail=f"無法讀取協作記憶文件: {str(e)}")


@app.post("/drive/collaborative_memory/save/")
async def save_collaborative_memory(request: Request, folder_id: str = Form(...), content: str = Form(...)):
    """保存協作記憶文件到 Google Drive（新增或更新）"""
    try:
        creds = get_drive_credentials(request)
        from googleapiclient.discovery import build
        import io
        from googleapiclient.http import MediaIoBaseUpload

        drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

        # 限制內容長度為 1000 字
        if len(content) > 1000:
            content = content[:1000]

        # 查找是否已存在
        q = f"name = '.photoidentifier_memory.md' and '{folder_id}' in parents and trashed = false"
        res = drive_service.files().list(q=q, fields="files(id)").execute()
        files = res.get("files", [])

        file_metadata = {"name": ".photoidentifier_memory.md"}
        media = MediaIoBaseUpload(io.BytesIO(content.encode('utf-8')), mimetype="text/markdown", resumable=True)

        if files:
            # 更新現有文件
            file_id = files[0]["id"]
            drive_service.files().update(
                fileId=file_id,
                body=file_metadata,
                media_body=media,
                fields="id"
            ).execute()
            return {"status": "updated", "message": "協作記憶文件已更新"}
        else:
            # 創建新文件
            file_metadata["mimeType"] = "text/markdown"
            file_metadata["parents"] = [folder_id]
            drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id"
            ).execute()
            return {"status": "created", "message": "協作記憶文件已創建"}

    except Exception as e:
        logger.exception("保存協作記憶文件失敗: %s", e)
        raise HTTPException(status_code=500, detail=f"無法保存協作記憶文件: {str(e)}")


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


class FaceClusterUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    status: Optional[Literal["unconfirmed", "pending", "confirmed", "merged"]] = None
    notes: Optional[str] = None


class DriveBatchExportRequest(BaseModel):
    session_id: str
    target_folder_id: str
    document: dict[str, Any]


class DriveOutputFolderRequest(BaseModel):
    name: str
    parent_folder_id: Optional[str] = None


class DriveOutputFolderRenameRequest(BaseModel):
    name: str


def _normalized_drive_folder_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="資料夾名稱不可留白")
    if len(normalized) > 100:
        raise HTTPException(status_code=400, detail="資料夾名稱不可超過 100 個字")
    return normalized


def _create_drive_output_folder(credentials, name: str, parent_folder_id: str) -> dict:
    from googleapiclient.discovery import build

    drive_service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    return drive_service.files().create(
        body={
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_folder_id],
        },
        fields="id,name,parents",
    ).execute()


def _rename_drive_output_folder(credentials, folder_id: str, name: str) -> dict:
    from googleapiclient.discovery import build

    drive_service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    existing = drive_service.files().get(fileId=folder_id, fields="id,mimeType").execute()
    if existing.get("mimeType") != "application/vnd.google-apps.folder":
        raise HTTPException(status_code=400, detail="指定的輸出區不是 Google 雲端資料夾")
    return drive_service.files().update(
        fileId=folder_id,
        body={"name": name},
        fields="id,name,parents",
    ).execute()


@app.post("/drive/output-folders")
async def create_drive_output_folder(req: DriveOutputFolderRequest, request: Request):
    name = _normalized_drive_folder_name(req.name)
    parent_folder_id = (req.parent_folder_id or "root").strip() or "root"
    credentials = get_drive_credentials(request)
    try:
        folder = await run_in_threadpool(
            _create_drive_output_folder, credentials, name, parent_folder_id
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Drive output folder creation failed")
        raise HTTPException(status_code=500, detail=f"無法建立 Google 雲端資料夾：{exc}") from exc
    return {"status": "created", "folder": folder}


@app.patch("/drive/output-folders/{folder_id}")
async def rename_drive_output_folder(
    folder_id: str, req: DriveOutputFolderRenameRequest, request: Request
):
    folder_id = folder_id.strip()
    if not folder_id:
        raise HTTPException(status_code=400, detail="Google 雲端輸出區不可留白")
    name = _normalized_drive_folder_name(req.name)
    credentials = get_drive_credentials(request)
    try:
        folder = await run_in_threadpool(
            _rename_drive_output_folder, credentials, folder_id, name
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Drive output folder rename failed folder=%s", folder_id)
        raise HTTPException(status_code=500, detail=f"無法重新命名 Google 雲端資料夾：{exc}") from exc
    return {"status": "renamed", "folder": folder}


def _save_json_export_to_drive(
    credentials,
    target_folder_id: str,
    file_name: str,
    content: bytes,
) -> dict:
    import io

    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload

    drive_service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype="application/json", resumable=False)
    return drive_service.files().create(
        body={
            "name": file_name,
            "mimeType": "application/json",
            "parents": [target_folder_id],
        },
        media_body=media,
        fields="id,name",
    ).execute()


def _drive_query_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _copy_people_folders_to_drive(
    credentials,
    target_folder_id: str,
    people_folders: list[dict[str, Any]],
) -> dict[str, Any]:
    from googleapiclient.discovery import build

    drive_service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    def get_or_create_subfolder(name: str, parent_id: str) -> str:
        safe_name = str(name or "未命名人物").strip()[:80] or "未命名人物"
        query_name = _drive_query_literal(safe_name)
        query_parent = _drive_query_literal(parent_id)
        query = (
            f"name = '{query_name}' and mimeType = 'application/vnd.google-apps.folder' "
            f"and '{query_parent}' in parents and trashed = false"
        )
        found = drive_service.files().list(q=query, fields="files(id)", pageSize=1).execute().get("files", [])
        if found:
            return found[0]["id"]
        created = drive_service.files().create(
            body={
                "name": safe_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            },
            fields="id",
        ).execute()
        return created["id"]

    copied_count = 0
    errors: list[str] = []
    folders_created_or_reused = 0

    for folder in people_folders:
        folder_name = str(folder.get("name") or "未命名人物").strip()[:80] or "未命名人物"
        photos = folder.get("photos") or []
        if not photos:
            continue
        try:
            folder_id = get_or_create_subfolder(folder_name, target_folder_id)
            folders_created_or_reused += 1
        except Exception as exc:
            errors.append(f"{folder_name}: 無法建立資料夾：{exc}")
            continue

        for photo in photos:
            file_id = photo.get("drive_id")
            file_name = str(photo.get("file_name") or "unknown")
            if not file_id:
                errors.append(f"{folder_name}/{file_name}: 缺少 drive_id")
                continue
            try:
                drive_service.files().copy(
                    fileId=file_id,
                    body={"name": file_name, "parents": [folder_id]},
                    fields="id",
                ).execute()
                copied_count += 1
            except Exception as exc:
                errors.append(f"{folder_name}/{file_name}: {exc}")

    return {"copied_count": copied_count, "folder_count": folders_created_or_reused, "errors": errors}


@app.post("/batch_exports/drive")
async def create_drive_batch_export(req: DriveBatchExportRequest, request: Request):
    session_id = req.session_id.strip()
    target_folder_id = req.target_folder_id.strip()
    if not session_id or not target_folder_id:
        raise HTTPException(status_code=400, detail="session_id 與 Google 雲端輸出區不可留白")

    session = await _owned_batch_session_async(request, session_id)
    if session.get("batch_mode") != "drive":
        raise HTTPException(status_code=400, detail="只有 Google 雲端批次可以備份到輸出區")
    if req.document.get("session_id") != session_id:
        raise HTTPException(status_code=400, detail="JSON 文件的 session_id 與批次不一致")

    content = json.dumps(req.document, ensure_ascii=False, indent=2).encode("utf-8")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="JSON 文件超過 10 MB 上限")

    credentials = get_drive_credentials(request)
    file_name = f"photo_people_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    try:
        saved = await run_in_threadpool(
            _save_json_export_to_drive,
            credentials,
            target_folder_id,
            file_name,
            content,
        )
        people_copy = {"copied_count": 0, "folder_count": 0, "errors": []}
        people_folders = req.document.get("people_folders")
        if isinstance(people_folders, list) and people_folders:
            people_copy = await run_in_threadpool(
                _copy_people_folders_to_drive,
                credentials,
                target_folder_id,
                people_folders,
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Drive relationship export failed session=%s", session_id)
        if batch_state_store.enabled:
            try:
                await batch_state_store.create_export_record(
                    session_id,
                    session["owner_id"],
                    "drive",
                    file_name,
                    "failed",
                    {"target_folder_id": target_folder_id, "error": str(exc)},
                )
            except Exception:
                logger.exception("Failed to persist failed export metadata session=%s", session_id)
        raise HTTPException(status_code=500, detail=f"無法將 JSON 備份到 Google 雲端：{exc}") from exc

    if batch_state_store.enabled:
        try:
            await batch_state_store.save_photo_assignments(session_id, session["owner_id"], req.document)
            await batch_state_store.create_export_record(
                session_id,
                session["owner_id"],
                "drive",
                saved.get("name") or file_name,
                "created",
                    {
                        "target_folder_id": target_folder_id,
                        "file_id": saved.get("id"),
                        "people_copy": people_copy,
                    },
                )
        except Exception:
            logger.exception("Failed to persist Drive export metadata session=%s", session_id)

    return {
        "status": "created",
        "file_id": saved.get("id"),
        "file_name": saved.get("name") or file_name,
        "people_copy": people_copy,
    }


@app.get("/face_clusters/{session_id}")
async def get_face_clusters(session_id: str, request: Request):
    session = await _owned_batch_session_async(request, session_id)
    return {"session_id": session_id, "clusters": session.get("face_clusters", [])}


@app.patch("/face_clusters/{session_id}/{cluster_id}")
async def update_face_cluster(session_id: str, cluster_id: str, req: FaceClusterUpdateRequest, request: Request):
    session = await _owned_batch_session_async(request, session_id)

    cluster = next(
        (item for item in session.get("face_clusters", []) if item.get("cluster_id") == cluster_id),
        None,
    )
    if cluster is None:
        raise HTTPException(status_code=404, detail="找不到指定的人物群組")

    updates = req.model_dump(exclude_none=True)
    if "display_name" in updates:
        updates["display_name"] = updates["display_name"].strip() or cluster["display_name"]
    if "notes" in updates:
        updates["notes"] = updates["notes"].strip()
    cluster.update(updates)
    if batch_state_store.enabled:
        stored_cluster = await batch_state_store.update_face_cluster(
            session_id,
            session["owner_id"],
            cluster_id,
            updates,
        )
        if stored_cluster is not None:
            cluster = stored_cluster
    return {"session_id": session_id, "cluster": cluster}


@app.post("/batch_summary/")
async def get_batch_summary(req: BatchSummaryRequest, request: Request):
    """獲取批次處理的綜合指標與混淆矩陣"""
    session_id = req.session_id
    session_data = await _owned_batch_session_async(request, session_id)
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
async def export_batch_summary(req: BatchSummaryRequest, request: Request):
    """匯出批次指標為 JSON 格式"""
    session_id = req.session_id
    session_data = await _owned_batch_session_async(request, session_id)
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
async def list_batch_sessions(request: Request):
    """列出所有活躍的批次會話"""
    sessions = []
    owner_id = _get_client_id(request)
    if batch_state_store.enabled:
        stored_sessions = await batch_state_store.list_sessions(owner_id)
        if stored_sessions:
            return {
                "sessions": [
                    {
                        "session_id": item.get("session_id"),
                        "batch_mode": item.get("batch_mode"),
                        "start_time": item.get("created_at"),
                        "result_count": item.get("result_count", 0),
                        "status": item.get("status", "processing"),
                    }
                    for item in stored_sessions
                ]
            }
    for session_id, session_data in _batch_sessions.items():
        if session_data.get("owner_id") != owner_id:
            continue
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
