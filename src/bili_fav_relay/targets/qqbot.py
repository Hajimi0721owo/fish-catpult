# 改编自 dy experiment/ux-polish 当前工作区，原 MIT 许可见 THIRD_PARTY_LICENSES.md。
from __future__ import annotations
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable
from .qq_upload import UploadMixin

from .errors import QQBotError



def _body_code(error):
    """只取错误响应里的数字错误码（如 11298 IP 白名单），不保留响应原文。"""
    try:
        result = json.loads(error.read(65536).decode("utf-8", errors="replace") or "{}")
    except (OSError, ValueError):
        return None
    code = result.get("code", result.get("err_code")) if isinstance(result, dict) else None
    return code if type(code) is int else None

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class QQBotClient(UploadMixin):
    """Small client for the documented QQ Bot OpenAPI.

    Owns short-lived tokens; uploads and sends are separate operations.
    """

    def __init__(
        self,
        app_id: str,
        client_secret: str,
        *,
        api_base: str = "https://api.bot.qq.com",
        timeout: float = 20.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.app_id = app_id.strip()
        self._client_secret = client_secret.strip()
        self.api_base = api_base.strip().rstrip("/")
        self.timeout = timeout
        self._clock = clock
        self._access_token = ""
        self._access_token_expires_at = 0.0
        if not self.app_id:
            raise ValueError("QQ Bot AppID 不能为空")
        if not self._client_secret:
            raise ValueError("QQ Bot ClientSecret 不能为空")
        if self.api_base not in {"https://api.bot.qq.com", "https://sandbox.api.sgroup.qq.com"}:
            raise ValueError("只允许官方生产或沙箱 API 地址")

    def __repr__(self) -> str:
        return f"QQBotClient(app_id={self.app_id!r}, api_base={self.api_base!r})"

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        access_token: str = "",
    ) -> dict[str, Any]:
        data = None
        headers = {
            "Accept": "application/json",
            "User-Agent": "dy-archive-qqbot-experiment/0.1",
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        if access_token:
            headers["Authorization"] = f"QQBot {access_token}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=self.timeout) as response:
                raw = response.read(1_048_576).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            code = _body_code(error)
            suffix = f" rejected: {code}" if code is not None else ""
            raise QQBotError(f"QQ API HTTP {error.code}{suffix}") from None
        except urllib.error.URLError as error:
            raise QQBotError("QQ API network error") from None
        try:
            result = json.loads(raw or "{}")
        except json.JSONDecodeError as error:
            raise QQBotError("QQ API invalid JSON") from None
        if not isinstance(result, dict):
            raise QQBotError("QQ Bot 返回格式不是 JSON 对象")
        error_code = result.get("err_code", result.get("code", result.get("errcode")))
        if error_code not in (None, 0, "0"):
            raise QQBotError(f"QQ API rejected: {error_code}")
        return result

    def access_token(self, *, force_refresh: bool = False) -> str:
        now = self._clock()
        if (
            not force_refresh
            and self._access_token
            and now < self._access_token_expires_at
        ):
            return self._access_token
        result = self._request_json(
            "POST",
            f"{self.api_base}/app/getAppAccessToken",
            payload={"appId": self.app_id, "clientSecret": self._client_secret},
        )
        token = str(result.get("access_token") or "").strip()
        if not token:
            raise QQBotError("QQ Bot 获取凭证成功，但响应中没有 access_token")
        try:
            expires_in = max(1, int(result.get("expires_in") or 7200))
        except (TypeError, ValueError):
            expires_in = 7200
        refresh_margin = min(60, max(1, expires_in // 10))
        self._access_token = token
        self._access_token_expires_at = now + expires_in - refresh_margin
        return token

    def call_api(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = "/" + path.strip().lstrip("/")
        return self._request_json(
            method.upper(),
            f"{self.api_base}{normalized}",
            payload=payload,
            access_token=self.access_token(),
        )

    def get_bot_info(self) -> dict[str, Any]:
        return self.call_api("GET", "/users/@me")

    def send_group_text(
        self,
        group_openid: str,
        text: str,
        *,
        reply_to_message_id: str = "",
    ) -> dict[str, Any]:
        group_openid = group_openid.strip()
        text = text.strip()
        if not group_openid:
            raise ValueError("QQ 群 OpenID 不能为空")
        if not text:
            raise ValueError("文本消息不能为空")
        encoded_group = urllib.parse.quote(group_openid, safe="")
        payload = {"content": text, "msg_type": 0}
        if reply_to_message_id.strip():
            payload["msg_id"] = reply_to_message_id.strip()
        return self.call_api(
            "POST",
            f"/v2/groups/{encoded_group}/messages",
            payload=payload,
        )

    @staticmethod
    def _group_path(group_openid: str) -> str:
        group_openid = group_openid.strip()
        if not group_openid:
            raise ValueError("QQ 群 OpenID 不能为空")
        return urllib.parse.quote(group_openid, safe="")

    def send_group_media(self, group_openid: str, file_info: str) -> dict[str, Any]:
        file_info = file_info.strip()
        if not file_info:
            raise ValueError("QQ 富媒体 file_info 不能为空")
        encoded_group = self._group_path(group_openid)
        return self.call_api(
            "POST",
            f"/v2/groups/{encoded_group}/messages",
            payload={"msg_type": 7, "media": {"file_info": file_info}},
        )

