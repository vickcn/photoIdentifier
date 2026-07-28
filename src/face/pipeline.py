from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path

from src.face.annotation import save_annotated_images_in_background
from src.face.clustering import cluster_faces
from src.face.detector import collect_images, detect_faces, load_face_app


logger = logging.getLogger("face.pipeline")


def run_face_classification(
    input_path: Path,
    output_dir: Path,
    eps: float = 0.35,
    min_samples: int = 2,
    annotation_workers: int = 4,
    skip_annotations: bool = False,
) -> dict:
    job_start = time.perf_counter()
    input_path = input_path.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    image_paths = collect_images(input_path)
    logger.info("job.start input=%s output=%s images=%s", input_path, output_dir, len(image_paths))

    app = load_face_app()
    records, embedding_summaries, embeddings = detect_faces(image_paths, input_path, app)
    embeddings_file = cluster_faces(records, embeddings, output_dir, eps, min_samples)

    annotated_dir = output_dir / "annotated"
    annotated_results: list[dict] = []
    if skip_annotations:
        annotated_dir = None
        logger.info("annotated_images.skip reason=argparse_skip_annotations")
    else:
        annotated_dir, annotated_results = save_annotated_images_in_background(
            image_paths=image_paths,
            input_path=input_path,
            output_dir=output_dir,
            records=records,
            max_workers=max(1, annotation_workers),
        )

    summary = {
        "input": str(input_path),
        "total_images": len(image_paths),
        "total_faces": len(records),
        "clusters": sorted({r.cluster for r in records}),
        "embeddings_file": embeddings_file,
        "annotations_enabled": not skip_annotations,
        "annotated_dir": str(annotated_dir) if annotated_dir else None,
        "annotated_results": annotated_results,
        "embedding_summaries": [asdict(s) for s in embedding_summaries],
        "records": [asdict(r) for r in records],
    }

    result_path = output_dir / "result.json"
    result_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "job.done images=%s faces=%s duration_ms=%s result=%s",
        summary["total_images"],
        summary["total_faces"],
        round((time.perf_counter() - job_start) * 1000, 2),
        result_path,
    )
    return summary

