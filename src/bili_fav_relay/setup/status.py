"""部署状态只读投影；不加载系统密钥、不请求外部平台。"""
import os
from pathlib import Path
import shutil
import time
from ..config import Config
from ..core.control import Control
from ..local_files import read_json
from ..runtime import runtime_status
from ..store import Store
from ..core.source_access import SourceAccess
from .folder_setup import provisional_config


def progress_path(path):
    return Path(path).with_suffix(".setup-state.json")


def setup_status(path, clock=time.time):
    path = Path(path).resolve()
    tools = {name: shutil.which(name) is not None for name in ("ffmpeg", "ffprobe")}
    result = {"config_path": str(path), "tools": tools, "folder": None, "bot": None, "binding": None,
              "baseline": False, "seen": 0, "send_enabled": False, "mode": "manual", "interval_minutes": 15,
              "runtime": {"active": False, "status": "stopped"}, "next_step": "folder",
              "job": read_json(progress_path(path), {}), "network_requests": 0}
    try:
        provisional = provisional_config(path)
    except (ValueError, TypeError, KeyError):
        result["configuration_incomplete"] = True
        return result
    if (provisional.data_dir / "state.sqlite3").exists():
        store = Store(provisional.data_dir / "state.sqlite3")
        try:
            result["source_access"] = SourceAccess(provisional, store, clock).summary()
        finally:
            store.close()
    if not path.exists():
        return result
    try:
        config = Config.load(path)
    except (ValueError, TypeError, KeyError):
        result["configuration_incomplete"] = True
        return result
    result.update(folder=config.folder, send_enabled=config.send_enabled, mode=config.sync_mode,
                  interval_minutes=config.poll_seconds // 60, next_step="credentials")
    info = read_json(config.data_dir / "setup-info.json", {})
    result.update(folder_title=info.get("folder_title", ""), folder_count=info.get("folder_count"))
    present = bool(config.credential_id) or all(os.getenv(k, "").strip() for k in (config.qq_app_id_env, config.qq_secret_env))
    if present:
        result["bot"] = {"name": info.get("bot_name", "QQ 机器人"), "source": "系统凭据库" if config.credential_id else "环境变量",
                         "verified": info.get("verified_ref") == config.credential_id and bool(config.credential_id)}
        result["next_step"] = "bind"
    binding = read_json(config.data_dir / "binding.json", {})
    if binding.get("group_openid") and binding.get("app_id"):
        result["binding"] = {"alias": binding.get("alias") or "已绑定群"}
        if present:
            result["next_step"] = "baseline"
    db = config.data_dir / "state.sqlite3"
    if db.exists():
        store = Store(db)
        try:
            status, control = store.status(), Control(config, store).summary()
            result.update(baseline=status["initialized"], seen=status["seen"], counts=status["counts"], control=control,
                          mode=control["mode"], interval_minutes=control["interval_minutes"],
                          folder_title=store.get("folder_title", "") or result["folder_title"])
        finally:
            store.close()
    if result["baseline"] and present and result["binding"]:
        result["next_step"] = "activate"
    result["runtime"] = runtime_status(config)
    if result["send_enabled"] and result["next_step"] == "activate":
        result["next_step"] = "ready"
    return result
