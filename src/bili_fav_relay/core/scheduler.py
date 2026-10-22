"""模式切换与有限同步任务；调度不认识来源/目标平台。"""
import time
from .control import ACTIVE, Control
from ..config import MIN_BACKOFF_SECONDS
from .pipeline import safe_error


class Scheduler:
    def __init__(self, config, store, pipeline_factory, clock=time.time):
        self.store, self.factory, self.clock = store, pipeline_factory, clock
        self.control = Control(config, store, clock)

    def tick(self):
        config = self.control.effective()
        request = self.store.get("sync_request")
        try:
            if request and request["status"] in ACTIVE:
                return self.manual(config, request)
            if config.sync_mode == "manual":
                return {"status": "manual_idle"}
            if self.clock() < self.store.get("scheduler_retry_at", 0):
                return {"status": "waiting", "next_action_at": self.store.get("scheduler_retry_at")}
            return self.automatic(config)
        except Exception as error:
            result = {"status": "failed", "error": safe_error(error)}
            if request and request["status"] in ACTIVE:
                self.store.update_sync(request["id"], **result, message="同步未完成，请查看状态后处理")
            else:
                self.store.set("scheduler_retry_at", self.clock() + max(MIN_BACKOFF_SECONDS, config.poll_seconds))
            return result

    def automatic(self, config):
        pipeline = self.factory(config)
        scanned = pipeline.scan()
        deliveries = []
        # 本轮额度持久化；检查控制命令的频率不能放大发送额度。
        if scanned["status"] == "ok":
            self.store.set("auto_remaining", config.max_sends_per_cycle)
        elif scanned["status"] != "cooldown":
            self.store.set("auto_remaining", 0)
        remaining = self.store.get("auto_remaining", 0)
        if config.send_enabled and remaining and self.control.effective().sync_mode == "auto":
            if self.clock() >= self.store.get("next_delivery_at", 0):
                self.store.set("next_delivery_at", self.clock() + config.delivery_gap_seconds)
                result = pipeline.deliver_one()
                deliveries.append(result)
                self.store.set("auto_remaining", remaining - 1 if result["status"] == "sent" else 0)
        return {"scan": scanned, "deliveries": deliveries}

    def manual(self, config, request):
        rid = request["id"]
        self.store.set("auto_remaining", 0)
        if request.get("cancel_requested"):
            return self.finish(rid, "cancelled", "本次同步已取消，待发队列保留")
        if not config.send_enabled:
            return self.finish(rid, "blocked", "转发未开启")
        if self.store.get("source_paused", False) or self.store.get("delivery_paused", False):
            return self.finish(rid, "blocked", "同步已暂停，请处理错误后重新发起")
        pipeline = self.factory(config)
        if "ids" not in request:
            scanned = pipeline.scan(manual=True)
            if scanned["status"] == "cooldown":
                return self.wait(rid, scanned["next_poll_at"], "等待收藏夹请求冷却")
            if scanned["status"] != "ok":
                return self.finish(rid, "failed", "收藏夹扫描未完成", scan=scanned)
            ids = self.store.queued_ids()
            request = self.store.update_sync(rid, ids=ids, scan=scanned, status="running",
                                             total=len(ids), message="处理本次待发视频")
        counts = self.store.job_counts(request["ids"])
        if any(counts.get(s, 0) for s in ("failed", "uncertain", "missing")):
            return self.finish(rid, "blocked", "存在失败或未确认任务，不自动重试", counts=counts)
        if not counts.get("queued", 0):
            return self.finish(rid, "completed", "本次新增视频已处理完成", counts=counts)
        if self.clock() < self.store.get("next_delivery_at", 0):
            return self.wait(rid, self.store.get("next_delivery_at"), "等待下一条视频投递", counts=counts)
        # 一次 tick 最多一条，每条之间可响应取消；任务范围在扫描后冻结。
        latest = self.store.get("sync_request")
        if latest.get("cancel_requested"):
            return self.finish(rid, "cancelled", "本次同步已取消，待发队列保留")
        self.store.set("next_delivery_at", self.clock() + config.delivery_gap_seconds)
        self.store.update_sync(rid, status="running", message="正在处理一条视频")
        result = pipeline.deliver_one()
        counts = self.store.job_counts(request["ids"])
        if result["status"] != "sent":
            return self.finish(rid, "blocked", "视频未确认送达，请查看状态", delivery=result, counts=counts)
        if not counts.get("queued", 0):
            return self.finish(rid, "completed", "本次新增视频已处理完成", counts=counts)
        self.store.update_sync(rid, counts=counts)
        return {"request_id": rid, "status": "running", "counts": counts, "delivery": result}

    def wait(self, rid, due, message, **details):
        result = {"status": "waiting", "next_action_at": due, "message": message, **details}
        self.store.update_sync(rid, **result)
        return {"request_id": rid, **result}

    def finish(self, rid, status, message, **details):
        result = {"status": status, "message": message, "finished_at": self.clock(), "next_action_at": None, **details}
        self.store.update_sync(rid, **result)
        return {"request_id": rid, **result}
