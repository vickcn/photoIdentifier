from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2

from src.face.detector import file_key_for_image
from src.face.models import FaceRecord


logger = logging.getLogger("face.annotation")


def face_color(cluster: int) -> tuple[int, int, int]:
    if cluster < 0:
        return (40, 40, 220)

    palette = [
        (40, 180, 40),
        (220, 120, 40),
        (40, 180, 220),
        (200, 80, 200),
        (180, 180, 40),
        (80, 120, 240),
    ]
    return palette[cluster % len(palette)]


def output_path_for_image(image_path: Path, input_path: Path, annotated_dir: Path) -> Path:
    if input_path.is_file():
        return annotated_dir / f"{image_path.stem}_annotated.jpg"

    return annotated_dir / image_path.relative_to(input_path)


def draw_and_save_one(
    image_path: Path,
    input_path: Path,
    annotated_dir: Path,
    records: list[FaceRecord],
) -> dict:
    start = time.perf_counter()
    file_key = file_key_for_image(image_path, input_path)
    out_path = output_path_for_image(image_path, input_path, annotated_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"圖片讀取失敗: {image_path}")

    for record in records:
        x1, y1, x2, y2 = [int(round(v)) for v in record.bbox]
        color = face_color(record.cluster)
        label = f"face {record.face_index} / cluster {record.cluster} / {record.score:.2f}"
        cv2.rectangle(image, (x1, y1), (x2, y2), color, 3)
        cv2.putText(
            image,
            label,
            (x1, max(24, y1 - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            color,
            2,
            cv2.LINE_AA,
        )

    if not cv2.imwrite(str(out_path), image):
        raise RuntimeError(f"後製圖寫入失敗: {out_path}")

    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    return {
        "file": file_key,
        "output": str(out_path),
        "face_count": len(records),
        "duration_ms": duration_ms,
    }


def save_annotated_images_in_background(
    image_paths: list[Path],
    input_path: Path,
    output_dir: Path,
    records: list[FaceRecord],
    max_workers: int,
) -> tuple[Path, list[dict]]:
    annotated_dir = output_dir / "annotated"
    annotated_dir.mkdir(parents=True, exist_ok=True)

    records_by_file: dict[str, list[FaceRecord]] = {}
    for record in records:
        records_by_file.setdefault(record.file, []).append(record)

    logger.info(
        "annotated_images.dispatch images=%s workers=%s output=%s",
        len(image_paths),
        max_workers,
        annotated_dir,
    )

    start = time.perf_counter()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="annotated-image") as executor:
        futures = [
            executor.submit(
                draw_and_save_one,
                image_path,
                input_path,
                annotated_dir,
                records_by_file.get(file_key_for_image(image_path, input_path), []),
            )
            for image_path in image_paths
        ]

        logger.info("annotated_images.wait pending=%s", len(futures))
        for future in as_completed(futures):
            try:
                result = future.result()
                results.append({**result, "status": "ok"})
                logger.info(
                    "annotated_images.saved file=%s faces=%s duration_ms=%s output=%s",
                    result["file"],
                    result["face_count"],
                    result["duration_ms"],
                    result["output"],
                )
            except Exception as exc:
                results.append({"status": "error", "error": str(exc)})
                logger.exception("annotated_images.failed error=%s", exc)

    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    success_count = sum(1 for item in results if item["status"] == "ok")
    failed_count = len(results) - success_count
    logger.info(
        "annotated_images.done total=%s success=%s failed=%s duration_ms=%s",
        len(results),
        success_count,
        failed_count,
        duration_ms,
    )
    return annotated_dir, results

