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


def person_id_for(google_user_id: str, session_id: str, cluster_id: str) -> str:
    value = f"{google_user_id}:{session_id}:{cluster_id}"
    return f"person_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def cluster_doc_id(session_id: str, cluster_id: str) -> str:
    return f"{session_id}__{cluster_id}"


def assignment_doc_id(session_id: str, photo_id: str) -> str:
    return f"{session_id}__{photo_id}"


def export_doc_id(session_id: str, file_name: str) -> str:
    digest = hashlib.sha1(f"{session_id}:{file_name}:{iso_utc()}".encode("utf-8")).hexdigest()[:16]
    return f"export_{digest}"


def build_training_linkage_records(
    session_id: str,
    owner_id: str,
    google_user_id: str,
    document: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    now = iso_utc()
    people_by_cluster: dict[str, dict[str, Any]] = {}
    people_records: list[dict[str, Any]] = []
    for person in document.get("people") or []:
        if not isinstance(person, dict):
            continue
        cluster_id = str(person.get("cluster_id") or "")
        if not cluster_id:
            continue
        person_id = str(person.get("person_id") or person_id_for(google_user_id, session_id, cluster_id))
        record = {
            "person_id": person_id,
            "google_user_id": google_user_id,
            "display_name": str(person.get("display_name") or cluster_id),
            "status": str(person.get("status") or "unconfirmed"),
            "source_session_id": session_id,
            "source_cluster_id": cluster_id,
            "updated_at": now,
        }
        people_by_cluster[cluster_id] = record
        people_records.append(record)

    assignments: list[dict[str, Any]] = []
    faces: list[dict[str, Any]] = []
    for photo in document.get("photos") or []:
        if not isinstance(photo, dict):
            continue
        file_name = str(photo.get("file_name") or photo.get("file") or "")
        photo_id = str(photo.get("photo_id") or photo_id_for(session_id, file_name))
        assigned_people = [item for item in photo.get("people") or [] if isinstance(item, dict)]
        cluster_ids = [str(item.get("cluster_id")) for item in assigned_people if item.get("cluster_id")]
        person_ids: list[str] = []
        for item in assigned_people:
            cluster_id = str(item.get("cluster_id") or "")
            definition = people_by_cluster.get(cluster_id, {})
            person_id = str(item.get("person_id") or definition.get("person_id") or "")
            if person_id:
                person_ids.append(person_id)
            for face in item.get("faces") or []:
                if not isinstance(face, dict) or not face.get("face_id"):
                    continue
                faces.append(
                    {
                        "face_id": str(face["face_id"]),
                        "google_user_id": google_user_id,
                        "person_id": person_id,
                        "session_id": session_id,
                        "job_id": str(document.get("job_id") or face.get("job_id") or ""),
                        "photo_id": photo_id,
                        "file_name": file_name,
                        "cluster_id": cluster_id,
                        "bbox": face.get("bbox"),
                        "score": face.get("score"),
                        "embedding_row": face.get("embedding_row"),
                        "embedding_sha256": str(face.get("embedding_sha256") or ""),
                        "embedding_uri": str(document.get("embedding_uri") or face.get("embedding_uri") or ""),
                        "model_version": str(document.get("model_version") or face.get("model_version") or ""),
                        "label_source": "user_export",
                        "updated_at": now,
                    }
                )
        assignments.append(
            {
                "session_id": session_id,
                "owner_id": owner_id,
                "google_user_id": google_user_id,
                "photo_id": photo_id,
                "file_name": file_name,
                "cluster_ids": cluster_ids,
                "person_ids": person_ids,
                "updated_at": now,
                "updated_by": owner_id,
                "expires_at": expires_after(DEFAULT_ACTIVE_TTL_DAYS),
            }
        )
    return {"people": people_records, "assignments": assignments, "faces": faces}


def strip_image_payload(value: Any) -> Any:
    """Firestore keeps durable state; large preview images stay out of the DB."""
    if isinstance(value, dict):
        return {
            key: strip_image_payload(item)
            for key, item in value.items()
            if key not in {"image_b64", "thumbnail_b64", "original_image_b64", "drawn_image_b64", "output_b64"}
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


def _non_negative_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def normalize_usage_metrics(value: Any) -> dict[str, int]:
    payload = value if isinstance(value, dict) else {}
    return {
        "preview_bytes_uploaded": _non_negative_int(payload.get("preview_bytes_uploaded")),
        "preview_object_count": _non_negative_int(payload.get("preview_object_count")),
        "storage_export_bytes": _non_negative_int(payload.get("storage_export_bytes")),
        "storage_export_image_bytes": _non_negative_int(payload.get("storage_export_image_bytes")),
        "storage_export_image_count": _non_negative_int(payload.get("storage_export_image_count")),
        "storage_export_count": _non_negative_int(payload.get("storage_export_count")),
        "storage_download_count": _non_negative_int(payload.get("storage_download_count")),
    }


def resolve_job_actor(session: dict[str, Any]) -> dict[str, str]:
    google_user_id = str(session.get("google_user_id") or "").strip()
    if google_user_id:
        return {"type": "user", "id": google_user_id}
    return {"type": "anonymous", "id": str(session.get("owner_id") or "").strip()}


def _number(value: Any) -> float:
    try:
        return max(float(value or 0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _integer(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def build_usage_cost_summary(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "job_count": 0,
        "photo_count": 0,
        "input_bytes": 0,
        "output_bytes": 0,
        "processing_seconds": 0.0,
        "billable_vcpu_seconds": 0.0,
        "billable_memory_gib_seconds": 0.0,
        "network_egress_bytes": 0,
        "cost": {
            "cpu": 0.0,
            "ram": 0.0,
            "network_egress": 0.0,
            "storage": 0.0,
            "other_gcp": 0.0,
            "total": 0.0,
        },
    }
    for job in jobs:
        if not isinstance(job, dict):
            continue
        resource = job.get("resource_actual") if isinstance(job.get("resource_actual"), dict) else {}
        cost = job.get("cost_actual") if isinstance(job.get("cost_actual"), dict) else {}
        summary["job_count"] += 1
        summary["photo_count"] += _integer(resource.get("photo_count"))
        summary["input_bytes"] += _integer(resource.get("input_bytes"))
        summary["output_bytes"] += _integer(resource.get("output_bytes"))
        summary["processing_seconds"] += _number(resource.get("processing_seconds"))
        summary["billable_vcpu_seconds"] += _number(resource.get("billable_vcpu_seconds"))
        summary["billable_memory_gib_seconds"] += _number(resource.get("billable_memory_gib_seconds"))
        summary["network_egress_bytes"] += _integer(resource.get("network_egress_bytes"))
        for key in ("cpu", "ram", "network_egress", "storage", "other_gcp", "total"):
            summary["cost"][key] += _number(cost.get(key))
    return summary


class NullBatchStateStore:
    enabled = False

    async def create_session(self, session: dict[str, Any]) -> None:
        return None

    async def update_session(self, session_id: str, updates: dict[str, Any]) -> None:
        return None

    async def add_photo_result(self, session_id: str, owner_id: str, result: dict[str, Any]) -> None:
        return None

    async def save_face_clusters(
        self,
        session_id: str,
        owner_id: str,
        clusters: list[dict[str, Any]],
        google_user_id: str = "",
    ) -> None:
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
        google_user_id: str = "",
    ) -> None:
        return None

    async def list_training_face_links(
        self,
        google_user_id: str,
        *,
        person_id: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        return []

    async def list_sessions(self, owner_id: str) -> list[dict[str, Any]]:
        return []

    async def list_usage_cost_sessions(
        self,
        *,
        start_at: str = "",
        end_at: str = "",
        actor_id: str = "",
        job_id: str = "",
        source_kind: str = "",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
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
        google_user_id: str = "",
    ) -> str:
        return ""

    async def get_export_record(self, owner_id: str, export_id: str) -> dict[str, Any] | None:
        return None

    async def get_export_record_for_user(
        self,
        export_id: str,
        google_user_id: str = "",
        user_account: str = "",
    ) -> dict[str, Any] | None:
        return None

    async def update_export_record_metadata(
        self,
        export_id: str,
        metadata: dict[str, Any],
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
            "google_user_id": str(session.get("google_user_id") or ""),
            "status": "processing",
            "created_at": session.get("start_time") or iso_utc(),
            "updated_at": iso_utc(),
            "completed_at": None,
            "expires_at": expires_after(DEFAULT_ACTIVE_TTL_DAYS),
            "processing_info": strip_image_payload(session.get("processing_info", {})),
            "result_count": 0,
            "usage": normalize_usage_metrics(session.get("usage")),
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
        if "usage" in payload:
            payload["usage"] = normalize_usage_metrics(payload.get("usage"))
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
            "google_user_id": str(result.get("google_user_id") or ""),
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

    async def save_face_clusters(
        self,
        session_id: str,
        owner_id: str,
        clusters: list[dict[str, Any]],
        google_user_id: str = "",
    ) -> None:
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
                    "google_user_id": str(google_user_id or ""),
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
        google_user_id: str = "",
    ) -> None:
        def write_batch() -> None:
            batch = self._client.batch()
            write_count = 0

            def queue_set(ref, payload: dict[str, Any], *, merge: bool = False) -> None:
                nonlocal batch, write_count
                batch.set(ref, payload, merge=merge)
                write_count += 1
                if write_count >= 400:
                    batch.commit()
                    batch = self._client.batch()
                    write_count = 0

            records = build_training_linkage_records(
                session_id, owner_id, str(google_user_id or ""), document
            )
            for payload in records["assignments"]:
                payload = {**payload, "user_account": user_account}
                ref = self._client.collection("photo_assignments").document(
                    assignment_doc_id(session_id, str(payload["photo_id"]))
                )
                queue_set(ref, payload)
            for payload in records["people"]:
                ref = self._client.collection("person_definitions").document(str(payload["person_id"]))
                queue_set(ref, payload, merge=True)
            for payload in records["faces"]:
                ref = self._client.collection("training_face_links").document(str(payload["face_id"]))
                queue_set(ref, payload, merge=True)
            if write_count:
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

    async def list_usage_cost_sessions(
        self,
        *,
        start_at: str = "",
        end_at: str = "",
        actor_id: str = "",
        job_id: str = "",
        source_kind: str = "",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        def read() -> list[dict[str, Any]]:
            query = self._client.collection("batch_sessions")
            if start_at:
                query = query.where("completed_at", ">=", start_at)
            if end_at:
                query = query.where("completed_at", "<=", end_at)
            try:
                docs = [
                    doc.to_dict()
                    for doc in query.order_by("completed_at", direction="DESCENDING")
                    .limit(max(1, min(int(limit), 2000)))
                    .stream()
                ]
            except Exception as exc:
                logger.warning("Falling back to unordered usage-cost query: %s", exc)
                docs = [doc.to_dict() for doc in query.limit(max(1, min(int(limit), 2000))).stream()]
            filtered: list[dict[str, Any]] = []
            normalized_actor_id = str(actor_id or "").strip()
            normalized_job_id = str(job_id or "").strip()
            normalized_source_kind = str(source_kind or "").strip()
            for item in docs:
                if not isinstance(item, dict) or not isinstance(item.get("classifier_accounting"), dict):
                    continue
                actor = item.get("actor") if isinstance(item.get("actor"), dict) else {}
                accounting = item.get("classifier_accounting") if isinstance(item.get("classifier_accounting"), dict) else {}
                resource = accounting.get("resource_actual") if isinstance(accounting.get("resource_actual"), dict) else {}
                if normalized_actor_id and str(actor.get("id") or "") != normalized_actor_id:
                    continue
                if normalized_job_id and str(accounting.get("job_id") or item.get("classifier_job_id") or "") != normalized_job_id:
                    continue
                if normalized_source_kind and str(resource.get("source_kind") or item.get("batch_mode") or "") != normalized_source_kind:
                    continue
                filtered.append(item)
            filtered.sort(key=lambda item: str(item.get("completed_at") or item.get("updated_at") or ""), reverse=True)
            return filtered

        return await run_in_threadpool(read)

    async def list_training_face_links(
        self,
        google_user_id: str,
        *,
        person_id: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        def read() -> list[dict[str, Any]]:
            query = self._client.collection("training_face_links").where(
                "google_user_id", "==", str(google_user_id)
            )
            if person_id:
                query = query.where("person_id", "==", str(person_id))
            return [doc.to_dict() for doc in query.limit(max(1, min(int(limit), 5000))).stream()]

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
        google_user_id: str = "",
    ) -> str:
        payload = {
            "export_id": export_doc_id(session_id, file_name),
            "session_id": session_id,
            "owner_id": owner_id,
            "user_account": str(user_account or ""),
            "google_user_id": str(google_user_id or ""),
            "target": target,
            "file_name": file_name,
            "status": status,
            "created_at": iso_utc(),
            "expires_at": expires_after(DEFAULT_HISTORY_TTL_DAYS),
            "metadata": strip_image_payload(metadata or {}),
            "usage": normalize_usage_metrics((metadata or {}).get("usage")),
        }
        await run_in_threadpool(
            self._client.collection("exports").document(payload["export_id"]).set,
            payload,
        )
        return str(payload["export_id"])

    async def get_export_record(self, owner_id: str, export_id: str) -> dict[str, Any] | None:
        def read() -> dict[str, Any] | None:
            snap = self._client.collection("exports").document(export_id).get()
            if not snap.exists:
                return None
            payload = snap.to_dict() or {}
            if payload.get("owner_id") != owner_id:
                return None
            return payload

        return await run_in_threadpool(read)

    async def get_export_record_for_user(
        self,
        export_id: str,
        google_user_id: str = "",
        user_account: str = "",
    ) -> dict[str, Any] | None:
        normalized_google_user_id = str(google_user_id or "").strip()
        normalized_user_account = str(user_account or "").strip().lower()

        def read() -> dict[str, Any] | None:
            snap = self._client.collection("exports").document(export_id).get()
            if not snap.exists:
                return None
            payload = snap.to_dict() or {}
            payload_google_user_id = str(payload.get("google_user_id") or "").strip()
            payload_user_account = str(payload.get("user_account") or "").strip().lower()
            if normalized_google_user_id and payload_google_user_id == normalized_google_user_id:
                return payload
            if normalized_user_account and payload_user_account == normalized_user_account:
                return payload
            return None

        return await run_in_threadpool(read)

    async def update_export_record_metadata(
        self,
        export_id: str,
        metadata: dict[str, Any],
    ) -> None:
        await run_in_threadpool(
            self._client.collection("exports").document(export_id).set,
            {
                "metadata": strip_image_payload(metadata),
                "usage": normalize_usage_metrics(metadata.get("usage")),
                "updated_at": iso_utc(),
            },
            merge=True,
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
