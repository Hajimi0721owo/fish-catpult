"""只监听回环地址的设置入口；会话令牌留在 URL fragment，不写访问日志。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import re
from pathlib import Path
import secrets
import threading
import time
import webbrowser
from ..core.lock import WorkerLock
from .jobs import SetupJobs
from .service import SetupService

MIME = {"html": "text/html", "js": "text/javascript", "css": "text/css", "png": "image/png"}
ASSET = re.compile(r"/((?:art/)?[a-z0-9-]+\.(js|css|png))")


def asset(path):
    """只认 ui/ 与 ui/art/ 下的简单文件名；不拼接任意路径。"""
    if path == "/":
        return "index.html", MIME["html"]
    match = ASSET.fullmatch(path)
    if not match:
        return None
    node = files("bili_fav_relay.setup").joinpath("ui", *match.group(1).split("/"))
    return (match.group(1), MIME[match.group(2)]) if node.is_file() else None


class SetupServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, service, port=0):
        self.service, self.jobs = service, SetupJobs(service)
        self.token = secrets.token_urlsafe(32)
        self.rate_lock, self.requests = threading.Lock(), []
        super().__init__(("127.0.0.1", port), SetupHandler)
        self.host = f"127.0.0.1:{self.server_port}"
        self.origin = f"http://{self.host}"

    @property
    def launch_url(self):
        return f"{self.origin}/#token={self.token}"


class SetupHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass  # 不记录 URL、请求体或浏览器提交的密钥。

    def reply(self, code, value, content_type="application/json"):
        raw = json.dumps(value, ensure_ascii=False).encode() if content_type == "application/json" else value
        self.send_response(code)
        self.send_header("Content-Type", content_type + ("" if content_type.startswith("image/") else "; charset=utf-8"))
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self, mutation=False):
        if self.headers.get("Host") != self.server.host:
            self.reply(403, {"error": "只接受原始本地地址"})
            return False
        if not secrets.compare_digest(self.headers.get("X-Setup-Token", "").encode(), self.server.token.encode()):
            self.reply(401, {"error": "设置会话已失效，请从终端重新打开完整设置链接"})
            return False
        origin = self.headers.get("Origin")
        if (mutation or origin) and origin != self.server.origin:
            self.reply(403, {"error": "请求来源不匹配"})
            return False
        return True

    def do_GET(self):
        if self.headers.get("Host") != self.server.host:
            return self.reply(403, {"error": "地址不匹配"})
        found = asset(self.path)
        if found:
            name, mime = found
            return self.reply(200, files("bili_fav_relay.setup").joinpath("ui", *name.split("/")).read_bytes(), mime)
        if self.path != "/api/status":
            return self.reply(404, {"error": "页面不存在"})
        if not self.authorized():
            return
        try:
            result = self.server.service.status()
            result["job"] = self.server.jobs.snapshot()
            self.reply(200, result)
        except Exception:
            self.reply(500, {"error": "配置状态暂时不可用，请核对配置文件格式与目录权限"})

    def do_POST(self):
        if not self.authorized(mutation=True):
            return
        if self.path != "/api/action":
            return self.reply(404, {"error": "操作不存在"})
        with self.server.rate_lock:
            now = time.monotonic()
            self.server.requests = [t for t in self.server.requests if now - t < 60]
            if len(self.server.requests) >= 30:
                return self.reply(429, {"error": "操作太频繁，请稍后再试"})
            self.server.requests.append(now)
        if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
            return self.reply(415, {"error": "仅接受 JSON 请求"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384:
                return self.reply(413, {"error": "请求内容过长或为空"})
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or not isinstance(body.get("payload", {}), dict):
                raise ValueError("请求格式不正确")
            result = self.server.jobs.submit(body.get("action"), body.get("payload", {}))
            self.reply(202, result)
        except (ValueError, TypeError):
            self.reply(400, {"error": "请求无效或上一项尚未结束，请等待当前结果后再试"})
        except Exception:
            self.reply(500, {"error": "操作未提交，请稍后再试"})


def serve_setup(path, emit, port=0, open_browser=True):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with WorkerLock(path.with_suffix(".setup.lock")):
        server = SetupServer(SetupService(path), port)
        emit({"status": "setup_ready", "url": server.launch_url,
              "message": "请在本地浏览器填写凭证；完整设置链接仅供本设备使用，Ctrl+C 关闭设置页服务"})
        if open_browser:
            webbrowser.open(server.launch_url)
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            server.server_close()
