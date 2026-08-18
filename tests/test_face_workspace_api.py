import json
import unittest
import zipfile
from io import BytesIO
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from starlette.requests import Request

import main


class FakeBatchStateStore:
    enabled = True

    def __init__(self):
        self.updated_clusters = []
        self.saved_assignments = []
        self.export_records = {}

    async def get_session(self, owner_id, session_id):
        if owner_id != "owner-a" or session_id != "stored-session":
            return None
        return {
            "session_id": "stored-session",
            "owner_id": "owner-a",
            "batch_mode": "drive",
            "status": "completed",
            "result_count": 1,
            "face_clusters": [
                {
                    "cluster_id": "cluster_001",
                    "display_name": "人物 001",
                    "status": "unconfirmed",
                    "face_count": 1,
                    "photo_count": 1,
                    "evidence_photos": [],
                    "notes": "",
                }
            ],
            "results": [],
        }

    async def update_face_cluster(self, session_id, owner_id, cluster_id, updates):
        self.updated_clusters.append((session_id, owner_id, cluster_id, updates))
        return {
            "cluster_id": cluster_id,
            "session_id": session_id,
            "owner_id": owner_id,
            "display_name": updates.get("display_name", "人物 001"),
            "status": updates.get("status", "unconfirmed"),
            "notes": updates.get("notes", ""),
            "face_count": 1,
            "photo_count": 1,
            "evidence_photos": [],
        }

    async def list_sessions(self, owner_id):
        if owner_id != "owner-a":
            return []
        return [
            {
                "session_id": "stored-session",
                "batch_mode": "drive",
                "created_at": "2026-08-01T00:00:00+00:00",
                "result_count": 1,
                "status": "completed",
            }
        ]

    async def save_photo_assignments(
        self,
        session_id,
        owner_id,
        document,
        user_account="",
        google_user_id="",
    ):
        self.saved_assignments.append((session_id, owner_id, document, user_account, google_user_id))

    async def create_export_record(
        self,
        session_id,
        owner_id,
        target,
        file_name,
        status,
        metadata=None,
        user_account="",
        google_user_id="",
    ):
        export_id = f"export-{len(self.export_records) + 1}"
        self.export_records[export_id] = {
            "export_id": export_id,
            "session_id": session_id,
            "owner_id": owner_id,
            "target": target,
            "file_name": file_name,
            "status": status,
            "metadata": metadata or {},
            "user_account": user_account,
            "google_user_id": google_user_id,
        }
        return export_id

    async def get_export_record(self, owner_id, export_id):
        record = self.export_records.get(export_id)
        if not record or record.get("owner_id") != owner_id:
            return None
        return dict(record)

    async def get_export_record_for_user(self, export_id, google_user_id="", user_account=""):
        record = self.export_records.get(export_id)
        if not record:
            return None
        if google_user_id and record.get("google_user_id") == google_user_id:
            return dict(record)
        if user_account and record.get("user_account") == user_account:
            return dict(record)
        return None

    async def update_export_record_metadata(self, export_id, metadata):
        if export_id in self.export_records:
            self.export_records[export_id]["metadata"] = dict(metadata)


