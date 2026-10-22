"""配置只描述本次订阅；凭据来自环境，永不进入状态或日志。"""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

MIN_POLL_SECONDS = 10
MAX_POLL_SECONDS = 86400
MIN_BACKOFF_SECONDS = 300
WORKER_TICK_SECONDS = 2


def poll_interval(*, minutes=None, seconds=None):
    """入口单位统一为整数秒；拒绝冲突、布尔值和小数，避免静默截断。"""
    if minutes is not None and seconds is not None:
        raise ValueError("同步间隔请选择秒或分钟，不要同时填写")
    value = seconds if seconds is not None else minutes
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError("同步间隔须为整数")
    value = value if seconds is not None else value * 60
    if not MIN_POLL_SECONDS <= value <= MAX_POLL_SECONDS:
        raise ValueError("同步间隔须为 10–86400 秒（最多 24 小时）")
    return value


def folder_id(value):
    value = str(value).strip()
    if re.fullmatch(r"[1-9]\d{0,19}", value):
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or parsed.hostname not in {
        "space.bilibili.com", "www.bilibili.com", "bilibili.com", "m.bilibili.com"}:
        raise ValueError("这不是 B 站收藏夹链接。请在 B 站打开收藏夹页面，复制地址栏链接")
    query = parse_qs(parsed.query)
    candidate = (query.get("fid") or query.get("media_id") or [""])[0]
    if not candidate:
        match = re.search(r"/(?:medialist/detail|list)/ml(\d+)", parsed.path)
        candidate = match.group(1) if match else ""
    if not candidate and "/video/" in parsed.path:
        raise ValueError("这是视频链接，不是收藏夹链接。请到收藏夹页面复制地址栏链接（含 fid=）")
    if not re.fullmatch(r"[1-9]\d{0,19}", candidate):
        raise ValueError("链接没有指明是哪个收藏夹。请先点开具体收藏夹，再复制地址栏链接（含 fid=）")
    return candidate


@dataclass(frozen=True)
class Config:
    data_dir: Path
    folder: str
    poll_seconds: int = 900
    sync_mode: str = "auto"
    delivery_gap_seconds: int = 10
    page_gap_seconds: float = 3
    max_pages: int = 5
    quality: int = 16
    max_video_bytes: int = 30_000_000
    max_duration_seconds: int = 1800
    max_cache_bytes: int = 1_000_000_000
    multipart_policy: str = "reject"
    send_enabled: bool = False
    max_sends_per_cycle: int = 1
    qq_app_id_env: str = "BILI_RELAY_QQ_APP_ID"
    qq_secret_env: str = "BILI_RELAY_QQ_APP_SECRET"
    credential_id: str = ""

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        raw = json.loads(path.read_text())
        return cls.from_mapping(raw, path)

    @classmethod
    def from_mapping(cls, value, path):
        path = Path(path).resolve()
        raw = dict(value)
        raw["folder"] = folder_id(raw["folder"])
        raw["data_dir"] = (path.parent / raw.get("data_dir", "data")).resolve()
        config = cls(**raw)
        for key, low, high in [("poll_seconds", MIN_POLL_SECONDS, MAX_POLL_SECONDS), ("max_pages", 1, 50),
                               ("delivery_gap_seconds", 10, 86400),
                               ("max_video_bytes", 1024, 30_000_000),
                               ("max_duration_seconds", 1, 7200),
                               ("max_cache_bytes", 30_000_000, 100_000_000_000),
                               ("max_sends_per_cycle", 1, 5)]:
            value = getattr(config, key)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{key} 必须是 {low}–{high} 之间的整数")
        if type(config.page_gap_seconds) not in {int, float} or not 2 <= config.page_gap_seconds <= 60:
            raise ValueError("分页间隔必须在 2–60 秒之间")
        if config.quality != 16 or config.multipart_policy not in {"reject", "first"}:
            raise ValueError("第一版只请求 360P；multipart_policy 只能是 reject 或 first")
        if type(config.send_enabled) is not bool:
            raise ValueError("send_enabled 必须是布尔值")
        if config.sync_mode not in {"auto", "manual"}:
            raise ValueError("sync_mode 只能是 auto 或 manual")
        if config.credential_id and not re.fullmatch(r"[a-f0-9]{32}", config.credential_id):
            raise ValueError("credential_id 格式无效")
        return config

    def credentials(self):
        if self.credential_id:
            from .credentials import CredentialVault
            return CredentialVault().load(self.credential_id)
        app_id = os.getenv(self.qq_app_id_env, "").strip()
        secret = os.getenv(self.qq_secret_env, "").strip()
        if not app_id or not secret:
            raise ValueError(f"请设置 {self.qq_app_id_env} 和 {self.qq_secret_env}")
        return app_id, secret
