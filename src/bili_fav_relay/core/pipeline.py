"""源与目标均通过接口传入；每次轮询有预算，失败不得污染基线。"""
import time
from .source_access import SourceAccess
from .delivery_errors import DeliveryError, delivery_details


def safe_error(error):
    kind = getattr(error, "kind", type(error).__name__)
    # 外部异常可能包含签名 URL、令牌或响应原文，仅记录受控种类。
    return str(kind)


class Pipeline:
    def __init__(self, config, store, source, download, target=None, clock=time.time):
        self.config, self.store, self.source = config, store, source
        self.download, self.target, self.clock = download, target, clock

    def next_poll_at(self, manual=False):
        return SourceAccess(self.config, self.store, self.clock).next_at(manual)

    def scan(self, manual=False):
        access = SourceAccess(self.config, self.store, self.clock)
        blocked = access.reserve(manual)
        if blocked:
            return blocked
        try:
            result = self.store.observe(self.source.snapshot())
            access.success()
            return {"status": "ok", **result}
        except Exception as error:
            return access.failure(error)

    def deliver_one(self):
        if not self.config.send_enabled or self.target is None:
            return {"status": "sending_disabled"}
        if self.store.get("delivery_paused", False):
            return {"status": "delivery_paused"}
        job = self.store.next_job()
        if not job:
            return {"status": "idle"}
        item_id = job["id"]
        try:
            path = self.download(item_id)
            prepared = self.target.prepare(path)
        except Exception as error:
            kind = safe_error(error)
            self.store.transition(item_id, "failed", kind)
            # 权限、风控、上传拒绝不会在下一条视频继续轰接口。
            self.store.set("delivery_paused", True)
            problem = {"id": item_id, "status": "failed", "error": kind, **delivery_details(error)}
            self.store.set("delivery_problem", problem)
            return problem
        self.store.transition(item_id, "sending")
        try:
            receipt = self.target.send(prepared)
            if not isinstance(receipt, str) or not receipt:
                raise ValueError("消息没有回执")
        except Exception as error:
            status = "failed" if isinstance(error, DeliveryError) and error.rejected else "uncertain"
            problem = {"id": item_id, "status": status, "error": safe_error(error), **delivery_details(error)}
            self.store.transition(item_id, status, problem["error"])
            self.store.set("delivery_problem", problem)
            self.store.set("delivery_paused", True)
            return problem
        self.store.set("delivery_problem", None)
        self.store.transition(item_id, "sent", receipt=receipt)
        return {"id": item_id, "status": "sent", "receipt": receipt}
