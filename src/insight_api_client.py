from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps
from src.pillow_heif_compat import register_pillow_heif

DEFAULT_CLUSTER_EPS = 0.9
DEFAULT_CLUSTER_MIN_SAMPLES = 2
DEFAULT_CLUSTER_BATCH_SIZE = 20
DEFAULT_CLUSTER_TRANSFER_MAX_MB = 4.0
DEFAULT_CLUSTER_JOB_POLL_INTERVAL_SEC = 1.0
DEFAULT_CLUSTER_JOB_TIMEOUT_SEC = 900.0
DEFAULT_INSIGHT_API_CONNECT_TIMEOUT_SEC = 20.0
logger = logging.getLogger(__name__)
ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
register_pillow_heif()


def _open_oriented_rgb_image(image_bytes: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(image_bytes))
    return ImageOps.exif_transpose(image).convert("RGB")


def _read_float_env(name: str, default: float, *, minimum: float) -> float:
    raw_value = os.environ.get(name)
    if raw_value is None or raw_value == "":
        return default
    try:
        value = float(raw_value)
    except ValueError:
        logger.warning("%s 無效，改用預設值 %s。", name, default)
        return default
    if value < minimum:
        logger.warning("%s 小於允許值 %s，改用預設值 %s。", name, minimum, default)
        return default
    return value


class InsightApiClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        client_id: str | None = None,
        actor: dict[str, str] | None = None,
        source_kind: str | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("INSIGHT_API_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("INSIGHT_API_KEY", "")
        self.client_id = str(client_id or "").strip()
        self.actor = actor if isinstance(actor, dict) else {}
        self.source_kind = str(source_kind or "").strip()
        if not self.base_url or not self.api_key:
            raise RuntimeError("必須設定 INSIGHT_API_URL 與 INSIGHT_API_KEY")

    async def detect(self, image_bytes: bytes, file_name: str, content_type: str = "image/jpeg") -> dict:
        return await self._post(
            "/v1/faces/detect",
            files={"file": (file_name, image_bytes, content_type)},
        )

    async def cluster(
        self,
        images: Sequence[tuple[str, bytes, str]],
        *,
        eps: float = DEFAULT_CLUSTER_EPS,
        min_samples: int = DEFAULT_CLUSTER_MIN_SAMPLES,
        transfer_batch_size: int = DEFAULT_CLUSTER_BATCH_SIZE,
        progress_callback: ProgressCallback | None = None,
    ) -> dict:
        submit_started_at = time.perf_counter()
        total_bytes = sum(len(data) for _, data, _ in images)
        max_image_bytes = max((len(data) for _, data, _ in images), default=0)
        logger.info(
            "insight.cluster.submit image_count=%s total_bytes=%s max_image_bytes=%s eps=%s min_samples=%s",
            len(images),
            total_bytes,
            max_image_bytes,
            eps,
            min_samples,
        )
        transfer_max_bytes = int(
            _read_float_env(
                "INSIGHT_CLUSTER_TRANSFER_MAX_MB",
                DEFAULT_CLUSTER_TRANSFER_MAX_MB,
                minimum=0.1,
            )
            * 1024
            * 1024
        )
        chunks = chunk_cluster_images(images, max_files=transfer_batch_size, max_bytes=transfer_max_bytes)
        if len(chunks) > 1:
            job = await self.create_staged_cluster_job(
                expected_total=len(images),
                chunk_total=len(chunks),
                eps=eps,
                min_samples=min_samples,
            )
            job_id = job.get("job_id")
            if not job_id:
                raise RuntimeError("Insight API 未回傳 job_id")
            logger.info(
                "insight.cluster.staged_created job_id=%s image_count=%s chunk_total=%s transfer_batch_size=%s transfer_max_bytes=%s",
                job_id,
                len(images),
                len(chunks),
                transfer_batch_size,
                transfer_max_bytes,
            )
            await self._emit_progress(progress_callback, job)
            for chunk_index, chunk in enumerate(chunks):
                logger.info(
                    "insight.cluster.chunk_upload job_id=%s chunk_index=%s/%s file_count=%s chunk_bytes=%s",
                    job_id,
                    chunk_index + 1,
                    len(chunks),
                    len(chunk),
                    sum(len(data) for _, data, _ in chunk),
                )
                await self.upload_cluster_job_chunk(
                    str(job_id),
                    chunk_index=chunk_index,
                    chunk_total=len(chunks),
                    images=chunk,
                )
                await self._emit_progress(
                    progress_callback,
                    {
                        "job_id": job_id,
                        "status": "staging",
                        "stage": "uploading",
                        "progress": {
                            "completed": min(sum(len(item) for item in chunks[: chunk_index + 1]), len(images)),
                            "total": len(images),
                            "percent": round(min(sum(len(item) for item in chunks[: chunk_index + 1]), len(images)) / len(images) * 100, 1),
                        },
                    },
                )
            job = await self.finalize_cluster_job(str(job_id))
            submit_elapsed = time.perf_counter() - submit_started_at
            logger.info("insight.cluster.job_created job_id=%s status=%s submit_elapsed_sec=%.3f", job_id, job.get("status"), submit_elapsed)
            await self._emit_progress(progress_callback, job)
            result = await self._wait_for_cluster_job(str(job_id), progress_callback=progress_callback)
            logger.info("insight.cluster.done job_id=%s total_elapsed_sec=%.3f", job_id, time.perf_counter() - submit_started_at)
            return result

        files = [("files", (name, data, content_type)) for name, data, content_type in images]
        job = await self._post(
            "/v1/faces/cluster/jobs",
            files=files,
            params={"eps": eps, "min_samples": min_samples},
        )
        submit_elapsed = time.perf_counter() - submit_started_at
        job_id = job.get("job_id")
        if not job_id:
            raise RuntimeError("Insight API 未回傳 job_id")
        logger.info("insight.cluster.job_created job_id=%s status=%s submit_elapsed_sec=%.3f", job_id, job.get("status"), submit_elapsed)
        await self._emit_progress(progress_callback, job)
        wait_started_at = time.perf_counter()
        result = await self._wait_for_cluster_job(str(job_id), progress_callback=progress_callback)
        logger.info("insight.cluster.done job_id=%s total_elapsed_sec=%.3f", job_id, time.perf_counter() - submit_started_at)
        return result

    async def create_staged_cluster_job(
        self,
        *,
        expected_total: int,
        chunk_total: int,
        eps: float = DEFAULT_CLUSTER_EPS,
        min_samples: int = DEFAULT_CLUSTER_MIN_SAMPLES,
    ) -> dict:
        return await self._post(
            "/v1/faces/cluster/jobs",
            params={
                "eps": eps,
                "min_samples": min_samples,
                "expected_total": expected_total,
                "chunk_total": chunk_total,
            },
        )

    async def upload_cluster_job_chunk(
        self,
        job_id: str,
        *,
        chunk_index: int,
        chunk_total: int,
        images: Sequence[tuple[str, bytes, str]],
    ) -> dict:
        files = [("files", (name, data, content_type)) for name, data, content_type in images]
        return await self._post(
            f"/v1/faces/cluster/jobs/{job_id}/chunks",
            files=files,
            params={"chunk_index": chunk_index, "chunk_total": chunk_total},
        )

    async def finalize_cluster_job(self, job_id: str) -> dict:
        return await self._post(f"/v1/faces/cluster/jobs/{job_id}/finalize")

    async def create_cluster_job(
        self,
        images: Sequence[tuple[str, bytes, str]],
        *,
        eps: float = DEFAULT_CLUSTER_EPS,
        min_samples: int = DEFAULT_CLUSTER_MIN_SAMPLES,
        transfer_batch_size: int = DEFAULT_CLUSTER_BATCH_SIZE,
    ) -> dict:
        transfer_max_bytes = int(
            _read_float_env(
                "INSIGHT_CLUSTER_TRANSFER_MAX_MB",
                DEFAULT_CLUSTER_TRANSFER_MAX_MB,
                minimum=0.1,
            )
            * 1024
            * 1024
        )
        chunks = chunk_cluster_images(images, max_files=transfer_batch_size, max_bytes=transfer_max_bytes)
        if len(chunks) > 1:
            job = await self.create_staged_cluster_job(
                expected_total=len(images),
                chunk_total=len(chunks),
                eps=eps,
                min_samples=min_samples,
            )
            job_id = job.get("job_id")
            if not job_id:
                raise RuntimeError("Insight API 未回傳 job_id")
            logger.info(
                "insight.cluster.staged_created job_id=%s image_count=%s chunk_total=%s transfer_batch_size=%s transfer_max_bytes=%s",
                job_id,
                len(images),
                len(chunks),
                transfer_batch_size,
                transfer_max_bytes,
            )
            for chunk_index, chunk in enumerate(chunks):
                logger.info(
                    "insight.cluster.chunk_upload job_id=%s chunk_index=%s/%s file_count=%s chunk_bytes=%s",
                    job_id,
                    chunk_index + 1,
                    len(chunks),
                    len(chunk),
                    sum(len(data) for _, data, _ in chunk),
                )
                await self.upload_cluster_job_chunk(
                    str(job_id),
                    chunk_index=chunk_index,
                    chunk_total=len(chunks),
                    images=chunk,
                )
            return await self.finalize_cluster_job(str(job_id))

        files = [("files", (name, data, content_type)) for name, data, content_type in images]
        return await self._post(
            "/v1/faces/cluster/jobs",
            files=files,
            params={"eps": eps, "min_samples": min_samples},
        )

    async def get_cluster_job(self, job_id: str) -> dict:
        return await self._get(f"/v1/faces/cluster/jobs/{job_id}")

    async def cancel_cluster_job(self, job_id: str) -> dict:
        return await self._post(f"/v1/faces/cluster/jobs/{job_id}/cancel")

    async def _wait_for_cluster_job(
        self,
        job_id: str,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> dict:
        poll_interval = _read_float_env(
            "INSIGHT_JOB_POLL_INTERVAL_SEC",
            DEFAULT_CLUSTER_JOB_POLL_INTERVAL_SEC,
            minimum=0.2,
        )
        timeout_sec = _read_float_env(
            "INSIGHT_JOB_TIMEOUT_SEC",
            DEFAULT_CLUSTER_JOB_TIMEOUT_SEC,
            minimum=1.0,
        )
        deadline = time.monotonic() + timeout_sec
        path = f"/v1/faces/cluster/jobs/{job_id}"
        latest_snapshot: dict[str, Any] = {"job_id": job_id, "status": "queued", "stage": "queued"}
        last_log_signature: tuple[Any, ...] | None = None
        logger.info("insight.cluster.wait_start job_id=%s poll_interval=%s timeout_sec=%s", job_id, poll_interval, timeout_sec)

        while True:
            try:
                snapshot = await self._get(path)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Insight API job {job_id} 等待逾時") from exc
                logger.warning(
                    "Insight API job poll retry job_id=%s error_type=%s error=%r",
                    job_id,
                    type(exc).__name__,
                    exc,
                )
                await self._emit_progress(
                    progress_callback,
                    {
                        **latest_snapshot,
                        "status": "running",
                        "stage": "connection_wait",
                    },
                )
                await asyncio.sleep(min(max(poll_interval, 1.0) * 2, 5.0))
                continue
            latest_snapshot = snapshot
            progress = snapshot.get("progress") if isinstance(snapshot.get("progress"), dict) else {}
            log_signature = (
                snapshot.get("status"),
                snapshot.get("stage"),
                snapshot.get("queue_position"),
                progress.get("completed"),
                progress.get("total"),
            )
            if log_signature != last_log_signature:
                logger.info(
                    "insight.cluster.poll job_id=%s status=%s stage=%s completed=%s/%s queue_position=%s elapsed_sec=%.3f",
                    job_id,
                    snapshot.get("status"),
                    snapshot.get("stage"),
                    progress.get("completed"),
                    progress.get("total"),
                    snapshot.get("queue_position"),
                    time.monotonic() - (deadline - timeout_sec),
                )
                last_log_signature = log_signature
            await self._emit_progress(progress_callback, snapshot)
            status = snapshot.get("status")
            if status == "success":
                result = snapshot.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError(f"Insight API job {job_id} 完成但沒有 result")
                logger.info(
                    "insight.cluster.success job_id=%s image_count=%s face_count=%s cluster_count=%s",
                    job_id,
                    result.get("image_count"),
                    result.get("face_count"),
                    result.get("cluster_count"),
                )
                return result
            if status == "failed":
                message = snapshot.get("error_message") or "未知錯誤"
                logger.error("insight.cluster.failed job_id=%s error=%s", job_id, message)
                raise RuntimeError(f"Insight API job {job_id} 失敗: {message}")
            if status == "cancelled":
                logger.warning("insight.cluster.cancelled job_id=%s", job_id)
                raise RuntimeError(f"Insight API job {job_id} 已取消")
            if status not in {"queued", "running"}:
                raise RuntimeError(f"Insight API job {job_id} 狀態異常: {status}")
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Insight API job {job_id} 等待逾時")
            await asyncio.sleep(poll_interval)

    async def _emit_progress(self, callback: ProgressCallback | None, snapshot: dict[str, Any]) -> None:
        if callback is None:
            return
        result = callback(snapshot)
        if result is not None:
            await result

    async def _post(self, path: str, **kwargs) -> dict:
        return await self._request("POST", path, **kwargs)

    async def _get(self, path: str, **kwargs) -> dict:
        return await self._request("GET", path, **kwargs)

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.client_id:
            headers["X-Client-Id"] = self.client_id
        actor_type = str(self.actor.get("type") or "").strip()
        actor_id = str(self.actor.get("id") or "").strip()
        if actor_type and actor_id:
            headers["X-Actor-Type"] = actor_type
            headers["X-Actor-Id"] = actor_id
        if self.source_kind:
            headers["X-Source-Kind"] = self.source_kind
        connect_timeout = _read_float_env(
            "INSIGHT_API_CONNECT_TIMEOUT_SEC",
            DEFAULT_INSIGHT_API_CONNECT_TIMEOUT_SEC,
            minimum=1.0,
        )
        timeout = httpx.Timeout(connect=connect_timeout, read=300, write=300, pool=10)
        async with httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=timeout) as client:
            if method == "POST":
                response = await client.post(path, **kwargs)
            elif method == "GET":
                response = await client.get(path, **kwargs)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                body = response.text[:500]
                logger.warning(
                    "Insight API failed method=%s path=%s status=%s request_size=%s response_size=%s body=%s",
                    method,
                    path,
                    response.status_code,
                    response.request.headers.get("content-length") or response.request.headers.get("Content-Length") or "unknown",
                    response.headers.get("content-length") or response.headers.get("Content-Length") or "unknown",
                    body,
                )
                raise RuntimeError(f"Insight API HTTP {response.status_code}: {body}") from exc
            return response.json()


async def detect_normalized_bboxes(
    image_bytes: bytes,
    file_name: str = "image.jpg",
    content_type: str = "image/jpeg",
) -> list[list[int]]:
    result = await InsightApiClient().detect(image_bytes, file_name, content_type)
    with _open_oriented_rgb_image(image_bytes) as image:
        width, height = image.size
    return [
        [
            int(round(face["bbox"][1] / height * 1000)),
            int(round(face["bbox"][0] / width * 1000)),
            int(round(face["bbox"][3] / height * 1000)),
            int(round(face["bbox"][2] / width * 1000)),
        ]
        for face in result.get("faces", [])
    ]


async def cluster_batch_results(
    results: list[dict],
    *,
    eps: float = DEFAULT_CLUSTER_EPS,
    min_samples: int = DEFAULT_CLUSTER_MIN_SAMPLES,
    batch_size: int = DEFAULT_CLUSTER_BATCH_SIZE,
    progress_callback: ProgressCallback | None = None,
    session_id: str | None = None,
    actor: dict[str, str] | None = None,
    source_kind: str | None = None,
) -> list[dict]:
    images, source_by_name = prepare_cluster_images(results)
    if not images:
        return []
    started_at = time.perf_counter()
    total_bytes = sum(len(data) for _, data, _ in images)
    logger.info(
        "insight.cluster_batch_results.start image_count=%s total_bytes=%s eps=%s min_samples=%s",
        len(images),
        total_bytes,
        eps,
        min_samples,
    )
    async def on_global_progress(snapshot: dict[str, Any]) -> None:
        progress = snapshot.get("progress") if isinstance(snapshot.get("progress"), dict) else {}
        completed = min(int(progress.get("completed") or snapshot.get("completed") or 0), len(images))
        aggregate = {
            **snapshot,
            "progress": {
                "completed": completed,
                "total": len(images),
                "percent": round(completed / len(images) * 100, 1),
            },
        }
        if progress_callback is not None:
            result = progress_callback(aggregate)
            if result is not None:
                await result

    # The classifier receives one logical job and performs detection in small
    # internal batches before fitting DBSCAN once across all embeddings.
    response = await InsightApiClient(client_id=session_id, actor=actor, source_kind=source_kind).cluster(
        images,
        eps=eps,
        min_samples=min_samples,
        transfer_batch_size=batch_size,
        progress_callback=on_global_progress,
    )
    logger.info(
        "insight.cluster_batch_results.done image_count=%s face_count=%s cluster_count=%s elapsed_sec=%.3f",
        len(images),
        response.get("face_count"),
        response.get("cluster_count"),
        time.perf_counter() - started_at,
    )
    return build_clusters_from_response(response, source_by_name)


def chunk_cluster_images(
    images: Sequence[tuple[str, bytes, str]],
    *,
    max_files: int,
    max_bytes: int,
) -> list[list[tuple[str, bytes, str]]]:
    chunks: list[list[tuple[str, bytes, str]]] = []
    current: list[tuple[str, bytes, str]] = []
    current_bytes = 0
    max_files = max(1, int(max_files or 1))
    max_bytes = max(1, int(max_bytes or 1))
    for image in images:
        image_bytes = len(image[1])
        would_exceed_files = len(current) >= max_files
        would_exceed_bytes = bool(current) and current_bytes + image_bytes > max_bytes
        if would_exceed_files or would_exceed_bytes:
            chunks.append(current)
            current = []
            current_bytes = 0
        current.append(image)
        current_bytes += image_bytes
    if current:
        chunks.append(current)
    return chunks


def prepare_cluster_images(results: list[dict]) -> tuple[list[tuple[str, bytes, str]], dict[str, dict]]:
    images: list[tuple[str, bytes, str]] = []
    source_by_name: dict[str, dict] = {}
    for index, result in enumerate(results, start=1):
        image_b64 = result.get("original_image_b64")
        original_path = result.get("original_path")
        if not image_b64 and not original_path:
            continue
        file_name = str(result.get("file_name") or result.get("file") or f"image_{index}.jpg")
        image_bytes = (
            base64.b64decode(image_b64, validate=True)
            if image_b64
            else Path(str(original_path)).read_bytes()
        )
        images.append((file_name, image_bytes, "image/jpeg"))
        source_by_name[file_name] = result
    return images, source_by_name


def _thumbnail_b64_for_source(source: dict | None) -> str | None:
    if not source:
        return None
    if source.get("thumbnail_b64"):
        return source.get("thumbnail_b64")
    image_b64 = source.get("original_image_b64")
    if not image_b64:
        return None
    try:
        image_bytes = base64.b64decode(image_b64, validate=True)
        with _open_oriented_rgb_image(image_bytes) as image:
            image.thumbnail((320, 320))
            output = io.BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=72, optimize=True)
        return base64.b64encode(output.getvalue()).decode("utf-8")
    except Exception:
        return image_b64


