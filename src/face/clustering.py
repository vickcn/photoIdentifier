from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
from sklearn.cluster import DBSCAN

from src.face.models import FaceRecord


logger = logging.getLogger("face.clustering")


def cluster_faces(
    records: list[FaceRecord],
    embeddings: list[np.ndarray],
    output_dir: Path,
    eps: float,
    min_samples: int,
) -> str | None:
    if not records:
        return None

    cluster_start = time.perf_counter()
    embedding_matrix = np.asarray(embeddings, dtype=np.float32)
    embeddings_path = output_dir / "embeddings.npy"
    np.save(embeddings_path, embedding_matrix)

    labels = DBSCAN(eps=eps, min_samples=min_samples, metric="euclidean").fit_predict(embedding_matrix)
    for record, label in zip(records, labels, strict=True):
        record.cluster = int(label)

    logger.info(
        "cluster.done faces=%s clusters=%s duration_ms=%s",
        len(records),
        sorted({r.cluster for r in records}),
        round((time.perf_counter() - cluster_start) * 1000, 2),
    )
    return str(embeddings_path)

