from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

DEFAULT_ACTIVE_TTL_DAYS = 7
DEFAULT_HISTORY_TTL_DAYS = 30


def get_backend_service_account_json() -> str:
    """Prefer the new backend env and keep the legacy Firestore env as rollback."""
    preferred = str(os.getenv("PHOTOIDENTIFIER_BACKEND_SERVICE_ACCOUNT_JSON") or "").strip()
    if preferred:
        return preferred
    return str(os.getenv("FIRESTORE_SERVICE_ACCOUNT_JSON") or "").strip()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def expires_after(days: int = DEFAULT_ACTIVE_TTL_DAYS) -> str:
    return (utc_now() + timedelta(days=days)).isoformat()


def photo_id_for(session_id: str, file_name: str) -> str:
    digest = hashlib.sha1(f"{session_id}:{file_name}".encode("utf-8")).hexdigest()[:16]
    return f"photo_{digest}"


def cluster_doc_id(session_id: str, cluster_id: str) -> str:
    return f"{session_id}__{cluster_id}"


def assignment_doc_id(session_id: str, photo_id: str) -> str:
    return f"{session_id}__{photo_id}"


def export_doc_id(session_id: str, file_name: str) -> str:
    digest = hashlib.sha1(f"{session_id}:{file_name}:{iso_utc()}".encode("utf-8")).hexdigest()[:16]
    return f"export_{digest}"


def strip_image_payload(value: Any) -> Any:
    """Firestore keeps durable state; large preview images stay out of the DB."""
    if isinstance(value, dict):
        return {
            key: strip_image_payload(item)
            for key, item in value.items()
            if key not in {"image_b64", "original_image_b64", "drawn_image_b64", "output_b64"}
        }
    if isinstance(value, list):
        return [strip_image_payload(item) for item in value]
    return value


def to_firestore_safe_json(value: Any) -> str:
    """Persist nested result payloads as plain JSON text to avoid Firestore entity shape limits."""
    return json.dumps(strip_image_payload(value), ensure_ascii=False, separators=(",", ":"))


def from_firestore_safe_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        logger.warning("Invalid persisted result_summary_json payload")
        return {}
    return parsed if isinstance(parsed, dict) else {}


