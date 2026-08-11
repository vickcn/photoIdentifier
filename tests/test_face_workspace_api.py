import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main


class FakeBatchStateStore:
    enabled = True

    def __init__(self):
        self.updated_clusters = []

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


class FaceWorkspaceApiTests(unittest.TestCase):
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
        self.assertEqual(
            save.call_args.args[3],
            json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"),
        )

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


if __name__ == "__main__":
    unittest.main()
