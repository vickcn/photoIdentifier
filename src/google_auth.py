import os
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
import secrets
from typing import Optional, Protocol, Sequence
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

DEFAULT_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/drive", # 需要完整的 Drive 權限以列出檔案並有可能移動檔案
]

# 偵測 Vercel 環境以確保檔案寫入權限 (Vercel 只有 /tmp 是可寫的)
IS_VERCEL = os.getenv("VERCEL") == "1"
if IS_VERCEL:
    TOKEN_DIR = Path("/tmp/drive_tokens")
else:
    TOKEN_DIR = Path(os.getenv("DRIVE_TOKEN_DIR", ".secrets/drive_tokens"))

class TokenStore(Protocol):
    def save(self, user_key: str, creds: Credentials) -> None: ...
    def load(self, user_key: str) -> Optional[Credentials]: ...
    def delete(self, user_key: str) -> None: ...

class FileTokenStore:
    def __init__(self, root: Path | str = TOKEN_DIR):
        self.root = Path(root)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"[!] Warning: Failed to prepare token directory {self.root}: {e}")

    def _path(self, user_key: str) -> Path:
        safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in user_key)
        return self.root / f"{safe_name}.json"

    def save(self, user_key: str, creds: Credentials) -> None:
        path = self._path(user_key)
        path.write_text(creds.to_json(), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def load(self, user_key: str) -> Optional[Credentials]:
        path = self._path(user_key)
        if not path.exists():
            return None
        return Credentials.from_authorized_user_file(str(path), scopes=DEFAULT_SCOPES)

    def delete(self, user_key: str) -> None:
        path = self._path(user_key)
        if path.exists():
            path.unlink()

token_store: TokenStore = FileTokenStore()

def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少必要的環境變數: {name}")
    return value

def _client_config() -> dict:
    return {
        "web": {
            "client_id": _require_env("GOOGLE_CLIENT_ID"),
            "client_secret": _require_env("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [_require_env("GOOGLE_REDIRECT_URI")],
        }
    }

def _scopes(scopes: Optional[Sequence[str]] = None) -> list[str]:
    return list(scopes or DEFAULT_SCOPES)

def get_auth_url(state: Optional[str] = None) -> tuple[str, str, str | None]:
    redirect_uri = _require_env("GOOGLE_REDIRECT_URI")
    flow = Flow.from_client_config(
        _client_config(),
        scopes=_scopes(),
        state=state or secrets.token_urlsafe(24),
    )
    flow.redirect_uri = redirect_uri
    auth_url, resolved_state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    code_verifier = getattr(flow, 'code_verifier', None)
    return auth_url, resolved_state, code_verifier

def exchange_code_for_token(
        code: str,
        user_key: str,
        state: Optional[str] = None,
        code_verifier: Optional[str] = None,
    ) -> Credentials:
    redirect_uri = _require_env("GOOGLE_REDIRECT_URI")
    flow = Flow.from_client_config(
        _client_config(),
        scopes=_scopes(),
        state=state,
    )
    flow.redirect_uri = redirect_uri
    if code_verifier:
        try:
            flow.fetch_token(code=code, code_verifier=code_verifier)
        except TypeError:
            flow.fetch_token(code=code)
    else:
        flow.fetch_token(code=code)

    creds = flow.credentials
    token_store.save(user_key, creds)
    return creds

def load_user_credentials(user_key: str) -> Credentials:
    creds = token_store.load(user_key)
    if creds is None:
        raise RuntimeError(f"找不到使用者憑證: user_key={user_key!r}")
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_store.save(user_key, creds)
    return creds
