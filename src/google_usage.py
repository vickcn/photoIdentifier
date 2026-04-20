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
    {"name": "藍色",   "keywords": ["藍"],          "hex": "#1E56D6", "rgb": [30,  86,  214], "safe": True},
    {"name": "深藍色", "keywords": ["深藍", "navy"], "hex": "#003087", "rgb": [0,   48,  135], "safe": True},
    {"name": "青色",   "keywords": ["青"],           "hex": "#00C0C0", "rgb": [0,   192, 192], "safe": False},
    {"name": "紅色",   "keywords": ["紅"],           "hex": "#DC2626", "rgb": [220, 38,  38],  "safe": True},
    {"name": "橙色",   "keywords": ["橙", "橘"],     "hex": "#EA580C", "rgb": [234, 88,  12],  "safe": True},
    {"name": "黃色",   "keywords": ["黃"],           "hex": "#D97706", "rgb": [217, 119, 6],   "safe": True},
    {"name": "深綠色", "keywords": ["深綠"],         "hex": "#1A4731", "rgb": [26,  71,  49],  "safe": False},
    {"name": "綠色",   "keywords": ["綠"],           "hex": "#16A34A", "rgb": [22,  163, 74],  "safe": True},
    {"name": "紫色",   "keywords": ["紫"],           "hex": "#7C3AED", "rgb": [124, 58,  237], "safe": True},
    {"name": "粉色",   "keywords": ["粉", "桃"],     "hex": "#EC4899", "rgb": [236, 72,  153], "safe": True},
    {"name": "黑色",   "keywords": ["黑"],           "hex": "#1A1A1A", "rgb": [26,  26,  26],  "safe": True},
    {"name": "白色",   "keywords": ["白"],           "hex": "#F0F0F0", "rgb": [240, 240, 240], "safe": True},
    {"name": "灰色",   "keywords": ["灰"],           "hex": "#6B7280", "rgb": [107, 114, 128], "safe": True},
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


def _parse_json_response(response_text: str) -> dict:
    """清理並解析 LLM 回傳的 JSON 文字"""
    cleaned = response_text.strip()
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    return json.loads(cleaned)


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


async def _call_api_safe(prompt: str, b64_image: str, content_type: str) -> str:
    """呼叫 Gemini API 並統一處理連線錯誤"""
    try:
        return await call_gemini_vision_api(prompt, b64_image, content_type)
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


async def _step1_check_unsafe_strap(
    b64_image: str, content_type: str, color_rules: list
) -> dict:
    """
    第一步：偵測人臉、帶子，並判斷帶子是否為不可公開顏色。
    回傳包含 face/strap bbox 與 has_unsafe_strap 的 dict。
    """
    unsafe_names = "、".join(r["name"] for r in color_rules if not r.get("safe"))
    safe_names   = "、".join(r["name"] for r in color_rules if r.get("safe"))
    color_ref_lines = "\n".join(
        f"   - {r['name']} (RGB: {r['rgb'][0]}, {r['rgb'][1]}, {r['rgb'][2]}, {r['hex']})"
        for r in color_rules
    )

    prompt = f"""
    你是一個專業的圖片審核系統。請分析這張圖片，並回傳純 JSON 格式的結果，不要任何 markdown 標記。

    【第一步：偵測人臉與帶子，並判斷帶子顏色是否違規】

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

    【必須輸出的 JSON 欄位】：
    - "has_face" (boolean)
    - "face_bboxes" (二維整數陣列，無人臉則為 [])
    - "face_confidences" (浮點數陣列，與 face_bboxes 一一對應，無人臉則為 [])
    - "has_brand_strap" (boolean)
    - "strap_bboxes" (二維整數陣列，無帶子則為 [])
    - "strap_confidences" (浮點數陣列，與 strap_bboxes 一一對應，無帶子則為 [])
    - "strap_color" (string 或 null)
    - "has_unsafe_strap" (boolean)
    - "moderation_reason" (string): 判定原因
    """

    response_text = await _call_api_safe(prompt, b64_image, content_type)
    if not response_text:
        raise HTTPException(status_code=502, detail="模型未回傳內容（步驟一）")

    try:
        result = _parse_json_response(response_text)
    except json.JSONDecodeError:
        logger.error("步驟一 JSON 解析失敗，原始回傳：\n%s", response_text)
        result = {
            "has_face": False, "face_bboxes": [], "face_confidences": [],
            "has_brand_strap": False, "strap_bboxes": [], "strap_confidences": [],
            "strap_color": None, "has_unsafe_strap": True,
            "moderation_reason": "AI 模組產生無效格式，系統強制阻擋公布。"
        }

    # 去重 bbox
    if isinstance(result.get("face_bboxes"), list) and isinstance(result.get("face_confidences"), list):
        result["face_bboxes"], result["face_confidences"] = _dedup_bboxes(
            result["face_bboxes"], result["face_confidences"]
        )
    if isinstance(result.get("strap_bboxes"), list) and isinstance(result.get("strap_confidences"), list):
        result["strap_bboxes"], result["strap_confidences"] = _dedup_bboxes(
            result["strap_bboxes"], result["strap_confidences"]
        )

    # 【雙重保險】以 color_rules 強制覆寫帶子安全判定
    if result.get("has_brand_strap") and color_rules:
        color = result.get("strap_color") or ""
        unsafe_hit = next(
            (r for r in color_rules if not r.get("safe") and any(kw in color for kw in r.get("keywords", []))),
            None
        )
        safe_hit = next(
            (r for r in color_rules if r.get("safe") and any(kw in color for kw in r.get("keywords", []))),
            None
        )
        if unsafe_hit:
            result["has_unsafe_strap"] = True
            result["moderation_reason"] = f"系統覆寫：帶子為{unsafe_hit['name']}，禁止公開"
        elif safe_hit:
            result["has_unsafe_strap"] = False
            result["moderation_reason"] = f"系統覆寫：帶子為{safe_hit['name']}，允許公開"

    return result


