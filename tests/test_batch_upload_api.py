import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from starlette.requests import Request

import main


class BatchUploadApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        main._active_batch_owners.clear()

    def tearDown(self):
        main._active_batch_owners.clear()
        main._batch_sessions.pop("upload-test", None)
        main._batch_sessions.pop("busy-test", None)
        main._batch_sessions.pop("drive-status-test", None)
        main._batch_sessions.pop("drive-face-timeout-test", None)

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

    def test_externalize_result_previews_replaces_base64_with_storage_urls(self):
        result = {
            "status": "ok",
            "file_name": "group photo.jpg",
            "original_image_b64": "b3JpZ2luYWw=",
            "drawn_image_b64": "ZHJhd24=",
        }
        uploaded = []

        def fake_upload(bucket_name, object_name, content, content_type):
            uploaded.append((bucket_name, object_name, content, content_type))
            return f"https://storage.example/{object_name}"

        externalized = main._externalize_result_previews(
            result,
            owner_id="owner/one",
            session_id="session-one",
            upload_preview=fake_upload,
        )

        self.assertNotIn("original_image_b64", externalized)
        self.assertNotIn("drawn_image_b64", externalized)
        self.assertEqual(
            externalized["original_preview_url"],
            "https://storage.example/previews/owner_one/session-one/group_photo_original.jpg",
        )
        self.assertEqual(
            externalized["annotated_preview_url"],
            "https://storage.example/previews/owner_one/session-one/group_photo_annotated.jpg",
        )
        self.assertEqual([entry[2] for entry in uploaded], [b"original", b"drawn"])
        self.assertEqual(externalized["usage"]["preview_object_count"], 2)
        self.assertEqual(externalized["usage"]["preview_bytes_uploaded"], len(b"original") + len(b"drawn"))

    def test_externalize_result_previews_keeps_legacy_bytes_when_upload_fails(self):
        result = {
            "status": "ok",
            "file_name": "photo.jpg",
            "original_image_b64": "b3JpZ2luYWw=",
            "drawn_image_b64": "ZHJhd24=",
        }

        def failed_upload(*_args):
            raise RuntimeError("storage unavailable")

        externalized = main._externalize_result_previews(
            result,
            owner_id="owner-one",
            session_id="session-one",
            upload_preview=failed_upload,
        )

        self.assertEqual(externalized["original_image_b64"], "b3JpZ2luYWw=")
        self.assertEqual(externalized["drawn_image_b64"], "ZHJhd24=")
        self.assertNotIn("original_preview_url", externalized)

    def test_storage_export_usage_metrics_sums_zip_and_image_bytes(self):
        usage = main._storage_export_usage_metrics(
            zip_bytes=b"zip-content",
            image_entries=[("a.jpg", b"1234"), ("b.jpg", b"567")],
        )

        self.assertEqual(usage["storage_export_bytes"], len(b"zip-content"))
        self.assertEqual(usage["storage_export_image_bytes"], 7)
        self.assertEqual(usage["storage_export_image_count"], 2)
        self.assertEqual(usage["storage_export_count"], 1)

    def test_build_session_export_document_strips_face_image_payloads(self):
        session = {
            "session_id": "session-test",
            "batch_mode": "local",
            "results": [
                {
                    "file_name": "group.jpg",
                    "original_image_b64": "original-data",
                    "drawn_image_b64": "drawn-data",
                    "thumbnail_b64": "thumb-data",
                    "result": {"face_count": 1},
                }
            ],
            "face_clusters": [
                {
                    "cluster_id": "cluster_001",
                    "display_name": "人物 001",
                    "evidence_photos": [
                        {
                            "file_name": "group.jpg",
                            "image_b64": "face-data",
                            "thumbnail_b64": "thumb-data",
                            "source_type": "drive",
                            "source_key": "drive-123",
                        }
                    ],
                }
            ],
        }

        document = main._build_session_export_document(session)

        self.assertNotIn("original_image_b64", document["results"][0])
        self.assertNotIn("drawn_image_b64", document["results"][0])
        self.assertNotIn("thumbnail_b64", document["results"][0])
        evidence = document["face_clusters"][0]["evidence_photos"][0]
        self.assertNotIn("image_b64", evidence)
        self.assertNotIn("thumbnail_b64", evidence)
        self.assertEqual(evidence["source_type"], "drive")
        self.assertEqual(evidence["source_key"], "drive-123")

    def test_find_drive_name_memory_prefers_app_folder_then_root_fallback(self):
        calls = []

        class FakeFiles:
            def list(self, **kwargs):
                calls.append(kwargs["q"])
                if f"name = '{main.DRIVE_APP_FOLDER_NAME}'" in kwargs["q"]:
                    return SimpleNamespace(execute=lambda: {"files": []})
                if "'root' in parents" in kwargs["q"]:
                    return SimpleNamespace(execute=lambda: {"files": [{"id": "legacy-root-file"}]})
                return SimpleNamespace(execute=lambda: {"files": []})

        drive_service = SimpleNamespace(files=lambda: FakeFiles())

        folder_id = main._find_drive_app_folder_id(drive_service)
        file_id = main._find_drive_name_memory_file_id(drive_service, "root")

        self.assertIsNone(folder_id)
        self.assertEqual(file_id, "legacy-root-file")
        self.assertTrue(any(main.DRIVE_APP_FOLDER_NAME in query for query in calls))

    def test_ensure_drive_app_folder_creates_hidden_folder_under_root(self):
        create_calls = []

        class FakeFiles:
            def list(self, **kwargs):
                return SimpleNamespace(execute=lambda: {"files": []})

            def create(self, **kwargs):
                create_calls.append(kwargs["body"])
                return SimpleNamespace(execute=lambda: {"id": "folder-123"})

        drive_service = SimpleNamespace(files=lambda: FakeFiles())

        folder_id = main._ensure_drive_app_folder_id(drive_service)

        self.assertEqual(folder_id, "folder-123")
        self.assertEqual(create_calls[0]["name"], main.DRIVE_APP_FOLDER_NAME)
        self.assertEqual(create_calls[0]["parents"], ["root"])

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

    def test_drive_status_syncs_failed_face_cluster_job(self):
        main._batch_sessions["drive-face-timeout-test"] = {
            "session_id": "drive-face-timeout-test",
            "owner_id": "owner-a",
            "batch_mode": "drive",
            "status": "processing",
            "stage": "face_clustering",
            "results": [{"status": "ok", "file_name": "a.jpg"}],
            "processing_info": {
                "run_face_clustering": True,
                "drive_files": [{"id": "file-1", "name": "a.jpg", "mimeType": "image/jpeg"}],
                "drive_next_index": 1,
                "file_count": 1,
            },
            "face_cluster_job_id": "job-timeout",
            "face_cluster_progress": {
                "status": "face_cluster_progress",
                "session_id": "drive-face-timeout-test",
                "job_id": "job-timeout",
                "job_status": "running",
                "stage": "detecting",
                "progress": {"completed": 0, "total": 1, "percent": 0},
            },
            "completed": False,
        }

        with (
            patch.object(main, "_get_client_id", return_value="owner-a"),
            patch.object(
                main,
                "get_cluster_job_snapshot",
                new=AsyncMock(
                    return_value={
                        "job_id": "job-timeout",
                        "status": "failed",
                        "stage": "timeout",
                        "error_message": "running timeout after 3600s",
                        "progress": {"completed": 0, "total": 1, "percent": 0.0},
                        "total": 1,
                    }
                ),
            ),
            patch.object(main, "_persist_session_update", new=AsyncMock()) as persist_update,
        ):
            response = self.client.get("/batch_sessions/drive-face-timeout-test/status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["stage"], "failed")
        self.assertEqual(payload["error_message"], "running timeout after 3600s")
        self.assertTrue(main._batch_sessions["drive-face-timeout-test"]["completed"])
        persist_update.assert_awaited()

    def test_drive_status_marks_session_failed_when_cluster_job_disappears(self):
        main._batch_sessions["drive-status-test"] = {
            "session_id": "drive-status-test",
            "owner_id": "owner-a",
            "batch_mode": "drive",
            "status": "processing",
            "stage": "face_clustering",
            "results": [{"file_name": "photo.jpg"}],
            "processing_info": {
                "run_face_clustering": True,
                "file_count": 1,
                "drive_files": [{}],
                "drive_next_index": 1,
            },
            "completed": False,
            "face_cluster_job_id": "job-missing-123",
        }

        with (
            patch.object(main, "_get_client_id", return_value="owner-a"),
            patch.object(
                main,
                "get_cluster_job_snapshot",
                new=AsyncMock(side_effect=RuntimeError('Insight API HTTP 404: {"detail":"找不到這個辨識工作"}')),
            ),
            patch.object(main, "_persist_session_update", new=AsyncMock()) as persist_update,
            patch.object(main, "_release_batch_slot") as release_slot,
        ):
            response = self.client.get("/batch_sessions/drive-status-test/status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["stage"], "failed")
        self.assertIn("辨識工作已失效", payload["error_message"])
        self.assertIsNone(main._batch_sessions["drive-status-test"]["face_cluster_job_id"])
        persist_update.assert_awaited()
        release_slot.assert_called_once_with("owner-a", "drive-status-test")

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

    def test_batch_upload_stream_sends_preview_urls_without_embedded_image_bytes(self):
        async def fake_batch_stream(images, **_kwargs):
            yield {
                "status": "ok",
                "file_name": images[0].filename,
                "total": 1,
                "index": 1,
                "result": {"moderation_status": "pending"},
                "original_image_b64": "b3JpZ2luYWw=",
                "drawn_image_b64": "ZHJhd24=",
            }

        def fake_externalize(result, **_kwargs):
            return {
                key: value
                for key, value in {
                    **result,
                    "original_preview_url": "https://storage.example/original.jpg",
                    "annotated_preview_url": "https://storage.example/annotated.jpg",
                }.items()
                if key not in {"original_image_b64", "drawn_image_b64"}
            }

        with (
            patch.object(main, "batch_process_uploads_stream", fake_batch_stream, create=True),
            patch.object(main, "_externalize_result_previews", fake_externalize),
            patch.object(main, "FACE_CLUSTERING_ENABLED", False, create=True),
        ):
            response = self.client.post(
                "/batch_upload_stream/",
                files=[("files", ("one.jpg", b"one", "image/jpeg"))],
                data={"concurrency": "1", "session_id": "upload-test"},
            )

        first_event = json.loads(response.text.splitlines()[0])
        self.assertNotIn("original_image_b64", first_event)
        self.assertNotIn("drawn_image_b64", first_event)
        self.assertEqual(first_event["original_preview_url"], "https://storage.example/original.jpg")
        self.assertEqual(first_event["annotated_preview_url"], "https://storage.example/annotated.jpg")

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

    def test_batch_upload_stream_auto_sends_email_when_google_user_is_logged_in(self):
        async def fake_batch_stream(images, **kwargs):
            yield {
                "status": "ok",
                "file_name": images[0].filename,
                "total": 1,
                "index": 1,
                "result": {"moderation_status": "public", "is_safe_for_public": True},
                "original_image_b64": "b3JpZ2luYWw=",
                "drawn_image_b64": "ZHJhd24=",
            }

        fake_store = AsyncMock()
        fake_store.enabled = True
        fake_store.create_export_record.return_value = "export-auto-1"
        credentials = object()
        async def fake_classify_session_faces(session_id):
            main._batch_sessions[session_id]["face_clusters"] = []
            return {"available": True, "cluster_count": 0}

        with (
            patch.object(main, "batch_state_store", fake_store),
            patch.object(main, "_get_batch_user_account", return_value="user@example.com"),
            patch.object(main, "_get_google_user_id", return_value="google-1"),
            patch.object(main, "get_drive_credentials", return_value=credentials),
            patch.object(main, "batch_process_uploads_stream", fake_batch_stream),
            patch.object(main, "_classify_session_faces", fake_classify_session_faces),
            patch.object(main, "_upload_storage_export"),
            patch.object(
                main,
                "_generate_storage_signed_url",
                return_value=("https://signed.example/export.zip", "2026-08-11T10:00:00+00:00"),
            ),
            patch.object(main, "_send_gmail_notification") as send_mail,
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
        send_mail.assert_called_once()
        self.assertEqual(send_mail.call_args.args[0], credentials)
        self.assertEqual(send_mail.call_args.args[1], "user@example.com")
        self.assertIn("/batch_exports/storage/export-auto-1/download", send_mail.call_args.args[3])
        self.assertEqual(fake_store.create_export_record.call_args.args[0], "upload-test")

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


class FeatureGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_require_feature_rejects_unauthenticated_with_login_message(self):
        request = Request({"type": "http", "session": {}})
        with patch.object(main, "_get_google_userinfo", return_value=None):
            with self.assertRaises(main.HTTPException) as ctx:
                await main._require_feature(request, "export_results", "ignored")

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, main.LOGIN_REQUIRED_DETAIL)

    async def test_require_feature_keeps_feature_specific_message_when_disabled(self):
        request = Request({"type": "http", "session": {}})
        user = {"google_user_id": "user-1", "enabled": True, "features": {"export_results": False}}
        with patch.object(main, "_get_google_userinfo", return_value={"id": "user-1", "email": "a@example.com"}):
            with patch.object(main, "_get_or_create_current_user_record", new=AsyncMock(return_value=user)):
                with self.assertRaises(main.HTTPException) as ctx:
                    await main._require_feature(request, "export_results", "此帳號尚未開放匯出辨識結果功能")

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "此帳號尚未開放匯出辨識結果功能")

    async def test_notify_completed_batch_session_skips_when_auto_email_disabled(self):
        main._batch_sessions["notify-pref-test"] = {
            "session_id": "notify-pref-test",
            "owner_id": "owner-a",
            "user_account": "user@example.com",
            "google_user_id": "google-1",
            "results": [],
        }
        request = Request({"type": "http", "session": {}})
        try:
            with patch.object(main.user_store, "get_user", new=AsyncMock(return_value={"preferences": {"auto_email_results": False}})):
                with patch.object(main, "_persist_session_update", new=AsyncMock()) as persist_update:
                    result = await main._notify_completed_batch_session("notify-pref-test", request)
        finally:
            main._batch_sessions.pop("notify-pref-test", None)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "auto_email_disabled")
        persist_update.assert_awaited_once()


class UserPreferencesApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_update_user_preferences_persists_auto_email_toggle(self):
        updated_user = {
            "google_user_id": "google-1",
            "email": "user@example.com",
            "name": "User",
            "picture": "",
            "enabled": True,
            "features": {},
            "preferences": {"auto_email_results": False},
        }
        with (
            patch.object(main, "_get_google_userinfo", return_value={"id": "google-1", "email": "user@example.com"}),
            patch.object(main.user_store, "update_preferences", new=AsyncMock(return_value=updated_user)) as update_preferences,
        ):
            response = self.client.patch("/api/user/preferences", json={"auto_email_results": False})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["preferences"]["auto_email_results"], False)
        update_preferences.assert_awaited_once_with("google-1", {"auto_email_results": False})
