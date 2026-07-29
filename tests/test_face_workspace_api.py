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


if __name__ == "__main__":
    unittest.main()
