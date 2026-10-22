"""独立运行入口；agent 只需帮助安装和配置，无 MCP 或模型依赖。"""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import time
from .config import Config
from .core.lock import WorkerLock, WorkerBusy
from .core.control import Control
from .core.pipeline import Pipeline, safe_error
from .sources.favorites import Favorites
from .store import Store
from .worker import run_worker
from .local_files import private_write


def emit(value):
    print(json.dumps(value, ensure_ascii=False, default=str), flush=True)


def bind(config, alias):
    from .targets.qqbot import QQBotClient
    from .targets.qq_gateway import QQBotGateway
    path = config.data_dir / "binding.json"
    if path.exists():
        raise ValueError("已有群绑定；更换目标请创建独立数据目录，避免旧队列误发")
    client = QQBotClient(*config.credentials())
    client.get_bot_info()
    code = f"{secrets.randbelow(1_000_000):06d}"
    def ready():
        emit({"status": "waiting", "instruction": f"请在目标 QQ 群 @机器人 发送：绑定 {code}", "expires_in": 300})
    group = QQBotGateway(client).wait_for_binding(code, on_ready=ready)
    private_write(path, {"app_id": client.app_id, "alias": alias, "group_openid": group.group_openid})
    emit({"status": "bound", "alias": alias, "message_sent": False})


def scan_cycle(config, store):
    pipeline = Pipeline(config, store, Favorites(config), None)
    return {"scan": pipeline.scan(), "deliveries": []}


def execute(args, config):
    config.data_dir.mkdir(parents=True, exist_ok=True)
    with WorkerLock(config.data_dir / "worker.lock"):
        store = Store(config.data_dir / "state.sqlite3")
        try:
            store.assert_folder(config.folder)
            store.recover()
            config = Control(config, store).effective()
            if args.command == "bind":
                return bind(config, args.alias)
            if args.command == "inspect":
                if time.time() < store.get("next_poll_at", 0):
                    raise ValueError("距离上次请求太近，请等到 next_poll_at 再试")
                if store.get("source_paused", False):
                    raise ValueError("收藏夹访问已暂停，请核对错误后再 resume")
                store.set("last_poll_at", time.time())
                store.set("next_poll_at", time.time() + config.poll_seconds)
                try:
                    data = Favorites(config).page(size=3)
                except Exception as error:
                    store.set("poll_error", safe_error(error))
                    if safe_error(error) in {"rate_or_risk_control", "api_error"}:
                        store.set("source_paused", True)
                    raise
                emit({"title": data["info"].get("title"), "count": data["info"].get("media_count"),
                      "folder": config.folder, "anonymous": True, "requests": 1,
                      "items": [{k: row.get(k) for k in ("bvid", "title", "fav_time")} for row in data.get("medias") or []],
                      "baseline_created": False})
            elif args.command == "scan":
                result = scan_cycle(config, store)
                emit(result)
                return 1 if result["scan"]["status"] in {"failed", "paused"} else 0
            elif args.command == "run":
                emit({"status": "running", "send_enabled": config.send_enabled, "poll_seconds": config.poll_seconds,
                      "mode": config.sync_mode})
                return run_worker(config, store, emit, once=args.once)
            elif args.command == "retry":
                store.retry(args.bvid)
                emit({"status": "queued", "id": args.bvid, "note": "如投递已暂停，需显式 resume"})
            elif args.command == "resolve":
                store.resolve(args.bvid, args.delivered == "yes")
                emit({"status": "resolved", "id": args.bvid})
            elif args.command == "resume":
                store.set("source_paused", False)
                store.set("delivery_paused", False)
                emit({"status": "resumed", "note": "保留已有冷却时间，不立即重试"})
            elif args.command == "backup":
                destination = Path(args.output).resolve()
                if destination.exists():
                    raise ValueError("备份路径已存在，不覆盖")
                store.backup(destination)
                emit({"backup": destination})
        finally:
            store.close()