def _image_dimensions_for_source(source: dict | None) -> tuple[int | None, int | None]:
    image_b64 = source.get("original_image_b64") if source else None
    if not image_b64:
        return None, None
    try:
        image_bytes = base64.b64decode(image_b64, validate=True)
        with _open_oriented_rgb_image(image_bytes) as image:
            return image.size
    except Exception:
        return None, None


def _read_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _bbox_basis_for_face(face: dict[str, Any], image: dict[str, Any], source: dict | None) -> tuple[int | None, int | None]:
    source_width, source_height = _image_dimensions_for_source(source)
    width = (
        _read_positive_int(face.get("image_width"))
        or _read_positive_int(face.get("source_width"))
        or _read_positive_int(image.get("image_width"))
        or _read_positive_int(image.get("width"))
        or source_width
    )
    height = (
        _read_positive_int(face.get("image_height"))
        or _read_positive_int(face.get("source_height"))
        or _read_positive_int(image.get("image_height"))
        or _read_positive_int(image.get("height"))
        or source_height
    )
    return width, height


def _source_ref_for_cluster_evidence(source: dict | None) -> tuple[str, str | None]:
    if not source:
        return "missing", None
    if source.get("drive_id"):
        return "drive", str(source.get("drive_id"))
    if source.get("original_path"):
        return "local_path", str(source.get("original_path"))
    if source.get("original_image_b64"):
        return "snapshot", None
    return "missing", None


