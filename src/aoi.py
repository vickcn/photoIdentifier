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

    # 動態計算字體大小 (約圖片高度的 5%)
    font_size = max(28, int(height * 0.05))
    try:
        # 優先嘗試載入 TrueType 字體以支援縮放 (macOS 路徑)
        from PIL import ImageFont
        font = ImageFont.truetype("/Library/Fonts/Arial.ttf", font_size)
    except:
        try:
            # Linux (Vercel/Docker) 常用路徑
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
        except:
            font = ImageFont.load_default()

    def draw_box(bbox, label, outline_color):
        if not bbox or len(bbox) != 4:
            return
        ymin_norm, xmin_norm, ymax_norm, xmax_norm = bbox
        ymin = int(ymin_norm / 1000.0 * height)
        xmin = int(xmin_norm / 1000.0 * width)
        ymax = int(ymax_norm / 1000.0 * height)
        xmax = int(xmax_norm / 1000.0 * width)
        
        # 畫框
        draw.rectangle([xmin, ymin, xmax, ymax], outline=outline_color, width=max(3, int(width/300)))
        
        # 畫文字背景並寫字
        text_bbox = draw.textbbox((xmin, ymin), label, font=font)
        draw.rectangle([text_bbox[0], text_bbox[1]-5, text_bbox[2]+10, text_bbox[3]+5], fill=outline_color)
        draw.text((xmin, ymin - (text_bbox[3]-text_bbox[1]) - 5), label, fill="white", font=font)

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

