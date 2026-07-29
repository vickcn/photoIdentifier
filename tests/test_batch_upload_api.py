import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


class BatchUploadApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        main._active_batch_owners.clear()

    def tearDown(self):
        main._active_batch_owners.clear()
        main._batch_sessions.pop("upload-test", None)
        main._batch_sessions.pop("busy-test", None)

    def test_frontend_config_exposes_batch_upload_limits(self):
        response = self.client.get("/api/config")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["batch_upload_max_files"], main.BATCH_UPLOAD_MAX_FILES)
        self.assertEqual(response.json()["batch_upload_max_file_mb"], main.BATCH_UPLOAD_MAX_FILE_MB)
        self.assertEqual(response.json()["batch_upload_max_total_mb"], main.BATCH_UPLOAD_MAX_TOTAL_MB)
        self.assertEqual(response.json()["batch_upload_concurrency"], main.BATCH_UPLOAD_CONCURRENCY)
        self.assertEqual(response.json()["batch_download_max_mb"], main.BATCH_DOWNLOAD_MAX_MB)

    def test_load_config_prefers_env_over_config_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_config_path = Path(temp_dir) / "config.json"
            temp_config_path.write_text(
                json.dumps(
                    {
                        "batch_upload_max_files": 9,
                        "batch_upload_max_file_mb": 7,
                        "batch_upload_max_total_mb": 21,
                        "batch_upload_concurrency": 5,
                        "batch_download_max_mb": 30,
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(main, "CONFIG_PATH", temp_config_path),
                patch.dict(
                    os.environ,
                    {
                        "BATCH_UPLOAD_MAX_FILES": "3",
                        "BATCH_UPLOAD_MAX_FILE_MB": "2",
                        "BATCH_UPLOAD_MAX_TOTAL_MB": "4",
                        "BATCH_UPLOAD_CONCURRENCY": "2",
                        "BATCH_DOWNLOAD_MAX_MB": "8",
                    },
                    clear=False,
                ),
            ):
                config = main.load_config()

        self.assertEqual(config["batch_upload_max_files"], 3)
        self.assertEqual(config["batch_upload_max_file_mb"], 2)
        self.assertEqual(config["batch_upload_max_total_mb"], 4)
        self.assertEqual(config["batch_upload_concurrency"], 2)
        self.assertEqual(config["batch_download_max_mb"], 8)

    def test_face_clustering_env_overrides_config_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_config_path = Path(temp_dir) / "config.json"
            temp_config_path.write_text(
                json.dumps({"face_clustering_enabled": True}),
                encoding="utf-8",
            )
            with (
                patch.object(main, "CONFIG_PATH", temp_config_path),
                patch.dict(os.environ, {"FACE_CLUSTERING_ENABLED": "false"}, clear=False),
            ):
                config = main.load_config()

        self.assertFalse(config["face_clustering_enabled"])

    def test_batch_upload_stream_accepts_multiple_images(self):
        async def fake_batch_stream(images, **kwargs):
            self.assertEqual([image.filename for image in images], ["one.jpg", "two.jpg"])
            self.assertEqual(kwargs["concurrency"], 2)
            for index, image in enumerate(images, start=1):
                yield {
                    "status": "ok",
                    "file_name": image.filename,
                    "total": len(images),
                    "index": index,
                    "result": {"moderation_status": "public", "is_safe_for_public": True},
                    "original_image_b64": "b3JpZ2luYWw=",
                    "drawn_image_b64": "ZHJhd24=",
                }

        files = [
            ("files", ("one.jpg", b"one", "image/jpeg")),
            ("files", ("two.jpg", b"two", "image/jpeg")),
        ]
        with (
            patch.object(main, "batch_process_uploads_stream", fake_batch_stream, create=True),
            patch.object(main, "FACE_CLUSTERING_ENABLED", False, create=True),
        ):
            response = self.client.post(
                "/batch_upload_stream/",
                files=files,
                data={"concurrency": "2", "session_id": "upload-test"},
            )

        self.assertEqual(response.status_code, 200)
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual([event["status"] for event in events], ["ok", "ok", "completed"])
        self.assertTrue(all(event["session_id"] == "upload-test" for event in events))
        self.assertIn("face_clustering", events[-1])
        self.assertIn("face_clusters", events[-1])
        self.assertEqual(events[-1]["face_clustering"]["reason"], "disabled")

        own_response = self.client.get("/face_clusters/upload-test")
        other_response = TestClient(main.app).get("/face_clusters/upload-test")
        self.assertEqual(own_response.status_code, 200)
        self.assertEqual(other_response.status_code, 404)

    def test_vercel_deployment_defaults_face_clustering_on(self):
        vercel_config = json.loads((Path(__file__).parents[1] / "vercel.json").read_text(encoding="utf-8"))

        self.assertEqual(vercel_config["env"]["FACE_CLUSTERING_ENABLED"], "true")
        self.assertEqual(vercel_config["env"]["VERCEL_SUPPORT_LARGE_FUNCTIONS"], "1")

    def test_batch_upload_stream_rejects_too_many_files(self):
        files = [
            ("files", (f"{index}.jpg", b"x", "image/jpeg"))
            for index in range(main.BATCH_UPLOAD_MAX_FILES + 1)
        ]

        response = self.client.post("/batch_upload_stream/", files=files)

        self.assertEqual(response.status_code, 413)
        self.assertIn("Google 雲端", response.json()["detail"])

    def test_batch_upload_rejects_when_same_client_is_already_busy(self):
        main._active_batch_owners["owner-a"] = "existing-session"

        with patch.object(main, "_get_client_id", return_value="owner-a"):
            response = self.client.post(
                "/batch_upload_stream/",
                files=[("files", ("one.jpg", b"one", "image/jpeg"))],
                data={"session_id": "busy-test"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("另一個頁籤", response.json()["detail"])
