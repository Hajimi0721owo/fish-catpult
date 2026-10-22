"""浏览器验收：独立临时配置与虚构凭据，外部平台适配器全部替换。"""
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import patch
from playwright.sync_api import sync_playwright, expect
from bili_fav_relay.setup.server import SetupServer
from bili_fav_relay.setup.service import SetupService
from test_setup import MemoryBackend, FakeClient, FakeGateway, FakeSource
from bili_fav_relay.vendor.bili_api import BiliError


def main():
    out = Path(".local/onboarding-review").resolve()
    out.mkdir(parents=True, exist_ok=True)
    backend = MemoryBackend()
    live = {"active": False, "status": "stopped"}
    class Source(FakeSource):
        risk = True
        def probe(self):
            if self.risk:
                raise BiliError("rate_or_risk_control", "fixture")
            return super().probe()
    class Gateway(FakeGateway):
        def wait_for_binding(self, code, on_ready):
            def ready():
                on_ready()
                time.sleep(3)
            return super().wait_for_binding(code, ready)
    def start(_):
        live.update(active=True, status="running")
        return dict(live)
    def stop(_):
        live.update(active=False, status="stopped")
        return dict(live)
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        stack.enter_context(patch("bili_fav_relay.credentials.native_backend", return_value=backend))
        stack.enter_context(patch("bili_fav_relay.setup.status.runtime_status", side_effect=lambda _: dict(live)))
        stack.enter_context(patch("bili_fav_relay.setup.service.runtime_status", side_effect=lambda _: dict(live)))
        stack.enter_context(patch("bili_fav_relay.setup.service.start_worker", side_effect=start))
        stack.enter_context(patch("bili_fav_relay.setup.service.stop_worker", side_effect=stop))
        now = [time.time()]
        service = SetupService(Path(tmp) / "config.json", client_factory=FakeClient, gateway_factory=Gateway, source_factory=Source, clock=lambda: now[0])
        server = SetupServer(service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1280, "height": 950}, device_scale_factor=1)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(server.launch_url)
                expect(page.get_by_role("heading", name="想分享哪个收藏夹？")).to_be_visible()
                page.screenshot(path=str(out / "01-folder-desktop.png"), full_page=True)
                page.locator("#folder").fill("https://space.bilibili.com/123/favlist?fid=456")
                page.get_by_role("button", name="检查并继续").click()
                expect(page.locator("#notice")).to_contain_text("暂停", timeout=10000)
                expect(page.get_by_role("button", name="恢复访问")).to_be_disabled()
                page.screenshot(path=str(out / "09-risk-wait-desktop.png"), full_page=True)
                assert not service.path.exists()
                now[0] = service.status()["source_access"]["next_poll_at"]
                page.reload()
                expect(page.get_by_role("button", name="恢复访问")).to_be_enabled(timeout=10000)
                page.get_by_role("button", name="恢复访问").click()
                expect(page.locator("#notice")).to_contain_text("访问已恢复", timeout=10000)
                Source.risk = False
                page.wait_for_timeout(5100)  # 保留 HTTP 任务层真实的 5 秒防连点限制。
                page.locator("#folder").fill("456")
                page.get_by_role("button", name="检查并继续").click()
                expect(page.get_by_role("heading", name="连接你的 QQ 机器人")).to_be_visible(timeout=10000)
                page.screenshot(path=str(out / "02-bot-desktop.png"), full_page=True)
                page.locator("#app-id").fill("123")
                page.locator("#secret").fill("browser-fixture-secret")
                page.get_by_role("button", name="验证并保存").click()
                expect(page.get_by_role("heading", name="在群里对个暗号")).to_be_visible(timeout=10000)
                page.locator("#alias").fill("周末视频分享群")
                page.get_by_role("button", name="生成口令").click()
                expect(page.locator("#binding-code")).to_be_visible(timeout=10000)
                page.screenshot(path=str(out / "03-binding-desktop.png"), full_page=True)
                expect(page.get_by_role("heading", name="最后，选个同步节奏")).to_be_visible(timeout=10000)
                expect(page.locator("#source-wait-text")).to_contain_text("下次可读取时间")
                expect(page.get_by_role("button", name="记下已有收藏")).to_be_disabled()
                now[0] += 301  # 只推进隔离服务的测试时钟，真实产品不提供跳过冷却入口。
                expect(page.get_by_role("button", name="记下已有收藏")).to_be_enabled(timeout=10000)
                page.get_by_role("button", name="记下已有收藏").click()
                expect(page.get_by_role("button", name="开始转发", exact=True)).to_be_enabled(timeout=10000)
                page.screenshot(path=str(out / "04-activate-desktop.png"), full_page=True)
                page.get_by_role("button", name="开始转发", exact=True).click()
                expect(page.get_by_role("heading", name="设置完成，正在为你转发")).to_be_visible(timeout=10000)
                expect(page.locator("#runtime-value")).to_have_text("后台运行中")
                page.screenshot(path=str(out / "05-ready-desktop.png"), full_page=True)
                page.locator("#schedule-mode").select_option("auto")
                page.locator("#schedule-minutes").fill("10")
                page.get_by_role("button", name="保存", exact=True).click()
                expect(page.locator("#mode-value")).to_have_text("每 10 分钟自动同步", timeout=10000)
                page.get_by_role("button", name="停止后台程序").click()
                expect(page.locator("#runtime-value")).to_have_text("后台已停止", timeout=10000)
                page.reload()
                expect(page.get_by_role("heading", name="设置完成，后台还没运行")).to_be_visible(timeout=10000)
                expect(page.locator("#runtime-value")).to_have_text("后台已停止")
                page.get_by_role("button", name="启动后台程序").click()
                expect(page.locator("#runtime-value")).to_have_text("后台运行中", timeout=10000)
                page.get_by_role("button", name="停止后台程序").click()
                expect(page.locator("#runtime-value")).to_have_text("后台已停止", timeout=10000)
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(out / "06-ready-mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.locator('[data-step="0"]').click()
                page.screenshot(path=str(out / "07-folder-mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.emulate_media(color_scheme="dark")
                page.wait_for_timeout(200)  # 等待颜色过渡完成后记录最终画面。
                page.screenshot(path=str(out / "08-folder-dark.png"), full_page=True)
                assert not errors, errors
                assert "browser-fixture-secret" not in json.dumps(service.status())
                browser.close()
            report = {"wizard": "passed", "risk_recovery": "passed", "shared_cooldown": "passed", "schedule_and_stop": "passed", "reopen_and_restart": "passed", "mobile_overflow": False,
                      "console_errors": errors, "real_bilibili_requests": 0, "real_qq_messages": 0,
                      "credentials_backend": "in-memory fixture", "screenshots": str(out)}
            (out / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps(report, ensure_ascii=False))
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()


if __name__ == "__main__":
    main()
