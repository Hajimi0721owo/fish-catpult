"""设置任务逐个执行，进度只包含白名单字段，不保存请求体或密钥。"""
import threading
import time
import uuid
from ..local_files import private_write
from .status import progress_path


class SetupJobs:
    def __init__(self, service):
        self.service, self.lock = service, threading.RLock()
        self.thread = None
        self.job = {}
        self.last_auth_at = {}

    def snapshot(self):
        with self.lock:
            return dict(self.job)

    def update(self, **fields):
        allowed = {"id", "action", "phase", "message", "code", "expires_at", "result", "error", "updated_at"}
        with self.lock:
            self.job.update({key: value for key, value in fields.items() if key in allowed})
            self.job["updated_at"] = time.time()
            private_write(progress_path(self.service.path), self.job)

    def submit(self, action, payload):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError("上一项操作尚未结束，请等待当前结果")
            if action not in {"folder", "credentials", "bind", "baseline", "activate", "control", "resume_source"}:
                raise ValueError("不支持的设置操作")
            if action in {"folder", "credentials", "bind"}:
                if time.monotonic() - self.last_auth_at.get(action, 0) < 5:
                    raise ValueError("请稍候几秒再验证，避免重复请求")
                self.last_auth_at[action] = time.monotonic()
            self.job = {"id": uuid.uuid4().hex, "action": action, "phase": "working", "message": "正在处理"}
            self.update()
            self.thread = threading.Thread(target=self.run, args=(action, payload), daemon=True)
            self.thread.start()
            return dict(self.job)

    def run(self, action, payload):
        try:
            result = self.service.dispatch(action, payload, self.update)
            self.update(phase="done", result=result, message=result.get("message", "操作完成"), code=None, expires_at=None)
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) else "操作未完成，请核对平台权限、网络和当前配置后重试。"
            self.update(phase="failed", error=type(error).__name__, message=message, code=None, expires_at=None)