class NullBatchStateStore:
    enabled = False

    async def create_session(self, session: dict[str, Any]) -> None:
        return None

    async def update_session(self, session_id: str, updates: dict[str, Any]) -> None:
        return None

    async def add_photo_result(self, session_id: str, owner_id: str, result: dict[str, Any]) -> None:
        return None

    async def save_face_clusters(self, session_id: str, owner_id: str, clusters: list[dict[str, Any]]) -> None:
        return None

    async def get_session(self, owner_id: str, session_id: str) -> dict[str, Any] | None:
        return None

    async def update_face_cluster(
        self,
        session_id: str,
        owner_id: str,
        cluster_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        return None

    async def delete_face_cluster(self, session_id: str, owner_id: str, cluster_id: str) -> bool:
        return False

    async def save_photo_assignments(
        self,
        session_id: str,
        owner_id: str,
        document: dict[str, Any],
        user_account: str = "",
    ) -> None:
        return None

    async def list_sessions(self, owner_id: str) -> list[dict[str, Any]]:
        return []

    async def create_export_record(
        self,
        session_id: str,
        owner_id: str,
        target: str,
        file_name: str,
        status: str,
        metadata: dict[str, Any] | None = None,
        user_account: str = "",
    ) -> None:
        return None


class FirestoreBatchStateStore:
    enabled = True

    def __init__(self, project_id: str | None = None, database: str = "(default)") -> None:
        from google.cloud import firestore

        service_account_json = get_backend_service_account_json()
        if service_account_json:
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_info(
                json.loads(service_account_json)
            )
            project_id = project_id or credentials.project_id
            self._client = firestore.Client(project=project_id, database=database, credentials=credentials)
        else:
            self._client = firestore.Client(project=project_id, database=database)

    async def create_session(self, session: dict[str, Any]) -> None:
        payload = {
            "session_id": session["session_id"],
            "owner_id": session["owner_id"],
            "batch_mode": session.get("batch_mode"),
            "user_account": str(session.get("user_account") or ""),
            "status": "processing",
            "created_at": session.get("start_time") or iso_utc(),
            "updated_at": iso_utc(),
            "completed_at": None,
            "expires_at": expires_after(DEFAULT_ACTIVE_TTL_DAYS),
            "processing_info": strip_image_payload(session.get("processing_info", {})),
            "result_count": 0,
        }
        info = session.get("processing_info", {})
        if "face_cluster_eps" in info:
            payload["face_cluster_eps"] = info["face_cluster_eps"]
        if "face_cluster_min_samples" in info:
            payload["face_cluster_min_samples"] = info["face_cluster_min_samples"]
        await run_in_threadpool(
            self._client.collection("batch_sessions").document(session["session_id"]).set,
            payload,
        )

    async def update_session(self, session_id: str, updates: dict[str, Any]) -> None:
        payload = {**updates, "updated_at": iso_utc()}
        await run_in_threadpool(
            self._client.collection("batch_sessions").document(session_id).set,
            strip_image_payload(payload),
            merge=True,
        )

    async def add_photo_result(self, session_id: str, owner_id: str, result: dict[str, Any]) -> None:
        file_name = str(result.get("file_name") or result.get("file") or "")
        photo_id = photo_id_for(session_id, file_name or result.get("drive_id", "photo"))
        analysis = result.get("result") or result
        safe_result = strip_image_payload(result)
        payload = {
            "photo_id": photo_id,
            "session_id": session_id,
            "owner_id": owner_id,
            "user_account": str(result.get("user_account") or ""),
            "file_name": file_name,
            "drive_id": result.get("drive_id"),
            "public_decision": analysis.get("moderation_status") or result.get("moderation_status"),
            "face_count": int(result.get("face_count") or len(analysis.get("face_bboxes", []))),
            "has_face": bool(analysis.get("has_face", False)),
            "has_brand_strap": bool(analysis.get("has_brand_strap", False)),
            "is_safe_for_public": bool(analysis.get("is_safe_for_public", False)),
            "strap_color": analysis.get("strap_color"),
            "result_status": result.get("status", "ok"),
            "result_summary_json": to_firestore_safe_json(safe_result),
            "updated_at": iso_utc(),
            "expires_at": expires_after(DEFAULT_ACTIVE_TTL_DAYS),
        }
        await run_in_threadpool(
            self._client.collection("photo_items").document(photo_id).set,
            payload,
        )

    async def save_face_clusters(self, session_id: str, owner_id: str, clusters: list[dict[str, Any]]) -> None:
        def write_batch() -> None:
            batch = self._client.batch()
            has_writes = False
            for cluster in clusters:
                cluster_id = str(cluster.get("cluster_id", ""))
                if not cluster_id:
                    continue
                payload = {
                    **strip_image_payload(cluster),
                    "cluster_id": cluster_id,
                    "session_id": session_id,
                    "owner_id": owner_id,
                    "updated_at": iso_utc(),
                    "expires_at": expires_after(DEFAULT_ACTIVE_TTL_DAYS),
                }
                ref = self._client.collection("face_clusters").document(cluster_doc_id(session_id, cluster_id))
                batch.set(ref, payload)
                has_writes = True
            if has_writes:
                batch.commit()

        await run_in_threadpool(write_batch)

    async def get_session(self, owner_id: str, session_id: str) -> dict[str, Any] | None:
        def read() -> dict[str, Any] | None:
            session_doc = self._client.collection("batch_sessions").document(session_id).get()
            if not session_doc.exists:
                return None
            session = session_doc.to_dict() or {}
            if session.get("owner_id") != owner_id:
                return None

            photo_docs = (
                self._client.collection("photo_items")
                .where("session_id", "==", session_id)
                .where("owner_id", "==", owner_id)
                .stream()
            )
            cluster_docs = (
                self._client.collection("face_clusters")
                .where("session_id", "==", session_id)
                .where("owner_id", "==", owner_id)
                .stream()
            )
            session["results"] = [
                from_firestore_safe_json(doc.to_dict().get("result_summary_json"))
                for doc in photo_docs
            ]
            session["face_clusters"] = [doc.to_dict() for doc in cluster_docs]
            session["completed"] = session.get("status") == "completed"
            session["start_time"] = session.get("created_at")
            session["end_time"] = session.get("completed_at")
            return session

        return await run_in_threadpool(read)

    async def update_face_cluster(
        self,
        session_id: str,
        owner_id: str,
        cluster_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        def update() -> dict[str, Any] | None:
            ref = self._client.collection("face_clusters").document(cluster_doc_id(session_id, cluster_id))
            snap = ref.get()
            if not snap.exists:
                return None
            cluster = snap.to_dict() or {}
            if cluster.get("owner_id") != owner_id:
                return None
            payload = {**strip_image_payload(updates), "updated_at": iso_utc()}
            ref.set(payload, merge=True)
            cluster.update(payload)
            return cluster

        return await run_in_threadpool(update)

    async def delete_face_cluster(self, session_id: str, owner_id: str, cluster_id: str) -> bool:
        def delete() -> bool:
            ref = self._client.collection("face_clusters").document(cluster_doc_id(session_id, cluster_id))
            snap = ref.get()
            if not snap.exists:
                return False
            cluster = snap.to_dict() or {}
            if cluster.get("owner_id") != owner_id:
                return False
            ref.delete()
            return True

        return await run_in_threadpool(delete)

    async def save_photo_assignments(
        self,
        session_id: str,
        owner_id: str,
        document: dict[str, Any],
        user_account: str = "",
    ) -> None:
        def write_batch() -> None:
            batch = self._client.batch()
            has_writes = False
            for photo in document.get("photos", []):
                file_name = str(photo.get("file_name") or photo.get("file") or "")
                photo_id = str(photo.get("photo_id") or photo_id_for(session_id, file_name))
                people = photo.get("people") or []
                cluster_ids = [
                    str(person.get("cluster_id"))
                    for person in people
                    if person.get("cluster_id")
                ]
                payload = {
                    "session_id": session_id,
                    "owner_id": owner_id,
                    "user_account": user_account,
                    "photo_id": photo_id,
                    "file_name": file_name,
                    "cluster_ids": cluster_ids,
                    "updated_at": iso_utc(),
                    "updated_by": owner_id,
                    "expires_at": expires_after(DEFAULT_ACTIVE_TTL_DAYS),
                }
                ref = self._client.collection("photo_assignments").document(
                    assignment_doc_id(session_id, photo_id)
                )
                batch.set(ref, payload)
                has_writes = True
            if has_writes:
                batch.commit()

        await run_in_threadpool(write_batch)

    async def list_sessions(self, owner_id: str) -> list[dict[str, Any]]:
        def read() -> list[dict[str, Any]]:
            query = self._client.collection("batch_sessions").where("owner_id", "==", owner_id)
            try:
                docs = query.order_by("updated_at", direction="DESCENDING").limit(50).stream()
                return [doc.to_dict() for doc in docs]
            except Exception as exc:
                logger.warning("Falling back to unindexed batch_sessions query: %s", exc)
                docs = query.stream()
                sessions = [doc.to_dict() for doc in docs]
                sessions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
                return sessions[:50]

        return await run_in_threadpool(read)

    async def create_export_record(
        self,
        session_id: str,
        owner_id: str,
        target: str,
        file_name: str,
        status: str,
        metadata: dict[str, Any] | None = None,
        user_account: str = "",
    ) -> None:
        payload = {
            "export_id": export_doc_id(session_id, file_name),
            "session_id": session_id,
            "owner_id": owner_id,
            "user_account": str(user_account or ""),
            "target": target,
            "file_name": file_name,
            "status": status,
            "created_at": iso_utc(),
            "expires_at": expires_after(DEFAULT_HISTORY_TTL_DAYS),
            "metadata": strip_image_payload(metadata or {}),
        }
        await run_in_threadpool(
            self._client.collection("exports").document(payload["export_id"]).set,
            payload,
        )


def create_batch_state_store() -> FirestoreBatchStateStore | NullBatchStateStore:
    backend = os.getenv("BATCH_STATE_BACKEND", "auto").strip().lower()
    if backend in {"memory", "none", "off", "disabled"}:
        return NullBatchStateStore()

    project_id = os.getenv("FIRESTORE_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    should_try_firestore = backend == "firestore" or bool(project_id) or bool(get_backend_service_account_json())
    if not should_try_firestore:
        return NullBatchStateStore()

    try:
        return FirestoreBatchStateStore(
            project_id=project_id,
            database=os.getenv("FIRESTORE_DATABASE", "(default)"),
        )
    except Exception as exc:
        if backend == "firestore":
            raise
        logger.warning("Firestore batch state disabled: %s", exc)
        return NullBatchStateStore()
