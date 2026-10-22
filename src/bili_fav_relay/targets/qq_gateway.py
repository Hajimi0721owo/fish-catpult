# 改编自 dy experiment/ux-polish；用于一次性口令绑定，不自动向群发消息。
from __future__ import annotations

import json
import platform
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from websockets.sync.client import connect

from .qqbot import QQBotClient, QQBotError


GROUP_AND_C2C_EVENT = 1 << 25
GROUP_MESSAGE_EVENTS = {"GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"}


@dataclass(frozen=True, slots=True)
class QQGroupBinding:
    group_openid: str
    event_id: str
    event_type: str
    author_member_openid: str = ""


def binding_code_from_content(content: str) -> str:
    """Extract a six-digit code only when it follows an explicit bind command."""
    match = re.search(r"(?:绑定|bind)\s*[:：]?\s*(\d{6})(?!\d)", content, re.I)
    return match.group(1) if match else ""


def binding_from_dispatch(payload: dict[str, Any], expected_code: str) -> QQGroupBinding | None:
    if payload.get("op") != 0 or payload.get("t") not in GROUP_MESSAGE_EVENTS:
        return None
    data = payload.get("d")
    if not isinstance(data, dict):
        return None
    if binding_code_from_content(str(data.get("content") or "")) != expected_code:
        return None
    group_openid = str(data.get("group_openid") or "").strip()
    if not group_openid:
        return None
    author = data.get("author")
    author_openid = (
        str(author.get("member_openid") or author.get("id") or "")
        if isinstance(author, dict)
        else ""
    )
    return QQGroupBinding(
        group_openid=group_openid,
        event_id=str(data.get("id") or ""),
        event_type=str(payload.get("t") or ""),
        author_member_openid=author_openid,
    )


class QQBotGateway:
    """Minimal synchronous QQ Gateway client used for one-time group binding."""

    def __init__(
        self,
        client: QQBotClient,
        *,
        connector: Callable[..., Any] = connect,
        clock: Callable[[], float] = time.monotonic,
        open_timeout: float = 20.0,
    ):
        self.client = client
        self._connector = connector
        self._clock = clock
        self.open_timeout = open_timeout
        self.sequence: int | None = None
        self.session_id = ""

    @staticmethod
    def _decode(raw: str | bytes) -> dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as error:
            raise QQBotError("QQ Gateway 返回了无法解析的数据") from error
        if not isinstance(payload, dict):
            raise QQBotError("QQ Gateway 返回格式不是 JSON 对象")
        return payload

    def _gateway_url(self) -> str:
        response = self.client.call_api("GET", "/gateway")
        url = str(response.get("url") or "").strip()
        if not url.startswith(("ws://", "wss://")):
            raise QQBotError("QQ Gateway 响应中没有有效的 WebSocket 地址")
        return url

    def wait_for_binding(self, code: str, *, timeout: float = 300.0, cancel_event=None, on_ready=None) -> QQGroupBinding:
        if not re.fullmatch(r"\d{6}", code):
            raise ValueError("绑定码必须是六位数字")
        if timeout <= 0:
            raise ValueError("等待时间必须大于 0 秒")

        deadline = self._clock() + timeout
        try:
            websocket_context = self._connector(
                self._gateway_url(),
                open_timeout=self.open_timeout,
                ping_interval=None,
                # QQ's Gateway is a domestic service. Inheriting a developer's
                # global SOCKS proxy adds an unnecessary optional dependency and
                # commonly breaks otherwise valid bot credentials.
                proxy=None,
            )
        except ImportError as error:
            raise QQBotError(
                "QQ Gateway 连接依赖缺失；请确认网络代理设置后重试"
            ) from error
        except OSError as error:
            raise QQBotError(f"无法连接 QQ Gateway：{error}") from error

        with websocket_context as websocket:
            hello = self._decode(websocket.recv(timeout=self.open_timeout))
            if hello.get("op") != 10 or not isinstance(hello.get("d"), dict):
                raise QQBotError("QQ Gateway 未发送 Hello 消息")
            try:
                heartbeat_seconds = max(
                    1.0, float(hello["d"]["heartbeat_interval"]) / 1000.0
                )
            except (KeyError, TypeError, ValueError) as error:
                raise QQBotError("QQ Gateway Hello 缺少心跳间隔") from error

            websocket.send(json.dumps({
                "op": 2,
                "d": {
                    "token": f"QQBot {self.client.access_token()}",
                    "intents": GROUP_AND_C2C_EVENT,
                    "shard": [0, 1],
                    "properties": {
                        "$os": platform.system().lower(),
                        "$browser": "bili-fav-relay",
                        "$device": "bili-fav-relay",
                    },
                },
            }))
            next_heartbeat = self._clock() + heartbeat_seconds

            while self._clock() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    raise QQBotError("绑定已取消")
                now = self._clock()
                receive_timeout = max(0.01, min(deadline, next_heartbeat) - now)
                if cancel_event is not None:
                    receive_timeout = min(receive_timeout, 1.0)
                try:
                    payload = self._decode(websocket.recv(timeout=receive_timeout))
                except TimeoutError:
                    payload = {}

                now = self._clock()
                if now >= next_heartbeat:
                    websocket.send(json.dumps({"op": 1, "d": self.sequence}))
                    next_heartbeat = now + heartbeat_seconds

                if not payload:
                    continue
                sequence = payload.get("s")
                if isinstance(sequence, int):
                    self.sequence = sequence
                if payload.get("op") == 7:
                    raise QQBotError("QQ Gateway 要求重新连接，请在应用中重新生成绑定口令")
                if payload.get("op") == 9:
                    raise QQBotError("QQ Gateway 拒绝了当前会话，请检查机器人事件权限")
                if payload.get("op") == 0 and payload.get("t") == "READY":
                    data = payload.get("d")
                    if isinstance(data, dict):
                        self.session_id = str(data.get("session_id") or "")
                        if on_ready is not None:
                            on_ready()
                binding = binding_from_dispatch(payload, code)
                if binding is not None:
                    return binding

        raise QQBotError(f"等待绑定超时（{int(timeout)} 秒），没有收到正确的群消息")