def control_command(args, config):
    store = Store(config.data_dir / "state.sqlite3")
    try:
        store.assert_folder(config.folder)
        control = Control(config, store)
        if args.command == "schedule":
            emit(control.schedule(args.mode, args.minutes))
            return 0
        if args.cancel:
            emit(control.cancel_sync())
            return 0
        request = control.request_sync()
        emit(request)
        lock = WorkerLock(config.data_dir / "worker.lock")
        try:
            lock.acquire()
        except WorkerBusy:
            emit({"status": "queued", "request_id": request["request_id"],
                  "message": "请求已保存；由 worker 执行。用 status 查看结果；若未运行持续 worker，请运行 run"})
            return 0
        try:
            store.recover()
            return run_worker(config, store, emit, request_id=request["request_id"])
        finally:
            lock.release()
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description="B 站公开收藏夹 → QQ 官方机器人；独立运行，无需 agent 或 Cookie")
    parser.add_argument("--config", default="config.json")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("doctor")
    sub.add_parser("status")
    setup = sub.add_parser("setup", help="打开本地部署向导；密钥不经过聊天")
    setup.add_argument("--no-open", action="store_true")
    setup.add_argument("--port", type=int, default=0)
    sub.add_parser("setup-status", help="只读配置进度，不读取密钥")
    sub.add_parser("start", help="启动独立后台进程，不设置开机自启")
    sub.add_parser("stop", help="当前一条结束后停止后台进程")
    sub.add_parser("inspect")
    sub.add_parser("scan")
    sub.add_parser("resume")
    schedule = sub.add_parser("schedule", help="查看或修改同步模式与间隔；不发网络请求")
    schedule.add_argument("--mode", choices=["auto", "manual"])
    schedule.add_argument("--minutes", type=int)
    sub.add_parser("sync", help="本次扫描并转发待发视频，保留原模式与冷却").add_argument("--cancel", action="store_true")
    sub.add_parser("bind").add_argument("--alias", default="我的 QQ 群")
    sub.add_parser("run").add_argument("--once", action="store_true")
    sub.add_parser("retry").add_argument("bvid")
    resolve = sub.add_parser("resolve")
    resolve.add_argument("bvid")
    resolve.add_argument("--delivered", choices=["yes", "no"], required=True)
    sub.add_parser("backup").add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "setup":
            from .setup.server import serve_setup
            return serve_setup(args.config, emit, args.port, not args.no_open)
        if args.command == "setup-status":
            from .setup.status import setup_status
            emit({**setup_status(args.config), "job_is_last_recorded": True})
            return 0
        if args.command == "init":
            path = Path(args.config)
            value = asdict(Config(Path("data"), "替换为你的收藏夹ID"))
            value["data_dir"] = "data"
            with path.open("x") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
            emit({"created": path, "next": "填写 folder，设置机器人环境变量，然后运行 doctor"})
            return 0
        config = Config.load(args.config)
        if args.command in {"start", "stop"}:
            from .runtime import start_worker, stop_worker
            emit(start_worker(args.config) if args.command == "start" else stop_worker(config))
            return 0
        if args.command in {"schedule", "sync"}:
            return control_command(args, config)
        if args.command == "doctor":
            tools = {name: shutil.which(name) is not None for name in ("ffmpeg", "ffprobe")}
            emit({"tools": tools, "qq_credentials_present": bool(config.credential_id) or all(os.getenv(k) for k in (config.qq_app_id_env, config.qq_secret_env)),
                  "binding_present": (config.data_dir / "binding.json").exists(),
                  "send_enabled": config.send_enabled, "network_requests": 0})
            return 0 if all(tools.values()) else 1
        if args.command == "status":
            from .runtime import runtime_status
            if not (config.data_dir / "state.sqlite3").exists():
                emit({"initialized": False, "seen": 0, "jobs": []})
            else:
                store = Store(config.data_dir / "state.sqlite3")
                try:
                    emit({**store.status(), "control": Control(config, store).summary(), "runtime": runtime_status(config)})
                finally:
                    store.close()
            return 0
        return execute(args, config) or 0
    except KeyboardInterrupt:
        emit({"status": "stopped"})
        return 130
    except Exception as error:
        # 本地配置错误可解释；外部错误不回显可能含凭据的任意原文。
        emit({"status": "failed", "error": safe_error(error),
              "message": str(error) if isinstance(error, (ValueError, FileExistsError)) else "操作失败，请查看状态与配置；未宣称发送成功"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
