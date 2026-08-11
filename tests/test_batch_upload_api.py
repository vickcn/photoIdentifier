import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
        self.assertEqual(response.json()["batch_upload_batch_size"], main.BATCH_UPLOAD_BATCH_SIZE)
        self.assertEqual(response.json()["batch_upload_total_max_files"], main.BATCH_UPLOAD_TOTAL_MAX_FILES)
        self.assertEqual(response.json()["batch_upload_max_files"], main.BATCH_UPLOAD_TOTAL_MAX_FILES)
        self.assertEqual(response.json()["batch_upload_max_file_mb"], main.BATCH_UPLOAD_MAX_FILE_MB)
        self.assertEqual(response.json()["batch_upload_max_total_mb"], main.BATCH_UPLOAD_MAX_TOTAL_MB)
        self.assertEqual(response.json()["batch_upload_concurrency"], main.BATCH_UPLOAD_CONCURRENCY)
        self.assertEqual(response.json()["local_upload_request_max_files"], main.LOCAL_UPLOAD_REQUEST_MAX_FILES)
        self.assertEqual(response.json()["local_upload_request_max_total_mb"], main.LOCAL_UPLOAD_REQUEST_MAX_TOTAL_MB)
        self.assertEqual(response.json()["batch_download_max_mb"], main.BATCH_DOWNLOAD_MAX_MB)
        self.assertEqual(response.json()["face_cluster_default_eps"], main.DEFAULT_CLUSTER_EPS)
        self.assertEqual(response.json()["face_cluster_default_min_samples"], main.DEFAULT_CLUSTER_MIN_SAMPLES)

    def test_drive_batch_rejects_high_cloud_api_concurrency_before_auth(self):
        response = self.client.post(
            "/batch_drive_stream/",
            json={
                "folder_id": "drive-folder",
                "concurrency": main.CLOUD_API_CONCURRENCY_CAP + 1,
                "run_public_classification": False,
                "run_face_clustering": True,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("雲端模式一次處理張數", response.json()["detail"])

    def test_load_config_prefers_env_over_config_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_config_path = Path(temp_dir) / "config.json"
            temp_config_path.write_text(
                json.dumps(
                    {
                        "batch_upload_batch_size": 9,
                        "batch_upload_total_max_files": 99,
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
                        "BATCH_UPLOAD_BATCH_SIZE": "3",
                        "BATCH_UPLOAD_TOTAL_MAX_FILES": "33",
                        "BATCH_UPLOAD_MAX_FILE_MB": "2",
                        "BATCH_UPLOAD_MAX_TOTAL_MB": "4",
                        "BATCH_UPLOAD_CONCURRENCY": "1",
                        "BATCH_DOWNLOAD_MAX_MB": "8",
                    },
                    clear=False,
                ),
            ):
                config = main.load_config()

        self.assertEqual(config["batch_upload_batch_size"], 3)
        self.assertEqual(config["batch_upload_total_max_files"], 33)
        self.assertEqual(config["batch_upload_max_file_mb"], 2)
        self.assertEqual(config["batch_upload_max_total_mb"], 4)
        self.assertEqual(config["batch_upload_concurrency"], 1)
        self.assertEqual(config["batch_download_max_mb"], 8)

    def test_load_config_allows_larger_batch_limits_outside_vercel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_config_path = Path(temp_dir) / "config.json"
            temp_config_path.write_text(json.dumps({}), encoding="utf-8")
            with (
                patch.object(main, "CONFIG_PATH", temp_config_path),
                patch.object(main, "CONFIG_BATCH_UPLOAD_CONCURRENCY_CAP", None),
                patch.dict(
                    os.environ,
                    {
                        "BATCH_UPLOAD_BATCH_SIZE": "30",
                        "BATCH_UPLOAD_CONCURRENCY": "20",
                    },
                    clear=False,
                ),
            ):
                config = main.load_config()

        self.assertEqual(config["batch_upload_batch_size"], 30)
        self.assertEqual(config["batch_upload_concurrency"], 20)

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

    def test_batch_upload_stream_allows_local_upload_chunks_to_complete_one_session(self):
        async def fake_batch_stream(images, **kwargs):
            for image in images:
                yield {
                    "status": "ok",
                    "file_name": image.filename,
                    "total": len(images),
                    "index": 1,
                    "result": {"moderation_status": "public", "is_safe_for_public": True},
                    "original_image_b64": "b3JpZ2luYWw=",
                    "drawn_image_b64": "ZHJhd24=",
                }

        classify_calls = []

        async def fake_classify_session_faces(session_id):
            classify_calls.append(session_id)
            self.assertEqual(
                [result["file_name"] for result in main._batch_sessions[session_id]["results"]],
                ["one.jpg", "two.jpg"],
            )
            main._batch_sessions[session_id]["face_clusters"] = []
            return {"available": True, "cluster_count": 0}

        with (
            patch.object(main, "batch_process_uploads_stream", fake_batch_stream, create=True),
            patch.object(main, "_classify_session_faces", fake_classify_session_faces),
        ):
            first_response = self.client.post(
                "/batch_upload_stream/",
                files=[("files", ("one.jpg", b"one", "image/jpeg"))],
                data={
                    "concurrency": "1",
                    "session_id": "upload-test",
                    "upload_chunk_index": "0",
                    "upload_chunk_total": "2",
                    "upload_total_files": "2",
                },
            )
            second_response = self.client.post(
                "/batch_upload_stream/",
                files=[("files", ("two.jpg", b"two", "image/jpeg"))],
                data={
                    "concurrency": "1",
                    "session_id": "upload-test",
                    "upload_chunk_index": "1",
                    "upload_chunk_total": "2",
                    "upload_total_files": "2",
                },
            )

        self.assertEqual(first_response.status_code, 200)
        first_events = [json.loads(line) for line in first_response.text.splitlines()]
        self.assertEqual([event["status"] for event in first_events], ["ok"])
        self.assertEqual(first_events[0]["index"], 1)
        self.assertEqual(first_events[0]["total"], 2)
        self.assertEqual(second_response.status_code, 200)
        second_events = [json.loads(line) for line in second_response.text.splitlines()]
        self.assertEqual([event["status"] for event in second_events], ["ok", "face_cluster_progress", "completed"])
        self.assertEqual(second_events[0]["index"], 2)
        self.assertEqual(second_events[0]["total"], 2)
        self.assertEqual(classify_calls, ["upload-test"])

    def test_batch_upload_stream_rejects_chunk_that_does_not_start_at_zero(self):
        response = self.client.post(
            "/batch_upload_stream/",
            files=[("files", ("two.jpg", b"two", "image/jpeg"))],
            data={
                "session_id": "upload-test",
                "upload_chunk_index": "1",
                "upload_chunk_total": "2",
                "upload_total_files": "2",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("第一批", response.json()["detail"])

    def test_batch_upload_stream_passes_face_cluster_params_into_session_processing(self):
        async def fake_batch_stream(images, **kwargs):
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

        async def fake_classify_session_faces(session_id):
            info = main._batch_sessions[session_id]["processing_info"]
            self.assertEqual(info["face_cluster_eps"], 0.42)
            self.assertEqual(info["face_cluster_min_samples"], 3)
            main._batch_sessions[session_id]["face_clusters"] = []
            return {"available": True, "cluster_count": 0, "eps": 0.42, "min_samples": 3}

        with (
            patch.object(main, "batch_process_uploads_stream", fake_batch_stream, create=True),
            patch.object(main, "_classify_session_faces", fake_classify_session_faces),
        ):
            response = self.client.post(
                "/batch_upload_stream/",
                files=[
                    ("files", ("one.jpg", b"one", "image/jpeg")),
                    ("files", ("two.jpg", b"two", "image/jpeg")),
                ],
                data={
                    "concurrency": "2",
                    "session_id": "upload-test",
                    "face_cluster_eps": "0.42",
                    "face_cluster_min_samples": "3",
                },
            )

        self.assertEqual(response.status_code, 200)
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(events[-1]["face_clustering"]["eps"], 0.42)
        self.assertEqual(events[-1]["face_clustering"]["min_samples"], 3)

    def test_batch_upload_can_run_face_clustering_without_public_classification(self):
        classify_calls = []

        async def fake_batch_stream(images, **kwargs):
            self.assertFalse(kwargs["evaluate_public"])
            yield {
                "status": "ok",
                "file_name": images[0].filename,
                "total": 1,
                "index": 1,
                "result": {
                    "moderation_status": "pending",
                    "is_safe_for_public": False,
                    "public_classification_performed": False,
                },
                "original_image_b64": "b3JpZ2luYWw=",
                "drawn_image_b64": "b3JpZ2luYWw=",
            }

        async def fake_classify_session_faces(session_id):
            classify_calls.append(session_id)
            main._batch_sessions[session_id]["face_clusters"] = []
            return {"available": True, "cluster_count": 0}

        with (
            patch.object(main, "batch_process_uploads_stream", fake_batch_stream),
            patch.object(main, "_classify_session_faces", fake_classify_session_faces),
        ):
            response = self.client.post(
                "/batch_upload_stream/",
                files=[("files", ("one.jpg", b"one", "image/jpeg"))],
                data={
                    "session_id": "upload-test",
                    "run_public_classification": "false",
                    "run_face_clustering": "true",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(classify_calls, ["upload-test"])
        self.assertFalse(main._batch_sessions["upload-test"]["processing_info"]["run_public_classification"])

    def test_batch_upload_can_run_public_classification_without_face_clustering(self):
        async def fake_batch_stream(images, **kwargs):
            self.assertTrue(kwargs["evaluate_public"])
            yield {
                "status": "ok",
                "file_name": images[0].filename,
                "total": 1,
                "index": 1,
                "result": {"moderation_status": "public", "is_safe_for_public": True},
                "original_image_b64": "b3JpZ2luYWw=",
                "drawn_image_b64": "ZHJhd24=",
            }

        with (
            patch.object(main, "_require_feature", new=AsyncMock(return_value={})),
            patch.object(main, "batch_process_uploads_stream", fake_batch_stream),
            patch.object(main, "_classify_session_faces") as classify,
        ):
            response = self.client.post(
                "/batch_upload_stream/",
                files=[("files", ("one.jpg", b"one", "image/jpeg"))],
                data={
                    "session_id": "upload-test",
                    "run_public_classification": "true",
                    "run_face_clustering": "false",
                },
            )

        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(response.status_code, 200)
        classify.assert_not_called()
        self.assertEqual(events[-1]["face_clustering"]["reason"], "not_requested")
        self.assertEqual(events[-1]["face_clusters"], [])

    def test_batch_upload_requires_at_least_one_processing_feature(self):
        response = self.client.post(
            "/batch_upload_stream/",
            files=[("files", ("one.jpg", b"one", "image/jpeg"))],
            data={
                "run_public_classification": "false",
                "run_face_clustering": "false",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("至少選擇一項", response.json()["detail"])

    def test_vercel_deployment_defaults_face_clustering_on(self):
        vercel_config = json.loads((Path(__file__).parents[1] / "vercel.json").read_text(encoding="utf-8"))

        self.assertEqual(vercel_config["env"]["FACE_CLUSTERING_ENABLED"], "true")
        self.assertEqual(vercel_config["env"]["VERCEL_SUPPORT_LARGE_FUNCTIONS"], "1")

    def test_batch_upload_stream_rejects_too_many_files_over_total_limit(self):
        files = [
            ("files", (f"{index}.jpg", b"x", "image/jpeg"))
            for index in range(main.BATCH_UPLOAD_TOTAL_MAX_FILES + 1)
        ]

        response = self.client.post("/batch_upload_stream/", files=files)

        self.assertEqual(response.status_code, 413)
        self.assertIn("一次最多上傳", response.json()["detail"])

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
