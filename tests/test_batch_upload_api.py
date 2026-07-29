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

    def test_frontend_config_exposes_batch_upload_limits(self):
        response = self.client.get("/api/config")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["batch_upload_max_files"], main.BATCH_UPLOAD_MAX_FILES)
        self.assertEqual(response.json()["batch_upload_max_file_mb"], main.BATCH_UPLOAD_MAX_FILE_MB)
        self.assertEqual(response.json()["batch_upload_max_total_mb"], main.BATCH_UPLOAD_MAX_TOTAL_MB)
        self.assertEqual(response.json()["batch_upload_concurrency"], main.BATCH_UPLOAD_CONCURRENCY)

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
                    },
                    clear=False,
                ),
            ):
                config = main.load_config()

        self.assertEqual(config["batch_upload_max_files"], 3)
        self.assertEqual(config["batch_upload_max_file_mb"], 2)
        self.assertEqual(config["batch_upload_max_total_mb"], 4)
        self.assertEqual(config["batch_upload_concurrency"], 2)

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
        with patch.object(main, "batch_process_uploads_stream", fake_batch_stream, create=True):
            response = self.client.post(
                "/batch_upload_stream/",
                files=files,
                data={"concurrency": "2", "session_id": "upload-test"},
            )

        self.assertEqual(response.status_code, 200)
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual([event["status"] for event in events], ["ok", "ok", "completed"])
        self.assertTrue(all(event["session_id"] == "upload-test" for event in events))

    def test_batch_upload_stream_rejects_too_many_files(self):
        files = [
            ("files", (f"{index}.jpg", b"x", "image/jpeg"))
            for index in range(main.BATCH_UPLOAD_MAX_FILES + 1)
        ]

        response = self.client.post("/batch_upload_stream/", files=files)

        self.assertEqual(response.status_code, 413)
        self.assertIn("Google 雲端", response.json()["detail"])
