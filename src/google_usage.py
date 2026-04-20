import os
import json
import re
import httpx
import logging
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, ValidationError
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# 讀取 config.json 設定
_CONFIG_PATH = Path(__file__).parent.parent / "config.json"
try:
    with _CONFIG_PATH.open(encoding="utf-8") as _f:
        _cfg = json.load(_f)
    REQUEST_TIMEOUT = int(_cfg.get("request_timeout", 180))
except Exception:
    REQUEST_TIMEOUT = 180

# 設定 API 金鑰與端點
VERTEX_API_KEY = os.environ.get("VERTEX_API_KEY") # 從 .env 或環境變數讀取
if not VERTEX_API_KEY:
    logger.error("缺少 VERTEX_API_KEY 環境變數，AI 辨識功能將無法運作")
PROJECT_ID = "vision-493709"
LOCATION = "us-central1"
MODEL_NAME = "gemini-2.5-flash-lite"
VERTEX_URL = f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/publishers/google/models/{MODEL_NAME}:generateContent?key={VERTEX_API_KEY}"


async def call_gemini_vision_api(prompt: str, b64_image: str, mime_type: str = "image/jpeg") -> str:
    """
    將圖片與提示詞傳送至 Gemini API 並回傳純文字結果（原生 async，無需 ThreadPool）
    """
    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": prompt},
                {"inlineData": {"mimeType": mime_type, "data": b64_image}}
            ]
        }],
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json"
        }
    }

    import asyncio
    timeout = httpx.Timeout(REQUEST_TIMEOUT)
    
    max_retries = 4
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(VERTEX_URL, json=payload)
                if resp.status_code == 429:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt * 2)  # Exponential backoff: 2s, 4s, 8s...
                        continue
                    else:
                        raise Exception("HTTP 429: 配額不足或速率限制")
                if resp.status_code != 200:
                    raise Exception(f"HTTP {resp.status_code}")
                resp_data = resp.json()
                break
        except httpx.TimeoutException:
            if attempt < max_retries - 1:
                continue
            raise

    try:
        return resp_data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, UnboundLocalError):
        raise ValueError("模型回傳格式非預期")


# ---------------- 顏色規則預設值 ----------------
DEFAULT_COLOR_RULES: list[dict] = [
    {"name": "藍色",   "keywords": ["藍"],        "hex": "#1E56D6", "rgb": [30,  86,  214], "safe": True},
    {"name": "深藍色", "keywords": ["深藍", "navy"],"hex": "#003087", "rgb": [0,   48,  135], "safe": True},
    {"name": "青色",   "keywords": ["青"],         "hex": "#00C0C0", "rgb": [0,   192, 192], "safe": False},
    {"name": "紅色",   "keywords": ["紅"],         "hex": "#DC2626", "rgb": [220, 38,  38],  "safe": False},
    {"name": "橙色",   "keywords": ["橙", "橘"],   "hex": "#EA580C", "rgb": [234, 88,  12],  "safe": False},
    {"name": "黃色",   "keywords": ["黃"],         "hex": "#D97706", "rgb": [217, 119, 6],   "safe": False},
    {"name": "綠色",   "keywords": ["綠"],         "hex": "#16A34A", "rgb": [22,  163, 74],  "safe": False},
    {"name": "紫色",   "keywords": ["紫"],         "hex": "#7C3AED", "rgb": [124, 58,  237], "safe": False},
    {"name": "粉色",   "keywords": ["粉", "桃"],   "hex": "#EC4899", "rgb": [236, 72,  153], "safe": False},
    {"name": "黑色",   "keywords": ["黑"],         "hex": "#1A1A1A", "rgb": [26,  26,  26],  "safe": False},
    {"name": "白色",   "keywords": ["白"],         "hex": "#F0F0F0", "rgb": [240, 240, 240], "safe": False},
    {"name": "灰色",   "keywords": ["灰"],         "hex": "#6B7280", "rgb": [107, 114, 128], "safe": False},
]

# ---------------- 業務邏輯層 (Business Logic) ----------------