async def _step2_check_children_without_badge(b64_image: str, content_type: str) -> tuple[bool, str]:
    """
    第二步：判斷圖片中是否有小孩未配戴名牌（識別證）。
    回傳 (has_children_without_badge, moderation_reason)。
    """
    prompt = """
    你是一個專業的圖片審核系統。請分析這張圖片，並回傳純 JSON 格式的結果，不要任何 markdown 標記。

    【第二步：檢查是否有小孩未配戴名牌】

    請判斷圖片中是否有看起來像小孩（兒童、未成年）的人物，且這些人物「沒有」配戴名牌（識別證、掛牌、strap/lanyard）。

    判斷依據：
    - 若圖片中的小孩「全部」都有配戴名牌 → has_children_without_badge = false
    - 若圖片中「至少一名」小孩沒有配戴名牌 → has_children_without_badge = true
    - 若圖片中沒有小孩 → has_children_without_badge = false

    【必須輸出的 JSON 欄位】：
    - "has_children_without_badge" (boolean)
    - "moderation_reason" (string): 判定原因，請說明圖片中小孩的狀況
    """

    response_text = await _call_api_safe(prompt, b64_image, content_type)
    if not response_text:
        raise HTTPException(status_code=502, detail="模型未回傳內容（步驟二）")

    try:
        result = _parse_json_response(response_text)
        return bool(result.get("has_children_without_badge", False)), str(result.get("moderation_reason", ""))
    except json.JSONDecodeError:
        logger.error("步驟二 JSON 解析失敗，原始回傳：\n%s", response_text)
        # 解析失敗時保守處理，標記為待人員判定
        return True, "AI 模組產生無效格式（步驟二），系統標記為待人員判定。"


async def analyze_brand_strap_image(b64_image: str, content_type: str, color_rules: list | None = None) -> PhotoAnalysisResult:
    """
    分三個階段判定照片是否可公開：
      1. LLM 判斷是否有不可公開的帶子 → 是則系統判定 private
      2. LLM 判斷是否有小孩未配戴名牌 → 是則系統判定 pending（待人員判定）
      3. 否則系統判定 public
    """
    if color_rules is None:
        color_rules = DEFAULT_COLOR_RULES

    # ── 步驟一：偵測帶子是否違規 ──
    step1 = await _step1_check_unsafe_strap(b64_image, content_type, color_rules)

    if step1.get("has_unsafe_strap"):
        return PhotoAnalysisResult(
            has_face=bool(step1.get("has_face", False)),
            face_bboxes=step1.get("face_bboxes", []),
            face_confidences=step1.get("face_confidences", []),
            has_brand_strap=bool(step1.get("has_brand_strap", False)),
            strap_bboxes=step1.get("strap_bboxes", []),
            strap_confidences=step1.get("strap_confidences", []),
            strap_color=step1.get("strap_color"),
            is_safe_for_public=False,
            moderation_status="private",
            moderation_reason=step1.get("moderation_reason", "帶子顏色不可公開"),
        )

    # ── 步驟二：檢查是否有小孩未配戴名牌 ──
    has_children_issue, reason2 = await _step2_check_children_without_badge(b64_image, content_type)

    if has_children_issue:
        return PhotoAnalysisResult(
            has_face=bool(step1.get("has_face", False)),
            face_bboxes=step1.get("face_bboxes", []),
            face_confidences=step1.get("face_confidences", []),
            has_brand_strap=bool(step1.get("has_brand_strap", False)),
            strap_bboxes=step1.get("strap_bboxes", []),
            strap_confidences=step1.get("strap_confidences", []),
            strap_color=step1.get("strap_color"),
            is_safe_for_public=False,
            moderation_status="pending",
            moderation_reason=reason2 or "圖片中有小孩未配戴名牌，需人員確認",
        )

    # ── 步驟三：系統判定可公開 ──
    return PhotoAnalysisResult(
        has_face=bool(step1.get("has_face", False)),
        face_bboxes=step1.get("face_bboxes", []),
        face_confidences=step1.get("face_confidences", []),
        has_brand_strap=bool(step1.get("has_brand_strap", False)),
        strap_bboxes=step1.get("strap_bboxes", []),
        strap_confidences=step1.get("strap_confidences", []),
        strap_color=step1.get("strap_color"),
        is_safe_for_public=True,
        moderation_status="public",
        moderation_reason=step1.get("moderation_reason", "系統判定：可公開"),
    )
