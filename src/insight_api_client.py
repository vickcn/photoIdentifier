from __future__ import annotations

import base64
import io
import logging
import os
from collections.abc import Sequence
from pathlib import Path

import httpx
from PIL import Image

DEFAULT_CLUSTER_EPS = 0.9
DEFAULT_CLUSTER_MIN_SAMPLES = 2
logger = logging.getLogger(__name__)


class InsightApiClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("INSIGHT_API_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("INSIGHT_API_KEY", "")
        if not self.base_url or not self.api_key:
            raise RuntimeError("必須設定 INSIGHT_API_URL 與 INSIGHT_API_KEY")

    async def detect(self, image_bytes: bytes, file_name: str, content_type: str = "image/jpeg") -> dict:
        return await self._post(
            "/v1/faces/detect",
            files={"file": (file_name, image_bytes, content_type)},
        )

    async def cluster(
        self,
        images: Sequence[tuple[str, bytes, str]],
        *,
        eps: float = DEFAULT_CLUSTER_EPS,
        min_samples: int = DEFAULT_CLUSTER_MIN_SAMPLES,
    ) -> dict:
        files = [("files", (name, data, content_type)) for name, data, content_type in images]
        return await self._post(
            "/v1/faces/cluster",
            files=files,
            params={"eps": eps, "min_samples": min_samples},
        )

    async def _post(self, path: str, **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = httpx.Timeout(connect=10, read=300, write=300, pool=10)
        async with httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=timeout) as client:
            response = await client.post(path, **kwargs)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                body = response.text[:500]
                logger.warning(
                    "Insight API failed path=%s status=%s body=%s",
                    path,
                    response.status_code,
                    body,
                )
                raise RuntimeError(f"Insight API HTTP {response.status_code}: {body}") from exc
            return response.json()


async def detect_normalized_bboxes(
    image_bytes: bytes,
    file_name: str = "image.jpg",
    content_type: str = "image/jpeg",
) -> list[list[int]]:
    result = await InsightApiClient().detect(image_bytes, file_name, content_type)
    with Image.open(io.BytesIO(image_bytes)) as image:
        width, height = image.size
    return [
        [
            int(round(face["bbox"][1] / height * 1000)),
            int(round(face["bbox"][0] / width * 1000)),
            int(round(face["bbox"][3] / height * 1000)),
            int(round(face["bbox"][2] / width * 1000)),
        ]
        for face in result.get("faces", [])
    ]


async def cluster_batch_results(
    results: list[dict],
    *,
    eps: float = DEFAULT_CLUSTER_EPS,
    min_samples: int = DEFAULT_CLUSTER_MIN_SAMPLES,
) -> list[dict]:
    images: list[tuple[str, bytes, str]] = []
    source_by_name: dict[str, dict] = {}
    for index, result in enumerate(results, start=1):
        image_b64 = result.get("original_image_b64")
        original_path = result.get("original_path")
        if not image_b64 and not original_path:
            continue
        file_name = str(result.get("file_name") or result.get("file") or f"image_{index}.jpg")
        image_bytes = (
            base64.b64decode(image_b64, validate=True)
            if image_b64
            else Path(str(original_path)).read_bytes()
        )
        images.append((file_name, image_bytes, "image/jpeg"))
        source_by_name[file_name] = result

    if not images:
        return []
    response = await InsightApiClient().cluster(images, eps=eps, min_samples=min_samples)
    grouped: dict[tuple[str, int], list[dict]] = {}
    noise_index = 0
    for image in response.get("images", []):
        file_name = image["file_name"]
        for face in image.get("faces", []):
            source_cluster = int(face["cluster"])
            if source_cluster >= 0:
                key = ("cluster", source_cluster)
            else:
                noise_index += 1
                key = ("noise", noise_index)
            grouped.setdefault(key, []).append({**face, "file_name": file_name})

    clusters = []
    for display_index, faces in enumerate(grouped.values(), start=1):
        clusters.append(
            {
                "cluster_id": f"cluster_{display_index:03d}",
                "source_cluster": faces[0]["cluster"],
                "display_name": f"人物 {display_index:03d}",
                "status": "unconfirmed",
                "face_count": len(faces),
                "photo_count": len({face["file_name"] for face in faces}),
                "evidence_photos": [
                    {
                        "file_name": face["file_name"],
                        "face_index": face["face_index"],
                        "bbox": face["bbox"],
                        "score": face["score"],
                        "image_b64": source_by_name[face["file_name"]].get("original_image_b64"),
                    }
                    for face in faces
                ],
                "notes": "",
            }
        )
    return clusters