def resolve_face_image_source(face: dict[str, Any]) -> dict[str, Any]:
    if face.get("source_type") == "drive" and face.get("source_key"):
        return {"kind": "drive", "drive_id": face["source_key"]}
    if face.get("source_type") == "local_path" and face.get("source_key"):
        return {"kind": "local_path", "path": face["source_key"]}
    if face.get("image_b64"):
        return {"kind": "snapshot", "image_b64": face.get("image_b64")}
    if face.get("thumbnail_b64"):
        return {"kind": "thumbnail", "image_b64": face.get("thumbnail_b64")}
    return {"kind": "placeholder"}


def build_clusters_from_response(response: dict, source_by_name: dict[str, dict]) -> list[dict]:
    grouped: dict[tuple[str, int], list[dict]] = {}
    noise_index = 0
    for image in response.get("images", []):
        file_name = image["file_name"]
        for face in image.get("faces", []):
            source_cluster = int(face["cluster"])
            if source_cluster >= 0:
                key = ("cluster", source_cluster)
            else:
                noise_index += 1
                key = ("noise", noise_index)
            grouped.setdefault(key, []).append({**face, "file_name": file_name})

    clusters = []
    for display_index, faces in enumerate(grouped.values(), start=1):
        evidence_photos = []
        missing_source_names: set[str] = set()
        for face in faces:
            file_name = face["file_name"]
            source = source_by_name.get(file_name)
            if source is None:
                missing_source_names.add(file_name)
            source_type, source_key = _source_ref_for_cluster_evidence(source)
            image_width, image_height = _bbox_basis_for_face(face, image, source)
            evidence_photos.append(
                {
                    "file_name": file_name,
                    "face_index": face["face_index"],
                    "bbox": face["bbox"],
                    "bbox_space": str(face.get("bbox_space") or "pixel"),
                    "bbox_order": str(face.get("bbox_order") or "xyxy"),
                    "bbox_basis_width": image_width,
                    "bbox_basis_height": image_height,
                    "score": face["score"],
                    "face_id": str(face.get("face_id") or ""),
                    "embedding_sha256": str(face.get("embedding_sha256") or ""),
                    "embedding_row": face.get("embedding_row"),
                    "embedding_uri": face.get("embedding_uri") or response.get("embedding_uri"),
                    "model_version": str(face.get("model_version") or response.get("model_version") or ""),
                    "job_id": str(response.get("job_id") or ""),
                    "image_b64": source.get("original_image_b64") if source else None,
                    "thumbnail_b64": _thumbnail_b64_for_source(source),
                    "image_width": image_width,
                    "image_height": image_height,
                    "source_type": source_type,
                    "source_key": source_key,
                }
            )
        if missing_source_names:
            logger.warning(
                "insight.cluster.missing_source_images cluster_id=%s missing_files=%s",
                f"cluster_{display_index:03d}",
                sorted(missing_source_names),
            )
        clusters.append(
            {
                "cluster_id": f"cluster_{display_index:03d}",
                "source_cluster": faces[0]["cluster"],
                "display_name": f"人物 {display_index:03d}",
                "status": "unconfirmed",
                "face_count": len(faces),
                "photo_count": len({face["file_name"] for face in faces}),
                "evidence_photos": evidence_photos,
                "notes": "",
                "source_job_id": str(response.get("job_id") or ""),
                "embedding_uri": response.get("embedding_uri"),
                "manifest_uri": response.get("manifest_uri"),
                "model_version": str(response.get("model_version") or ""),
            }
        )
    return clusters


async def cancel_cluster_job(job_id: str) -> dict:
    return await InsightApiClient().cancel_cluster_job(job_id)


async def create_cluster_job_from_results(
    results: list[dict],
    *,
    eps: float = DEFAULT_CLUSTER_EPS,
    min_samples: int = DEFAULT_CLUSTER_MIN_SAMPLES,
    start_index: int = 0,
    batch_size: int = DEFAULT_CLUSTER_BATCH_SIZE,
    session_id: str | None = None,
    actor: dict[str, str] | None = None,
    source_kind: str | None = None,
) -> dict:
    images, _source_by_name = prepare_cluster_images(results)
    if not images:
        return {"job_id": None, "status": "success", "result": {"images": []}}
    # One classifier job owns the complete dataset. The classifier itself
    # detects images in bounded internal batches, then fits globally.
    job = await InsightApiClient(client_id=session_id, actor=actor, source_kind=source_kind).create_cluster_job(
        images,
        eps=eps,
        min_samples=min_samples,
        transfer_batch_size=batch_size,
    )
    return {**job, "batch_start_index": 0, "batch_size": len(images), "total": len(images)}


async def get_cluster_job_snapshot(job_id: str) -> dict:
    return await InsightApiClient().get_cluster_job(job_id)
