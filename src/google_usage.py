import os
import json
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

    timeout = httpx.Timeout(REQUEST_TIMEOUT)
    
    for attempt in range(2):  # 最多重試 1 次
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(VERTEX_URL, json=payload)
                if resp.status_code != 200:
                    raise Exception(f"HTTP {resp.status_code}")
                resp_data = resp.json()
                break
        except httpx.TimeoutException:
            if attempt == 0:
                continue
            raise

    try:
        return resp_data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise ValueError("模型回傳格式非預期")


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


async def analyze_brand_strap_image(b64_image: str, content_type: str) -> PhotoAnalysisResult:
    """執行特化的「名牌帶子」圖片分析業務邏輯"""
    prompt = """
    你是一個專業的圖片審核系統。請分析這張圖片，並回傳純 JSON 格式的結果，不要任何 markdown 標記。
    
    【辨識目標與規則】：
    1. 偵測圖片中「所有」清晰可見的人臉，每個人臉都需要各自提供:
       - bbox: [ymin, xmin, ymax, xmax] 以 0 到 1000 的整數表示
       - confidence: 0.0 到 1.0 的信心度浮點數
    2. 偵測圖片中「所有」名牌帶子 (識別證帶、掛繩、lanyard/strap)，每條帶子各自提供:
       - bbox: [ymin, xmin, ymax, xmax] 以 0 到 1000 的整數表示  
       - confidence: 0.0 到 1.0 的信心度浮點數
    3. 如果有帶子，請辨識其整體主要顏色。請特別區分『青色 (Cyan/Teal)』與『藍色 (Blue)』。
    4. 關於是否適合公開展示 (is_safe_for_public)，請嚴格遵守以下業務規則：
       - 如果任何帶子顏色判定為「藍色」，則 `is_safe_for_public` 為 false。
       - 如果帶子顏色為「青色」或無帶子且無違規內容，則為 true。

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
        result_json = json.loads(response_text)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail="模型回傳非 JSON 格式") from e
        
    if not isinstance(result_json, dict):
        raise HTTPException(status_code=502, detail="模型回傳內容格式錯誤")
    
    # 【雙重保險】: 在 Backend 再次強制執行業務邏輯 (避免 AI 偶發的邏輯幻覺)
    if result_json.get("has_brand_strap"):
        color = result_json.get("strap_color", "")
        if color and "青" in color:
            result_json["is_safe_for_public"] = True
            result_json["moderation_reason"] = "系統覆寫：帶子為青色，允許公開"
        elif color and "藍" in color:
            result_json["is_safe_for_public"] = False
            result_json["moderation_reason"] = "系統覆寫：帶子為藍色，禁止公開"

    return PhotoAnalysisResult.model_validate(result_json)
