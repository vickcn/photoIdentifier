import json
from typing import Literal, Optional
from datetime import datetime
from collections import defaultdict


def calculate_confusion_matrix(
    y_true: list[str],
    y_pred: list[str],
    labels: list[str] = None
) -> dict:
    """
    計算混淆矩陣（Confusion Matrix）

    Args:
        y_true: 實際標籤列表 (AI 判定)
        y_pred: 預測標籤列表 (用戶覆寫後)
        labels: 類別標籤，預設為 ["safe", "unsafe", "pending"]

    Returns:
        混淆矩陣字典，格式為 {true_label: {pred_label: count}}
    """
    if labels is None:
        labels = ["safe", "unsafe", "pending"]

    # 初始化混淆矩陣
    cm = {true_label: {pred_label: 0 for pred_label in labels} for true_label in labels}

    # 填充混淆矩陣
    for true, pred in zip(y_true, y_pred):
        if true in cm and pred in cm[true]:
            cm[true][pred] += 1

    return cm


def compute_class_metrics(
    confusion_matrix: dict,
    labels: list[str] = None
) -> dict:
    """
    根據混淆矩陣計算各類別的精準度、召回率、F1 分數

    Args:
        confusion_matrix: 混淆矩陣
        labels: 類別標籤

    Returns:
        字典，包含各類別的 precision, recall, f1_score
    """
    if labels is None:
        labels = ["safe", "unsafe", "pending"]

    metrics = {}

    for label in labels:
        tp = confusion_matrix.get(label, {}).get(label, 0)
        fp = sum(confusion_matrix.get(other, {}).get(label, 0) for other in labels if other != label)
        fn = sum(confusion_matrix.get(label, {}).get(other, 0) for other in labels if other != label)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

        metrics[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1_score": round(f1, 4),
            "support": tp + fn
        }

    return metrics


def compute_accuracy(y_true: list[str], y_pred: list[str]) -> float:
    """
    計算整體準確度

    Args:
        y_true: 實際標籤列表
        y_pred: 預測標籤列表

    Returns:
        準確度 (0.0 - 1.0)
    """
    if len(y_true) == 0:
        return 0.0

    correct = sum(1 for true, pred in zip(y_true, y_pred) if true == pred)
    return round(correct / len(y_true), 4)


def compute_batch_metrics(
    results: list[dict],
    start_time: datetime,
    end_time: datetime,
    batch_mode: Literal["local", "drive"] = "local",
    session_id: Optional[str] = None,
    processing_info: Optional[dict] = None
) -> dict:
    """
    計算整個批次的綜合指標

    Args:
        results: 分析結果清單，每個 result 包含 ai_decision, user_decision, moderation_reason 等
        start_time: 批次開始時間
        end_time: 批次結束時間
        batch_mode: 批次模式 ("local" 或 "drive")
        session_id: 會話 ID
        processing_info: 處理資訊 (e.g., 並行數量、總檔案數)

    Returns:
        包含混淆矩陣、指標、決策分佈等的字典
    """
    labels = ["safe", "unsafe", "pending"]

    # 提取 AI 決定和用戶決定
    ai_decisions = []
    user_decisions = []
    valid_results = []

    for result in results:
        if isinstance(result, dict) and "ai_decision" in result and "user_decision" in result:
            ai_decision = result.get("ai_decision", "safe")
            user_decision = result.get("user_decision", "safe")

            # 確保決定值在有效標籤內
            if ai_decision in labels and user_decision in labels:
                ai_decisions.append(ai_decision)
                user_decisions.append(user_decision)
                valid_results.append(result)

    # 計算混淆矩陣
    cm = calculate_confusion_matrix(ai_decisions, user_decisions, labels)

    # 計算類別級指標
    class_metrics = compute_class_metrics(cm, labels)

    # 計算整體準確度
    accuracy = compute_accuracy(ai_decisions, user_decisions)

    # 計算決策分佈
    decision_dist = {label: 0 for label in labels}
    for decision in user_decisions:
        decision_dist[decision] += 1

    # AI 決定分佈
    ai_dist = {label: 0 for label in labels}
    for decision in ai_decisions:
        ai_dist[decision] += 1

    # 計算一致率（AI 和用戶決定相同的比例）
    agreement_count = sum(1 for ai, user in zip(ai_decisions, user_decisions) if ai == user)
    agreement_rate = agreement_count / len(ai_decisions) if ai_decisions else 0.0

    # 計算改變的檔案數量
    changed_count = len(ai_decisions) - agreement_count

    # 處理時間
    duration_seconds = (end_time - start_time).total_seconds()

    metrics_output = {
        "session_id": session_id,
        "batch_mode": batch_mode,
        "timestamp": {
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
            "duration_seconds": round(duration_seconds, 2)
        },
        "total_processed": len(valid_results),
        "total_errors": len(results) - len(valid_results),
        "confusion_matrix": cm,
        "metrics": {label: class_metrics.get(label, {}) for label in labels},
        "accuracy": accuracy,
        "agreement_rate": round(agreement_rate, 4),
        "changed_count": changed_count,
        "decision_distribution": {
            "user_decisions": decision_dist,
            "ai_decisions": ai_dist
        },
        "processing_info": processing_info or {}
    }

    return metrics_output


