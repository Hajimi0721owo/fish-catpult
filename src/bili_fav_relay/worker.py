"""平台装配与本地 worker；控制设置由 SQLite 共享，不另开网络端口。"""
import time
import os
import uuid
from .config import WORKER_TICK_SECONDS
from .core.control import ACTIVE
from .core.lock import write_owner
from .core.pipeline import Pipeline
from .core.scheduler import Scheduler
from .sources.favorites import Favorites
from .sources.media import download_one


def make_pipeline(config, store):
    target = None
    if config.send_enabled:
        from .targets.target import Target
        target = Target(config)
    return Pipeline(config, store, Favorites(config), lambda bvid: download_one(config, bvid), target)


def run_worker(config, store, emit, once=False, request_id=None):
    scheduler = Scheduler(config, store, lambda cfg: make_pipeline(cfg, store))
    previous = None
    session = {"id": uuid.uuid4().hex, "pid": os.getpid(), "active": True, "started_at": time.time()}
    store.set("worker_session", session)
    store.set("last_cycle_at", None)
    write_owner(config.data_dir / "worker.lock", {"id": session["id"], "pid": session["pid"], "kind": "worker"})
    try:
        while True:
            if store.get("worker_stop_session") == session["id"]:
                emit({"status": "stopped", "message": "后台进程已停止"})
                return 0
            result = scheduler.tick()
            if result != previous:
                emit(result)
                store.set("last_result", result)
                previous = result
            store.set("last_cycle_at", time.time())
            if request_id:
                request = store.get("sync_request")
                if request["id"] != request_id or request["status"] not in ACTIVE:
                    return 0 if request["status"] == "completed" else 1
            if once:
                return 1 if result.get("status") in {"failed", "blocked"} or result.get("scan", {}).get(
                    "status") in {"failed", "paused"} or any(d["status"] in {"failed", "uncertain", "delivery_paused"}
                                                            for d in result.get("deliveries", [])) else 0
            time.sleep(WORKER_TICK_SECONDS)
    except KeyboardInterrupt:
        if request_id:
            # 中断时只整理本地状态；不能再 tick 而意外启动一次自动扫描。
            store.recover()
            request = store.get("sync_request")
            if request and request["id"] == request_id and request["status"] in ACTIVE:
                store.update_sync(request_id, status="cancelled", cancel_requested=True,
                                  message="前台同步已中断，请核对任务状态", finished_at=time.time())
        raise
    finally:
        store.set("worker_session", {**session, "active": False})
