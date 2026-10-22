"""首次收藏夹配置：先预约请求、验证候选，成功后才改变已保存的归属。"""
from dataclasses import replace
from urllib.parse import urlsplit
from ..config import Config
from ..core.lock import WorkerLock
from ..core.source_access import SourceAccess
from ..local_files import private_write, read_json
from ..store import Store
from ..vendor.bili_api import BiliError
from .folder_check import SHORT_HOSTS, check_folder, explain, extract_link, pages_for, resolve_folder


def provisional_config(path):
    raw = read_json(path, {"data_dir": "data", "sync_mode": "manual", "send_enabled": False})
    return Config.from_mapping({**raw, "folder": raw.get("folder") or "1"}, path)


def save_folder(service, payload, progress):
    value = extract_link(payload.get("folder", ""))
    short = (urlsplit(value).hostname or "").lower() in SHORT_HOSTS
    folder = None if short else resolve_folder(value)  # 普通链接格式错误不消耗请求预算。
    config = provisional_config(service.path)
    with WorkerLock(config.data_dir / "worker.lock"):
        store = Store(config.data_dir / "state.sqlite3")
        try:
            if folder and store.validate_setup_folder(folder):
                return {"status": "saved", "message": "收藏夹未变化", "next_step": "credentials"}
            access = SourceAccess(config, store, service.clock)
            blocked = access.reserve(manual=True, repair=not store.get("initialized", False))
            if blocked:
                if blocked["status"] == "paused":
                    raise ValueError("收藏夹访问已暂停。请等冷却结束，确认原因已处理后点击“恢复访问”。")
                return {**blocked, "next_step": "folder"}
            progress(message="正在确认收藏夹是公开的")
            try:
                folder = folder or resolve_folder(value)
                store.validate_setup_folder(folder)
                found = check_folder(service.source_factory(replace(config, folder=folder)))
            except Exception as error:
                access.failure(error)
                if isinstance(error, BiliError):
                    raise ValueError(explain(error)) from None
                raise
            raw = {**service.raw(), "folder": folder,
                   "max_pages": max(pages_for(found["count"]), config.max_pages)}
            Config.from_mapping(raw, service.path)
            store.commit_setup_folder(folder, lambda: private_write(service.path, raw))
            service.note(config, folder_title=found["title"], folder_count=found["count"])
        finally:
            store.close()
    name = f"「{found['title']}」" if found["title"] else "收藏夹"
    return {"status": "saved", "message": f"找到{name}，共 {found['count']} 个内容", "next_step": "credentials"}


def resume_source(service, payload, progress):
    """首次配置的显式恢复不发请求、不缩短冷却，也不解除投递暂停。"""
    config = provisional_config(service.path)
    with WorkerLock(config.data_dir / "worker.lock"):
        store = Store(config.data_dir / "state.sqlite3")
        try:
            if store.get("initialized", False):
                raise ValueError("已有历史记录，请让 agent 核对同步状态后恢复")
            status = SourceAccess(config, store, service.clock).summary()
            if status["cooling"]:
                return {"status": "cooldown", "next_poll_at": status["next_poll_at"]}
            store.set("source_paused", False)
        finally:
            store.close()
    return {"status": "resumed", "message": "访问已恢复，请重新提交收藏夹或读取已有收藏"}
