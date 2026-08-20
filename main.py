from dotenv import load_dotenv

load_dotenv()

import asyncio
from collections import deque
import email.message
import inspect
import io
import json
import logging
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Optional
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Response, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.concurrency import run_in_threadpool
import base64
import shutil

from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
from pydantic import BaseModel, Field, ValidationError
import os
import uuid
from urllib.parse import quote, urlparse

from src.insight_api_client import (
    DEFAULT_CLUSTER_EPS,
    DEFAULT_CLUSTER_MIN_SAMPLES,
    DEFAULT_CLUSTER_BATCH_SIZE,
    build_clusters_from_response,
    cancel_cluster_job,
    cluster_batch_results,
    create_cluster_job_from_results,
    detect_normalized_bboxes,
    get_cluster_job_snapshot,
    prepare_cluster_images,
)
from src.batch_state_store import (
    create_batch_state_store,
    get_backend_service_account_json,
    person_id_for,
    photo_id_for,
    strip_image_payload,
)
from src.drive_name_memory import (
    NAME_MEMORY_FILE_NAME,
    default_name_memory_document,
    is_storable_person_name,
    merge_name_memory_names,
    normalize_name_memory_document,
    normalize_person_name,
)
from src.user_store import (
    PUBLIC_CLASSIFICATION_DENIED_DETAIL,
    create_user_store,
    default_user_record,
    feature_enabled,
    normalize_google_user_id,
    public_user_payload,
)

DEFAULT_MAX_UPLOAD_SIZE_MB = 25
DEFAULT_BATCH_UPLOAD_BATCH_SIZE = DEFAULT_CLUSTER_BATCH_SIZE
DEFAULT_BATCH_UPLOAD_BATCH_SIZE_LOCAL = DEFAULT_CLUSTER_BATCH_SIZE
DEFAULT_BATCH_UPLOAD_BATCH_SIZE_CLOUD = DEFAULT_CLUSTER_BATCH_SIZE
DEFAULT_BATCH_UPLOAD_TOTAL_MAX_FILES = 200
DEFAULT_BATCH_UPLOAD_MAX_FILE_MB = 20
DEFAULT_BATCH_UPLOAD_MAX_TOTAL_MB = 500
DEFAULT_LOCAL_UPLOAD_REQUEST_MAX_FILES = 3
DEFAULT_LOCAL_UPLOAD_REQUEST_MAX_TOTAL_MB = 4
DEFAULT_LOCAL_UPLOAD_ANONYMOUS_MAX_FILES = 10
DEFAULT_BATCH_UPLOAD_CONCURRENCY = 5
DEFAULT_BATCH_UPLOAD_CONCURRENCY_LOCAL_CAP = 5
DEFAULT_BATCH_UPLOAD_CONCURRENCY_CLOUD_CAP = 3
DEFAULT_BATCH_DOWNLOAD_MAX_MB = 4000
DEFAULT_FACE_CLUSTERING_ENABLED = True
DEFAULT_EXPORT_SIGNED_URL_TTL_MINUTES = 60
DEFAULT_PREVIEW_SIGNED_URL_TTL_MINUTES = 24 * 60
FACE_CLUSTER_EPS_MIN = 0.05
FACE_CLUSTER_EPS_MAX = 1.5
IS_VERCEL = os.getenv("VERCEL") == "1"
IS_GCP = bool(os.getenv("K_SERVICE") or os.getenv("GAE_SERVICE"))
CONFIG_BATCH_UPLOAD_CONCURRENCY_CAP = None
CONFIG_PATH = Path(__file__).with_name("config.json")
logger = logging.getLogger(__name__)
FaceClusterProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
LOGIN_REQUIRED_DETAIL = "請先登入 Google 帳號再使用這個功能"


def _env_url(name: str) -> str:
    value = str(os.getenv(name, "") or "").strip().rstrip("/")
    return value


def _public_app_origin() -> str:
    return _env_url("PUBLIC_APP_ORIGIN") or _env_url("APP_BASE_URL")


def _app_base_url() -> str:
    return _env_url("APP_BASE_URL") or _public_app_origin()


def _setup_logging() -> None:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return
    level_name = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
    if level <= logging.DEBUG:
        logging.getLogger("httpx").setLevel(logging.INFO)
        logging.getLogger("httpcore").setLevel(logging.INFO)
        logging.getLogger("urllib3").setLevel(logging.INFO)
        logging.getLogger("multipart").setLevel(logging.WARNING)
        logging.getLogger("python_multipart").setLevel(logging.WARNING)
        logging.getLogger("watchfiles").setLevel(logging.WARNING)
        logging.getLogger("watchfiles.main").setLevel(logging.WARNING)


_setup_logging()


def _log_validation_rejection(scope: str, field: str, *, value: Any, minimum: Any | None = None, maximum: Any | None = None, reason: str) -> None:
    logger.warning(
        "validation_rejected scope=%s field=%s value=%r minimum=%r maximum=%r reason=%s",
        scope,
        field,
        value,
        minimum,
        maximum,
        reason,
    )


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


def _runtime_platform_name() -> str | None:
    if IS_VERCEL:
        return "vercel"
    if IS_GCP:
        return "gcp"
    return None


def _apply_platform_upload_overrides(config: dict[str, Any]) -> None:
    platform_name = _runtime_platform_name()
    if not platform_name:
        return
    suffix = platform_name.upper()
    for key, minimum, maximum in (
        ("batch_upload_batch_size_local", 1, None),
        ("batch_upload_batch_size_cloud", 1, None),
        ("batch_upload_max_file_mb", 1, None),
        ("batch_upload_max_total_mb", 1, None),
        ("local_upload_request_max_files", 1, None),
        ("local_upload_request_max_total_mb", 1, None),
    ):
        env_key = f"{key.upper()}_{suffix}"
        raw_value = os.environ.get(env_key)
        if raw_value is None or raw_value == "":
            continue
        config[key] = _read_positive_int(
            raw_value,
            key_name=env_key,
            default=int(config[key]),
            minimum=minimum,
            maximum=maximum,
        )


def _read_face_cluster_params(raw_eps: Any, raw_min_samples: Any, *, max_files: int) -> tuple[float, int]:
    try:
        eps = float(raw_eps)
    except (TypeError, ValueError) as exc:
        _log_validation_rejection("face_cluster", "eps", value=raw_eps, minimum=FACE_CLUSTER_EPS_MIN, maximum=FACE_CLUSTER_EPS_MAX, reason="not_a_number")
        raise HTTPException(status_code=400, detail="分群 eps 必須是數字") from exc
    if not FACE_CLUSTER_EPS_MIN <= eps <= FACE_CLUSTER_EPS_MAX:
        _log_validation_rejection("face_cluster", "eps", value=eps, minimum=FACE_CLUSTER_EPS_MIN, maximum=FACE_CLUSTER_EPS_MAX, reason="out_of_range")
        raise HTTPException(
            status_code=400,
            detail=f"分群 eps 必須介於 {FACE_CLUSTER_EPS_MIN} 到 {FACE_CLUSTER_EPS_MAX}",
        )

    try:
        min_samples = int(raw_min_samples)
    except (TypeError, ValueError) as exc:
        _log_validation_rejection("face_cluster", "min_samples", value=raw_min_samples, minimum=1, maximum=max_files, reason="not_an_integer")
        raise HTTPException(status_code=400, detail="分群 min_samples 必須是整數") from exc
    if not 1 <= min_samples <= max_files:
        _log_validation_rejection("face_cluster", "min_samples", value=min_samples, minimum=1, maximum=max_files, reason="out_of_range")
        raise HTTPException(
            status_code=400,
            detail=f"分群 min_samples 必須介於 1 到 {max_files}",
        )
    return eps, min_samples


def _validate_processing_scope(run_public_classification: bool, run_face_clustering: bool) -> None:
    if not run_public_classification and not run_face_clustering:
        _log_validation_rejection("processing_scope", "features", value={"run_public_classification": run_public_classification, "run_face_clustering": run_face_clustering}, reason="all_disabled")
        raise HTTPException(status_code=400, detail="至少選擇一項：可公開性判定或人臉分群")


def _validate_cloud_api_concurrency(concurrency: int) -> None:
    if concurrency < 1:
        _log_validation_rejection("cloud_batch", "concurrency", value=concurrency, minimum=1, maximum=CLOUD_API_CONCURRENCY_CAP, reason="below_minimum")
        raise HTTPException(status_code=400, detail="一次處理張數必須至少為 1")
    if concurrency > CLOUD_API_CONCURRENCY_CAP:
        _log_validation_rejection("cloud_batch", "concurrency", value=concurrency, minimum=1, maximum=CLOUD_API_CONCURRENCY_CAP, reason="above_maximum")
        raise HTTPException(
            status_code=400,
            detail=f"雲端模式一次處理張數必須介於 1 到 {CLOUD_API_CONCURRENCY_CAP}",
        )


def _validate_local_api_concurrency(concurrency: int) -> None:
    if concurrency < 1:
        _log_validation_rejection("local_batch", "concurrency", value=concurrency, minimum=1, maximum=LOCAL_BATCH_CONCURRENCY_CAP, reason="below_minimum")
        raise HTTPException(status_code=400, detail="一次處理張數必須至少為 1")
    if concurrency > LOCAL_BATCH_CONCURRENCY_CAP:
        _log_validation_rejection("local_batch", "concurrency", value=concurrency, minimum=1, maximum=LOCAL_BATCH_CONCURRENCY_CAP, reason="above_maximum")
        raise HTTPException(
            status_code=400,
            detail=f"本機模式一次處理張數必須介於 1 到 {LOCAL_BATCH_CONCURRENCY_CAP}",
        )


def _validate_batch_file_count(file_count: int, *, mode: str) -> None:
    if file_count < 1:
        _log_validation_rejection(f"{mode}_batch", "file_count", value=file_count, minimum=1, reason="below_minimum")
        raise HTTPException(status_code=400, detail="請先準備至少 1 張照片")


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
        "batch_upload_batch_size": DEFAULT_BATCH_UPLOAD_BATCH_SIZE,
        "batch_upload_batch_size_local": DEFAULT_BATCH_UPLOAD_BATCH_SIZE_LOCAL,
        "batch_upload_batch_size_cloud": DEFAULT_BATCH_UPLOAD_BATCH_SIZE_CLOUD,
        "batch_upload_total_max_files": DEFAULT_BATCH_UPLOAD_TOTAL_MAX_FILES,
        "batch_upload_max_file_mb": DEFAULT_BATCH_UPLOAD_MAX_FILE_MB,
        "batch_upload_max_total_mb": DEFAULT_BATCH_UPLOAD_MAX_TOTAL_MB,
        "local_upload_request_max_files": DEFAULT_LOCAL_UPLOAD_REQUEST_MAX_FILES,
        "local_upload_request_max_total_mb": DEFAULT_LOCAL_UPLOAD_REQUEST_MAX_TOTAL_MB,
        "batch_upload_concurrency": DEFAULT_BATCH_UPLOAD_CONCURRENCY,
        "batch_upload_concurrency_local_cap": DEFAULT_BATCH_UPLOAD_CONCURRENCY_LOCAL_CAP,
        "batch_upload_concurrency_cloud_cap": DEFAULT_BATCH_UPLOAD_CONCURRENCY_CLOUD_CAP,
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
    for key, env_key, legacy_key, legacy_env_key, default, minimum, maximum in (
        ("batch_upload_batch_size", "BATCH_UPLOAD_BATCH_SIZE", "batch_upload_max_files", "BATCH_UPLOAD_MAX_FILES", DEFAULT_BATCH_UPLOAD_BATCH_SIZE, 1, None),
        ("batch_upload_batch_size_local", "BATCH_UPLOAD_BATCH_SIZE_LOCAL", "batch_upload_max_files_local", "BATCH_UPLOAD_MAX_FILES_LOCAL", DEFAULT_BATCH_UPLOAD_BATCH_SIZE_LOCAL, 1, None),
        ("batch_upload_batch_size_cloud", "BATCH_UPLOAD_BATCH_SIZE_CLOUD", "batch_upload_max_files_cloud", "BATCH_UPLOAD_MAX_FILES_CLOUD", DEFAULT_BATCH_UPLOAD_BATCH_SIZE_CLOUD, 1, None),
        ("batch_upload_total_max_files", "BATCH_UPLOAD_TOTAL_MAX_FILES", None, None, DEFAULT_BATCH_UPLOAD_TOTAL_MAX_FILES, 1, None),
        # 先不限制上限，避免本機 / .env / config.json 的批次容量參數被硬性擋掉。
        ("batch_upload_max_file_mb", "BATCH_UPLOAD_MAX_FILE_MB", None, None, DEFAULT_BATCH_UPLOAD_MAX_FILE_MB, 1, None),
        ("batch_upload_max_total_mb", "BATCH_UPLOAD_MAX_TOTAL_MB", None, None, DEFAULT_BATCH_UPLOAD_MAX_TOTAL_MB, 1, None),
        ("local_upload_request_max_files", "LOCAL_UPLOAD_REQUEST_MAX_FILES", None, None, DEFAULT_LOCAL_UPLOAD_REQUEST_MAX_FILES, 1, None),
        ("local_upload_request_max_total_mb", "LOCAL_UPLOAD_REQUEST_MAX_TOTAL_MB", None, None, DEFAULT_LOCAL_UPLOAD_REQUEST_MAX_TOTAL_MB, 1, None),
        ("batch_upload_concurrency", "BATCH_UPLOAD_CONCURRENCY", None, None, DEFAULT_BATCH_UPLOAD_CONCURRENCY, 1, CONFIG_BATCH_UPLOAD_CONCURRENCY_CAP),
        ("batch_upload_concurrency_local_cap", "BATCH_UPLOAD_CONCURRENCY_LOCAL_CAP", None, None, DEFAULT_BATCH_UPLOAD_CONCURRENCY_LOCAL_CAP, 1, None),
        ("batch_upload_concurrency_cloud_cap", "BATCH_UPLOAD_CONCURRENCY_CLOUD_CAP", None, None, DEFAULT_BATCH_UPLOAD_CONCURRENCY_CLOUD_CAP, 1, None),
        ("batch_download_max_mb", "BATCH_DOWNLOAD_MAX_MB", None, None, DEFAULT_BATCH_DOWNLOAD_MAX_MB, 1, None),
    ):
        raw_value = os.environ.get(env_key, raw_config.get(key, default))
        if raw_value == default and legacy_key and legacy_env_key:
            raw_value = os.environ.get(legacy_env_key, raw_config.get(legacy_key, default))
        config[key] = _read_positive_int(
            raw_value,
            key_name=env_key,
            default=default,
            minimum=minimum,
            maximum=maximum,
        )
    _apply_platform_upload_overrides(config)
    config["host"] = str(os.environ.get("HOST", raw_config.get("host", "0.0.0.0")) or "0.0.0.0")
    config["port"] = _read_positive_int(
        os.environ.get("PORT", raw_config.get("port", 8000)) or 8000,
        key_name="PORT",
        default=8000,
        minimum=1,
        maximum=65535,
    )
    return config


CONFIG = load_config()
MAX_UPLOAD_SIZE_MB = CONFIG["max_upload_size_mb"]
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
BATCH_UPLOAD_BATCH_SIZE = CONFIG["batch_upload_batch_size"]
BATCH_UPLOAD_BATCH_SIZE_LOCAL = CONFIG["batch_upload_batch_size_local"]
BATCH_UPLOAD_BATCH_SIZE_CLOUD = CONFIG["batch_upload_batch_size_cloud"]
BATCH_UPLOAD_TOTAL_MAX_FILES = CONFIG["batch_upload_total_max_files"]
BATCH_UPLOAD_MAX_FILE_MB = CONFIG["batch_upload_max_file_mb"]
BATCH_UPLOAD_MAX_TOTAL_MB = CONFIG["batch_upload_max_total_mb"]
LOCAL_UPLOAD_REQUEST_MAX_FILES = CONFIG["local_upload_request_max_files"]
LOCAL_UPLOAD_REQUEST_MAX_TOTAL_MB = CONFIG["local_upload_request_max_total_mb"]
BATCH_UPLOAD_CONCURRENCY = CONFIG["batch_upload_concurrency"]
LOCAL_BATCH_CONCURRENCY_CAP = CONFIG["batch_upload_concurrency_local_cap"]
CLOUD_API_CONCURRENCY_CAP = CONFIG["batch_upload_concurrency_cloud_cap"]
BATCH_DOWNLOAD_MAX_MB = CONFIG["batch_download_max_mb"]
FACE_CLUSTERING_ENABLED = CONFIG["face_clustering_enabled"]
EXPORT_SIGNED_URL_TTL_MINUTES = _read_positive_int(
    os.getenv("EXPORT_SIGNED_URL_TTL_MINUTES", DEFAULT_EXPORT_SIGNED_URL_TTL_MINUTES),
    key_name="EXPORT_SIGNED_URL_TTL_MINUTES",
    default=DEFAULT_EXPORT_SIGNED_URL_TTL_MINUTES,
    minimum=1,
    maximum=24 * 60,
)
PREVIEW_SIGNED_URL_TTL_MINUTES = _read_positive_int(
    os.getenv("PREVIEW_SIGNED_URL_TTL_MINUTES", DEFAULT_PREVIEW_SIGNED_URL_TTL_MINUTES),
    key_name="PREVIEW_SIGNED_URL_TTL_MINUTES",
    default=DEFAULT_PREVIEW_SIGNED_URL_TTL_MINUTES,
    minimum=1,
    maximum=7 * 24 * 60,
)
PHOTOIDENTIFIER_EXPORTS_BUCKET = str(
    os.getenv("PHOTOIDENTIFIER_EXPORTS_BUCKET") or "vision-493709-photoidentifier-exports"
).strip().removeprefix("gs://")
BATCH_UPLOAD_MAX_FILE_BYTES = BATCH_UPLOAD_MAX_FILE_MB * 1024 * 1024
BATCH_UPLOAD_MAX_TOTAL_BYTES = BATCH_UPLOAD_MAX_TOTAL_MB * 1024 * 1024
LOCAL_UPLOAD_REQUEST_MAX_TOTAL_BYTES = LOCAL_UPLOAD_REQUEST_MAX_TOTAL_MB * 1024 * 1024


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
    https_only=IS_GCP or os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true",
)


