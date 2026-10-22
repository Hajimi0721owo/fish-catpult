"""日常控制服务；CLI 和未来页面共用，不读取或修改机器人凭据。"""
from dataclasses import replace
import time
import uuid
from ..config import MIN_POLL_SECONDS, poll_interval

ACTIVE = {"pending", "waiting", "running"}


class Control:
    def __init__(self, config, store, clock=time.time):
        self.config, self.store, self.clock = config, store, clock

    def effective(self):
        saved = self.store.get("controls", {})
        return replace(self.config, sync_mode=saved.get("mode", self.config.sync_mode),
                       poll_seconds=saved.get("poll_seconds", self.config.poll_seconds))

    def schedule(self, mode=None, minutes=None, seconds=None):
        if mode is not None and mode not in {"auto", "manual"}:
            raise ValueError("同步模式只能是 auto 或 manual")
        interval = poll_interval(minutes=minutes, seconds=seconds)
        if mode is not None or interval is not None:
            def update(saved):
                result = dict(saved or {})
                if mode is not None:
                    result["mode"] = mode
                if interval is not None:
                    result["poll_seconds"] = interval
                return result
            self.store.mutate_meta("controls", update, {})
        return self.summary()

    def summary(self):
        config = self.effective()
        last = self.store.get("last_poll_at")
        due = self.store.get("next_poll_at", 0)
        if last is not None and not self.store.get("poll_failures", 0):
            due = last + config.poll_seconds
        request = self.store.get("sync_request")
        public_request = None
        if request:
            public_request = {key: value for key, value in request.items() if key != "ids"}
            public_request["counts"] = self.store.job_counts(request.get("ids", []))
        return {"mode": config.sync_mode, "interval_seconds": config.poll_seconds,
                "interval_minutes": config.poll_seconds / 60,
                "send_enabled": config.send_enabled, "minimum_interval_seconds": MIN_POLL_SECONDS,
                "minimum_interval_minutes": MIN_POLL_SECONDS / 60,
                "next_auto_scan_at": due if config.sync_mode == "auto" else None,
                "source_paused": self.store.get("source_paused", False),
                "delivery_paused": self.store.get("delivery_paused", False),
                "sync": public_request,
                "note": "模式与间隔已保存；自动执行需要运行中的 worker，当前一条处理完后应用新模式"}

    def request_sync(self):
        if not self.config.send_enabled:
            raise ValueError("转发尚未开启；请先完成群绑定并明确开启 send_enabled，立即同步不会代为开启")
        if self.store.get("source_paused", False) or self.store.get("delivery_paused", False):
            raise ValueError("同步已因错误暂停；请先核对 status，解决原因后 resume")
        request_id = uuid.uuid4().hex
        def update(current):
            if current and current["status"] in ACTIVE:
                return current
            return {"id": request_id, "status": "pending", "created_at": self.clock(),
                    "cancel_requested": False, "message": "等待扫描新增收藏"}
        result = self.store.mutate_meta("sync_request", update)
        return {"request_id": result["id"], "status": result["status"],
                "coalesced": result["id"] != request_id}

    def cancel_sync(self):
        def update(current):
            if current and current["status"] in ACTIVE:
                return {**current, "cancel_requested": True, "message": "当前一条完成后取消，未发送队列保留"}
            return current
        self.store.mutate_meta("sync_request", update)
        return self.summary()
