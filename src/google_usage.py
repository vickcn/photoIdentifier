import os
import json
import re
import httpx
import logging
from pathlib import Path
from typing import Literal, Optional
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
    {"name": "藍色",   "keywords": ["藍", "blue"],          "hex": "#1E56D6", "rgb": [30,  86,  214], "safe": True},
    {"name": "深藍色", "keywords": ["深藍", "navy", "dark blue"], "hex": "#003087", "rgb": [0,   48,  135], "safe": True},
    {"name": "青色",   "keywords": ["青", "cyan"],           "hex": "#00C0C0", "rgb": [0,   192, 192], "safe": False},
    {"name": "紅色",   "keywords": ["紅", "red"],           "hex": "#DC2626", "rgb": [220, 38,  38],  "safe": True},
    {"name": "橙色",   "keywords": ["橙", "橘", "orange"],     "hex": "#EA580C", "rgb": [234, 88,  12],  "safe": True},
    {"name": "黃色",   "keywords": ["黃", "yellow"],           "hex": "#D97706", "rgb": [217, 119, 6],   "safe": True},
    {"name": "深綠色", "keywords": ["深綠", "dark green"],         "hex": "#1A4731", "rgb": [26,  71,  49],  "safe": False},
    {"name": "綠色",   "keywords": ["綠", "green"],           "hex": "#16A34A", "rgb": [22,  163, 74],  "safe": True},
    {"name": "紫色",   "keywords": ["紫", "purple"],           "hex": "#7C3AED", "rgb": [124, 58,  237], "safe": True},
    {"name": "粉色",   "keywords": ["粉", "桃", "pink"],     "hex": "#EC4899", "rgb": [236, 72,  153], "safe": True},
    {"name": "黑色",   "keywords": ["黑", "black"],           "hex": "#1A1A1A", "rgb": [26,  26,  26],  "safe": True},
    {"name": "白色",   "keywords": ["白", "white"],           "hex": "#F0F0F0", "rgb": [240, 240, 240], "safe": True},
    {"name": "灰色",   "keywords": ["灰", "gray", "grey"],           "hex": "#6B7280", "rgb": [107, 114, 128], "safe": True},
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
    moderation_status: Literal["public", "private", "pending"] = "public"
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


def _dedup_bboxes(bboxes: list, confidences: list) -> tuple[list, list]:
    """過濾重複的 BBox，防止 AI 幻覺"""
    seen: set = set()
    new_bboxes, new_confs = [], []
    for b, c in zip(bboxes, confidences):
        key = tuple(b)
        if key not in seen:
            seen.add(key)
            new_bboxes.append(b)
            new_confs.append(c)
    return new_bboxes, new_confs


