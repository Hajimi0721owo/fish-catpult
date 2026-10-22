"""首次配置业务；网页与 agent 共享现有状态、绑定和调度能力。"""
from contextlib import contextmanager
from pathlib import Path
import re
import secrets
import shutil
import time
import uuid
from ..config import Config, poll_interval
from ..credentials import CredentialVault
from ..core.control import Control
from ..core.lock import WorkerLock
from ..core.pipeline import Pipeline
from ..local_files import private_write, read_json
from ..runtime import start_worker, stop_worker, runtime_status
from ..sources.favorites import Favorites
from ..store import Store
from ..targets.qqbot import QQBotClient
from ..targets.qq_gateway import QQBotGateway
from ..targets.target import load_binding
from .folder_check import scan_reason
from .folder_setup import save_folder, resume_source
from .qq_hints import qq_reason
from .status import setup_status


def explained(call, stage):
    """平台错误换成可操作的说明；未识别的异常原样上抛，由任务层给出通用提示。"""
    try:
        return call()
    except Exception as error:
        reason = qq_reason(error, stage)
        if reason:
            raise ValueError(reason) from None
        raise


class SetupService:
    def __init__(self, path, vault=None, client_factory=QQBotClient, gateway_factory=QQBotGateway, source_factory=Favorites, clock=time.time):
        self.path = Path(path).resolve()
        self.vault = vault or CredentialVault()
        self.client_factory, self.gateway_factory, self.source_factory = client_factory, gateway_factory, source_factory
        self.clock = clock

    def status(self):
        return setup_status(self.path, self.clock)

    def config(self):
        return Config.load(self.path)

    def raw(self):
        return read_json(self.path, {"data_dir": "data", "sync_mode": "manual", "send_enabled": False})

    @contextmanager
    def store(self, config):
        store = Store(config.data_dir / "state.sqlite3")
        try:
            store.assert_folder(config.folder)
            yield store
        finally:
            store.close()

    def note(self, config, **fields):
        """setup-info.json 只放展示用信息（标题、昵称），多步骤合并写入。"""
        path = config.data_dir / "setup-info.json"
        private_write(path, {**read_json(path, {}), **fields})

    def save_folder(self, payload, progress):
        return save_folder(self, payload, progress)

    def save_credentials(self, payload, progress):
        config = self.config()
        app_id, secret = payload.get("app_id", ""), payload.get("secret", "")
        if not isinstance(app_id, str) or not re.fullmatch(r"\d{1,30}", app_id.strip()):
            raise ValueError("请填写数字 AppID")
        if not isinstance(secret, str) or not 1 <= len(secret.strip()) <= 512:
            raise ValueError("请填写 AppSecret")
        app_id, secret = app_id.strip(), secret.strip()
        with WorkerLock(config.data_dir / "worker.lock"):
            binding = read_json(config.data_dir / "binding.json", {})
            if binding and binding.get("app_id") != app_id:
                raise ValueError("此配置已绑定另一机器人；请新建一份配置，避免将旧队列发到其他群")
            progress(message="正在验证机器人身份")
            bot = explained(lambda: self.client_factory(app_id, secret).get_bot_info(), "验证")
            reference = config.credential_id or uuid.uuid4().hex
            self.vault.save(reference, app_id, secret)
            private_write(self.path, {**self.raw(), "credential_id": reference})
            self.note(config, bot_name=str(bot.get("username") or "QQ 机器人")[:80],
                      verified_ref=reference, verified_at=time.time())
        return {"status": "verified", "message": "机器人身份已验证，密钥已保存", "next_step": "bind"}

    def bind(self, payload, progress):
        config = self.config()
        alias = payload.get("alias", "视频分享群")
        if not isinstance(alias, str) or not 1 <= len(alias.strip()) <= 60:
            raise ValueError("请填写 1–60 字的群备注")
        with WorkerLock(config.data_dir / "worker.lock"):
            if (config.data_dir / "binding.json").exists():
                load_binding(config)
                return {"status": "bound", "message": "已有有效群绑定", "next_step": "baseline"}
            client = self.client_factory(*config.credentials())
            explained(client.get_bot_info, "验证")
            code = f"{secrets.randbelow(1_000_000):06d}"
            expires_at = time.time() + 300
            def ready():
                progress(message="请在目标 QQ 群 @机器人 发送以下口令", code=f"绑定 {code}", expires_at=expires_at)
            group = explained(lambda: self.gateway_factory(client).wait_for_binding(code, on_ready=ready), "绑定")
            private_write(config.data_dir / "binding.json", {"app_id": client.app_id,
                          "alias": alias.strip(), "group_openid": group.group_openid})
        return {"status": "bound", "message": "群绑定已完成", "next_step": "baseline", "message_sent": False}

    def baseline(self, payload, progress):
        config = self.config()
        with WorkerLock(config.data_dir / "worker.lock"), self.store(config) as store:
            if store.get("initialized", False):
                return {"status": "ready", "message": "已有历史记录，本次无需重复读取", "next_step": "activate"}
            progress(message="正在记录已有收藏；历史视频不会转发")
            source = self.source_factory(config)
            result = Pipeline(config, store, source, None, clock=self.clock).scan(manual=True)
            if result["status"] == "cooldown":
                return {**result, "message": "请求间隔未到，请稍后再读取", "next_step": "baseline"}
            if result["status"] != "ok":
                raise ValueError(scan_reason(result.get("error")))
            store.set("folder_title", getattr(source, "title", ""))
        return {"status": "ready", "message": "已有收藏已记录，之后只转发新增", "next_step": "activate"}

    def activate(self, payload, progress):
        config = self.config()
        mode = payload.get("mode", "manual")
        interval = poll_interval(minutes=payload.get("minutes"), seconds=payload.get("seconds"))
        if mode not in {"auto", "manual"}:
            raise ValueError("请选择手动或自动模式")
        if not all(shutil.which(name) for name in ("ffmpeg", "ffprobe")):
            raise ValueError("缺少 FFmpeg 或 ffprobe，请先按安装指南补齐")
        with WorkerLock(config.data_dir / "worker.lock"), self.store(config) as store:
            if not store.get("initialized", False):
                raise ValueError("请先记录已有收藏，再开启转发")
            load_binding(config)
            if store.get("source_paused", False) or store.get("delivery_paused", False):
                raise ValueError("同步处于暂停状态；请先核对并解决 status 中的错误")
            Control(config, store).schedule(mode=mode, seconds=interval)
            private_write(self.path, {**self.raw(), "send_enabled": True})
        progress(message="正在启动后台程序")
        return {"status": "started", "runtime": start_worker(self.path), "next_step": "ready"}

    def control(self, payload, progress):
        config = self.config()
        operation = payload.get("operation")
        if operation == "stop":
            return stop_worker(config)
        if operation == "start":
            return start_worker(self.path)
        with self.store(config) as store:
            control = Control(config, store)
            if operation == "schedule":
                return control.schedule(mode=payload.get("mode"), minutes=payload.get("minutes"), seconds=payload.get("seconds"))
            if operation == "cancel":
                return control.cancel_sync()
            if operation == "sync":
                if not runtime_status(config)["active"]:
                    raise ValueError("后台程序未运行，请先点击后台启动")
                return control.request_sync()
        raise ValueError("不支持的控制操作")

    def dispatch(self, action, payload, progress):
        actions = {"folder": self.save_folder, "credentials": self.save_credentials, "bind": self.bind,
                   "baseline": self.baseline, "activate": self.activate, "control": self.control,
                   "resume_source": lambda p, cb: resume_source(self, p, cb)}
        if action not in actions or not isinstance(payload, dict):
            raise ValueError("不支持的设置操作")
        return actions[action](payload, progress)
