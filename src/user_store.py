from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi.concurrency import run_in_threadpool

from src.batch_state_store import get_backend_service_account_json, iso_utc

logger = logging.getLogger(__name__)

DEFAULT_FEATURES = {
    "face_clustering": True,
    "drive_batch": True,
    "export_results": True,
    "public_classification": False,
}
DEFAULT_PREFERENCES = {
    "auto_email_results": True,
}

PUBLIC_CLASSIFICATION_DENIED_DETAIL = "此帳號尚未開放可公開性判定功能"


def normalize_google_user_id(userinfo: dict[str, Any] | None) -> str:
    return str((userinfo or {}).get("id") or (userinfo or {}).get("google_user_id") or "").strip()


def default_user_record(userinfo: dict[str, Any] | None = None) -> dict[str, Any]:
    google_user_id = normalize_google_user_id(userinfo)
    return {
        "google_user_id": google_user_id,
        "email": str((userinfo or {}).get("email") or ""),
        "name": str((userinfo or {}).get("name") or ""),
        "picture": str((userinfo or {}).get("picture") or ""),
        "enabled": True,
        "features": dict(DEFAULT_FEATURES),
        "preferences": dict(DEFAULT_PREFERENCES),
        "created_at": iso_utc(),
        "updated_at": iso_utc(),
        "last_login_at": iso_utc(),
    }


def public_user_payload(user: dict[str, Any] | None) -> dict[str, Any]:
    payload = default_user_record(user)
    if isinstance(user, dict):
        payload.update(
            {
                "google_user_id": str(user.get("google_user_id") or ""),
                "email": str(user.get("email") or ""),
                "name": str(user.get("name") or ""),
                "picture": str(user.get("picture") or ""),
                "enabled": user.get("enabled") is True,
                "features": _read_features(user),
                "preferences": _read_preferences(user),
                "created_at": user.get("created_at") or "",
                "updated_at": user.get("updated_at") or "",
                "last_login_at": user.get("last_login_at") or "",
            }
        )
    return payload


def _read_preferences(user: dict[str, Any] | None) -> dict[str, bool]:
    raw_preferences = (user or {}).get("preferences")
    raw_preferences = raw_preferences if isinstance(raw_preferences, dict) else {}
    preferences: dict[str, bool] = {}
    for preference, default_value in DEFAULT_PREFERENCES.items():
        preferences[preference] = raw_preferences.get(preference, default_value) is True
    return preferences


def _read_features(user: dict[str, Any] | None) -> dict[str, bool]:
    raw_features = (user or {}).get("features")
    raw_features = raw_features if isinstance(raw_features, dict) else {}
    features: dict[str, bool] = {}
    for feature, default_value in DEFAULT_FEATURES.items():
        if feature == "public_classification":
            features[feature] = raw_features.get(feature) is True
        else:
            features[feature] = raw_features.get(feature, default_value) is True
    return features


def feature_enabled(user: dict[str, Any] | None, feature: str) -> bool:
    if not isinstance(user, dict) or user.get("enabled") is not True:
        return False
    if feature not in DEFAULT_FEATURES:
        return False
    return _read_features(user).get(feature) is True


class NullUserStore:
    enabled = False

    def get_user_sync(self, google_user_id: str) -> dict[str, Any] | None:
        return None

    async def get_user(self, google_user_id: str) -> dict[str, Any] | None:
        return None

    def get_or_create_user_sync(self, userinfo: dict[str, Any]) -> dict[str, Any]:
        return default_user_record(userinfo)

    async def get_or_create_user(self, userinfo: dict[str, Any]) -> dict[str, Any]:
        return default_user_record(userinfo)

    def update_last_login_sync(self, userinfo: dict[str, Any]) -> dict[str, Any]:
        return default_user_record(userinfo)

    async def update_last_login(self, userinfo: dict[str, Any]) -> dict[str, Any]:
        return default_user_record(userinfo)

    async def has_feature(self, google_user_id: str, feature: str) -> bool:
        if feature == "public_classification":
            return False
        return DEFAULT_FEATURES.get(feature) is True

    async def update_preferences(self, google_user_id: str, preferences: dict[str, bool]) -> dict[str, Any]:
        payload = default_user_record({"id": google_user_id})
        payload["preferences"] = {**DEFAULT_PREFERENCES, **preferences}
        return payload