@app.middleware("http")
async def protect_state_changing_requests(request: Request, call_next):
    _same_origin_state_change(request)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response

from src.google_usage import analyze_brand_strap_image, PhotoAnalysisResult
from src.google_auth import get_auth_url, exchange_code_for_token, load_user_credentials, token_store, DEFAULT_SCOPES
from src.metrics import compute_batch_metrics, collect_changed_files, compute_analysis_stats, format_metrics_for_export
from photoIdentifier import (
    process_and_visualize_photo,
    batch_process_folder,
    batch_process_drive,
    batch_process_drive_stream,
    batch_process_uploads_stream,
    list_drive_image_files,
    process_drive_file_item,
)
from src.upload_batch import read_upload_batch

# Session storage for batch operations
_batch_sessions: dict[str, dict] = {}
_active_batch_owners: dict[str, str] = {}
_batch_session_locks: dict[str, asyncio.Lock] = {}
_cluster_job_snapshot_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_request_rate_windows: dict[tuple[str, str], deque[float]] = {}
batch_state_store = create_batch_state_store()
user_store = create_user_store()


def _request_client_key(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded_for.split(",", 1)[0].strip() if forwarded_for else ""
    return client_ip or (request.client.host if request.client else "unknown")


def _enforce_request_rate_limit(request: Request, bucket: str, limit: int, window_sec: int = 60) -> None:
    now = time.monotonic()
    key = (bucket, f"{_request_client_key(request)}:{_get_client_id(request)}")
    window = _request_rate_windows.setdefault(key, deque())
    while window and now - window[0] >= window_sec:
        window.popleft()
    if len(window) >= limit:
        raise HTTPException(status_code=429, detail="請稍後再試，要求頻率過高。", headers={"Retry-After": str(window_sec)})
    window.append(now)


def _same_origin_state_change(request: Request) -> None:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    origin = request.headers.get("origin")
    if not origin:
        return
    allowed = {_public_app_origin(), _app_base_url()}
    if origin.rstrip("/") not in {value for value in allowed if value}:
        raise HTTPException(status_code=403, detail="不允許的跨來源要求。")


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


def _batch_session_lock(session_id: str) -> asyncio.Lock:
    lock = _batch_session_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _batch_session_locks[session_id] = lock
    return lock


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
        session = _batch_sessions.get(session_id) or {}
        await batch_state_store.add_photo_result(
            session_id,
            owner_id,
            {
                **result,
                "user_account": str(session.get("user_account") or ""),
                "google_user_id": str(session.get("google_user_id") or ""),
            },
        )
    except Exception:
        logger.exception("Failed to persist photo result session=%s", session_id)


def _result_image_telemetry(result: dict[str, Any] | None) -> dict[str, int]:
    if not isinstance(result, dict):
        return {
            "original_b64_chars": 0,
            "drawn_b64_chars": 0,
            "output_b64_chars": 0,
            "result_bytes": 0,
        }
    original_b64 = str(result.get("original_image_b64") or "").strip()
    drawn_b64 = str(result.get("drawn_image_b64") or "").strip()
    output_b64 = str(result.get("output_b64") or "").strip()
    payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
    return {
        "original_b64_chars": len(original_b64),
        "drawn_b64_chars": len(drawn_b64),
        "output_b64_chars": len(output_b64),
        "result_bytes": len(payload),
    }


def _log_result_telemetry(scope: str, result: dict[str, Any] | None, *, extra: dict[str, Any] | None = None) -> None:
    telemetry = _result_image_telemetry(result)
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "image_telemetry_detail scope=%s file=%s telemetry=%s extra=%s",
            scope,
            (result or {}).get("file_name") or (result or {}).get("file") or "",
            telemetry,
            extra or {},
        )
    logger.info(
        "image_telemetry scope=%s file=%s result_bytes=%s b64_chars=%s/%s/%s extra=%s",
        scope,
        (result or {}).get("file_name") or (result or {}).get("file") or "",
        telemetry["result_bytes"],
        telemetry["original_b64_chars"],
        telemetry["drawn_b64_chars"],
        telemetry["output_b64_chars"],
        extra or {},
    )


def _log_preview_externalized(scope: str, result: dict[str, Any] | None, *, extra: dict[str, Any] | None = None) -> None:
    usage = (result or {}).get("usage") if isinstance(result, dict) else {}
    logger.info(
        "preview_externalized scope=%s file=%s original_preview_url=%s annotated_preview_url=%s original_b64_chars=%s drawn_b64_chars=%s usage=%s extra=%s",
        scope,
        (result or {}).get("file_name") or (result or {}).get("file") or "",
        bool((result or {}).get("original_preview_url")),
        bool((result or {}).get("annotated_preview_url")),
        (result or {}).get("original_b64_chars", 0),
        (result or {}).get("drawn_b64_chars", 0),
        usage,
        extra or {},
    )


def _log_bytes_telemetry(
    scope: str,
    *,
    file_name: str = "",
    byte_count: int = 0,
    content_type: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    details = extra or {}
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "bytes_telemetry_detail scope=%s file=%s bytes=%s content_type=%s extra=%s",
            scope,
            file_name,
            byte_count,
            content_type,
            details,
        )
    logger.info(
        "bytes_telemetry scope=%s file=%s bytes=%s content_type=%s extra=%s",
        scope,
        file_name,
        byte_count,
        content_type,
        details,
    )


def _log_gcs_object_written(scope: str, *, bucket_name: str, object_name: str, byte_count: int, extra: dict[str, Any] | None = None) -> None:
    logger.info(
        "gcs_object_written scope=%s bucket_name=%s object_name=%s bytes=%s extra=%s",
        scope,
        bucket_name,
        object_name,
        byte_count,
        extra or {},
    )


def _log_firestore_usage_saved(scope: str, *, owner_id: str = "", session_id: str = "", export_id: str = "", usage: dict[str, Any] | None = None, extra: dict[str, Any] | None = None) -> None:
    logger.info(
        "firestore_usage_saved scope=%s owner_id=%s session_id=%s export_id=%s usage=%s extra=%s",
        scope,
        owner_id,
        session_id,
        export_id,
        usage or {},
        extra or {},
    )


def _result_payload_size_bytes(result: dict[str, Any] | None) -> int:
    if not isinstance(result, dict):
        return 0
    return len(json.dumps(result, ensure_ascii=False).encode("utf-8"))


def _empty_usage_metrics() -> dict[str, int]:
    return {
        "preview_bytes_uploaded": 0,
        "preview_object_count": 0,
        "storage_export_bytes": 0,
        "storage_export_image_bytes": 0,
        "storage_export_image_count": 0,
        "storage_export_count": 0,
        "storage_download_count": 0,
    }


def _merge_usage_metrics(*values: dict[str, Any] | None) -> dict[str, int]:
    merged = _empty_usage_metrics()
    for value in values:
        if not isinstance(value, dict):
            continue
        for key in merged:
            try:
                merged[key] += max(int(value.get(key) or 0), 0)
            except (TypeError, ValueError):
                continue
    return merged


async def _persist_session_update(session_id: str, updates: dict[str, Any]) -> None:
    if not batch_state_store.enabled:
        return
    try:
        await batch_state_store.update_session(session_id, updates)
    except Exception:
        logger.exception("Failed to persist batch session update=%s", session_id)


def _face_cluster_progress_event(session_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    progress = snapshot.get("progress") if isinstance(snapshot.get("progress"), dict) else {}
    batch_start_index = int(snapshot.get("batch_start_index") or 0)
    completed = int(progress.get("completed") or snapshot.get("completed") or 0)
    total = int(snapshot.get("total") or progress.get("total") or 0)
    aggregate_completed = min(batch_start_index + completed, total) if total else completed
    return {
        "status": "face_cluster_progress",
        "session_id": session_id,
        "job_id": snapshot.get("job_id"),
        "job_status": snapshot.get("status", "queued"),
        "stage": snapshot.get("stage", "queued"),
        "queue_position": snapshot.get("queue_position"),
        "progress": {
            "completed": aggregate_completed,
            "total": total,
            "percent": round(aggregate_completed / total * 100, 1) if total else float(progress.get("percent") or 0),
        },
    }


def _face_cluster_starting_event(session_id: str) -> dict[str, Any]:
    session = _batch_sessions.get(session_id) or {}
    results = session.get("results") or []
    return {
        "status": "face_cluster_progress",
        "session_id": session_id,
        "job_id": None,
        "job_status": "starting",
        "stage": "uploading",
        "queue_position": None,
        "progress": {
            "completed": 0,
            "total": len(results),
            "percent": 0,
        },
    }


def _cluster_job_snapshot_cache_ttl_sec() -> float:
    try:
        return max(float(os.getenv("CLASSIFIER_STATUS_CACHE_TTL_SEC", "8")), 0.0)
    except ValueError:
        return 8.0


async def _get_cluster_job_snapshot_cached(job_id: str) -> dict[str, Any]:
    ttl_sec = _cluster_job_snapshot_cache_ttl_sec()
    now = time.monotonic()
    if ttl_sec > 0:
        cached = _cluster_job_snapshot_cache.get(job_id)
        if cached and now - cached[0] < ttl_sec:
            return cached[1]
    snapshot = await get_cluster_job_snapshot(job_id)
    if ttl_sec > 0:
        _cluster_job_snapshot_cache[job_id] = (now, snapshot)
    return snapshot


def _clear_cluster_job_snapshot_cache(job_id: str | None) -> None:
    if job_id:
        _cluster_job_snapshot_cache.pop(str(job_id), None)


async def _classify_session_faces(
    session_id: str,
    progress_callback: FaceClusterProgressCallback | None = None,
) -> dict[str, Any]:
    session = _batch_sessions[session_id]
    started_at = time.perf_counter()
    logger.info(
        "face.cluster.start session=%s result_count=%s enabled=%s",
        session_id,
        len(session.get("results") or []),
        FACE_CLUSTERING_ENABLED,
    )
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
        logger.info(
            "face.cluster.submit session=%s result_count=%s batch_size=%s eps=%s min_samples=%s",
            session_id,
            len(session.get("results") or []),
            processing_info.get("face_cluster_batch_size") or BATCH_UPLOAD_BATCH_SIZE,
            eps,
            min_samples,
        )
        clusters = await cluster_batch_results(
            session.get("results", []),
            eps=eps,
            min_samples=min_samples,
            batch_size=int(processing_info.get("face_cluster_batch_size") or BATCH_UPLOAD_BATCH_SIZE),
            progress_callback=progress_callback,
            session_id=session_id,
        )
        if session.get("cancel_requested"):
            session["face_clusters"] = []
            session["face_clustering"] = {
                "available": False,
                "reason": "cancelled",
                "cluster_count": 0,
                "message": "人物整理已中止。",
            }
            return session["face_clustering"]
        session["face_clusters"] = clusters
        logger.info(
            "face.cluster.success session=%s cluster_count=%s elapsed_sec=%.3f",
            session_id,
            len(clusters),
            time.perf_counter() - started_at,
        )
        if batch_state_store.enabled:
            try:
                await batch_state_store.save_face_clusters(
                    session_id,
                    session["owner_id"],
                    clusters,
                    str(session.get("google_user_id") or ""),
                )
            except Exception:
                logger.exception("Failed to persist face clusters session=%s", session_id)
        session["face_clustering"] = {
            "available": True,
            "cluster_count": len(clusters),
            "eps": eps,
            "min_samples": min_samples,
        }
    except Exception as exc:
        logger.exception(
            "Face clustering unavailable session=%s error_type=%s error=%r",
            session_id,
            type(exc).__name__,
            exc,
        )
        session["face_clusters"] = []
        if session.get("cancel_requested"):
            session["face_clustering"] = {
                "available": False,
                "reason": "cancelled",
                "cluster_count": 0,
                "message": "人物整理已中止。",
            }
        else:
            session["face_clustering"] = {
                "available": False,
                "cluster_count": 0,
                "message": "人臉分類服務目前無法使用，請檢查 classifier API。",
            }
        logger.error(
            "face.cluster.failed session=%s reason=%s elapsed_sec=%.3f",
            session_id,
            session.get("face_clustering", {}).get("message"),
            time.perf_counter() - started_at,
        )
    return session["face_clustering"]


async def _request_batch_cancel(session: dict[str, Any]) -> dict[str, Any]:
    session["cancel_requested"] = True
    session["cancelled_at"] = datetime.now().isoformat()

    job_id = str(session.get("face_cluster_job_id") or "").strip() or None
    if not job_id:
        return {
            "success": True,
            "session_id": session.get("session_id"),
            "job_id": None,
            "message": "已記下中止請求，會在目前階段結束後停止。",
        }

    try:
        result = await cancel_cluster_job(job_id)
    except Exception as exc:
        logger.exception(
            "Failed to cancel face cluster job session=%s job_id=%s",
            session.get("session_id"),
            job_id,
        )
        return {
            "success": False,
            "session_id": session.get("session_id"),
            "job_id": job_id,
            "message": f"中止請求送出失敗：{exc}",
        }

    session["face_cluster_cancel_requested"] = True
    return {
        "success": True,
        "session_id": session.get("session_id"),
        "job_id": job_id,
        "message": str(result.get("message") or "已送出中止請求，會在目前照片處理完後停止。"),
        "cancel_requested": bool(result.get("cancel_requested", True)),
        "status": result.get("status"),
    }


def _is_missing_cluster_job_error(exc: Exception) -> bool:
    message = str(exc)
    return "Insight API HTTP 404" in message and "找不到這個辨識工作" in message


def _start_face_clustering_task(
    session_id: str,
    *,
    enabled: bool,
) -> tuple[asyncio.Task[dict[str, Any]], asyncio.Queue[dict[str, Any]]]:
    progress_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def on_progress(snapshot: dict[str, Any]) -> None:
        session = _batch_sessions.get(session_id)
        if session is not None:
            session["face_cluster_job_id"] = snapshot.get("job_id")
            if (
                session.get("cancel_requested")
                and snapshot.get("job_id")
                and not session.get("face_cluster_cancel_requested")
            ):
                session["face_cluster_cancel_requested"] = True
                try:
                    await cancel_cluster_job(str(snapshot["job_id"]))
                except Exception:
                    logger.exception(
                        "Failed to cancel delayed face cluster job session=%s job_id=%s",
                        session_id,
                        snapshot.get("job_id"),
                    )
        await progress_queue.put(_face_cluster_progress_event(session_id, snapshot))

    async def run() -> dict[str, Any]:
        if not enabled:
            logger.info("face.cluster.skip session=%s reason=disabled", session_id)
            return _skip_session_face_clustering(session_id)
        if "progress_callback" not in inspect.signature(_classify_session_faces).parameters:
            logger.info("face.cluster.dispatch session=%s mode=legacy", session_id)
            return await _classify_session_faces(session_id)
        logger.info("face.cluster.dispatch session=%s mode=progress", session_id)
        return await _classify_session_faces(session_id, progress_callback=on_progress)

    return asyncio.create_task(run()), progress_queue


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


def _is_logged_in_google_user(request: Request) -> bool:
    return _get_google_userinfo(request) is not None


def _local_upload_total_max_files(request: Request) -> int:
    if _is_logged_in_google_user(request):
        return BATCH_UPLOAD_TOTAL_MAX_FILES
    return DEFAULT_LOCAL_UPLOAD_ANONYMOUS_MAX_FILES


def _local_upload_limits_message(request: Request) -> str:
    local_total_max_files = _local_upload_total_max_files(request)
    if local_total_max_files >= BATCH_UPLOAD_TOTAL_MAX_FILES:
        return (
            f"這台電腦一次可先準備 {local_total_max_files} 張；"
            f"上傳會每次最多 {LOCAL_UPLOAD_REQUEST_MAX_FILES} 張、"
            f"合計 {LOCAL_UPLOAD_REQUEST_MAX_TOTAL_MB}MB 分批送出。"
        )
    return (
        f"這台電腦未登入時，一次最多先準備 {local_total_max_files} 張；"
        "登入 Google 後可使用這個平台較寬鬆的上傳限制。"
    )


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
    config = _build_frontend_config(request)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "batch_upload_limits_local_message": config["batch_upload_limits_local_message"],
        },
    )


