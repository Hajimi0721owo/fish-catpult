"""同一订阅的源请求预算；预检和正式扫描共用持久冷却与风控暂停。"""
import time
from ..config import MIN_POLL_SECONDS


class SourceAccess:
    def __init__(self, config, store, clock=time.time):
        self.config, self.store, self.clock = config, store, clock

    def next_at(self, manual=False):
        last = self.store.get("last_poll_at")
        due = self.store.get("next_poll_at", 0)
        if last is not None and not self.store.get("poll_failures", 0):
            return last + (MIN_POLL_SECONDS if manual else self.config.poll_seconds)
        return due

    def reserve(self, manual=False, repair=False):
        kind = self.store.get("poll_error", "")
        recoverable = repair and kind in {"api_error", "page_budget"}
        if self.store.get("source_paused", False) and not recoverable:
            return {"status": "paused", "error": kind}
        now, due = self.clock(), self.next_at(manual)
        if now < due:
            return {"status": "cooldown", "next_poll_at": due}
        self.store.set("last_poll_at", now)
        self.store.set("next_poll_at", now + (MIN_POLL_SECONDS if manual else self.config.poll_seconds))
        return None

    def failure(self, error):
        kind = str(getattr(error, "kind", type(error).__name__))
        failures = self.store.get("poll_failures", 0) + 1
        self.store.set("poll_failures", failures)
        self.store.set("poll_error", kind)
        self.store.set("next_poll_at", self.clock() + min(86400, self.config.poll_seconds * 2 ** min(failures, 6)))
        if kind in {"rate_or_risk_control", "api_error", "page_budget"}:
            self.store.set("source_paused", True)
        return {"status": "failed", "error": kind, "source_paused": self.store.get("source_paused", False)}

    def success(self):
        self.store.set("poll_error", "")
        self.store.set("poll_failures", 0)

    def summary(self):
        due = self.next_at(manual=True)
        return {"paused": self.store.get("source_paused", False), "error": self.store.get("poll_error", ""),
                "next_poll_at": due, "cooling": self.clock() < due}
