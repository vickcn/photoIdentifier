import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


class FaceWorkspaceApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
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
        main._batch_sessions.pop("face-workspace-test", None)

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


if __name__ == "__main__":
    unittest.main()