def collect_changed_files(
    results: list[dict],
    session_id: Optional[str] = None
) -> list[dict]:
    """
    收集所有用戶決定與 AI 決定不同的檔案

    Args:
        results: 分析結果清單
        session_id: 會話 ID

    Returns:
        改變的檔案清單，每筆包含 index, file_name, ai_decision, user_decision, reason, timestamp, drive_id
    """
    changed_files = []

    for index, result in enumerate(results, start=1):
        if isinstance(result, dict):
            ai_decision = result.get("ai_decision", "safe")
            user_decision = result.get("user_decision", "safe")

            # 只收集被改變的檔案
            if ai_decision != user_decision:
                changed_file = {
                    "index": index,
                    "file_name": result.get("file_name", result.get("file", "unknown")),
                    "ai_decision": ai_decision,
                    "user_decision": user_decision,
                    "reason": result.get("moderation_reason", ""),
                    "timestamp": result.get("timestamp", datetime.now().isoformat()),
                    "drive_id": result.get("drive_id")
                }
                changed_files.append(changed_file)

    return changed_files


def compute_analysis_stats(results: list[dict]) -> dict:
    """
    計算分析統計信息

    Args:
        results: 分析結果清單

    Returns:
        統計信息字典，包含各類特徵的檢測率
    """
    if not results:
        return {
            "total_images": 0,
            "images_with_faces": 0,
            "images_with_straps": 0,
            "images_with_unsafe_straps": 0,
            "images_with_children_issues": 0,
            "faces_detected": 0,
            "straps_detected": 0,
            "average_faces_per_image": 0.0,
            "average_straps_per_image": 0.0
        }

    stats = {
        "total_images": 0,
        "images_with_faces": 0,
        "images_with_straps": 0,
        "images_with_unsafe_straps": 0,
        "images_with_children_issues": 0,
        "faces_detected": 0,
        "straps_detected": 0,
    }

    for result in results:
        if isinstance(result, dict) and "result" in result:
            analysis = result["result"]
            stats["total_images"] += 1

            if analysis.get("has_face"):
                stats["images_with_faces"] += 1
                stats["faces_detected"] += len(analysis.get("face_bboxes", []))

            if analysis.get("has_brand_strap"):
                stats["images_with_straps"] += 1
                stats["straps_detected"] += len(analysis.get("strap_bboxes", []))

            if analysis.get("moderation_status") == "private":
                stats["images_with_unsafe_straps"] += 1

            if analysis.get("moderation_status") == "pending":
                stats["images_with_children_issues"] += 1

    # 計算平均值
    if stats["total_images"] > 0:
        stats["average_faces_per_image"] = round(
            stats["faces_detected"] / stats["total_images"], 2
        )
        stats["average_straps_per_image"] = round(
            stats["straps_detected"] / stats["total_images"], 2
        )

    return stats


def format_metrics_for_export(metrics: dict, stats: dict) -> str:
    """
    格式化指標以供 JSON 匯出

    Args:
        metrics: compute_batch_metrics 的輸出
        stats: compute_analysis_stats 的輸出

    Returns:
        JSON 格式的字串
    """
    export_data = {
        "metrics": metrics,
        "analysis_stats": stats
    }
    return json.dumps(export_data, ensure_ascii=False, indent=2)
