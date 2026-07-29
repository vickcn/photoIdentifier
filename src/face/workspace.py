from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import asdict
from typing import Callable
from pathlib import Path

import numpy as np

from src.face.models import FaceCluster, FaceEvidence, FaceRecord


def build_face_clusters(
    records: list[FaceRecord],
    images_by_file: Mapping[str, dict],
) -> list[FaceCluster]:
    grouped: dict[tuple[str, int], list[FaceRecord]] = {}
    for record_index, record in enumerate(records):
        # DBSCAN uses -1 for every noise point; each noise face must remain distinct.
        key = ("cluster", record.cluster) if record.cluster >= 0 else ("noise", record_index)
        grouped.setdefault(key, []).append(record)

    clusters: list[FaceCluster] = []
    for display_index, group_records in enumerate(grouped.values(), start=1):
        evidence = []
        for record in group_records:
            image = images_by_file.get(record.file, {})
            evidence.append(
                FaceEvidence(
                    file_name=str(image.get("file_name") or image.get("file") or record.file),
                    face_index=record.face_index,
                    bbox=record.bbox,
                    score=record.score,
                    image_b64=image.get("original_image_b64"),
                )
            )

        clusters.append(
            FaceCluster(
                cluster_id=f"cluster_{display_index:03d}",
                source_cluster=group_records[0].cluster,
                display_name=f"人物 {display_index:03d}",
                status="unconfirmed",
                face_count=len(group_records),
                photo_count=len({item.file for item in group_records}),
                evidence_photos=evidence,
            )
        )
    return clusters


def classify_batch_results(
    results: list[dict],
    *,
    detector: Callable[[bytes], list[tuple[list[float], float, np.ndarray]]] | None = None,
    eps: float = 0.35,
    min_samples: int = 2,
) -> list[FaceCluster]:
    if detector is None:
        from src.face.detector import detect_face_features_from_image_bytes

        detector = detect_face_features_from_image_bytes

    records: list[FaceRecord] = []
    embeddings: list[np.ndarray] = []
    images_by_file: dict[str, dict] = {}
    for result_index, result in enumerate(results, start=1):
        image_b64 = result.get("original_image_b64")
        original_path = result.get("original_path")
        if not image_b64 and not original_path:
            continue
        file_name = str(result.get("file_name") or result.get("file") or f"image_{result_index}.jpg")
        images_by_file[file_name] = result
        image_bytes = (
            base64.b64decode(image_b64, validate=True)
            if image_b64
            else Path(str(original_path)).read_bytes()
        )
        for face_index, (bbox, score, embedding) in enumerate(detector(image_bytes)):
            records.append(FaceRecord(file_name, face_index, bbox, score, -1))
            embeddings.append(embedding)

    if records:
        from src.face.clustering import assign_cluster_labels

        assign_cluster_labels(records, embeddings, eps, min_samples)
    return build_face_clusters(records, images_by_file)


def serialize_face_clusters(clusters: list[FaceCluster]) -> list[dict]:
    return [asdict(cluster) for cluster in clusters]
