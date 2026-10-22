"""独立后台进程与协作停止；只向持锁 worker 的会话发停止请求。"""
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from .config import Config
from .core.lock import WorkerBusy, WorkerLock, read_owner
from .store import Store

# Windows 的进程句柄由启动端保留；退出进程时操作系统接管，业务状态仍只在 SQLite。
_windows_children = []


def runtime_status(config):
    if not (config.data_dir / "state.sqlite3").exists():
        return {"active": False, "status": "stopped"}
    store = Store(config.data_dir / "state.sqlite3")
    try:
        session = store.get("worker_session", {})
        try:
            with WorkerLock(config.data_dir / "worker.lock"):
                return {"active": False, "status": "stopped"}
        except WorkerBusy:
            owner = read_owner(config.data_dir / "worker.lock")
            if session.get("active") and owner.get("id") == session.get("id") and owner.get("kind") == "worker":
                return {"active": True, "status": "stopping" if store.get("worker_stop_session") == session["id"] else "running",
                        "pid": session.get("pid"), "session_id": session["id"], "last_tick_at": store.get("last_cycle_at"),
                        "last_result": store.get("last_result")}
            return {"active": False, "status": "busy"}
    finally:
        store.close()


def start_worker(config_path):
    config_path = Path(config_path).resolve()
    config = Config.load(config_path)
    with WorkerLock(config.data_dir / "launch.lock"):
        status = runtime_status(config)
        if status["active"]:
            return {**status, "already_running": True}
        if status["status"] == "busy":
            raise ValueError("另一项操作尚未结束，请稍后启动")
        if config.send_enabled:
            from .targets.target import load_binding
            load_binding(config)
        log_path = config.data_dir / "worker.log"
        if log_path.exists() and log_path.stat().st_size > 2_000_000:
            os.replace(log_path, log_path.with_suffix(".log.1"))
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as log:
            argv = [sys.executable, "-m", "bili_fav_relay", "--config", str(config_path), "run"]
            launch_id = uuid.uuid4().hex
            child_env = {**os.environ, "BILI_RELAY_LAUNCH_ID": launch_id}
            if os.name != "nt":
                actions = [(os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0o600),
                           (os.POSIX_SPAWN_DUP2, log.fileno(), 1), (os.POSIX_SPAWN_DUP2, log.fileno(), 2)]
                pid = os.posix_spawn(sys.executable, argv, child_env, file_actions=actions, setsid=True)
                def exited():
                    return os.waitpid(pid, os.WNOHANG)[0] == pid
            else:
                proc = subprocess.Popen(argv, cwd=config_path.parent, env=child_env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
                _windows_children.append(proc)
                pid = proc.pid
                def exited():
                    return proc.poll() is not None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if exited():
                raise ValueError("后台进程启动失败，请查看 data/worker.log；未宣称同步已启动")
            status = runtime_status(config)
            if status.get("session_id") == launch_id and status["active"] and status.get("last_tick_at"):
                return {**status, "log": str(log_path), "starts_at_login": False}
            time.sleep(0.1)
        raise ValueError("后台进程尚未确认就绪，请先查看 status，勿重复启动")


def stop_worker(config):
    status = runtime_status(config)
    if not status["active"]:
        return status
    store = Store(config.data_dir / "state.sqlite3")
    try:
        store.set("worker_stop_session", status["session_id"])
    finally:
        store.close()
    return {"status": "stop_requested", "message": "当前一条处理结束后退出后台进程"}
