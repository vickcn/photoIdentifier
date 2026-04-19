import io
from PIL import Image, ImageDraw

def draw_bboxes_on_image(
    image_bytes: bytes,
    face_bboxes: list,
    strap_bboxes: list,
    strap_color: str | None,
    face_confidences: list = [],
    strap_confidences: list = [],
) -> bytes:
    """
    讀取原始圖片，根據多個 0-1000 的正規化 bbox 座標畫框，並轉回 bytes 回傳
    """
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size

    def draw_box(bbox, label, outline_color):
        if not bbox or len(bbox) != 4:
            return
        ymin_norm, xmin_norm, ymax_norm, xmax_norm = bbox
        ymin = int(ymin_norm / 1000.0 * height)
        xmin = int(xmin_norm / 1000.0 * width)
        ymax = int(ymax_norm / 1000.0 * height)
        xmax = int(xmax_norm / 1000.0 * width)
        draw.rectangle([xmin, ymin, xmax, ymax], outline=outline_color, width=5)
        draw.text((xmin, max(0, ymin - 15)), label, fill=outline_color)

    # 1. 繪製所有人臉 (紅框)
    for i, bbox in enumerate(face_bboxes):
        conf = face_confidences[i] if i < len(face_confidences) else None
        label = f"Face {conf:.0%}" if conf is not None else "Face"
        draw_box(bbox, label, "red")

    # 2. 繪製所有帶子 (黃框)
    for i, bbox in enumerate(strap_bboxes):
        conf = strap_confidences[i] if i < len(strap_confidences) else None
        label = "Strap"
        if strap_color:
            label += f" ({strap_color})"
        if conf is not None:
            label += f" {conf:.0%}"
        draw_box(bbox, label, "yellow")

    out_io = io.BytesIO()
    image.save(out_io, format="JPEG")
    return out_io.getvalue()

