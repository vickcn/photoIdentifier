import unittest
from unittest.mock import patch

import httpx

from src.insight_api_client import InsightApiClient


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://insight.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)


class InsightApiClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_cluster_uses_queued_job_endpoint(self):
        calls = []

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def post(self, path, **kwargs):
                calls.append(("POST", path, kwargs))
                return FakeResponse(202, {"job_id": "job_123", "status": "queued"})

            async def get(self, path, **kwargs):
                calls.append(("GET", path, kwargs))
                return FakeResponse(200, {"status": "success", "result": {"images": []}})

        with patch("src.insight_api_client.httpx.AsyncClient", FakeAsyncClient):
            result = await InsightApiClient("https://insight.test", "secret").cluster(
                [("one.jpg", b"image", "image/jpeg")],
                eps=0.35,
                min_samples=2,
            )

        self.assertEqual(result, {"images": []})
        self.assertEqual(calls[0][0:2], ("POST", "/v1/faces/cluster/jobs"))
        self.assertEqual(calls[0][2]["params"], {"eps": 0.35, "min_samples": 2})
        self.assertEqual(calls[1][0:2], ("GET", "/v1/faces/cluster/jobs/job_123"))

    async def test_cluster_raises_when_job_failed(self):
        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def post(self, path, **kwargs):
                return FakeResponse(202, {"job_id": "job_failed", "status": "queued"})

            async def get(self, path, **kwargs):
                return FakeResponse(200, {"status": "failed", "error_message": "quota"})

        with patch("src.insight_api_client.httpx.AsyncClient", FakeAsyncClient):
            with self.assertRaisesRegex(RuntimeError, "quota"):
                await InsightApiClient("https://insight.test", "secret").cluster(
                    [("one.jpg", b"image", "image/jpeg")]
                )

    async def test_cluster_retries_transient_poll_connect_timeout(self):
        calls = []
        progress_events = []

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def post(self, path, **kwargs):
                return FakeResponse(
                    202,
                    {
                        "job_id": "job_retry",
                        "status": "queued",
                        "progress": {"completed": 0, "total": 2, "percent": 0},
                    },
                )

            async def get(self, path, **kwargs):
                calls.append(path)
                if len(calls) == 1:
                    request = httpx.Request("GET", "https://insight.test")
                    raise httpx.ConnectTimeout("slow", request=request)
                return FakeResponse(
                    200,
                    {
                        "job_id": "job_retry",
                        "status": "success",
                        "progress": {"completed": 2, "total": 2, "percent": 100},
                        "result": {"images": []},
                    },
                )

        async def on_progress(snapshot):
            progress_events.append(snapshot)

        with (
            patch("src.insight_api_client.httpx.AsyncClient", FakeAsyncClient),
            patch("src.insight_api_client.asyncio.sleep", return_value=None),
        ):
            result = await InsightApiClient("https://insight.test", "secret").cluster(
                [("one.jpg", b"image", "image/jpeg")],
                progress_callback=on_progress,
            )

        self.assertEqual(result, {"images": []})
        self.assertEqual(len(calls), 2)
        self.assertIn("connection_wait", [event.get("stage") for event in progress_events])


if __name__ == "__main__":
    unittest.main()
