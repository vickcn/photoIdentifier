from __future__ import annotations

import io
import logging
import time
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from src.face.models import EmbeddingSummary, FaceRecord


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
logger = logging.getLogger("face.detector")


def collect_images(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in IMAGE_SUFFIXES else []

    return [
        candidate
        for candidate in sorted(path.rglob("*"))
        if candidate.is_file() and candidate.suffix.lower() in IMAGE_SUFFIXES
    ]


def file_key_for_image(image_path: Path, input_path: Path) -> str:
    return image_path.name if input_path.is_file() else str(image_path.relative_to(input_path))


@lru_cache(maxsize=1)
def load_face_app():
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name="buffalo_l")
    app.prepare(ctx_id=-1, det_size=(640, 640))
    return app


def detect_face_bboxes_from_image_bytes(image_bytes: bytes) -> list[list[int]]:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    image_np = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    height, width = image_np.shape[:2]
    faces = load_face_app().get(image_np)

    face_bboxes: list[list[int]] = []
    for face in faces:
        x1, y1, x2, y2 = face.bbox.tolist()
        face_bboxes.append([
            int(round(y1 / height * 1000)),
            int(round(x1 / width * 1000)),
            int(round(y2 / height * 1000)),
            int(round(x2 / width * 1000)),
        ])

    return face_bboxes


def detect_faces(
    image_paths: list[Path],
    input_path: Path,
    app,
) -> tuple[list[FaceRecord], list[EmbeddingSummary], list[np.ndarray]]:
    records: list[FaceRecord] = []
    embedding_summaries: list[EmbeddingSummary] = []
    embeddings: list[np.ndarray] = []

    for image_path in image_paths:
        image_start = time.perf_counter()
        image = cv2.imread(str(image_path))
        if image is None:
            logger.warning("image.skip unreadable=%s", image_path)
            continue

        faces = app.get(image)
        file_key = file_key_for_image(image_path, input_path)
        logger.info(
            "image.detected file=%s faces=%s duration_ms=%s",
            file_key,
            len(faces),
            round((time.perf_counter() - image_start) * 1000, 2),
        )

        for idx, face in enumerate(faces):
            embedding = np.asarray(face.normed_embedding, dtype=np.float32)
            embeddings.append(embedding)
            records.append(
                FaceRecord(
                    file=file_key,
                    face_index=idx,
                    bbox=[float(x) for x in face.bbox.tolist()],
                    score=float(face.det_score),
                    cluster=-1,
                )
            )
            embedding_summaries.append(
                EmbeddingSummary(
                    file=file_key,
                    face_index=idx,
                    dim=int(embedding.shape[0]),
                    norm=float(np.linalg.norm(embedding)),
                    preview=[float(x) for x in embedding[:8].tolist()],
                )
            )

    return records, embedding_summaries, embeddings
