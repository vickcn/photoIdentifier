from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FaceRecord:
    file: str
    face_index: int
    bbox: list[float]
    score: float
    cluster: int


@dataclass
class EmbeddingSummary:
    file: str
    face_index: int
    dim: int
    norm: float
    preview: list[float]


@dataclass
class FaceEvidence:
    file_name: str
    face_index: int
    bbox: list[float]
    score: float
    image_b64: str | None = None


@dataclass
class FaceCluster:
    cluster_id: str
    source_cluster: int
    display_name: str
    status: str
    face_count: int
    photo_count: int
    evidence_photos: list[FaceEvidence] = field(default_factory=list)
    notes: str = ""