class FaceWorkspaceApiTests(unittest.TestCase):
    def test_export_document_is_enriched_with_server_side_face_linkage(self):
        session = {
            "session_id": "session-1",
            "google_user_id": "google-1",
            "results": [{"file_name": "one.jpg"}],
            "face_clusters": [
                {
                    "cluster_id": "cluster_001",
                    "display_name": "小明",
                    "source_job_id": "job-1",
                    "embedding_uri": "gs://bucket/job-1/embeddings.npy",
                    "model_version": "buffalo_l-v1",
                    "evidence_photos": [
                        {
                            "file_name": "one.jpg",
                            "face_id": "face-1",
                            "embedding_row": 0,
                            "embedding_sha256": "abc123",
                        }
                    ],
                }
            ],
        }
        browser_document = {
            "session_id": "session-1",
            "people": [{"cluster_id": "cluster_001", "display_name": "小明"}],
            "photos": [
                {
                    "file_name": "one.jpg",
                    "people": [{"cluster_id": "cluster_001", "display_name": "小明"}],
                }
            ],
        }

        enriched = main._enrich_training_linkage_document(browser_document, session)

        self.assertEqual(enriched["job_id"], "job-1")
        self.assertTrue(enriched["people"][0]["person_id"].startswith("person_"))
        self.assertEqual(enriched["photos"][0]["people"][0]["faces"][0]["face_id"], "face-1")

    def setUp(self):
        self.client = TestClient(main.app)
        self.feature_patch = patch.object(
            main,
            "_require_feature",
            new=AsyncMock(return_value={"enabled": True, "features": {}}),
        )
        self.feature_patch.start()
        main._batch_sessions["face-workspace-test"] = {
            "session_id": "face-workspace-test",
            "owner_id": "owner-a",
            "face_clusters": [
                {
                    "cluster_id": "cluster_001",
                    "display_name": "人物 001",
                    "status": "unconfirmed",
                    "face_count": 2,
                    "photo_count": 2,
                    "evidence_photos": [],
                    "notes": "",
                }
            ],
        }

    def tearDown(self):
        self.feature_patch.stop()
        main._batch_sessions.pop("face-workspace-test", None)
        main._batch_sessions.pop("stored-session", None)

    def test_returns_face_clusters_for_batch_session(self):
        with patch.object(main, "_get_client_id", return_value="owner-a"):
            response = self.client.get("/face_clusters/face-workspace-test")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["clusters"][0]["cluster_id"], "cluster_001")

    def test_updates_cluster_name_status_and_notes(self):
        with patch.object(main, "_get_client_id", return_value="owner-a"):
            response = self.client.patch(
                "/face_clusters/face-workspace-test/cluster_001",
                json={"display_name": "王小明", "status": "confirmed", "notes": "講師"},
            )

        self.assertEqual(response.status_code, 200)
        cluster = response.json()["cluster"]
        self.assertEqual(cluster["display_name"], "王小明")
        self.assertEqual(cluster["status"], "confirmed")
        self.assertEqual(cluster["notes"], "講師")

    def test_rejects_unknown_cluster_status(self):
        with patch.object(main, "_get_client_id", return_value="owner-a"):
            response = self.client.patch(
                "/face_clusters/face-workspace-test/cluster_001",
                json={"status": "deleted"},
            )

        self.assertEqual(response.status_code, 422)

    def test_hides_face_clusters_from_another_client(self):
        with patch.object(main, "_get_client_id", return_value="owner-b"):
            response = self.client.get("/face_clusters/face-workspace-test")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "找不到這場活動的辨識紀錄")

    def test_only_lists_sessions_owned_by_current_client(self):
        main._batch_sessions["another-session"] = {
            "session_id": "another-session",
            "owner_id": "owner-b",
            "results": [],
        }
        try:
            with patch.object(main, "_get_client_id", return_value="owner-a"):
                response = self.client.get("/batch_sessions/")
        finally:
            main._batch_sessions.pop("another-session", None)

        self.assertEqual(response.status_code, 200)
        session_ids = {item["session_id"] for item in response.json()["sessions"]}
        self.assertIn("face-workspace-test", session_ids)
        self.assertNotIn("another-session", session_ids)

    def test_drive_export_requires_owned_batch_session(self):
        with patch.object(main, "_get_client_id", return_value="owner-b"):
            response = self.client.post(
                "/batch_exports/drive",
                json={
                    "session_id": "face-workspace-test",
                    "target_folder_id": "target-1",
                    "document": {"session_id": "face-workspace-test"},
                },
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "找不到這場活動的辨識紀錄")

    def test_creates_drive_output_folder_and_uses_root_by_default(self):
        with (
            patch.object(main, "get_drive_credentials", return_value=object()) as credentials,
            patch.object(
                main,
                "_create_drive_output_folder",
                return_value={"id": "folder-1", "name": "活動輸出", "parents": ["root"]},
            ) as create_folder,
        ):
            response = self.client.post("/drive/output-folders", json={"name": "  活動輸出  "})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["folder"]["id"], "folder-1")
        self.assertEqual(create_folder.call_args.args, (credentials.return_value, "活動輸出", "root"))

    def test_creates_drive_output_folder_inside_selected_parent(self):
        with (
            patch.object(main, "get_drive_credentials", return_value=object()),
            patch.object(
                main,
                "_create_drive_output_folder",
                return_value={"id": "folder-2", "name": "人物整理", "parents": ["parent-1"]},
            ) as create_folder,
        ):
            response = self.client.post(
                "/drive/output-folders",
                json={"name": "人物整理", "parent_folder_id": "parent-1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(create_folder.call_args.args[2], "parent-1")

    def test_renames_selected_drive_output_folder(self):
        with (
            patch.object(main, "get_drive_credentials", return_value=object()),
            patch.object(
                main,
                "_rename_drive_output_folder",
                return_value={"id": "folder-1", "name": "新名稱"},
            ) as rename_folder,
        ):
            response = self.client.patch(
                "/drive/output-folders/folder-1", json={"name": "新名稱"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(rename_folder.call_args.args[1:], ("folder-1", "新名稱"))

    def test_rejects_blank_drive_output_folder_name(self):
        response = self.client.post("/drive/output-folders", json={"name": "   "})

        self.assertEqual(response.status_code, 400)

    def test_drive_export_rejects_non_drive_batch(self):
        with patch.object(main, "_get_client_id", return_value="owner-a"):
            response = self.client.post(
                "/batch_exports/drive",
                json={
                    "session_id": "face-workspace-test",
                    "target_folder_id": "target-1",
                    "document": {"session_id": "face-workspace-test"},
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Google 雲端", response.json()["detail"])

    def test_drive_export_rejects_mismatched_document_session(self):
        main._batch_sessions["face-workspace-test"]["batch_mode"] = "drive"
        with patch.object(main, "_get_client_id", return_value="owner-a"):
            response = self.client.post(
                "/batch_exports/drive",
                json={
                    "session_id": "face-workspace-test",
                    "target_folder_id": "target-1",
                    "document": {"session_id": "another-session"},
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("session_id", response.json()["detail"])

    def test_drive_export_rejects_documents_over_ten_mb(self):
        main._batch_sessions["face-workspace-test"]["batch_mode"] = "drive"
        with patch.object(main, "_get_client_id", return_value="owner-a"):
            response = self.client.post(
                "/batch_exports/drive",
                json={
                    "session_id": "face-workspace-test",
                    "target_folder_id": "target-1",
                    "document": {
                        "session_id": "face-workspace-test",
                        "payload": "x" * (10 * 1024 * 1024),
                    },
                },
            )

        self.assertEqual(response.status_code, 413)

    def test_drive_export_requires_google_credentials(self):
        main._batch_sessions["face-workspace-test"]["batch_mode"] = "drive"
        with (
            patch.object(main, "_get_client_id", return_value="owner-a"),
            patch.object(
                main,
                "get_drive_credentials",
                side_effect=main.HTTPException(status_code=401, detail="尚未登入 Google 帳號"),
            ),
        ):
            response = self.client.post(
                "/batch_exports/drive",
                json={
                    "session_id": "face-workspace-test",
                    "target_folder_id": "target-1",
                    "document": {"session_id": "face-workspace-test"},
                },
            )

        self.assertEqual(response.status_code, 401)

    def test_drive_export_creates_timestamped_json(self):
        main._batch_sessions["face-workspace-test"]["batch_mode"] = "drive"
        document = {"session_id": "face-workspace-test", "photos": []}
        with (
            patch.object(main, "_get_client_id", return_value="owner-a"),
            patch.object(main, "get_drive_credentials", return_value=object()),
            patch.object(
                main,
                "_save_json_export_to_drive",
                return_value={"id": "file-1"},
                create=True,
            ) as save,
        ):
            response = self.client.post(
                "/batch_exports/drive",
                json={
                    "session_id": "face-workspace-test",
                    "target_folder_id": "target-1",
                    "document": document,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertRegex(response.json()["file_name"], r"^photo_people_\d{8}_\d{6}\.json$")
        self.assertEqual(response.json()["file_id"], "file-1")
        self.assertEqual(save.call_args.args[1], "target-1")
        self.assertEqual(save.call_args.args[2], response.json()["file_name"])
        saved_document = json.loads(save.call_args.args[3].decode("utf-8"))
        self.assertEqual(saved_document["session_id"], document["session_id"])
        self.assertEqual(saved_document["photos"], document["photos"])
        self.assertIn("job_id", saved_document)
        self.assertIn("embedding_uri", saved_document)
        self.assertIn("model_version", saved_document)

    def test_drive_export_copies_people_folders_when_present(self):
        main._batch_sessions["face-workspace-test"]["batch_mode"] = "drive"
        photo_angle_folders = [{
            "name": "王小明",
            "photos": [{"file_name": "a.jpg", "drive_id": "drive-a"}],
        }]
        document = {
            "session_id": "face-workspace-test",
            "photos": [],
            "photo_angle_folders": photo_angle_folders,
        }
        with (
            patch.object(main, "_get_client_id", return_value="owner-a"),
            patch.object(main, "get_drive_credentials", return_value=object()) as credentials,
            patch.object(
                main,
                "_save_json_export_to_drive",
                return_value={"id": "file-1", "name": "photo_people.json"},
                create=True,
            ),
            patch.object(
                main,
                "_copy_people_folders_to_drive",
                return_value={"copied_count": 1, "folder_count": 1, "errors": []},
                create=True,
            ) as copy_folders,
        ):
            response = self.client.post(
                "/batch_exports/drive",
                json={
                    "session_id": "face-workspace-test",
                    "target_folder_id": "target-1",
                    "document": document,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["people_copy"]["copied_count"], 1)
        self.assertEqual(copy_folders.call_args.args[0], credentials.return_value)
        self.assertEqual(copy_folders.call_args.args[1], "target-1")
        self.assertEqual(copy_folders.call_args.args[2], photo_angle_folders)

    def test_returns_face_clusters_from_persistent_store_when_memory_misses(self):
        store = FakeBatchStateStore()
        with (
            patch.object(main, "batch_state_store", store),
            patch.object(main, "_get_client_id", return_value="owner-a"),
        ):
            response = self.client.get("/face_clusters/stored-session")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["clusters"][0]["cluster_id"], "cluster_001")

    def test_updates_face_cluster_in_persistent_store_when_memory_was_rehydrated(self):
        store = FakeBatchStateStore()
        main._batch_sessions.pop("stored-session", None)
        with (
            patch.object(main, "batch_state_store", store),
            patch.object(main, "_get_client_id", return_value="owner-a"),
        ):
            response = self.client.patch(
                "/face_clusters/stored-session/cluster_001",
                json={"display_name": "王小明"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cluster"]["display_name"], "王小明")
        self.assertEqual(store.updated_clusters[0][0], "stored-session")
        main._batch_sessions.pop("stored-session", None)

    def test_lists_sessions_from_persistent_store(self):
        store = FakeBatchStateStore()
        with (
            patch.object(main, "batch_state_store", store),
            patch.object(main, "_get_client_id", return_value="owner-a"),
        ):
            response = self.client.get("/batch_sessions/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sessions"][0]["session_id"], "stored-session")

    def test_storage_export_creates_zip_metadata_and_signed_url(self):
        store = FakeBatchStateStore()
        main._batch_sessions["face-workspace-test"]["batch_mode"] = "drive"
        main._batch_sessions["face-workspace-test"]["user_account"] = "user@example.com"
        document = {"session_id": "face-workspace-test", "photos": []}
        with (
            patch.object(main, "batch_state_store", store),
            patch.object(main, "_get_client_id", return_value="owner-a"),
            patch.object(main, "PHOTOIDENTIFIER_EXPORTS_BUCKET", "test-exports"),
            patch.object(main, "_upload_storage_export") as upload_export,
            patch.object(
                main,
                "_generate_storage_signed_url",
                return_value=("https://signed.example/export.zip", "2026-08-11T10:00:00+00:00"),
            ) as signed_url,
        ):
            response = self.client.post(
                "/batch_exports/storage",
                json={"session_id": "face-workspace-test", "document": document},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["export_id"], "export-1")
        self.assertEqual(payload["bucket_name"], "test-exports")
        self.assertRegex(payload["file_name"], r"^results_face-workspace-test_\d{8}_\d{6}\.zip$")
        self.assertEqual(
            upload_export.call_args.args[:2],
            ("test-exports", f"exports/owner-a/face-workspace-test/{payload['file_name']}"),
        )
        self.assertTrue(isinstance(upload_export.call_args.args[2], bytes))
        self.assertEqual(signed_url.call_args.args[:2], ("test-exports", payload["object_name"]))
        self.assertEqual(store.saved_assignments[0][0], "face-workspace-test")
        self.assertEqual(
            store.export_records["export-1"]["metadata"]["notify_email"],
            "user@example.com",
        )

    def test_storage_export_sends_gmail_notification_with_app_download_link(self):
        store = FakeBatchStateStore()
        main._batch_sessions["face-workspace-test"]["batch_mode"] = "drive"
        main._batch_sessions["face-workspace-test"]["user_account"] = "user@example.com"
        document = {"session_id": "face-workspace-test", "photos": []}
        credentials = object()
        with (
            patch.object(main, "batch_state_store", store),
            patch.object(main, "_get_client_id", return_value="owner-a"),
            patch.object(main, "get_drive_credentials", return_value=credentials),
            patch.object(main, "PHOTOIDENTIFIER_EXPORTS_BUCKET", "test-exports"),
            patch.object(main, "_upload_storage_export"),
            patch.object(
                main,
                "_generate_storage_signed_url",
                return_value=("https://signed.example/export.zip", "2026-08-11T10:00:00+00:00"),
            ),
            patch.object(main, "_send_gmail_notification", create=True) as send_mail,
        ):
            response = self.client.post(
                "/batch_exports/storage",
                json={"session_id": "face-workspace-test", "document": document},
            )

        self.assertEqual(response.status_code, 200)
        send_mail.assert_called_once()
        self.assertEqual(send_mail.call_args.args[0], credentials)
        self.assertEqual(send_mail.call_args.args[1], "user@example.com")
        self.assertIn("/batch_exports/storage/export-1/download", send_mail.call_args.args[3])
        self.assertNotIn("https://signed.example/export.zip", send_mail.call_args.args[3])
        self.assertEqual(store.export_records["export-1"]["metadata"]["notification_status"], "sent")

    def test_storage_export_refreshes_signed_url_for_owner_only(self):
        store = FakeBatchStateStore()
        store.export_records["export-9"] = {
            "export_id": "export-9",
            "session_id": "face-workspace-test",
            "owner_id": "owner-a",
            "target": "storage",
            "file_name": "results_face-workspace-test_20260811_100000.zip",
            "metadata": {
                "bucket_name": "test-exports",
                "object_name": "exports/owner-a/face-workspace-test/results_face-workspace-test_20260811_100000.zip",
            },
        }
        with (
            patch.object(main, "batch_state_store", store),
            patch.object(main, "_get_client_id", return_value="owner-a"),
            patch.object(
                main,
                "_generate_storage_signed_url",
                return_value=("https://signed.example/fresh.zip", "2026-08-11T11:00:00+00:00"),
            ) as signed_url,
        ):
            response = self.client.get("/batch_exports/storage/export-9")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["download_url"], "https://signed.example/fresh.zip")
        self.assertEqual(
            signed_url.call_args.args[:2],
            (
                "test-exports",
                "exports/owner-a/face-workspace-test/results_face-workspace-test_20260811_100000.zip",
            ),
        )

    def test_storage_export_refresh_rejects_other_owner(self):
        store = FakeBatchStateStore()
        store.export_records["export-9"] = {
            "export_id": "export-9",
            "session_id": "face-workspace-test",
            "owner_id": "owner-a",
            "target": "storage",
            "file_name": "results.zip",
            "metadata": {"bucket_name": "test-exports", "object_name": "exports/owner-a/face-workspace-test/results.zip"},
        }
        with (
            patch.object(main, "batch_state_store", store),
            patch.object(main, "_get_client_id", return_value="owner-b"),
        ):
            response = self.client.get("/batch_exports/storage/export-9")

        self.assertEqual(response.status_code, 404)

    def test_storage_export_download_entry_redirects_to_fresh_signed_url(self):
        store = FakeBatchStateStore()
        store.export_records["export-9"] = {
            "export_id": "export-9",
            "session_id": "face-workspace-test",
            "owner_id": "owner-a",
            "target": "storage",
            "file_name": "results.zip",
            "metadata": {"bucket_name": "test-exports", "object_name": "exports/owner-a/face-workspace-test/results.zip"},
        }
        with (
            patch.object(main, "batch_state_store", store),
            patch.object(main, "_get_client_id", return_value="owner-a"),
            patch.object(main, "_get_google_userinfo", return_value={"id": "user-1", "email": "user@example.com"}),
            patch.object(
                main,
                "_generate_storage_signed_url",
                return_value=("https://signed.example/fresh.zip", "2026-08-11T11:00:00+00:00"),
            ),
        ):
            response = self.client.get("/batch_exports/storage/export-9/download", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "https://signed.example/fresh.zip")

    def test_google_auth_stores_relative_next_redirect(self):
        request = Request({"type": "http", "session": {}})
        with (
            patch.object(main, "_validate_oauth_request_host"),
            patch.object(main, "get_auth_url", return_value=("https://accounts.google.com/o/oauth2/auth", "state-1", "verifier-1")),
        ):
            response = main.google_auth(request, next="/batch_exports/storage/export-1/download")

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "https://accounts.google.com/o/oauth2/auth")
        self.assertEqual(request.session.get("oauth_next"), "/batch_exports/storage/export-1/download")

    def test_storage_export_download_redirects_to_login_when_not_authenticated(self):
        store = FakeBatchStateStore()
        store.export_records["export-9"] = {
            "export_id": "export-9",
            "session_id": "face-workspace-test",
            "owner_id": "owner-a",
            "target": "storage",
            "file_name": "results.zip",
            "metadata": {"bucket_name": "test-exports", "object_name": "exports/owner-a/face-workspace-test/results.zip"},
        }
        with (
            patch.object(main, "batch_state_store", store),
            patch.object(main, "_get_client_id", return_value="owner-a"),
            patch.object(main, "_get_google_userinfo", return_value=None),
        ):
            response = self.client.get("/batch_exports/storage/export-9/download", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(
            response.headers["location"],
            "/auth/google?next=/batch_exports/storage/export-9/download",
        )

    def test_storage_export_download_recovers_record_by_google_identity(self):
        store = FakeBatchStateStore()
        store.export_records["export-9"] = {
            "export_id": "export-9",
            "session_id": "face-workspace-test",
            "owner_id": "owner-original",
            "target": "storage",
            "file_name": "results.zip",
            "user_account": "user@example.com",
            "google_user_id": "google-1",
            "metadata": {"bucket_name": "test-exports", "object_name": "exports/owner-original/face-workspace-test/results.zip"},
        }
        with (
            patch.object(main, "batch_state_store", store),
            patch.object(main, "_get_client_id", return_value="owner-new"),
            patch.object(main, "_get_google_userinfo", return_value={"id": "google-1", "email": "user@example.com"}),
            patch.object(
                main,
                "_generate_storage_signed_url",
                return_value=("https://signed.example/fresh.zip", "2026-08-11T11:00:00+00:00"),
            ),
        ):
            response = self.client.get("/batch_exports/storage/export-9/download", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "https://signed.example/fresh.zip")

    def test_storage_export_zip_contains_importable_workspace_files(self):
        document = {
            "schema_version": "photoidentifier.export.v1",
            "session_id": "face-workspace-test",
            "batch_mode": "drive",
            "results": [{"file_name": "one.jpg"}],
            "face_clusters": [],
            "photos": [{"file_name": "one.jpg", "people": []}],
        }

        file_name, content = main._build_storage_export_zip(document, "face-workspace-test")

        self.assertTrue(file_name.endswith(".zip"))
        with zipfile.ZipFile(BytesIO(content)) as archive:
            names = set(archive.namelist())
            self.assertIn("result.json", names)
            self.assertIn("workspace.json", names)
            self.assertTrue(any(name.startswith("photo_people_") and name.endswith(".json") for name in names))
            self.assertEqual(json.loads(archive.read("workspace.json")), document)
            self.assertEqual(json.loads(archive.read("result.json")), document)

    def test_storage_export_zip_contains_original_images_and_archive_paths(self):
        document = {
            "schema_version": "photoidentifier.export.v1",
            "session_id": "face-workspace-test",
            "batch_mode": "upload",
            "photo_angle_folders": [
                {
                    "name": "0人",
                    "path_segments": ["0人"],
                    "photos": [{"file_name": "one.jpg", "drive_id": None, "people": []}],
                }
            ],
            "results": [{"file_name": "one.jpg"}],
            "face_clusters": [],
            "photos": [{"file_name": "one.jpg", "people": []}],
        }
        session = {
            "session_id": "face-workspace-test",
            "results": [{"file_name": "one.jpg", "original_image_b64": "cHJldmlldy1pbWFnZQ=="}],
            "original_images": {"one.jpg": b"full-original-image"},
        }

        export_document, image_entries = main._build_storage_export_images(document, session)
        _file_name, content = main._build_storage_export_zip(export_document, "face-workspace-test", image_entries)

        self.assertEqual(len(image_entries), 1)
        with zipfile.ZipFile(BytesIO(content)) as archive:
            names = set(archive.namelist())
            self.assertIn("0人/one.jpg", names)
            self.assertEqual(archive.read("0人/one.jpg"), b"full-original-image")
            workspace = json.loads(archive.read("workspace.json"))
            self.assertEqual(workspace["photos"][0]["archive_relative_path"], "0人/one.jpg")
            self.assertEqual(workspace["results"][0]["archive_relative_path"], "0人/one.jpg")

    def test_session_export_document_builds_photo_angle_folders(self):
        session = {
            "session_id": "face-workspace-test",
            "batch_mode": "upload",
            "results": [{"file_name": "one.jpg", "status": "ok"}],
            "face_clusters": [
                {
                    "cluster_id": "cluster_001",
                    "display_name": "人物 001",
                    "status": "confirmed",
                    "evidence_photos": [{"file_name": "one.jpg"}],
                }
            ],
        }

        document = main._build_session_export_document(session)

        self.assertEqual(document["photos"][0]["people"][0]["cluster_id"], "cluster_001")
        self.assertEqual(document["photo_angle_folders"][0]["path_segments"], ["1人", "人物 001"])
        self.assertEqual(document["photo_angle_folders"][0]["photos"][0]["file_name"], "one.jpg")


if __name__ == "__main__":
    unittest.main()