def _build_frontend_config(request: Request) -> dict[str, Any]:
    """提供前端啟動 Google Picker 所需的公開設定 (不含 Secret)"""
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    project_number = os.environ.get("GOOGLE_PROJECT_NUMBER", "")
    local_total_max_files = _local_upload_total_max_files(request)
    return {
        "app_base_url": _app_base_url(),
        "public_app_origin": _public_app_origin(),
        "google_client_id": client_id,
        "google_api_key": os.environ.get("GOOGLE_API_KEY", ""),
        "google_app_id": project_number or client_id,
        "batch_upload_batch_size": BATCH_UPLOAD_BATCH_SIZE,
        "batch_upload_batch_size_local": BATCH_UPLOAD_BATCH_SIZE_LOCAL,
        "batch_upload_batch_size_cloud": BATCH_UPLOAD_BATCH_SIZE_CLOUD,
        "batch_upload_total_max_files": BATCH_UPLOAD_TOTAL_MAX_FILES,
        "batch_upload_max_files": local_total_max_files,
        "batch_upload_max_files_local": local_total_max_files,
        "batch_upload_max_files_cloud": BATCH_UPLOAD_TOTAL_MAX_FILES,
        "batch_upload_max_file_mb": BATCH_UPLOAD_MAX_FILE_MB,
        "batch_upload_max_total_mb": BATCH_UPLOAD_MAX_TOTAL_MB,
        "local_upload_request_max_files": LOCAL_UPLOAD_REQUEST_MAX_FILES,
        "local_upload_request_max_total_mb": LOCAL_UPLOAD_REQUEST_MAX_TOTAL_MB,
        "batch_upload_concurrency": BATCH_UPLOAD_CONCURRENCY,
        "batch_upload_concurrency_local_cap": LOCAL_BATCH_CONCURRENCY_CAP,
        "batch_upload_concurrency_cloud_cap": CLOUD_API_CONCURRENCY_CAP,
        "local_upload_logged_in": local_total_max_files >= BATCH_UPLOAD_TOTAL_MAX_FILES,
        "batch_upload_concurrency_local_message": f"這台電腦一次最多先看 {LOCAL_BATCH_CONCURRENCY_CAP} 張，我會慢慢幫你整理好。",
        "batch_upload_concurrency_cloud_message": f"Google 雲端一次最多先看 {CLOUD_API_CONCURRENCY_CAP} 張，這樣整理起來會比較穩。",
        "batch_upload_limits_local_message": _local_upload_limits_message(request),
        "batch_upload_limits_cloud_message": f"Google 雲端會每 {BATCH_UPLOAD_BATCH_SIZE_CLOUD} 張分成一批送出，全部準備好後再整理人物。",
        "batch_download_max_mb": BATCH_DOWNLOAD_MAX_MB,
        "face_clustering_enabled": FACE_CLUSTERING_ENABLED,
        "face_cluster_default_eps": DEFAULT_CLUSTER_EPS,
        "face_cluster_default_min_samples": DEFAULT_CLUSTER_MIN_SAMPLES,
        "face_cluster_eps_min": FACE_CLUSTER_EPS_MIN,
        "face_cluster_eps_max": FACE_CLUSTER_EPS_MAX,
    }


@app.get("/api/config")
async def get_frontend_config(request: Request):
    return _build_frontend_config(request)

@app.get("/api/user/me")
async def get_current_user(request: Request):
    """取得目前登入的 Google 帳號資訊"""
    userinfo = _get_google_userinfo(request)
    if userinfo is None:
        return {"logged_in": False}
    user_record = await _get_or_create_current_user_record(request, userinfo)
    return {
        "logged_in": True,
        **public_user_payload(user_record),
    }


class UserPreferencesUpdateRequest(BaseModel):
    auto_email_results: bool


@app.patch("/api/user/preferences")
async def update_current_user_preferences(request: Request, req: UserPreferencesUpdateRequest):
    userinfo = _get_google_userinfo(request)
    if not isinstance(userinfo, dict):
        raise HTTPException(status_code=401, detail=LOGIN_REQUIRED_DETAIL)
    google_user_id = normalize_google_user_id(userinfo)
    if not google_user_id:
        raise HTTPException(status_code=400, detail="Google user id 不可留白")
    user_record = await user_store.update_preferences(
        google_user_id,
        {"auto_email_results": req.auto_email_results},
    )
    return {
        "success": True,
        "preferences": public_user_payload(user_record).get("preferences", {}),
    }


class NameMemoryRecordRequest(BaseModel):
    names: list[str] = Field(default_factory=list)
    source: str = "manual"


DRIVE_APP_FOLDER_NAME = ".photoidentifier"


def _require_logged_in_google_user_id(request: Request) -> str:
    userinfo = _get_google_userinfo(request)
    if not isinstance(userinfo, dict):
        raise HTTPException(status_code=401, detail=LOGIN_REQUIRED_DETAIL)
    google_user_id = normalize_google_user_id(userinfo)
    if not google_user_id:
        raise HTTPException(status_code=400, detail="Google user id 不可留白")
    return google_user_id


@app.get("/training-dataset")
async def get_training_dataset(
    request: Request,
    person_id: str | None = None,
    limit: int = 1000,
):
    google_user_id = _require_logged_in_google_user_id(request)
    items = await batch_state_store.list_training_face_links(
        google_user_id,
        person_id=str(person_id or "").strip() or None,
        limit=max(1, min(int(limit), 5000)),
    )
    return {
        "google_user_id": google_user_id,
        "person_id": str(person_id or "").strip() or None,
        "count": len(items),
        "items": items,
    }


def _find_drive_app_folder_id(drive_service) -> str | None:
    query = (
        f"name = '{DRIVE_APP_FOLDER_NAME}' and "
        "mimeType = 'application/vnd.google-apps.folder' and 'root' in parents and trashed = false"
    )
    response = drive_service.files().list(
        q=query,
        fields="files(id)",
        pageSize=1,
    ).execute()
    files = response.get("files", [])
    if not files:
        return None
    return str(files[0].get("id") or "").strip() or None


def _ensure_drive_app_folder_id(drive_service) -> str:
    existing = _find_drive_app_folder_id(drive_service)
    if existing:
        return existing
    created = drive_service.files().create(
        body={
            "name": DRIVE_APP_FOLDER_NAME,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["root"],
        },
        fields="id",
    ).execute()
    folder_id = str(created.get("id") or "").strip()
    if not folder_id:
        raise RuntimeError("無法建立 Google Drive 應用資料夾")
    return folder_id


def _find_drive_name_memory_file_id(drive_service, parent_id: str) -> str | None:
    query = f"name = '{NAME_MEMORY_FILE_NAME}' and '{parent_id}' in parents and trashed = false"
    response = drive_service.files().list(
        q=query,
        fields="files(id)",
        pageSize=1,
    ).execute()
    files = response.get("files", [])
    if not files:
        return None
    return str(files[0].get("id") or "").strip() or None


def _load_drive_name_memory_document(credentials) -> dict[str, Any]:
    from googleapiclient.discovery import build

    drive_service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    folder_id = _find_drive_app_folder_id(drive_service)
    file_id = _find_drive_name_memory_file_id(drive_service, folder_id) if folder_id else None
    if not file_id:
        file_id = _find_drive_name_memory_file_id(drive_service, "root")
    if not file_id:
        return default_name_memory_document()
    try:
        raw_bytes = drive_service.files().get_media(fileId=file_id).execute()
        raw = json.loads(raw_bytes.decode("utf-8"))
    except Exception:
        logger.warning("Drive name memory parse failed file_id=%s", file_id, exc_info=True)
        return default_name_memory_document()
    return normalize_name_memory_document(raw)


def _save_drive_name_memory_document(credentials, document: dict[str, Any]) -> dict[str, Any]:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload

    drive_service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    normalized_document = normalize_name_memory_document(document)
    content = json.dumps(normalized_document, ensure_ascii=False, indent=2).encode("utf-8")
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype="application/json", resumable=False)
    folder_id = _ensure_drive_app_folder_id(drive_service)
    file_id = _find_drive_name_memory_file_id(drive_service, folder_id)
    metadata = {"name": NAME_MEMORY_FILE_NAME}
    if file_id:
        drive_service.files().update(
            fileId=file_id,
            body=metadata,
            media_body=media,
            fields="id",
        ).execute()
    else:
        drive_service.files().create(
            body={**metadata, "mimeType": "application/json", "parents": [folder_id]},
            media_body=media,
            fields="id",
        ).execute()
    return normalized_document


@app.get("/drive/name-memory")
async def get_current_user_drive_name_memory(request: Request):
    _require_logged_in_google_user_id(request)
    credentials = get_drive_credentials(request)
    document = await run_in_threadpool(_load_drive_name_memory_document, credentials)
    items = document.get("names", []) if isinstance(document.get("names"), list) else []
    return {
        "logged_in": True,
        "summary": {
            "count": len(items),
            "updated_at": str(document.get("updated_at") or ""),
            "file_name": NAME_MEMORY_FILE_NAME,
        },
        "items": items,
    }


@app.post("/drive/name-memory/record")
async def record_current_user_drive_name_memory(request: Request, req: NameMemoryRecordRequest):
    _require_logged_in_google_user_id(request)
    normalized_names = [
        normalize_person_name(name)
        for name in (req.names or [])
        if is_storable_person_name(name)
    ]
    if not normalized_names:
        return {
            "success": True,
            "recorded": 0,
            "summary": {
                "count": 0,
                "updated_at": "",
                "file_name": NAME_MEMORY_FILE_NAME,
            },
            "items": [],
        }
    credentials = get_drive_credentials(request)

    def _record() -> dict[str, Any]:
        current_document = _load_drive_name_memory_document(credentials)
        merged_document = merge_name_memory_names(
            current_document,
            normalized_names,
            source=req.source or "manual",
        )
        return _save_drive_name_memory_document(credentials, merged_document)

    document = await run_in_threadpool(_record)
    items = document.get("names", []) if isinstance(document.get("names"), list) else []
    return {
        "success": True,
        "recorded": len({name for name in normalized_names}),
        "summary": {
            "count": len(items),
            "updated_at": str(document.get("updated_at") or ""),
            "file_name": NAME_MEMORY_FILE_NAME,
        },
        "items": items,
    }


def _cache_google_userinfo(request: Request, userinfo: dict[str, Any]) -> dict[str, Any]:
    cached = {
        "id": str(userinfo.get("id") or userinfo.get("sub") or userinfo.get("google_user_id") or ""),
        "email": userinfo.get("email"),
        "name": userinfo.get("name"),
        "picture": userinfo.get("picture"),
    }
    request.session["google_userinfo"] = cached
    google_user_id = normalize_google_user_id(cached)
    if google_user_id:
        request.session["google_user_id"] = google_user_id
    return cached


def _sync_logged_in_user_sync(request: Request, userinfo: dict[str, Any]) -> dict[str, Any] | None:
    cached = _cache_google_userinfo(request, userinfo)
    if not normalize_google_user_id(cached):
        logger.warning("Google userinfo missing stable id; email=%s", cached.get("email"))
        return None
    try:
        return user_store.get_or_create_user_sync(cached)
    except Exception:
        logger.exception("Failed to sync application user google_user_id=%s", cached.get("id"))
        return None


async def _get_or_create_current_user_record(
    request: Request,
    userinfo: dict[str, Any] | None = None,
) -> dict[str, Any]:
    userinfo = userinfo or _get_google_userinfo(request)
    if not isinstance(userinfo, dict):
        raise HTTPException(status_code=403, detail=PUBLIC_CLASSIFICATION_DENIED_DETAIL)
    cached = _cache_google_userinfo(request, userinfo)
    try:
        return await user_store.get_or_create_user(cached)
    except Exception:
        logger.exception("Failed to read application user google_user_id=%s", cached.get("id"))
        fallback = default_user_record(cached)
        fallback["enabled"] = False
        fallback["features"]["public_classification"] = False
        return fallback


async def _require_feature(
    request: Request,
    feature: str,
    detail: str | None = None,
) -> dict[str, Any]:
    userinfo = _get_google_userinfo(request)
    if not isinstance(userinfo, dict):
        logger.info(
            "Feature gate rejected session=%s feature=%s reason=not_authenticated",
            request.session.get("session_id"),
            feature,
        )
        raise HTTPException(status_code=403, detail=LOGIN_REQUIRED_DETAIL)
    user = await _get_or_create_current_user_record(request, userinfo)
    if not feature_enabled(user, feature):
        logger.info(
            "Feature gate rejected session=%s feature=%s reason=feature_disabled google_user_id=%s",
            request.session.get("session_id"),
            feature,
            user.get("google_user_id"),
        )
        raise HTTPException(status_code=403, detail=detail or PUBLIC_CLASSIFICATION_DENIED_DETAIL)
    return user


async def _require_public_classification_if_requested(
    request: Request,
    run_public_classification: bool,
) -> None:
    if run_public_classification:
        await _require_feature(request, "public_classification", PUBLIC_CLASSIFICATION_DENIED_DETAIL)


def _get_google_user_id(request: Request) -> str:
    google_user_id = str(request.session.get("google_user_id") or "").strip()
    if google_user_id:
        return google_user_id
    userinfo = _get_google_userinfo(request)
    if not isinstance(userinfo, dict):
        return ""
    return normalize_google_user_id(userinfo)


def _get_google_userinfo(request: Request) -> dict[str, Any] | None:
    cached = request.session.get("google_userinfo")
    if isinstance(cached, dict) and cached.get("email") and normalize_google_user_id(cached):
        return cached
    try:
        creds = get_drive_credentials(request)
    except Exception:
        return None

    from googleapiclient.discovery import build
    try:
        service = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        userinfo = service.userinfo().get().execute()
        if isinstance(userinfo, dict):
            return _cache_google_userinfo(request, userinfo)
        return None
    except Exception as e:
        logger.error(f"取得使用者資訊失敗: {e}")
        return None


def _get_batch_user_account(request: Request, batch_mode: str) -> str:
    userinfo = _get_google_userinfo(request)
    email = userinfo.get("email") if isinstance(userinfo, dict) else ""
    return str(email or "")


def _oauth_request_host_redirect(request: Request) -> str | None:
    redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", "").strip()
    if not redirect_uri:
        return None
    redirect_host = (urlparse(redirect_uri).hostname or "").lower()
    request_host = (request.url.hostname or "").lower()
    if redirect_host and request_host and redirect_host != request_host:
        target = redirect_uri.rsplit("/auth/callback", 1)[0]
        query = str(request.url.query or "").strip()
        if query:
            target = f"{target}{request.url.path}?{query}"
        else:
            target = f"{target}{request.url.path}"
        return target
    return None


def _normalize_post_auth_redirect(target: str | None) -> str:
    value = str(target or "").strip()
    if not value:
        return "/?auth=success"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return "/?auth=success"
    if not value.startswith("/"):
        return "/?auth=success"
    return value

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