class FirestoreUserStore:
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

    def _user_ref(self, google_user_id: str):
        return self._client.collection("users").document(google_user_id)

    def get_user_sync(self, google_user_id: str) -> dict[str, Any] | None:
        google_user_id = str(google_user_id or "").strip()
        if not google_user_id:
            return None
        snap = self._user_ref(google_user_id).get()
        if not snap.exists:
            return None
        user = snap.to_dict() or {}
        user["google_user_id"] = str(user.get("google_user_id") or google_user_id)
        return user

    async def get_user(self, google_user_id: str) -> dict[str, Any] | None:
        return await run_in_threadpool(self.get_user_sync, google_user_id)

    def get_or_create_user_sync(self, userinfo: dict[str, Any]) -> dict[str, Any]:
        google_user_id = normalize_google_user_id(userinfo)
        if not google_user_id:
            raise ValueError("Google user id is required")

        ref = self._user_ref(google_user_id)
        snap = ref.get()
        now = iso_utc()
        profile_payload = {
            "google_user_id": google_user_id,
            "email": str(userinfo.get("email") or ""),
            "name": str(userinfo.get("name") or ""),
            "picture": str(userinfo.get("picture") or ""),
            "updated_at": now,
            "last_login_at": now,
        }
        if snap.exists:
            ref.set(profile_payload, merge=True)
            user = snap.to_dict() or {}
            user.update(profile_payload)
            return user

        payload = {
            **profile_payload,
            "enabled": True,
            "features": dict(DEFAULT_FEATURES),
            "preferences": dict(DEFAULT_PREFERENCES),
            "created_at": now,
        }
        ref.set(payload)
        return payload

    async def get_or_create_user(self, userinfo: dict[str, Any]) -> dict[str, Any]:
        return await run_in_threadpool(self.get_or_create_user_sync, userinfo)

    def update_last_login_sync(self, userinfo: dict[str, Any]) -> dict[str, Any]:
        return self.get_or_create_user_sync(userinfo)

    async def update_last_login(self, userinfo: dict[str, Any]) -> dict[str, Any]:
        return await run_in_threadpool(self.update_last_login_sync, userinfo)

    async def has_feature(self, google_user_id: str, feature: str) -> bool:
        try:
            user = await self.get_user(google_user_id)
        except Exception:
            logger.exception("Failed to read user permission google_user_id=%s feature=%s", google_user_id, feature)
            return False
        return feature_enabled(user, feature)

    def update_preferences_sync(self, google_user_id: str, preferences: dict[str, bool]) -> dict[str, Any]:
        google_user_id = str(google_user_id or "").strip()
        if not google_user_id:
            raise ValueError("Google user id is required")
        payload = {
            "preferences": {
                **DEFAULT_PREFERENCES,
                **{key: value is True for key, value in preferences.items() if key in DEFAULT_PREFERENCES},
            },
            "updated_at": iso_utc(),
        }
        ref = self._user_ref(google_user_id)
        ref.set(payload, merge=True)
        user = ref.get().to_dict() or {}
        user["google_user_id"] = google_user_id
        return user

    async def update_preferences(self, google_user_id: str, preferences: dict[str, bool]) -> dict[str, Any]:
        return await run_in_threadpool(self.update_preferences_sync, google_user_id, preferences)


def create_user_store() -> FirestoreUserStore | NullUserStore:
    backend = os.getenv("USER_STORE_BACKEND", os.getenv("BATCH_STATE_BACKEND", "auto")).strip().lower()
    if backend in {"memory", "none", "off", "disabled"}:
        return NullUserStore()

    project_id = os.getenv("FIRESTORE_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    should_try_firestore = backend == "firestore" or bool(project_id) or bool(get_backend_service_account_json())
    if not should_try_firestore:
        return NullUserStore()

    try:
        return FirestoreUserStore(
            project_id=project_id,
            database=os.getenv("FIRESTORE_DATABASE", "(default)"),
        )
    except Exception as exc:
        if backend == "firestore":
            raise
        logger.warning("Firestore user store disabled: %s", exc)
        return NullUserStore()
