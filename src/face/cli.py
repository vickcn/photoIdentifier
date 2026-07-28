from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from src.face.pipeline import run_face_classification


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="人臉偵測與分群測試")
    parser.add_argument("-i", "--input", required=True, help="圖片檔案或資料夾")
    parser.add_argument("-o", "--output", required=True, help="輸出資料夾")
    parser.add_argument("-e", "--eps", type=float, default=0.35, help="DBSCAN eps")
    parser.add_argument("-m", "--min-samples", type=int, default=2, help="DBSCAN min_samples")
    parser.add_argument(
        "-w",
        "--annotation-workers",
        type=int,
        default=min(4, os.cpu_count() or 1),
        help="後製圖背景存圖 thread 數",
    )
    parser.add_argument(
        "-s",
        "--skip-annotations",
        action="store_true",
        help="不輸出後製圖，只產生 result.json 與 embeddings.npy",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    summary = run_face_classification(
        input_path=Path(args.input),
        output_dir=Path(args.output),
        eps=args.eps,
        min_samples=args.min_samples,
        annotation_workers=args.annotation_workers,
        skip_annotations=args.skip_annotations,
    )
    print(json.dumps({k: summary[k] for k in ("total_images", "total_faces", "clusters")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