class PhotoAnalysisResult(BaseModel):
    has_face: bool
    face_bboxes: list[list[int]] = []       # 所有偵測到的人臉 bbox 清單
    face_confidences: list[float] = []      # 對應各人臉的信心度
    has_brand_strap: bool
    strap_bboxes: list[list[int]] = []      # 所有偵測到的帶子 bbox 清單
    strap_confidences: list[float] = []     # 對應各帶子的信心度
    strap_color: Optional[str]
    is_safe_for_public: bool
    moderation_reason: str


def map_google_api_error_to_http(status_code: int, error_text: str) -> HTTPException:
    if status_code == 404:
        return HTTPException(
            status_code=503,
            detail="Vertex AI 模型不可用，請確認 model/region 設定與專案存取權限"
        )
    if status_code == 403:
        return HTTPException(status_code=503, detail="API Key 權限不足或 API 未啟用")
    if status_code == 429:
        return HTTPException(status_code=429, detail="API 配額不足，請稍後再試")
    return HTTPException(status_code=502, detail=f"API 呼叫失敗 ({status_code})")


async def analyze_brand_strap_image(b64_image: str, content_type: str, color_rules: list | None = None) -> PhotoAnalysisResult:
    """執行特化的「名牌帶子」圖片分析業務邏輯"""
    if color_rules is None:
        color_rules = DEFAULT_COLOR_RULES

    safe_names   = "、".join(r["name"] for r in color_rules if r.get("safe"))
    unsafe_names = "、".join(r["name"] for r in color_rules if not r.get("safe"))

    color_ref_lines = "\n".join(
        f"   - {r['name']} (RGB: {r['rgb'][0]}, {r['rgb'][1]}, {r['rgb'][2]}, {r['hex']})"
        for r in color_rules
    )

    safe_rule_str   = f"帶子顏色為「{safe_names}」→ is_safe_for_public = true" if safe_names else ""
    unsafe_rule_str = f"帶子顏色為「{unsafe_names}」→ is_safe_for_public = false" if unsafe_names else ""

    prompt = f"""
    你是一個專業的圖片審核系統。請分析這張圖片，並回傳純 JSON 格式的結果，不要任何 markdown 標記。

    【辨識目標與規則】：
    1. 偵測圖片中「所有」清晰可見的人臉，每個人臉都需要各自提供 (⚠️嚴格限制：最多只列出前 5 個最清晰的人臉):
       - bbox: [ymin, xmin, ymax, xmax] 以 0 到 1000 的整數表示
       - confidence: 0.0 到 1.0 的信心度浮點數
    2. 偵測圖片中「所有」名牌帶子 (識別證帶、掛繩、lanyard/strap)，每條帶子各自提供 (⚠️嚴格限制：最多只列出前 5 個最清晰的帶子):
       - bbox: [ymin, xmin, ymax, xmax] 以 0 到 1000 的整數表示
       - confidence: 0.0 到 1.0 的信心度浮點數
    3. 如果有帶子，請辨識其整體主要顏色。請對照以下色票精準區分顏色：
{color_ref_lines}
    4. 關於是否適合公開展示 (is_safe_for_public)，請嚴格遵守以下業務規則：
       - {safe_rule_str}
       - {unsafe_rule_str}
       - 無帶子且無違規內容 → true

    【必須輸出的 JSON 欄位】：
    - "has_face" (boolean)
    - "face_bboxes" (二維整數陣列，每個元素為 [ymin, xmin, ymax, xmax]，無人臉則為空陣列 [])
    - "face_confidences" (浮點數陣列，與 face_bboxes 一一對應，無人臉則為 [])
    - "has_brand_strap" (boolean)
    - "strap_bboxes" (二維整數陣列，每個元素為 [ymin, xmin, ymax, xmax]，無帶子則為空陣列 [])
    - "strap_confidences" (浮點數陣列，與 strap_bboxes 一一對應，無帶子則為 [])
    - "strap_color" (string 或 null): 帶子的主要顏色。無帶子則為 null。
    - "is_safe_for_public" (boolean)
    - "moderation_reason" (string): 判定原因。
    """

    try:
        response_text = await call_gemini_vision_api(prompt, b64_image, content_type)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        if "HTTP 404" in str(e):
            raise map_google_api_error_to_http(404, str(e))
        if "HTTP 403" in str(e):
            raise map_google_api_error_to_http(403, str(e))
        if isinstance(e, (httpx.HTTPError, httpx.TimeoutException)):
            logger.exception("Google API connection error: %s", e)
            raise HTTPException(status_code=502, detail="無法連線至 Google API") from e
        logger.exception("Unexpected error: %s", e)
        raise map_google_api_error_to_http(502, str(e))

    if not response_text:
        raise HTTPException(status_code=502, detail="模型未回傳內容")
        
    try:
        # 強制清理 Markdown 或者雜訊 (抓出第一個 { 到最後一個 })
        cleaned_text = response_text.strip()
        match = re.search(r'\{.*\}', cleaned_text, re.DOTALL)
        if match:
            cleaned_text = match.group(0)
            
        result_json = json.loads(cleaned_text)
        
        # 【邏輯防護】自動過濾掉重複的 BBox，防止 AI 幻覺產生大量一模一樣的框
        def dedup_bboxes(bboxes, confidences):
            seen = set()
            new_bboxes = []
            new_confs = []
            for b, c in zip(bboxes, confidences):
                idx_tuple = tuple(b)
                if idx_tuple not in seen:
                    seen.add(idx_tuple)
                    new_bboxes.append(b)
                    new_confs.append(c)
            return new_bboxes, new_confs
            
        if isinstance(result_json.get("face_bboxes"), list) and isinstance(result_json.get("face_confidences"), list):
            result_json["face_bboxes"], result_json["face_confidences"] = dedup_bboxes(
                result_json["face_bboxes"], result_json["face_confidences"]
            )
        if isinstance(result_json.get("strap_bboxes"), list) and isinstance(result_json.get("strap_confidences"), list):
            result_json["strap_bboxes"], result_json["strap_confidences"] = dedup_bboxes(
                result_json["strap_bboxes"], result_json["strap_confidences"]
            )
            
    except json.JSONDecodeError as e:
        logger.error("JSON 解析失敗，原始回傳內容:\n%s", response_text)
        # 如果真的解析失敗（例如被截斷），為了保持批次處理不中斷，我們可以給一個預設的安全失敗值而不是直接噴 502
        logger.warning("解析破裂，啟用預設空白結果回傳")
        result_json = {
             "has_face": False, "face_bboxes": [], "face_confidences": [],
             "has_brand_strap": False, "strap_bboxes": [], "strap_confidences": [],
             "strap_color": None, "is_safe_for_public": False,
             "moderation_reason": "AI 模組產生無效格式（可能為物件過多），系統強制阻擋公布。"
        }
        
    if not isinstance(result_json, dict):
        raise HTTPException(status_code=502, detail="模型回傳內容格式錯誤")
    
    # 【雙重保險】: 以 color_rules 強制覆寫業務邏輯 (避免 AI 偶發的邏輯幻覺)
    if result_json.get("has_brand_strap") and color_rules:
        color = result_json.get("strap_color") or ""
        unsafe_hit = next(
            (r for r in color_rules if not r.get("safe") and any(kw in color for kw in r.get("keywords", []))),
            None
        )
        safe_hit = next(
            (r for r in color_rules if r.get("safe") and any(kw in color for kw in r.get("keywords", []))),
            None
        )
        if unsafe_hit:
            result_json["is_safe_for_public"] = False
            result_json["moderation_reason"] = f"系統覆寫：帶子為{unsafe_hit['name']}，禁止公開"
        elif safe_hit:
            result_json["is_safe_for_public"] = True
            result_json["moderation_reason"] = f"系統覆寫：帶子為{safe_hit['name']}，允許公開"

    return PhotoAnalysisResult.model_validate(result_json)