async def analyze_brand_strap_image(b64_image: str, content_type: str, color_rules: list | None = None) -> PhotoAnalysisResult:
    """
    單次 LLM 呼叫取得所有偵測結果，再由系統邏輯分三段 if 判定：
      1. 有不可公開帶子 → private
      2. 有小孩未配戴名牌 → pending（待人員判定）
      3. 否則 → public
    """
    if color_rules is None:
        color_rules = DEFAULT_COLOR_RULES

    safe_names   = "、".join(r["name"] for r in color_rules if r.get("safe"))
    unsafe_names = "、".join(r["name"] for r in color_rules if not r.get("safe"))
    color_ref_lines = "\n".join(
        f"   - {r['name']} (RGB: {r['rgb'][0]}, {r['rgb'][1]}, {r['rgb'][2]}, {r['hex']})"
        for r in color_rules
    )

    prompt = f"""
    你是一個專業的圖片審核系統。請分析這張圖片，並回傳純 JSON 格式的結果，不要任何 markdown 標記。

    【辨識目標】：
    1. 偵測圖片中「所有」清晰可見的人臉（⚠️最多列出前 5 個最清晰的人臉）：
       - bbox: [ymin, xmin, ymax, xmax]，以 0 到 1000 的整數表示
       - confidence: 0.0 到 1.0 的信心度浮點數

    2. 偵測圖片中「所有」名牌帶子（識別證帶、掛繩、lanyard/strap）（⚠️最多列出前 5 個）：
       - bbox: [ymin, xmin, ymax, xmax]，以 0 到 1000 的整數表示
       - confidence: 0.0 到 1.0 的信心度浮點數

    3. 如果有帶子，請對照以下色票辨識整體主要顏色：
{color_ref_lines}

    4. 判斷帶子是否為「不可公開」顏色：
       - 不可公開顏色：「{unsafe_names}」→ has_unsafe_strap = true
       - 可公開顏色：「{safe_names}」→ has_unsafe_strap = false
       - 無帶子 → has_unsafe_strap = false

    5. 逐一確認圖片中每一名「小孩（兒童、未成年）」：
       請判斷每個小孩「自己身上」是否有掛著名牌帶子（lanyard/strap 繞過頸部/肩膀）。

       ⚠️ 重要判斷原則：
       - 只有帶子明顯掛在「該小孩自身」頸部或肩膀上才算配戴
       - 不可把鄰近其他人的帶子算作這個小孩的帶子
       - 若有任何一名小孩「沒有在自己身上」看到名牌帶子 → has_children_without_badge = true
       - 若所有小孩都明確有自己的名牌帶子 → has_children_without_badge = false
       - 圖中沒有小孩 → has_children_without_badge = false
       - ⚠️ 寧可標記為待確認（true），不可漏判

    【必須輸出的 JSON 欄位】：
    - "has_face" (boolean)
    - "face_bboxes" (二維整數陣列，無人臉則為 [])
    - "face_confidences" (浮點數陣列，與 face_bboxes 一一對應，無人臉則為 [])
    - "has_brand_strap" (boolean)
    - "strap_bboxes" (二維整數陣列，無帶子則為 [])
    - "strap_confidences" (浮點數陣列，與 strap_bboxes 一一對應，無帶子則為 [])
    - "strap_color" (string 或 null)
    - "has_unsafe_strap" (boolean)
    - "has_children_without_badge" (boolean)
    - "moderation_reason" (string): 判定原因
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
        cleaned = response_text.strip()
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if match:
            cleaned = match.group(0)
        result_json = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.error("JSON 解析失敗，原始回傳內容:\n%s", response_text)
        logger.warning("解析破裂，啟用預設空白結果回傳")
        result_json = {
            "has_face": False, "face_bboxes": [], "face_confidences": [],
            "has_brand_strap": False, "strap_bboxes": [], "strap_confidences": [],
            "strap_color": None, "has_unsafe_strap": True, "has_children_without_badge": False,
            "moderation_reason": "AI 模組產生無效格式（可能為物件過多），系統強制阻擋公布。"
        }

    if not isinstance(result_json, dict):
        raise HTTPException(status_code=502, detail="模型回傳內容格式錯誤")

    # 去重 bbox
    if isinstance(result_json.get("face_bboxes"), list) and isinstance(result_json.get("face_confidences"), list):
        result_json["face_bboxes"], result_json["face_confidences"] = _dedup_bboxes(
            result_json["face_bboxes"], result_json["face_confidences"]
        )
    if isinstance(result_json.get("strap_bboxes"), list) and isinstance(result_json.get("strap_confidences"), list):
        result_json["strap_bboxes"], result_json["strap_confidences"] = _dedup_bboxes(
            result_json["strap_bboxes"], result_json["strap_confidences"]
        )

    # 【雙重保險】以 color_rules 強制覆寫帶子安全判定（避免 AI 偶發幻覺）
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
            result_json["has_unsafe_strap"] = True
            result_json["moderation_reason"] = f"系統覆寫：帶子為{unsafe_hit['name']}，禁止公開"
        elif safe_hit:
            result_json["has_unsafe_strap"] = False

    # ── 三段 if 判定 ──
    has_unsafe_strap = bool(result_json.get("has_unsafe_strap", False))
    has_children_issue = bool(result_json.get("has_children_without_badge", False))

    if has_unsafe_strap:
        moderation_status: Literal["public", "private", "pending"] = "private"
        is_safe = False
        reason = result_json.get("moderation_reason", "帶子顏色不可公開")
    elif has_children_issue:
        moderation_status = "pending"
        is_safe = False
        reason = result_json.get("moderation_reason", "圖片中有小孩未配戴名牌，需人員確認")
    else:
        moderation_status = "public"
        is_safe = True
        reason = result_json.get("moderation_reason", "系統判定：可公開")

    return PhotoAnalysisResult(
        has_face=bool(result_json.get("has_face", False)),
        face_bboxes=result_json.get("face_bboxes", []),
        face_confidences=result_json.get("face_confidences", []),
        has_brand_strap=bool(result_json.get("has_brand_strap", False)),
        strap_bboxes=result_json.get("strap_bboxes", []),
        strap_confidences=result_json.get("strap_confidences", []),
        strap_color=result_json.get("strap_color"),
        is_safe_for_public=is_safe,
        moderation_status=moderation_status,
        moderation_reason=reason,
    )