@app.get("/drive_file/{file_id}")
async def get_drive_file(file_id: str, request: Request):
    creds = get_drive_credentials(request)
    import httpx

    url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
    headers = {"Authorization": f"Bearer {creds.token}"}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail="Drive image download failed")
    content_type = resp.headers.get("content-type") or "image/jpeg"
    _log_bytes_telemetry(
        "drive_file_download",
        file_name=file_id,
        byte_count=len(resp.content),
        content_type=content_type,
        extra={"status_code": resp.status_code},
    )
    return Response(
        content=resp.content,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


@app.post("/analyze/", response_model=PhotoAnalysisResult)
async def analyze_photo(
    request: Request,
    file: UploadFile = File(...),
    collaborative_memory: str = Form(None),
):
    await _require_feature(request, "public_classification", PUBLIC_CLASSIFICATION_DENIED_DETAIL)
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
async def visualize_photo(
    request: Request,
    file: UploadFile = File(...),
    collaborative_memory: str = Form(None),
):
    await _require_feature(request, "public_classification", PUBLIC_CLASSIFICATION_DENIED_DETAIL)
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
    request: Request,
    file: UploadFile = File(...),
    color_rules_json: Optional[str] = Form(None),
    collaborative_memory: Optional[str] = Form(None),
):
    """專門給單圖 UI 使用，回傳 JSON 結果，且夾帶畫好框的 base64 圖片供前端立即渲染"""
    await _require_feature(request, "public_classification", PUBLIC_CLASSIFICATION_DENIED_DETAIL)
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
        _log_bytes_telemetry(
            "single_analyze_drawn_image",
            file_name=file.filename or "",
            byte_count=len(drawn_image_bytes),
            content_type="image/jpeg",
            extra={
                "input_bytes": len(image_bytes),
                "drawn_b64_chars": len(drawn_b64),
            },
        )
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
    upload_chunk_index: int = Form(0),
    upload_chunk_total: int = Form(1),
    upload_total_files: Optional[int] = Form(None),
):
    _enforce_request_rate_limit(request, "batch-upload", limit=30)
    local_total_max_files = _local_upload_total_max_files(request)
    _validate_processing_scope(run_public_classification, run_face_clustering)
    await _require_public_classification_if_requested(request, run_public_classification)
    _validate_local_api_concurrency(concurrency)
    _validate_batch_file_count(len(files), mode="local")
    if upload_chunk_total < 1:
        raise HTTPException(status_code=400, detail="上傳分批總數必須至少為 1")
    if upload_chunk_index < 0 or upload_chunk_index >= upload_chunk_total:
        raise HTTPException(status_code=400, detail="上傳分批序號超出範圍")
    is_chunked_upload = upload_chunk_total > 1
    is_final_upload_chunk = upload_chunk_index == upload_chunk_total - 1
    expected_total_files = upload_total_files if is_chunked_upload and upload_total_files else len(files)
    _validate_batch_file_count(expected_total_files, mode="local")
    if expected_total_files > local_total_max_files:
        if local_total_max_files >= BATCH_UPLOAD_TOTAL_MAX_FILES:
            detail = f"一次最多上傳 {local_total_max_files} 張圖片。請減少檔案，或改用 Google 雲端資料夾模式。"
        else:
            detail = f"未登入時一次最多上傳 {local_total_max_files} 張圖片；登入 Google 後可使用這個平台較寬鬆的上傳限制。"
        raise HTTPException(status_code=413, detail=detail)

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
            max_files=BATCH_UPLOAD_BATCH_SIZE_LOCAL,
        )
    else:
        face_cluster_eps, face_cluster_min_samples = DEFAULT_CLUSTER_EPS, DEFAULT_CLUSTER_MIN_SAMPLES

    current_session_id = session_id or str(uuid.uuid4())
    existing_session = _batch_sessions.get(current_session_id)
    is_new_session = existing_session is None
    if is_new_session:
        owner_id = _acquire_batch_slot(request, current_session_id)
    elif is_chunked_upload:
        owner_id = _get_client_id(request)
        if existing_session.get("owner_id") != owner_id:
            raise HTTPException(status_code=404, detail="找不到這場活動的辨識紀錄")
        if existing_session.get("completed"):
            raise HTTPException(status_code=409, detail="這場活動已經完成，請勿繼續送出分批上傳。")
        existing_processing_info = existing_session.get("processing_info") if isinstance(existing_session.get("processing_info"), dict) else {}
        if int(existing_processing_info.get("upload_next_chunk_index") or 0) != upload_chunk_index:
            raise HTTPException(status_code=409, detail="上傳分批順序不一致，請重新送出。")
        active_session_id = _active_batch_owners.get(owner_id)
        if active_session_id and active_session_id != current_session_id:
            raise HTTPException(status_code=409, detail="另一個頁籤正在處理照片，請等它完成後再試。")
        _active_batch_owners[owner_id] = current_session_id
    else:
        owner_id = _acquire_batch_slot(request, current_session_id)
    if is_new_session and is_chunked_upload and upload_chunk_index != 0:
        _release_batch_slot(owner_id, current_session_id)
        raise HTTPException(status_code=400, detail="上傳分批必須從第一批開始。")
    previous_result_count = len(existing_session.get("results") or []) if existing_session else 0
    try:
        images = await read_upload_batch(
            files,
            max_files=LOCAL_UPLOAD_REQUEST_MAX_FILES if is_chunked_upload else local_total_max_files,
            max_file_bytes=BATCH_UPLOAD_MAX_FILE_BYTES,
            max_total_bytes=LOCAL_UPLOAD_REQUEST_MAX_TOTAL_BYTES if is_chunked_upload else BATCH_UPLOAD_MAX_TOTAL_BYTES,
        )
    except Exception:
        _release_batch_slot(owner_id, current_session_id)
        raise
    uploaded_originals = {image.filename: image.content for image in images}
    if previous_result_count + len(images) > expected_total_files:
        _release_batch_slot(owner_id, current_session_id)
        raise HTTPException(status_code=400, detail="上傳分批張數超過原本宣告的總張數")
    start_time = datetime.now()
    if is_new_session:
        _batch_sessions[current_session_id] = {
            "session_id": current_session_id,
            "owner_id": owner_id,
            "batch_mode": "upload",
            "user_account": _get_batch_user_account(request, "upload"),
            "google_user_id": _get_google_user_id(request),
            "status": "processing",
            "stage": "photos",
            "start_time": start_time.isoformat(),
            "end_time": None,
            "results": [],
            "browser_results": [],
            "usage": _empty_usage_metrics(),
            "original_images": dict(uploaded_originals),
            "processing_info": {
                "file_count": expected_total_files,
                "concurrency": concurrency,
                "face_cluster_eps": face_cluster_eps,
                "face_cluster_min_samples": face_cluster_min_samples,
                "face_cluster_batch_size": BATCH_UPLOAD_BATCH_SIZE_LOCAL,
                "run_public_classification": run_public_classification,
                "public_classification_authorized": bool(run_public_classification),
                "run_face_clustering": run_face_clustering,
                "upload_chunk_total": upload_chunk_total,
                "upload_next_chunk_index": upload_chunk_index + 1,
            },
            "completed": False,
            "cancel_requested": False,
            "cancelled_at": None,
            "face_cluster_job_id": None,
            "face_cluster_cancel_requested": False,
        }
        await _persist_session_created(_batch_sessions[current_session_id])
    else:
        _batch_sessions[current_session_id].setdefault("original_images", {}).update(uploaded_originals)
        processing_info = _batch_sessions[current_session_id].setdefault("processing_info", {})
        processing_info["file_count"] = expected_total_files
        processing_info["upload_next_chunk_index"] = upload_chunk_index + 1
        await _persist_session_update(
            current_session_id,
            {
                "status": "processing",
                "stage": "photos",
                "processing_info": processing_info,
            },
        )

    async def event_generator():
        try:
            async for chunk in batch_process_uploads_stream(
                images,
                concurrency=concurrency,
                color_rules=color_rules,
                collaborative_memory=collaborative_memory,
                evaluate_public=run_public_classification,
            ):
                chunk_index = previous_result_count + int(chunk.get("index") or 0)
                chunk = {
                    **chunk,
                    "index": chunk_index,
                    "total": expected_total_files,
                    "upload_chunk_index": upload_chunk_index,
                    "upload_chunk_total": upload_chunk_total,
                }
                if chunk.get("status") == "ok":
                    browser_chunk = await run_in_threadpool(
                        _externalize_result_previews,
                        chunk,
                        owner_id=owner_id,
                        session_id=current_session_id,
                    )
                    _log_result_telemetry(
                        "batch_upload_stream",
                        browser_chunk,
                        extra={
                            "session_id": current_session_id,
                            "index": chunk_index,
                            "total": expected_total_files,
                            "upload_chunk_index": upload_chunk_index,
                            "upload_chunk_total": upload_chunk_total,
                        },
                    )
                    _batch_sessions[current_session_id]["browser_results"].append(browser_chunk)
                    _batch_sessions[current_session_id]["results"].append(chunk)
                    _batch_sessions[current_session_id]["usage"] = _merge_usage_metrics(
                        _batch_sessions[current_session_id].get("usage"),
                        browser_chunk.get("usage"),
                    )
                    _log_preview_externalized(
                        "batch_upload_stream",
                        browser_chunk,
                        extra={
                            "session_id": current_session_id,
                            "index": chunk_index,
                            "total": expected_total_files,
                        },
                    )
                    await _persist_photo_result(current_session_id, owner_id, browser_chunk)
                else:
                    browser_chunk = chunk
                yield json.dumps({**browser_chunk, "session_id": current_session_id}, ensure_ascii=False) + "\n"

            if is_chunked_upload and not is_final_upload_chunk:
                await _persist_session_update(
                    current_session_id,
                    {
                        "status": "processing",
                        "stage": "photos",
                        "result_count": len(_batch_sessions[current_session_id]["results"]),
                        "usage": _batch_sessions[current_session_id].get("usage", _empty_usage_metrics()),
                    },
                )
                return

            _batch_sessions[current_session_id]["end_time"] = datetime.now().isoformat()
            _batch_sessions[current_session_id]["completed"] = True
            if _batch_sessions[current_session_id].get("cancel_requested"):
                await _persist_session_update(
                    current_session_id,
                    {
                        "status": "cancelled",
                        "completed_at": _batch_sessions[current_session_id]["end_time"],
                        "result_count": len(_batch_sessions[current_session_id]["results"]),
                        "usage": _batch_sessions[current_session_id].get("usage", _empty_usage_metrics()),
                    },
                )
                yield json.dumps(
                    {
                        "status": "cancelled",
                        "session_id": current_session_id,
                        "message": "已中止本次整理。",
                    },
                    ensure_ascii=False,
                ) + "\n"
                return
            if run_face_clustering and FACE_CLUSTERING_ENABLED:
                yield json.dumps(_face_cluster_starting_event(current_session_id), ensure_ascii=False) + "\n"
            face_task, face_progress_queue = _start_face_clustering_task(
                current_session_id,
                enabled=run_face_clustering,
            )
            while not face_task.done() or not face_progress_queue.empty():
                try:
                    progress_event = await asyncio.wait_for(face_progress_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                yield json.dumps(progress_event, ensure_ascii=False) + "\n"
            face_clustering = await face_task
            _batch_sessions[current_session_id]["results"] = _batch_sessions[current_session_id].pop(
                "browser_results", []
            )
            _log_firestore_usage_saved(
                "batch_upload_stream",
                owner_id=owner_id,
                session_id=current_session_id,
                usage=_batch_sessions[current_session_id].get("usage", _empty_usage_metrics()),
                extra={"status": "completed"},
            )
            await _persist_session_update(
                current_session_id,
                {
                    "status": "completed",
                    "completed_at": _batch_sessions[current_session_id]["end_time"],
                    "result_count": len(_batch_sessions[current_session_id]["results"]),
                    "usage": _batch_sessions[current_session_id].get("usage", _empty_usage_metrics()),
                    "face_clustering": face_clustering,
                },
            )
            completion_notification = await _notify_completed_batch_session(current_session_id, request)
            yield json.dumps(
                {
                    "status": "completed",
                    "session_id": current_session_id,
                    "message": f"批次處理完成，共 {len(_batch_sessions[current_session_id]['results'])} 張圖片",
                    "face_clustering": face_clustering,
                    "face_clusters": _batch_sessions[current_session_id]["face_clusters"],
                    "completion_notification": completion_notification,
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
                    "usage": _batch_sessions[current_session_id].get("usage", _empty_usage_metrics()),
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
    await _require_public_classification_if_requested(request, req.run_public_classification)
    input_path = Path(req.input_folder)
    if not input_path.exists() or not input_path.is_dir():
        raise HTTPException(status_code=400, detail=f"資料夾不存在：{req.input_folder}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_folder = str(input_path / f"review_temp_{ts}")
    if req.run_face_clustering:
        face_cluster_eps, face_cluster_min_samples = _read_face_cluster_params(
            req.face_cluster_eps,
            req.face_cluster_min_samples,
            max_files=BATCH_UPLOAD_BATCH_SIZE,
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
        "user_account": _get_batch_user_account(request, "local"),
        "google_user_id": _get_google_user_id(request),
        "start_time": start_time.isoformat(),
        "end_time": None,
        "results": [],
        "processing_info": {
            "input_folder": req.input_folder,
            "concurrency": req.concurrency,
            "face_cluster_eps": face_cluster_eps,
            "face_cluster_min_samples": face_cluster_min_samples,
            "face_cluster_batch_size": BATCH_UPLOAD_BATCH_SIZE,
            "run_public_classification": req.run_public_classification,
            "public_classification_authorized": bool(req.run_public_classification),
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
        completion_notification = await _notify_completed_batch_session(session_id, request)

        return {
            "session_id": session_id,
            "total": len(results),
            "success": len(ok),
            "failed": len(err),
            "temp_folder": temp_folder,
            "results": results,
            "face_clustering": face_clustering,
            "face_clusters": _batch_sessions[session_id]["face_clusters"],
            "completion_notification": completion_notification,
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
    folder_id: Optional[str] = None
    drive_files: Optional[list[dict[str, Any]]] = None
    target_folder_id: Optional[str] = None
    concurrency: int = 3
    color_rules: Optional[list] = None
    session_id: Optional[str] = None
    collaborative_memory: Optional[str] = None
    face_cluster_eps: float = DEFAULT_CLUSTER_EPS
    face_cluster_min_samples: int = DEFAULT_CLUSTER_MIN_SAMPLES
    run_public_classification: bool = False
    run_face_clustering: bool = True


async def _load_drive_collaborative_memory(req: DriveBatchRequest, creds) -> str | None:
    collaborative_memory = req.collaborative_memory
    if collaborative_memory or not req.run_public_classification:
        return collaborative_memory
    if not req.folder_id:
        return collaborative_memory
    try:
        from googleapiclient.discovery import build
        drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
        q = f"name = '.photoidentifier_memory.md' and '{req.folder_id}' in parents and trashed = false"
        res = drive_service.files().list(q=q, fields="files(id)").execute()
        files = res.get("files", [])
        if not files:
            return None
        file_id = files[0]["id"]
        import httpx
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
        headers = {"Authorization": f"Bearer {creds.token}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return None
        content = resp.text
        return content[:1000] if len(content) > 1000 else content
    except Exception as exc:
        logger.warning("無法獲取協作記憶文件: %s", exc)
        return None


def _normalize_selected_drive_files(files: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in files or []:
        file_id = str(item.get("id") or item.get("file_id") or "").strip()
        name = str(item.get("name") or item.get("file_name") or file_id).strip()
        mime_type = str(item.get("mimeType") or item.get("mime_type") or "").strip()
        if not file_id or not mime_type.startswith("image/"):
            continue
        normalized.append(
            {
                "id": file_id,
                "name": name or file_id,
                "mimeType": mime_type,
                "size": item.get("size"),
                "thumbnailLink": item.get("thumbnailLink") or item.get("thumbnail_link") or "",
            }
        )
    return normalized


async def _resolve_drive_batch_files(req: DriveBatchRequest, creds) -> list[dict[str, Any]]:
    selected_files = _normalize_selected_drive_files(req.drive_files)
    if selected_files:
        return selected_files
    if not req.folder_id:
        raise HTTPException(status_code=400, detail="請選擇 Google 雲端資料夾或圖片")
    return await list_drive_image_files(req.folder_id, creds)


def _batch_status_payload(session: dict[str, Any]) -> dict[str, Any]:
    results = session.get("results") or []
    processing_info = session.get("processing_info") if isinstance(session.get("processing_info"), dict) else {}
    drive_files = processing_info.get("drive_files") if isinstance(processing_info.get("drive_files"), list) else []
    total = int(processing_info.get("file_count") or len(drive_files) or len(results))
    failed_count = sum(1 for item in results if item.get("status") == "error")
    success_count = sum(1 for item in results if item.get("status") == "ok")
    processed = success_count + failed_count
    status = session.get("status") or ("completed" if session.get("completed") else "processing")
    payload = {
        "status": status,
        "session_id": session.get("session_id"),
        "batch_mode": session.get("batch_mode"),
        "stage": session.get("stage") or processing_info.get("stage") or "processing",
        "message": session.get("message"),
        "results": results,
        "success": success_count,
        "failed": failed_count,
        "total": total,
        "progress": {
            "completed": processed,
            "total": total,
            "percent": round(processed / total * 100, 1) if total else 0,
        },
        "face_cluster_progress": session.get("face_cluster_progress"),
        "face_clustering": session.get("face_clustering"),
        "face_clusters": session.get("face_clusters", []),
        "blocked_files": session.get("blocked_files", []),
        "error_message": session.get("error_message"),
        "completion_notification": session.get("completion_notification"),
    }
    return payload


def _split_drive_files_by_upload_limit(drive_files: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for item in drive_files:
        raw_size = item.get("size")
        try:
            size = int(raw_size) if raw_size not in (None, "") else 0
        except (TypeError, ValueError):
            size = 0
        if size > BATCH_UPLOAD_MAX_FILE_BYTES:
            blocked.append(
                {
                    "file_name": item.get("name") or "未命名檔案",
                    "size": size,
                    "limit": BATCH_UPLOAD_MAX_FILE_BYTES,
                    "preview_url": item.get("thumbnailLink") or "",
                    "reason": "file_size_too_large",
                }
            )
            continue
        accepted.append(item)
    return accepted, blocked


async def _advance_drive_session(session: dict[str, Any], request: Request) -> None:
    if session.get("status") in {"completed", "failed", "cancelled"}:
        return
    session_id = str(session["session_id"])
    owner_id = str(session["owner_id"])
    if session.get("cancel_requested"):
        session["status"] = "cancelled"
        session["stage"] = "cancelled"
        session["completed"] = True
        session["end_time"] = datetime.now().isoformat()
        await _persist_session_update(
            session_id,
            {
                "status": "cancelled",
                "completed_at": session["end_time"],
                "result_count": len(session.get("results") or []),
            },
        )
        _release_batch_slot(owner_id, session_id)
        return

    processing_info = session.get("processing_info") if isinstance(session.get("processing_info"), dict) else {}
    drive_files = processing_info.get("drive_files") if isinstance(processing_info.get("drive_files"), list) else []
    next_index = int(processing_info.get("drive_next_index") or 0)
    concurrency = max(1, min(int(processing_info.get("concurrency") or 1), CLOUD_API_CONCURRENCY_CAP))

    if next_index < len(drive_files):
        session["status"] = "processing"
        session["stage"] = "photos"
        if processing_info.get("run_public_classification") and not processing_info.get("public_classification_authorized"):
            raise HTTPException(status_code=403, detail=PUBLIC_CLASSIFICATION_DENIED_DETAIL)
        creds = get_drive_credentials(request)
        batch_items = drive_files[next_index: next_index + concurrency]
        tasks = [
            process_drive_file_item(
                item,
                index=next_index + offset,
                total=len(drive_files),
                credentials=creds,
                color_rules=processing_info.get("color_rules"),
                collaborative_memory=processing_info.get("collaborative_memory"),
                evaluate_public=bool(processing_info.get("run_public_classification")),
            )
            for offset, item in enumerate(batch_items)
        ]
        for result in await asyncio.gather(*tasks):
            session.setdefault("results", []).append(result)
            if result.get("status") == "ok":
                await _persist_photo_result(session_id, owner_id, result)
        processing_info["drive_next_index"] = next_index + len(batch_items)
        processing_info["stage"] = "photos"
        await _persist_session_update(
            session_id,
            {
                "status": "processing",
                "stage": "photos",
                "result_count": len(session.get("results") or []),
                "processing_info": processing_info,
            },
        )
        return

    if not bool(processing_info.get("run_face_clustering")):
        face_clustering = _skip_session_face_clustering(session_id)
        session["status"] = "completed"
        session["stage"] = "completed"
        session["completed"] = True
        session["end_time"] = datetime.now().isoformat()
        await _persist_session_update(
            session_id,
            {
                "status": "completed",
                "completed_at": session["end_time"],
                "result_count": len(session.get("results") or []),
                "face_clustering": face_clustering,
            },
        )
        await _notify_completed_batch_session(session_id, request)
        _release_batch_slot(owner_id, session_id)
        return

    if not FACE_CLUSTERING_ENABLED:
        face_clustering = _skip_session_face_clustering(session_id)
        face_clustering["reason"] = "disabled"
        session["status"] = "completed"
        session["stage"] = "completed"
        session["completed"] = True
        session["end_time"] = datetime.now().isoformat()
        await _persist_session_update(
            session_id,
            {
                "status": "completed",
                "completed_at": session["end_time"],
                "result_count": len(session.get("results") or []),
                "face_clustering": face_clustering,
            },
        )
        await _notify_completed_batch_session(session_id, request)
        _release_batch_slot(owner_id, session_id)
        return

    if not session.get("face_cluster_job_id"):
        images, _source_by_name = prepare_cluster_images(session.get("results") or [])
        if not images:
            session["face_clusters"] = []
            session["face_clustering"] = {"available": True, "cluster_count": 0}
            session["status"] = "completed"
            session["stage"] = "completed"
            session["completed"] = True
            session["end_time"] = datetime.now().isoformat()
            await _persist_session_update(
                session_id,
                {
                    "status": "completed",
                    "completed_at": session["end_time"],
                    "result_count": len(session.get("results") or []),
                    "face_clustering": session["face_clustering"],
                },
            )
            await _notify_completed_batch_session(session_id, request)
            _release_batch_slot(owner_id, session_id)
            return
        session["stage"] = "face_uploading"
        job = await create_cluster_job_from_results(
            session.get("results") or [],
            eps=float(processing_info.get("face_cluster_eps", DEFAULT_CLUSTER_EPS)),
            min_samples=int(processing_info.get("face_cluster_min_samples", DEFAULT_CLUSTER_MIN_SAMPLES)),
            start_index=0,
            batch_size=len(images),
            session_id=session_id,
        )
        session["face_cluster_job_id"] = job.get("job_id")
        processing_info["face_cluster_current_start_index"] = 0
        processing_info["face_cluster_current_batch_size"] = len(images)
        session["face_cluster_progress"] = _face_cluster_progress_event(session_id, job)
        await _persist_session_update(
            session_id,
            {
                "status": "processing",
                "stage": "face_clustering",
                "face_cluster_job_id": session["face_cluster_job_id"],
                "face_cluster_progress": session["face_cluster_progress"],
                "processing_info": processing_info,
            },
        )
        return

    job_id = str(session["face_cluster_job_id"])
    try:
        snapshot = await _get_cluster_job_snapshot_cached(job_id)
    except Exception as exc:
        if not _is_missing_cluster_job_error(exc):
            raise
        logger.warning(
            "Face cluster job missing session=%s job_id=%s reason=remote_job_missing_after_restart_or_expiry",
            session_id,
            job_id,
        )
        _clear_cluster_job_snapshot_cache(job_id)
        session["face_cluster_job_id"] = None
        session["status"] = "failed"
        session["stage"] = "failed"
        session["error_message"] = "辨識工作已失效，可能因辨識服務重啟或工作逾期被清除，請重新執行這次整理。"
        session["completed"] = True
        session["end_time"] = datetime.now().isoformat()
        await _persist_session_update(
            session_id,
            {
                "status": "failed",
                "completed_at": session["end_time"],
                "error_message": session["error_message"],
                "face_cluster_job_id": None,
                "face_cluster_progress": session.get("face_cluster_progress"),
            },
        )
        _release_batch_slot(owner_id, session_id)
        return
    images, source_by_name = prepare_cluster_images(session.get("results") or [])
    current_start_index = 0
    current_batch_size = len(images)
    snapshot = {
        **snapshot,
        "batch_start_index": current_start_index,
        "total": len(images),
    }
    session["face_cluster_progress"] = _face_cluster_progress_event(session_id, snapshot)
    session["stage"] = "face_clustering"
    status = snapshot.get("status")
    if status in {"queued", "running"}:
        await _persist_session_update(
            session_id,
            {
                "status": "processing",
                "stage": "face_clustering",
                "face_cluster_progress": session["face_cluster_progress"],
            },
        )
        return
    if status == "success":
        result = snapshot.get("result") if isinstance(snapshot.get("result"), dict) else {}
        _clear_cluster_job_snapshot_cache(job_id)
        session["face_cluster_job_id"] = None
        clusters = build_clusters_from_response(result, source_by_name)
        session["face_clusters"] = clusters
        session["face_clustering"] = {
            "available": True,
            "cluster_count": len(clusters),
            "eps": float(processing_info.get("face_cluster_eps", DEFAULT_CLUSTER_EPS)),
            "min_samples": int(processing_info.get("face_cluster_min_samples", DEFAULT_CLUSTER_MIN_SAMPLES)),
        }
        if batch_state_store.enabled:
            await batch_state_store.save_face_clusters(
                session_id,
                owner_id,
                clusters,
                str(session.get("google_user_id") or ""),
            )
        session["status"] = "completed"
        session["stage"] = "completed"
        session["completed"] = True
        session["end_time"] = datetime.now().isoformat()
        await _persist_session_update(
            session_id,
            {
                "status": "completed",
                "completed_at": session["end_time"],
                "result_count": len(session.get("results") or []),
                "face_clustering": session["face_clustering"],
                "face_cluster_progress": session["face_cluster_progress"],
            },
        )
        await _notify_completed_batch_session(session_id, request)
        _release_batch_slot(owner_id, session_id)
        return
    if status == "cancelled":
        _clear_cluster_job_snapshot_cache(job_id)
        session["status"] = "cancelled"
        session["stage"] = "cancelled"
        session["completed"] = True
        session["end_time"] = datetime.now().isoformat()
        await _persist_session_update(session_id, {"status": "cancelled", "completed_at": session["end_time"]})
        _release_batch_slot(owner_id, session_id)
        return

    _clear_cluster_job_snapshot_cache(job_id)
    session["status"] = "failed"
    session["stage"] = "failed"
    session["error_message"] = snapshot.get("error_message") or "人臉分群沒有完成"
    session["completed"] = True
    session["end_time"] = datetime.now().isoformat()
    await _persist_session_update(
        session_id,
        {
            "status": "failed",
            "completed_at": session["end_time"],
            "error_message": session["error_message"],
            "face_cluster_progress": session.get("face_cluster_progress"),
        },
    )
    _release_batch_slot(owner_id, session_id)


async def _sync_face_cluster_terminal_status(session: dict[str, Any]) -> bool:
    if session.get("completed"):
        return False
    job_id = str(session.get("face_cluster_job_id") or "").strip()
    if not job_id:
        return False

    status = str(session.get("status") or "")
    stage = str(session.get("stage") or "")
    if status not in {"processing", "queued"} and stage != "face_clustering":
        return False

    try:
        snapshot = await _get_cluster_job_snapshot_cached(job_id)
    except Exception as exc:
        if not _is_missing_cluster_job_error(exc):
            return False
        logger.warning(
            "Face cluster job missing during status sync session=%s job_id=%s reason=remote_job_missing_after_restart_or_expiry",
            session.get("session_id"),
            job_id,
        )
        _clear_cluster_job_snapshot_cache(job_id)
        session["face_cluster_job_id"] = None
        session["status"] = "failed"
        session["stage"] = "failed"
        session["error_message"] = "辨識工作已失效，可能因辨識服務重啟或工作逾期被清除，請重新執行這次整理。"
        session["completed"] = True
        session["end_time"] = datetime.now().isoformat()
        await _persist_session_update(
            str(session["session_id"]),
            {
                "status": "failed",
                "completed_at": session["end_time"],
                "error_message": session["error_message"],
                "face_cluster_job_id": None,
                "face_cluster_progress": session.get("face_cluster_progress"),
            },
        )
        _release_batch_slot(str(session.get("owner_id")), str(session["session_id"]))
        return True

    snapshot_status = str(snapshot.get("status") or "")
    if snapshot_status in {"queued", "running"}:
        return False

    session_id = str(session["session_id"])
    owner_id = str(session.get("owner_id") or "")
    session["face_cluster_progress"] = _face_cluster_progress_event(session_id, snapshot)

    if snapshot_status == "success":
        images, source_by_name = prepare_cluster_images(session.get("results") or [])
        result = snapshot.get("result") if isinstance(snapshot.get("result"), dict) else {}
        _clear_cluster_job_snapshot_cache(job_id)
        session["face_cluster_job_id"] = None
        clusters = build_clusters_from_response(result, source_by_name)
        session["face_clusters"] = clusters
        processing_info = session.get("processing_info") if isinstance(session.get("processing_info"), dict) else {}
        session["face_clustering"] = {
            "available": True,
            "cluster_count": len(clusters),
            "eps": float(processing_info.get("face_cluster_eps", DEFAULT_CLUSTER_EPS)),
            "min_samples": int(processing_info.get("face_cluster_min_samples", DEFAULT_CLUSTER_MIN_SAMPLES)),
        }
        session["status"] = "completed"
        session["stage"] = "completed"
        session["completed"] = True
        session["end_time"] = datetime.now().isoformat()
        await _persist_session_update(
            session_id,
            {
                "status": "completed",
                "completed_at": session["end_time"],
                "result_count": len(session.get("results") or []),
                "face_clustering": session["face_clustering"],
                "face_cluster_progress": session["face_cluster_progress"],
                "face_cluster_job_id": None,
            },
        )
        if batch_state_store.enabled:
            try:
                await batch_state_store.save_face_clusters(
                    session_id,
                    owner_id,
                    clusters,
                    str(session.get("google_user_id") or ""),
                )
            except Exception:
                logger.exception("Failed to persist synced face clusters session=%s", session_id)
        _release_batch_slot(owner_id, session_id)
        return True

    if snapshot_status == "cancelled":
        _clear_cluster_job_snapshot_cache(job_id)
        session["status"] = "cancelled"
        session["stage"] = "cancelled"
        session["completed"] = True
        session["end_time"] = datetime.now().isoformat()
        await _persist_session_update(
            session_id,
            {
                "status": "cancelled",
                "completed_at": session["end_time"],
                "face_cluster_progress": session.get("face_cluster_progress"),
            },
        )
        _release_batch_slot(owner_id, session_id)
        return True

    _clear_cluster_job_snapshot_cache(job_id)
    session["status"] = "failed"
    session["stage"] = "failed"
    session["error_message"] = str(snapshot.get("error_message") or "人臉分群沒有完成")
    session["completed"] = True
    session["end_time"] = datetime.now().isoformat()
    await _persist_session_update(
        session_id,
        {
            "status": "failed",
            "completed_at": session["end_time"],
            "error_message": session["error_message"],
            "face_cluster_progress": session.get("face_cluster_progress"),
        },
    )
    _release_batch_slot(owner_id, session_id)
    return True


@app.post("/batch_drive_start/")
async def batch_visualize_drive_start(req: DriveBatchRequest, request: Request):
    _enforce_request_rate_limit(request, "batch-drive-start", limit=6)
    _validate_processing_scope(req.run_public_classification, req.run_face_clustering)
    _validate_cloud_api_concurrency(req.concurrency)
    user_key = request.session.get("user_key")
    if not user_key:
        raise HTTPException(status_code=401, detail="尚未登入 Google 帳號")
    await _require_feature(request, "drive_batch", "此帳號尚未開放 Google 雲端批次功能")
    await _require_public_classification_if_requested(request, req.run_public_classification)

    creds = get_drive_credentials(request)
    if req.run_face_clustering:
        face_cluster_eps, face_cluster_min_samples = _read_face_cluster_params(
            req.face_cluster_eps,
            req.face_cluster_min_samples,
            max_files=BATCH_UPLOAD_BATCH_SIZE_CLOUD,
        )
    else:
        face_cluster_eps, face_cluster_min_samples = DEFAULT_CLUSTER_EPS, DEFAULT_CLUSTER_MIN_SAMPLES
    drive_files = await _resolve_drive_batch_files(req, creds)
    accepted_drive_files, blocked_drive_files = _split_drive_files_by_upload_limit(drive_files)
    _validate_batch_file_count(len(accepted_drive_files), mode="cloud")
    session_id = req.session_id or str(uuid.uuid4())
    owner_id = _acquire_batch_slot(request, session_id)
    start_time = datetime.now()
    collaborative_memory = await _load_drive_collaborative_memory(req, creds)
    _batch_sessions[session_id] = {
        "session_id": session_id,
        "owner_id": owner_id,
        "batch_mode": "drive",
        "user_account": _get_batch_user_account(request, "drive"),
        "google_user_id": _get_google_user_id(request),
        "status": "processing",
        "stage": "queued",
        "start_time": start_time.isoformat(),
        "end_time": None,
        "results": [],
        "face_clusters": [],
        "processing_info": {
            "folder_id": req.folder_id,
            "target_folder_id": req.target_folder_id,
            "file_count": len(accepted_drive_files),
            "drive_files": accepted_drive_files,
            "drive_next_index": 0,
            "concurrency": req.concurrency,
            "color_rules": req.color_rules,
            "collaborative_memory": collaborative_memory,
            "face_cluster_eps": face_cluster_eps,
            "face_cluster_min_samples": face_cluster_min_samples,
            "face_cluster_batch_size": BATCH_UPLOAD_BATCH_SIZE_CLOUD,
            "run_public_classification": req.run_public_classification,
            "public_classification_authorized": bool(req.run_public_classification),
            "run_face_clustering": req.run_face_clustering,
        },
        "completed": False,
        "blocked_files": blocked_drive_files,
        "cancel_requested": False,
        "cancelled_at": None,
        "face_cluster_job_id": None,
        "face_cluster_cancel_requested": False,
    }
    await _persist_session_created(_batch_sessions[session_id])
    return _batch_status_payload(_batch_sessions[session_id])


@app.get("/batch_sessions/{session_id}/status")
async def get_batch_session_status(session_id: str, request: Request):
    _enforce_request_rate_limit(request, "batch-status", limit=90)
    session = await _owned_batch_session_async(request, session_id)
    async with _batch_session_lock(session_id):
        session = await _owned_batch_session_async(request, session_id)
        if session.get("batch_mode") == "drive":
            try:
                await _advance_drive_session(session, request)
            except Exception as exc:
                logger.exception("Drive batch status tick failed session=%s", session_id)
                session["status"] = "failed"
                session["stage"] = "failed"
                session["error_message"] = str(exc) or "雲端批次處理中斷"
                session["completed"] = True
                session["end_time"] = datetime.now().isoformat()
                await _persist_session_update(
                    session_id,
                    {
                        "status": "failed",
                        "completed_at": session["end_time"],
                        "error_message": session["error_message"],
                    },
                )
                _release_batch_slot(str(session.get("owner_id")), session_id)
        await _sync_face_cluster_terminal_status(session)
    return _batch_status_payload(session)

@app.post("/batch_drive/")
async def batch_visualize_drive(req: DriveBatchRequest, request: Request):
    """雲端硬碟批量處理入口 (舊 - 一次性回傳)"""
    _validate_processing_scope(req.run_public_classification, req.run_face_clustering)
    _validate_cloud_api_concurrency(req.concurrency)
    user_key = request.session.get("user_key")
    if not user_key:
        raise HTTPException(status_code=401, detail="尚未登入 Google 帳號")
    await _require_feature(request, "drive_batch", "此帳號尚未開放 Google 雲端批次功能")
    await _require_public_classification_if_requested(request, req.run_public_classification)
    
    try:
        creds = get_drive_credentials(request)
        drive_files = await _resolve_drive_batch_files(req, creds)
        accepted_drive_files, blocked_drive_files = _split_drive_files_by_upload_limit(drive_files)
        _validate_batch_file_count(len(accepted_drive_files), mode="cloud")
        if req.run_face_clustering:
            face_cluster_eps, face_cluster_min_samples = _read_face_cluster_params(
                req.face_cluster_eps,
                req.face_cluster_min_samples,
                max_files=BATCH_UPLOAD_BATCH_SIZE_CLOUD,
            )
        else:
            face_cluster_eps, face_cluster_min_samples = DEFAULT_CLUSTER_EPS, DEFAULT_CLUSTER_MIN_SAMPLES
        results = await batch_process_drive(
            folder_id=req.folder_id,
            credentials=creds,
            target_folder_id=req.target_folder_id,
            concurrency=req.concurrency,
            evaluate_public=req.run_public_classification,
            drive_files=accepted_drive_files,
        )
        
        success_count = sum(1 for r in results if r.get("status") == "ok")
        failed_count = len(results) - success_count
        if req.run_face_clustering and FACE_CLUSTERING_ENABLED:
            face_clusters = await cluster_batch_results(
                results,
                eps=face_cluster_eps,
                min_samples=face_cluster_min_samples,
                batch_size=BATCH_UPLOAD_BATCH_SIZE_CLOUD,
                session_id=session_id,
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
            "blocked_files": blocked_drive_files,
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
    _validate_cloud_api_concurrency(req.concurrency)
    user_key = request.session.get("user_key")
    if not user_key:
        raise HTTPException(status_code=401, detail="尚未登入 Google 帳號")
    await _require_feature(request, "drive_batch", "此帳號尚未開放 Google 雲端批次功能")
    await _require_public_classification_if_requested(request, req.run_public_classification)

    try:
        creds = get_drive_credentials(request)
        drive_files = await _resolve_drive_batch_files(req, creds)
        accepted_drive_files, blocked_drive_files = _split_drive_files_by_upload_limit(drive_files)
        _validate_batch_file_count(len(accepted_drive_files), mode="cloud")
        if req.run_face_clustering:
            face_cluster_eps, face_cluster_min_samples = _read_face_cluster_params(
                req.face_cluster_eps,
                req.face_cluster_min_samples,
                max_files=BATCH_UPLOAD_BATCH_SIZE_CLOUD,
            )
        else:
            face_cluster_eps, face_cluster_min_samples = DEFAULT_CLUSTER_EPS, DEFAULT_CLUSTER_MIN_SAMPLES

        # 1. 獲取協作記憶：優先使用請求提供的，再從遠端讀取
        collaborative_memory = req.collaborative_memory

        if not collaborative_memory and req.run_public_classification and req.folder_id:
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
            "user_account": _get_batch_user_account(request, "drive"),
            "google_user_id": _get_google_user_id(request),
            "start_time": start_time.isoformat(),
            "end_time": None,
            "results": [],
            "browser_results": [],
            "usage": _empty_usage_metrics(),
            "processing_info": {
                "folder_id": req.folder_id,
                "concurrency": req.concurrency,
                "face_cluster_eps": face_cluster_eps,
                "face_cluster_min_samples": face_cluster_min_samples,
                "run_public_classification": req.run_public_classification,
                "public_classification_authorized": bool(req.run_public_classification),
                "run_face_clustering": req.run_face_clustering,
            },
            "completed": False,
            "blocked_files": blocked_drive_files,
            "cancel_requested": False,
            "cancelled_at": None,
            "face_cluster_job_id": None,
            "face_cluster_cancel_requested": False,
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
                    drive_files=accepted_drive_files,
                ):
                    # 儲存結果到 session
                    if chunk.get("status") == "ok":
                        browser_chunk = await run_in_threadpool(
                            _externalize_result_previews,
                            chunk,
                            owner_id=owner_id,
                            session_id=session_id,
                        )
                        _log_result_telemetry(
                            "batch_drive_stream",
                            browser_chunk,
                            extra={
                                "session_id": session_id,
                                "folder_id": req.folder_id,
                                "target_folder_id": req.target_folder_id or "",
                            },
                        )
                        _batch_sessions[session_id]["browser_results"].append(browser_chunk)
                        _batch_sessions[session_id]["results"].append(chunk)
                        _batch_sessions[session_id]["usage"] = _merge_usage_metrics(
                            _batch_sessions[session_id].get("usage"),
                            browser_chunk.get("usage"),
                        )
                        _log_preview_externalized(
                            "batch_drive_stream",
                            browser_chunk,
                            extra={
                                "session_id": session_id,
                                "folder_id": req.folder_id,
                                "target_folder_id": req.target_folder_id or "",
                            },
                        )
                        await _persist_photo_result(session_id, owner_id, browser_chunk)
                    else:
                        browser_chunk = chunk

                    # 每一筆結果都轉成 JSON 並加上換行符號推播出去
                    chunk_with_session = {**browser_chunk, "session_id": session_id}
                    yield json.dumps(chunk_with_session, ensure_ascii=False) + "\n"

                # 標記完成
                _batch_sessions[session_id]["end_time"] = datetime.now().isoformat()
                _batch_sessions[session_id]["completed"] = True
                if _batch_sessions[session_id].get("cancel_requested"):
                    await _persist_session_update(
                        session_id,
                        {
                            "status": "cancelled",
                            "completed_at": _batch_sessions[session_id]["end_time"],
                            "result_count": len(_batch_sessions[session_id]["results"]),
                            "usage": _batch_sessions[session_id].get("usage", _empty_usage_metrics()),
                        },
                    )
                    yield json.dumps(
                        {
                            "status": "cancelled",
                            "session_id": session_id,
                            "message": "已中止本次整理。",
                        },
                        ensure_ascii=False,
                    ) + "\n"
                    return
                if req.run_face_clustering and FACE_CLUSTERING_ENABLED:
                    yield json.dumps(_face_cluster_starting_event(session_id), ensure_ascii=False) + "\n"
                face_task, face_progress_queue = _start_face_clustering_task(
                    session_id,
                    enabled=req.run_face_clustering,
                )
                while not face_task.done() or not face_progress_queue.empty():
                    try:
                        progress_event = await asyncio.wait_for(face_progress_queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    yield json.dumps(progress_event, ensure_ascii=False) + "\n"
                face_clustering = await face_task
                _batch_sessions[session_id]["results"] = _batch_sessions[session_id].pop(
                    "browser_results", []
                )
                _log_firestore_usage_saved(
                    "batch_drive_stream",
                    owner_id=owner_id,
                    session_id=session_id,
                    usage=_batch_sessions[session_id].get("usage", _empty_usage_metrics()),
                    extra={"status": "completed"},
                )
                await _persist_session_update(
                    session_id,
                    {
                        "status": "completed",
                        "completed_at": _batch_sessions[session_id]["end_time"],
                        "result_count": len(_batch_sessions[session_id]["results"]),
                        "usage": _batch_sessions[session_id].get("usage", _empty_usage_metrics()),
                        "face_clustering": face_clustering,
                    },
                )
                completion_notification = await _notify_completed_batch_session(session_id, request)
                yield json.dumps({
                    "status": "completed",
                    "session_id": session_id,
                    "message": f"批次處理完成，共 {len(_batch_sessions[session_id]['results'])} 個結果",
                    "face_clustering": face_clustering,
                    "face_clusters": _batch_sessions[session_id]["face_clusters"],
                    "completion_notification": completion_notification,
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
def google_auth(request: Request, next: str | None = None):
    try:
        redirect_target = _oauth_request_host_redirect(request)
        if redirect_target:
            return RedirectResponse(url=redirect_target, status_code=307)
        user_key = request.session.get("user_key")
        if not user_key:
            user_key = uuid.uuid4().hex
            request.session["user_key"] = user_key

        auth_url, state, code_verifier = get_auth_url()
        request.session["oauth_state"] = state
        request.session["oauth_user_key"] = user_key
        request.session["oauth_next"] = _normalize_post_auth_redirect(next)
        if code_verifier:
            request.session["oauth_code_verifier"] = code_verifier
        
        return RedirectResponse(url=auth_url)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Auth URL Error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/auth/callback")
def google_auth_callback(request: Request, code: str, state: str):
    try:
        expected_state = request.session.get("oauth_state")
        if not expected_state or state != expected_state:
            raise HTTPException(
                status_code=400,
                detail="Google 登入驗證失效，通常是因為混用了 localhost、127.0.0.1 或 0.0.0.0，請固定用同一個網址重新登入。",
            )
        
        user_key = request.session.get("oauth_user_key")
        if not user_key:
            raise HTTPException(status_code=401, detail="Missing session user_key")
            
        code_verifier = request.session.get("oauth_code_verifier")
        
        creds = exchange_code_for_token(code=code, user_key=user_key, state=state, code_verifier=code_verifier)

        # 同步備份到 session，供 Vercel /tmp 失效時使用
        _save_creds_to_session(request, creds)
        userinfo = _get_google_userinfo(request)
        if isinstance(userinfo, dict):
            _sync_logged_in_user_sync(request, userinfo)

        request.session.pop("oauth_state", None)
        request.session.pop("oauth_code_verifier", None)
        redirect_target = _normalize_post_auth_redirect(request.session.pop("oauth_next", None))

        # 授權成功後，導向回原本頁面或首頁
        return RedirectResponse(url=redirect_target)
    except HTTPException:
        raise
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


class BatchCancelRequest(BaseModel):
    job_id: Optional[str] = None


class DriveBatchExportRequest(BaseModel):
    session_id: str
    target_folder_id: str
    document: dict[str, Any]


class StorageBatchExportRequest(BaseModel):
    session_id: str
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
    await _require_feature(request, "export_results", "此帳號尚未開放匯出辨識結果功能")
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
    await _require_feature(request, "export_results", "此帳號尚未開放匯出辨識結果功能")
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


def _require_exports_bucket_name() -> str:
    bucket_name = PHOTOIDENTIFIER_EXPORTS_BUCKET.strip().removeprefix("gs://")
    if not bucket_name:
        raise HTTPException(status_code=503, detail="尚未設定暫存匯出 bucket")
    return bucket_name


def _create_storage_client():
    from google.cloud import storage

    service_account_json = get_backend_service_account_json()
    if service_account_json:
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_info(
            json.loads(service_account_json)
        )
        return storage.Client(project=credentials.project_id, credentials=credentials)
    project_id = os.getenv("FIRESTORE_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    return storage.Client(project=project_id or None)


def _safe_storage_path_segment(value: str, fallback: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._")
    return segment[:120] or fallback


def _upload_storage_preview(
    bucket_name: str,
    object_name: str,
    content: bytes,
    content_type: str,
) -> str:
    client = _create_storage_client()
    blob = client.bucket(bucket_name).blob(object_name)
    blob.upload_from_string(content, content_type=content_type)
    _log_gcs_object_written(
        "preview_externalize",
        bucket_name=bucket_name,
        object_name=object_name,
        byte_count=len(content),
        extra={"content_type": content_type},
    )
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=PREVIEW_SIGNED_URL_TTL_MINUTES)
    return str(
        blob.generate_signed_url(
            version="v4",
            expiration=expires_at,
            method="GET",
            response_disposition="inline",
            response_type=content_type,
        )
    )


def _externalize_result_previews(
    result: dict[str, Any],
    *,
    owner_id: str,
    session_id: str,
    upload_preview: Callable[[str, str, bytes, str], str] = _upload_storage_preview,
) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("status") != "ok":
        return result

    preview_fields = (
        ("original_image_b64", "original_preview_url", "original"),
        ("drawn_image_b64", "annotated_preview_url", "annotated"),
    )
    available = [(source, target, suffix, str(result.get(source) or "").strip()) for source, target, suffix in preview_fields]
    available = [entry for entry in available if entry[3]]
    if not available:
        return result

    safe_owner = _safe_storage_path_segment(owner_id, "anonymous")
    safe_session = _safe_storage_path_segment(session_id, "session")
    raw_name = str(result.get("file_name") or result.get("file") or "image.jpg")
    safe_stem = _safe_storage_path_segment(Path(raw_name).stem, "image")
    identity = result.get("drive_id") or result.get("index")
    if identity:
        safe_stem = f"{safe_stem}_{_safe_storage_path_segment(str(identity), 'item')}"
    next_result = dict(result)
    uploaded_urls: dict[str, str] = {}
    try:
        bucket_name = _require_exports_bucket_name()
        for source_field, target_field, suffix, encoded in available:
            image_bytes = base64.b64decode(encoded, validate=True)
            object_name = f"previews/{safe_owner}/{safe_session}/{safe_stem}_{suffix}.jpg"
            uploaded_urls[target_field] = upload_preview(
                bucket_name,
                object_name,
                image_bytes,
                "image/jpeg",
            )
    except Exception as exc:
        logger.warning(
            "Preview externalization failed session=%s file=%s error=%s; retaining embedded previews",
            session_id,
            raw_name,
            exc,
        )
        return result

    for source_field, target_field, _suffix, _encoded in available:
        next_result.pop(source_field, None)
        next_result[target_field] = uploaded_urls[target_field]
    next_result["usage"] = _merge_usage_metrics(
        next_result.get("usage"),
        {
            "preview_bytes_uploaded": sum(len(base64.b64decode(encoded, validate=True)) for _source, _target, _suffix, encoded in available),
            "preview_object_count": len(available),
        },
    )
    return next_result


def _safe_zip_path_segment(value: str, fallback: str) -> str:
    segment = str(value or fallback or "").strip()
    segment = re.sub(r'[\\/:*?"<>|]+', "_", segment)
    segment = re.sub(r"\s+", " ", segment).strip(". ")
    return segment[:80] or fallback


def _storage_export_folder_segments(folder: dict[str, Any], fallback: str) -> list[str]:
    raw_segments = folder.get("path_segments")
    segments = raw_segments if isinstance(raw_segments, list) and raw_segments else [folder.get("name") or fallback]
    return [
        _safe_zip_path_segment(str(segment or ""), fallback if index == 0 else f"group_{index}")
        for index, segment in enumerate(segments)
        if str(segment or "").strip()
    ] or [fallback]


def _storage_export_file_name(value: str, fallback: str) -> str:
    name = _safe_zip_path_segment(value, fallback)
    return name or fallback


def _storage_export_image_bytes_from_result(result: dict[str, Any] | None) -> bytes | None:
    if not isinstance(result, dict):
        return None
    image_b64 = str(result.get("original_image_b64") or "").strip()
    if not image_b64:
        return None
    try:
        return base64.b64decode(image_b64, validate=True)
    except Exception:
        return None


def _download_drive_file_bytes(credentials, file_id: str) -> bytes | None:
    if credentials is None or not file_id:
        return None
    from googleapiclient.discovery import build

    service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    content = service.files().get_media(fileId=file_id).execute()
    return bytes(content) if content else None


def _build_storage_export_images(
    document: dict[str, Any],
    session: dict[str, Any],
    credentials=None,
) -> tuple[dict[str, Any], list[tuple[str, bytes]]]:
    results = session.get("results") if isinstance(session.get("results"), list) else []
    original_images = session.get("original_images") if isinstance(session.get("original_images"), dict) else {}
    result_by_name: dict[str, dict[str, Any]] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        analysis = result.get("result") if isinstance(result.get("result"), dict) else result
        file_name = str(result.get("file_name") or result.get("file") or analysis.get("file_name") or analysis.get("file") or "")
        if file_name:
            result_by_name[file_name] = result

    next_document = json.loads(json.dumps(document, ensure_ascii=False))
    image_entries: list[tuple[str, bytes]] = []
    archive_by_file_name: dict[str, str] = {}
    used_paths: set[str] = set()
    folders = next_document.get("photo_angle_folders")
    folders = folders if isinstance(folders, list) else []

    for folder_index, folder in enumerate(folders, start=1):
        if not isinstance(folder, dict):
            continue
        folder_segments = _storage_export_folder_segments(folder, f"group_{folder_index}")
        photos = folder.get("photos") if isinstance(folder.get("photos"), list) else []
        for photo_index, photo in enumerate(photos, start=1):
            if not isinstance(photo, dict):
                continue
            file_name = str(photo.get("file_name") or f"image_{photo_index}.jpg")
            result = result_by_name.get(file_name)
            original_bytes = original_images.get(file_name)
            image_bytes = bytes(original_bytes) if isinstance(original_bytes, (bytes, bytearray)) else None
            image_source = "session.original_images"
            if image_bytes is None:
                image_bytes = _storage_export_image_bytes_from_result(result)
                if image_bytes is not None:
                    image_source = "result.original_image_b64"
            if image_bytes is None:
                drive_id = str(photo.get("drive_id") or (result or {}).get("drive_id") or "").strip()
                try:
                    image_bytes = _download_drive_file_bytes(credentials, drive_id)
                    if image_bytes is not None:
                        image_source = "drive_fallback"
                except Exception as exc:
                    logger.warning(
                        "Storage export image fetch failed session=%s file=%s drive_id=%s error=%s",
                        session.get("session_id"),
                        file_name,
                        drive_id,
                        exc,
                    )
                    image_bytes = None
            if not image_bytes:
                continue

            photo_name = _storage_export_file_name(file_name, f"image_{photo_index}.jpg")
            relative_path = f"{'/'.join(folder_segments)}/{photo_name}"
            if relative_path in used_paths:
                stem, dot, suffix = photo_name.rpartition(".")
                dedupe_name = f"{stem or photo_name}_{len(used_paths) + 1}{dot}{suffix}" if dot else f"{photo_name}_{len(used_paths) + 1}"
                relative_path = f"{'/'.join(folder_segments)}/{dedupe_name}"
            used_paths.add(relative_path)
            image_entries.append((relative_path, image_bytes))
            archive_by_file_name[file_name] = relative_path
            photo["archive_relative_path"] = relative_path
            _log_bytes_telemetry(
                "storage_export_image",
                file_name=file_name,
                byte_count=len(image_bytes),
                content_type="image/jpeg",
                extra={
                    "session_id": session.get("session_id"),
                    "image_source": image_source,
                    "relative_path": relative_path,
                },
            )

    for photo in (next_document.get("photos") if isinstance(next_document.get("photos"), list) else []):
        if isinstance(photo, dict) and photo.get("file_name") in archive_by_file_name:
            photo["archive_relative_path"] = archive_by_file_name[photo["file_name"]]
    for result in (next_document.get("results") if isinstance(next_document.get("results"), list) else []):
        if not isinstance(result, dict):
            continue
        file_name = result.get("file_name") or result.get("file")
        if file_name in archive_by_file_name:
            result["archive_relative_path"] = archive_by_file_name[file_name]

    return next_document, image_entries


def _build_storage_export_zip(
    document: dict[str, Any],
    session_id: str,
    image_entries: list[tuple[str, bytes]] | None = None,
) -> tuple[str, bytes]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_file_name = f"photo_people_{timestamp}.json"
    payload = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
    if len(payload) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="JSON 文件超過 10 MB 上限")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("result.json", payload)
        archive.writestr("workspace.json", payload)
        archive.writestr(json_file_name, payload)
        for relative_path, image_bytes in image_entries or []:
            archive.writestr(relative_path, image_bytes)
    zip_bytes = buffer.getvalue()
    logger.info(
        "storage_export_zip session=%s json_bytes=%s image_count=%s zip_bytes=%s",
        session_id,
        len(payload),
        len(image_entries or []),
        len(zip_bytes),
    )
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "storage_export_zip_detail session=%s image_entries=%s",
            session_id,
            [path for path, _ in (image_entries or [])],
        )
    return f"results_{session_id}_{timestamp}.zip", zip_bytes


def _storage_export_usage_metrics(
    *,
    zip_bytes: bytes | bytearray,
    image_entries: list[tuple[str, bytes]] | None = None,
) -> dict[str, int]:
    entries = image_entries or []
    return {
        "storage_export_bytes": len(zip_bytes or b""),
        "storage_export_image_bytes": sum(len(image_bytes) for _path, image_bytes in entries),
        "storage_export_image_count": len(entries),
        "storage_export_count": 1,
    }


def _upload_storage_export(bucket_name: str, object_name: str, content: bytes) -> None:
    client = _create_storage_client()
    blob = client.bucket(bucket_name).blob(object_name)
    blob.upload_from_string(content, content_type="application/zip")
    _log_gcs_object_written(
        "storage_export_zip",
        bucket_name=bucket_name,
        object_name=object_name,
        byte_count=len(content),
    )


def _generate_storage_signed_url(bucket_name: str, object_name: str, expires_minutes: int) -> tuple[str, str]:
    client = _create_storage_client()
    blob = client.bucket(bucket_name).blob(object_name)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    url = blob.generate_signed_url(
        version="v4",
        expiration=expires_at,
        method="GET",
        response_disposition=f'attachment; filename="{Path(object_name).name}"',
    )
    return str(url), expires_at.isoformat()


def _send_gmail_notification(credentials, recipient: str, subject: str, body: str) -> dict[str, Any]:
    from googleapiclient.discovery import build

    message = email.message.EmailMessage()
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()


def _storage_export_notification_body(download_entry_url: str, expires_minutes: int) -> str:
    return (
        "PhotoIdentifier 的辨識結果已整理完成。\n\n"
        f"請用原本的 Google 帳號開啟下載入口：\n{download_entry_url}\n\n"
        f"系統會在登入驗證後產生短效下載連結，連結約 {expires_minutes} 分鐘後失效。\n"
        "暫存檔會依 Cloud Storage lifecycle 約 1 天後自動刪除。"
    )


def _credential_scope_list(credentials) -> list[str]:
    scopes = getattr(credentials, "scopes", None)
    if not scopes:
        return []
    if isinstance(scopes, (list, tuple, set)):
        return [str(scope) for scope in scopes if scope]
    return [str(scopes)]


def _gmail_notification_error_code(exc: Exception) -> str | None:
    message = str(exc)
    if "accessNotConfigured" in message or "Gmail API has not been used in project" in message:
        return "gmail_api_not_enabled"
    if "insufficient authentication scopes" in message or "insufficientPermissions" in message:
        return "gmail_scope_missing"
    return None


def _storage_export_public_decision(item: dict[str, Any]) -> str | None:
    analysis = item.get("result") if isinstance(item.get("result"), dict) else item
    return (
        item.get("user_decision")
        or item.get("ai_decision")
        or analysis.get("ai_decision")
        or analysis.get("moderation_status")
    )


def _export_face_count_folder_name(face_count: int) -> str:
    return f"{max(0, int(face_count or 0))}人"


def _export_person_folder_name(value: str, fallback: str) -> str:
    return _safe_zip_path_segment(value, fallback or "未命名人物")


def _build_session_export_document(session: dict[str, Any]) -> dict[str, Any]:
    results = session.get("results") if isinstance(session.get("results"), list) else []
    clusters = session.get("face_clusters") if isinstance(session.get("face_clusters"), list) else []
    cluster_by_id = {
        str(cluster.get("cluster_id") or ""): cluster
        for cluster in clusters
        if isinstance(cluster, dict) and cluster.get("cluster_id")
    }
    session_id = str(session.get("session_id") or "")
    google_user_id = str(session.get("google_user_id") or "")
    for cluster_id, cluster in cluster_by_id.items():
        cluster.setdefault("person_id", person_id_for(google_user_id, session_id, cluster_id))
    assignments: dict[str, list[str]] = {}
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        cluster_id = str(cluster.get("cluster_id") or "")
        if not cluster_id:
            continue
        for evidence in cluster.get("evidence_photos") or []:
            if not isinstance(evidence, dict):
                continue
            file_name = str(evidence.get("file_name") or "")
            if not file_name:
                continue
            assignments.setdefault(file_name, [])
            if cluster_id not in assignments[file_name]:
                assignments[file_name].append(cluster_id)
    people = [
        {
            "cluster_id": str(cluster.get("cluster_id") or ""),
            "person_id": str(cluster.get("person_id") or ""),
            "display_name": str(cluster.get("display_name") or cluster.get("cluster_id") or ""),
            "status": str(cluster.get("status") or "unconfirmed"),
            "notes": str(cluster.get("notes") or ""),
        }
        for cluster in clusters
        if isinstance(cluster, dict)
    ]
    photos = []
    for index, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue
        analysis = item.get("result") if isinstance(item.get("result"), dict) else item
        file_name = str(item.get("file_name") or item.get("file") or analysis.get("file_name") or f"photo_{index}")
        assigned_people = []
        for cluster_id in assignments.get(file_name, []):
            cluster = cluster_by_id.get(cluster_id)
            if not cluster:
                continue
            assigned_people.append(
                {
                    "cluster_id": cluster_id,
                    "person_id": str(cluster.get("person_id") or ""),
                    "display_name": str(cluster.get("display_name") or cluster_id),
                    "faces": [
                        strip_image_payload(evidence)
                        for evidence in cluster.get("evidence_photos") or []
                        if isinstance(evidence, dict) and str(evidence.get("file_name") or "") == file_name
                    ],
                }
            )
        photos.append(
            {
                "photo_id": photo_id_for(session_id, file_name),
                "file_name": file_name,
                "drive_id": item.get("drive_id") or analysis.get("drive_id"),
                "public_decision": _storage_export_public_decision(item),
                "people": assigned_people,
            }
        )
    folders_by_key: dict[str, dict[str, Any]] = {}
    for photo in photos:
        assigned_people = photo.get("people") if isinstance(photo.get("people"), list) else []
        count_folder_name = _export_face_count_folder_name(len(assigned_people))
        if len(assigned_people) == 1:
            person = assigned_people[0]
            path_segments = [
                count_folder_name,
                _export_person_folder_name(
                    str(person.get("display_name") or ""),
                    str(person.get("cluster_id") or "未命名人物"),
                ),
            ]
        else:
            path_segments = [count_folder_name]
        folder_key = "/".join(path_segments)
        if folder_key not in folders_by_key:
            folders_by_key[folder_key] = {
                "name": path_segments[-1],
                "face_count": len(assigned_people),
                "path_segments": path_segments,
                "photos": [],
            }
        folders_by_key[folder_key]["photos"].append(
            {
                "file_name": photo.get("file_name"),
                "drive_id": photo.get("drive_id"),
                "people": [
                    {
                        "cluster_id": person.get("cluster_id"),
                        "person_id": person.get("person_id"),
                        "display_name": person.get("display_name"),
                        "faces": person.get("faces") or [],
                    }
                    for person in assigned_people
                ],
            }
        )
    return {
        "schema_version": "photoidentifier.export.v2",
        "session_id": session.get("session_id"),
        "job_id": next((str(cluster.get("source_job_id")) for cluster in clusters if cluster.get("source_job_id")), ""),
        "embedding_uri": next((cluster.get("embedding_uri") for cluster in clusters if cluster.get("embedding_uri")), None),
        "manifest_uri": next((cluster.get("manifest_uri") for cluster in clusters if cluster.get("manifest_uri")), None),
        "model_version": next((str(cluster.get("model_version")) for cluster in clusters if cluster.get("model_version")), ""),
        "batch_mode": session.get("batch_mode"),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "people": people,
        "photo_angle_folders": list(folders_by_key.values()),
        "photos": photos,
        "results": [
            {
                key: value
                for key, value in item.items()
                if key not in {"image_b64", "thumbnail_b64", "original_image_b64", "drawn_image_b64", "output_b64"}
            }
            for item in results
            if isinstance(item, dict)
        ],
        "face_clusters": [strip_image_payload(cluster) for cluster in clusters if isinstance(cluster, dict)],
        "face_clustering": session.get("face_clustering"),
        "blocked_files": session.get("blocked_files", []),
    }


def _enrich_training_linkage_document(
    document: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any]:
    enriched = json.loads(json.dumps(document, ensure_ascii=False))
    server_document = _build_session_export_document(session)
    enriched["schema_version"] = "photoidentifier.export.v2"
    for key in ("job_id", "embedding_uri", "manifest_uri", "model_version"):
        enriched[key] = server_document.get(key)

    server_people = {
        str(item.get("cluster_id") or ""): item
        for item in server_document.get("people") or []
        if isinstance(item, dict)
    }
    for person in enriched.get("people") or []:
        if not isinstance(person, dict):
            continue
        server_person = server_people.get(str(person.get("cluster_id") or ""), {})
        person["person_id"] = str(person.get("person_id") or server_person.get("person_id") or "")

    server_photos = {
        str(item.get("file_name") or ""): item
        for item in server_document.get("photos") or []
        if isinstance(item, dict)
    }
    for photo in enriched.get("photos") or []:
        if not isinstance(photo, dict):
            continue
        server_photo = server_photos.get(str(photo.get("file_name") or ""), {})
        photo["photo_id"] = str(photo.get("photo_id") or server_photo.get("photo_id") or "")
        server_assignments = {
            str(item.get("cluster_id") or ""): item
            for item in server_photo.get("people") or []
            if isinstance(item, dict)
        }
        for person in photo.get("people") or []:
            if not isinstance(person, dict):
                continue
            server_person = server_assignments.get(str(person.get("cluster_id") or ""), {})
            person["person_id"] = str(person.get("person_id") or server_person.get("person_id") or "")
            person["faces"] = server_person.get("faces") or []
    return enriched


async def _notify_completed_batch_session(session_id: str, request: Request) -> dict[str, Any] | None:
    session = _batch_sessions.get(session_id)
    if not isinstance(session, dict):
        return None
    existing = session.get("completion_notification")
    if isinstance(existing, dict) and existing.get("status") in {"sent", "skipped"}:
        return existing

    notify_email = str(session.get("user_account") or "").strip()
    if not notify_email:
        result = {"status": "skipped", "reason": "no_recipient"}
        session["completion_notification"] = result
        logger.info("Batch completion notify skipped session=%s reason=no_recipient", session_id)
        await _persist_session_update(session_id, {"completion_notification": result})
        return result

    google_user_id = str(session.get("google_user_id") or "").strip()
    if google_user_id:
        try:
            user_record = await user_store.get_user(google_user_id)
        except Exception:
            logger.exception("Failed to read notification preference google_user_id=%s", google_user_id)
            user_record = None
        preferences = public_user_payload(user_record).get("preferences", {}) if isinstance(user_record, dict) else {}
        if preferences.get("auto_email_results") is False:
            result = {"status": "skipped", "reason": "auto_email_disabled", "recipient": notify_email}
            session["completion_notification"] = result
            logger.info("Batch completion notify skipped session=%s recipient=%s reason=auto_email_disabled", session_id, notify_email)
            await _persist_session_update(session_id, {"completion_notification": result})
            return result

    if not batch_state_store.enabled:
        result = {"status": "skipped", "reason": "state_store_disabled", "recipient": notify_email}
        session["completion_notification"] = result
        logger.info("Batch completion notify skipped session=%s recipient=%s reason=state_store_disabled", session_id, notify_email)
        await _persist_session_update(session_id, {"completion_notification": result})
        return result

    result: dict[str, Any] = {"status": "pending", "recipient": notify_email}
    session["completion_notification"] = result
    try:
        credentials = get_drive_credentials(request)
        document = _build_session_export_document(session)
        document, image_entries = await run_in_threadpool(
            _build_storage_export_images,
            document,
            session,
            credentials,
        )
        file_name, content = _build_storage_export_zip(document, session_id, image_entries)
        bucket_name = _require_exports_bucket_name()
        object_name = f"exports/{session['owner_id']}/{session_id}/{file_name}"
        logger.info(
            "Batch completion notify export build session=%s recipient=%s bucket=%s object=%s image_count=%s",
            session_id,
            notify_email,
            bucket_name,
            object_name,
            len(image_entries),
        )
        await run_in_threadpool(_upload_storage_export, bucket_name, object_name, content)
        _download_url, download_url_expires_at = await run_in_threadpool(
            _generate_storage_signed_url,
            bucket_name,
            object_name,
            EXPORT_SIGNED_URL_TTL_MINUTES,
        )
        await batch_state_store.save_photo_assignments(
            session_id,
            session["owner_id"],
            document,
            str(session.get("user_account") or ""),
            str(session.get("google_user_id") or ""),
        )
        metadata = {
            "bucket_name": bucket_name,
            "object_name": object_name,
            "content_type": "application/zip",
            "download_url_expires_at": download_url_expires_at,
            "notify_email": notify_email,
            "notification_status": "pending",
            "trigger": "batch_completed",
            "usage": _storage_export_usage_metrics(zip_bytes=content, image_entries=image_entries),
        }
        export_id = await batch_state_store.create_export_record(
            session_id,
            session["owner_id"],
            "storage",
            file_name,
            "created",
            metadata,
            str(session.get("user_account") or ""),
            str(session.get("google_user_id") or ""),
        )
        download_entry_url = str(
            request.url_for("download_storage_batch_export", export_id=export_id)
        )
        logger.info(
            "Batch completion notify start export_id=%s session=%s recipient=%s",
            export_id,
            session_id,
            notify_email,
        )
        await run_in_threadpool(
            _send_gmail_notification,
            credentials,
            notify_email,
            "PhotoIdentifier 辨識結果已可下載",
            _storage_export_notification_body(
                download_entry_url,
                EXPORT_SIGNED_URL_TTL_MINUTES,
            ),
        )
        metadata["notification_status"] = "sent"
        await batch_state_store.update_export_record_metadata(export_id, metadata)
        result = {"status": "sent", "recipient": notify_email, "export_id": export_id}
        logger.info(
            "Batch completion notify sent export_id=%s session=%s recipient=%s",
            export_id,
            session_id,
            notify_email,
        )
    except Exception as exc:
        credentials = locals().get("credentials")
        scope_list = _credential_scope_list(credentials) if credentials is not None else []
        error_code = _gmail_notification_error_code(exc)
        result = {
            "status": "failed",
            "recipient": notify_email,
            "error": str(exc),
            "error_code": error_code,
            "gmail_send_scope": "https://www.googleapis.com/auth/gmail.send" in scope_list,
        }
        logger.warning(
            "Batch completion notify failed session=%s recipient=%s gmail_send_scope=%s error_code=%s error=%s",
            session_id,
            notify_email,
            result["gmail_send_scope"],
            error_code,
            exc,
        )
        logger.exception("Batch completion notification failed session=%s", session_id)

    session["completion_notification"] = result
    await _persist_session_update(session_id, {"completion_notification": result})
    return result


def _drive_query_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _copy_people_folders_to_drive(
    credentials,
    target_folder_id: str,
    photo_angle_folders: list[dict[str, Any]],
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

    for folder in photo_angle_folders:
        raw_segments = folder.get("path_segments")
        if isinstance(raw_segments, list) and raw_segments:
            folder_segments = [str(item or "").strip()[:80] for item in raw_segments]
            folder_segments = [item for item in folder_segments if item]
        else:
            folder_segments = [str(folder.get("name") or "無人").strip()[:80] or "無人"]
        folder_name = "/".join(folder_segments)
        photos = folder.get("photos") or []
        if not photos:
            continue
        try:
            folder_id = target_folder_id
            for segment in folder_segments:
                folder_id = get_or_create_subfolder(segment, folder_id)
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
    await _require_feature(request, "export_results", "此帳號尚未開放匯出辨識結果功能")
    session_id = req.session_id.strip()
    target_folder_id = req.target_folder_id.strip()
    if not session_id or not target_folder_id:
        raise HTTPException(status_code=400, detail="session_id 與 Google 雲端輸出區不可留白")

    session = await _owned_batch_session_async(request, session_id)
    if session.get("batch_mode") != "drive":
        raise HTTPException(status_code=400, detail="只有 Google 雲端批次可以備份到輸出區")
    if req.document.get("session_id") != session_id:
        raise HTTPException(status_code=400, detail="JSON 文件的 session_id 與批次不一致")

    export_document = _enrich_training_linkage_document(req.document, session)
    content = json.dumps(export_document, ensure_ascii=False, indent=2).encode("utf-8")
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
        photo_angle_folders = export_document.get("photo_angle_folders")
        if isinstance(photo_angle_folders, list) and photo_angle_folders:
            people_copy = await run_in_threadpool(
                _copy_people_folders_to_drive,
                credentials,
                target_folder_id,
                photo_angle_folders,
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
                    str(session.get("user_account") or ""),
                    str(session.get("google_user_id") or ""),
                )
            except Exception:
                logger.exception("Failed to persist failed export metadata session=%s", session_id)
        raise HTTPException(status_code=500, detail=f"無法將 JSON 備份到 Google 雲端：{exc}") from exc

    if batch_state_store.enabled:
        try:
            await batch_state_store.save_photo_assignments(
                session_id,
                session["owner_id"],
                export_document,
                str(session.get("user_account") or ""),
                str(session.get("google_user_id") or ""),
            )
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
                str(session.get("user_account") or ""),
                str(session.get("google_user_id") or ""),
            )
        except Exception:
            logger.exception("Failed to persist Drive export metadata session=%s", session_id)

    return {
        "status": "created",
        "file_id": saved.get("id"),
        "file_name": saved.get("name") or file_name,
        "people_copy": people_copy,
    }


@app.post("/batch_exports/storage")
async def create_storage_batch_export(req: StorageBatchExportRequest, request: Request):
    await _require_feature(request, "export_results", "此帳號尚未開放匯出辨識結果功能")
    session_id = req.session_id.strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id 不可留白")

    session = await _owned_batch_session_async(request, session_id)
    if req.document.get("session_id") != session_id:
        raise HTTPException(status_code=400, detail="JSON 文件的 session_id 與批次不一致")

    input_document = _enrich_training_linkage_document(req.document, session)

    credentials = None
    file_name = f"results_{session_id}_failed.zip"
    bucket_name = _require_exports_bucket_name()
    object_name = f"exports/{session['owner_id']}/{session_id}/{file_name}"
    try:
        try:
            credentials = get_drive_credentials(request)
        except Exception:
            credentials = None
        export_document, image_entries = await run_in_threadpool(
            _build_storage_export_images,
            input_document,
            session,
            credentials,
        )
        file_name, content = _build_storage_export_zip(export_document, session_id, image_entries)
        object_name = f"exports/{session['owner_id']}/{session_id}/{file_name}"
        logger.info(
            "Storage export build session=%s owner=%s bucket=%s object=%s image_count=%s",
            session_id,
            session["owner_id"],
            bucket_name,
            object_name,
            len(image_entries),
        )
        await run_in_threadpool(_upload_storage_export, bucket_name, object_name, content)
        download_url, download_url_expires_at = await run_in_threadpool(
            _generate_storage_signed_url,
            bucket_name,
            object_name,
            EXPORT_SIGNED_URL_TTL_MINUTES,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Storage export failed session=%s", session_id)
        if batch_state_store.enabled:
            try:
                await batch_state_store.create_export_record(
                    session_id,
                    session["owner_id"],
                    "storage",
                    file_name,
                    "failed",
                    {"bucket_name": bucket_name, "object_name": object_name, "error": str(exc)},
                    str(session.get("user_account") or ""),
                    str(session.get("google_user_id") or ""),
                )
            except Exception:
                logger.exception("Failed to persist failed storage export metadata session=%s", session_id)
        raise HTTPException(status_code=500, detail=f"無法建立暫存匯出檔：{exc}") from exc

    export_id = ""
    notification_status = "skipped"
    notification_error = ""
    if batch_state_store.enabled:
        try:
            await batch_state_store.save_photo_assignments(
                session_id,
                session["owner_id"],
                export_document,
                str(session.get("user_account") or ""),
                str(session.get("google_user_id") or ""),
            )
            metadata = {
                "bucket_name": bucket_name,
                "object_name": object_name,
                "content_type": "application/zip",
                "download_url_expires_at": download_url_expires_at,
                "notify_email": str(session.get("user_account") or ""),
                "notification_status": "pending",
                "usage": _storage_export_usage_metrics(zip_bytes=content, image_entries=image_entries),
            }
            export_id = await batch_state_store.create_export_record(
                session_id,
                session["owner_id"],
                "storage",
                file_name,
                "created",
                metadata,
                str(session.get("user_account") or ""),
                str(session.get("google_user_id") or ""),
            )
            notify_email = str(session.get("user_account") or "").strip()
            if not notify_email:
                logger.info(
                    "Storage export notify skipped export_id=%s session=%s reason=no_recipient",
                    export_id,
                    session_id,
                )
            if notify_email and export_id:
                download_entry_url = str(
                    request.url_for("download_storage_batch_export", export_id=export_id)
                )
                try:
                    logger.info(
                        "Storage export notify start export_id=%s session=%s recipient=%s",
                        export_id,
                        session_id,
                        notify_email,
                    )
                    if credentials is None:
                        credentials = get_drive_credentials(request)
                    await run_in_threadpool(
                        _send_gmail_notification,
                        credentials,
                        notify_email,
                        "PhotoIdentifier 辨識結果已可下載",
                        _storage_export_notification_body(
                            download_entry_url,
                            EXPORT_SIGNED_URL_TTL_MINUTES,
                        ),
                    )
                    notification_status = "sent"
                    logger.info(
                        "Storage export notify sent export_id=%s session=%s recipient=%s",
                        export_id,
                        session_id,
                        notify_email,
                    )
                except Exception as exc:
                    notification_status = "failed"
                    notification_error = str(exc)
                    error_code = _gmail_notification_error_code(exc)
                    scope_list = _credential_scope_list(credentials)
                    logger.warning(
                        "Storage export notify failed export_id=%s session=%s recipient=%s gmail_send_scope=%s recipient_present=%s error_code=%s error=%s",
                        export_id,
                        session_id,
                        notify_email,
                        "https://www.googleapis.com/auth/gmail.send" in scope_list,
                        bool(notify_email),
                        error_code,
                        exc,
                    )
                    logger.exception("Storage export notification failed export_id=%s", export_id)
                metadata["notification_status"] = notification_status
                if notification_error:
                    metadata["notification_error"] = notification_error
                if notification_status == "failed" and error_code:
                    metadata["notification_error_code"] = error_code
                await batch_state_store.update_export_record_metadata(export_id, metadata)
        except Exception:
            logger.exception("Failed to persist storage export metadata session=%s", session_id)

    return {
        "status": "created",
        "export_id": export_id,
        "file_name": file_name,
        "bucket_name": bucket_name,
        "object_name": object_name,
        "download_url": download_url,
        "download_url_expires_at": download_url_expires_at,
        "notification_status": notification_status,
    }


@app.get("/batch_exports/storage/{export_id}")
async def get_storage_batch_export_download(export_id: str, request: Request):
    await _require_feature(request, "export_results", "此帳號尚未開放匯出辨識結果功能")
    if not batch_state_store.enabled:
        raise HTTPException(status_code=404, detail="找不到這份暫存匯出")

    owner_id = _get_client_id(request)
    record = await batch_state_store.get_export_record(owner_id, export_id)
    if not isinstance(record, dict) or record.get("target") != "storage":
        raise HTTPException(status_code=404, detail="找不到這份暫存匯出")

    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    bucket_name = str(metadata.get("bucket_name") or "").strip()
    object_name = str(metadata.get("object_name") or "").strip()
    if not bucket_name or not object_name:
        raise HTTPException(status_code=409, detail="這份暫存匯出缺少下載資訊")

    try:
        download_url, download_url_expires_at = await run_in_threadpool(
            _generate_storage_signed_url,
            bucket_name,
            object_name,
            EXPORT_SIGNED_URL_TTL_MINUTES,
        )
    except Exception as exc:
        logger.exception("Storage export signed URL refresh failed export_id=%s", export_id)
        raise HTTPException(status_code=500, detail=f"無法建立下載連結：{exc}") from exc

    return {
        "status": "ready",
        "export_id": export_id,
        "session_id": record.get("session_id"),
        "file_name": record.get("file_name"),
        "download_url": download_url,
        "download_url_expires_at": download_url_expires_at,
    }


@app.get("/batch_exports/storage/{export_id}/download")
async def download_storage_batch_export(export_id: str, request: Request):
    userinfo = _get_google_userinfo(request)
    if not isinstance(userinfo, dict):
        login_url = app.url_path_for("google_auth")
        next_path = str(request.url.path)
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        logger.info(
            "Storage export download requires login export_id=%s redirect_to_auth=true",
            export_id,
        )
        return RedirectResponse(url=f"{login_url}?next={quote(next_path, safe='/?=&')}", status_code=307)
    await _require_feature(request, "export_results", "此帳號尚未開放匯出辨識結果功能")
    if not batch_state_store.enabled:
        raise HTTPException(status_code=404, detail="找不到這份暫存匯出")

    owner_id = _get_client_id(request)
    record = await batch_state_store.get_export_record(owner_id, export_id)
    if not isinstance(record, dict):
        google_user_id = normalize_google_user_id(userinfo)
        user_account = str(userinfo.get("email") or "").strip()
        record = await batch_state_store.get_export_record_for_user(
            export_id,
            google_user_id,
            user_account,
        )
        if isinstance(record, dict):
            logger.info(
                "Storage export download recovered by google identity export_id=%s owner=%s google_user_id=%s",
                export_id,
                owner_id,
                google_user_id,
            )
    if not isinstance(record, dict) or record.get("target") != "storage":
        raise HTTPException(status_code=404, detail="找不到這份暫存匯出")

    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    bucket_name = str(metadata.get("bucket_name") or "").strip()
    object_name = str(metadata.get("object_name") or "").strip()
    if not bucket_name or not object_name:
        raise HTTPException(status_code=409, detail="這份暫存匯出缺少下載資訊")

    try:
        download_url, _ = await run_in_threadpool(
            _generate_storage_signed_url,
            bucket_name,
            object_name,
            EXPORT_SIGNED_URL_TTL_MINUTES,
        )
    except Exception as exc:
        logger.exception("Storage export signed URL redirect failed export_id=%s", export_id)
        raise HTTPException(status_code=500, detail=f"無法建立下載連結：{exc}") from exc
    logger.info(
        "Storage export download redirect export_id=%s session=%s owner=%s",
        export_id,
        record.get("session_id"),
        owner_id,
    )
    return RedirectResponse(url=download_url)


@app.get("/face_clusters/{session_id}")
async def get_face_clusters(session_id: str, request: Request):
    session = await _owned_batch_session_async(request, session_id)
    return {"session_id": session_id, "clusters": session.get("face_clusters", [])}


@app.post("/batch_sessions/{session_id}/cancel")
async def cancel_batch_session(session_id: str, request: Request, req: BatchCancelRequest | None = None):
    _enforce_request_rate_limit(request, "batch-cancel", limit=10)
    session = await _owned_batch_session_async(request, session_id)
    processing_info = session.get("processing_info") if isinstance(session.get("processing_info"), dict) else {}
    has_active_face_job = bool(processing_info.get("run_face_clustering")) and not session.get("face_clustering")
    if session.get("completed") and not has_active_face_job:
        return {
            "success": True,
            "session_id": session_id,
            "job_id": session.get("face_cluster_job_id"),
            "message": "這場整理已經結束，不需要再中止。",
        }
    if req and req.job_id and not session.get("face_cluster_job_id"):
        session["face_cluster_job_id"] = req.job_id
    result = await _request_batch_cancel(session)
    await _persist_session_update(
        session_id,
        {
            "cancel_requested": True,
            "cancelled_at": session.get("cancelled_at"),
            "face_cluster_job_id": session.get("face_cluster_job_id"),
            "face_cluster_cancel_requested": session.get("face_cluster_cancel_requested", False),
        },
    )
    return result


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


@app.delete("/face_clusters/{session_id}/{cluster_id}")
async def delete_face_cluster(session_id: str, cluster_id: str, request: Request):
    session = await _owned_batch_session_async(request, session_id)
    clusters = session.get("face_clusters", [])
    cluster = next(
        (item for item in clusters if item.get("cluster_id") == cluster_id),
        None,
    )
    if cluster is None:
        raise HTTPException(status_code=404, detail="找不到指定的人物群組")

    session["face_clusters"] = [
        item for item in clusters if item.get("cluster_id") != cluster_id
    ]
    deleted = False
    if batch_state_store.enabled:
        deleted = await batch_state_store.delete_face_cluster(
            session_id,
            session["owner_id"],
            cluster_id,
        )
    return {"session_id": session_id, "cluster_id": cluster_id, "deleted": True, "persisted": deleted}


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
