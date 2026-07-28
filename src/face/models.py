from __future__ import annotations

from dataclasses import dataclass


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

