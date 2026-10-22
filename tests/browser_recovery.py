"""会话恢复和权限拒绝页面验收；只访问临时回环服务，不调用平台。"""
from pathlib import Path
import json
import tempfile
import threading
import time
from playwright.sync_api import sync_playwright, expect
from bili_fav_relay.setup.server import SetupServer
from bili_fav_relay.setup.service import SetupService
from bili_fav_relay.targets.errors import GROUP_PERMISSION_HINT


class Service(SetupService):
    permission_fixture = False
    wait_fixture = False

    def status(self):
        result = super().status()
        if self.permission_fixture:
            result.update(next_step="ready", folder="456", folder_title="演示收藏夹", folder_count=1,
                          bot={"name": "演示机器人"}, binding={"alias": "演示分享群"}, baseline=True,
                          seen=1, send_enabled=True, counts={"failed": 1}, mode="manual",
                          runtime={"active": True, "status": "running"},
                          control={"delivery_paused": True, "delivery_problem": {
                              "status": "failed", "code": 40034105, "message": GROUP_PERMISSION_HINT}})
        if self.wait_fixture:
            result.update(mode="auto", interval_seconds=10, counts={},
                          control={"sync": {"status": "waiting", "next_action_at": time.time() + 600}},
                          runtime={"active": True, "status": "stopping"},
                          source_access={"cooling": True, "error": "favorite_changed",
                                         "next_poll_at": time.time() + 600})
        return result


def main():
    out = Path(".local/permission-review").resolve()
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        service = Service(Path(tmp) / "config.json")
        server = SetupServer(service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1280, "height": 950}, reduced_motion="reduce")
                errors, external = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                def local_only(route):
                    if route.request.url.startswith(server.origin + "/"):
                        route.continue_()
                    else:
                        external.append("unexpected external request")
                        route.abort()
                page.route("**/*", local_only)
                page.goto(server.origin)
                expect(page.get_by_role("heading", name="无法读取设置")).to_be_visible()
                page.goto(server.origin + "/#token=invalid-fixture")
                expect(page.get_by_role("heading", name="无法读取设置")).to_be_visible()
                page.goto(server.launch_url)
                expect(page.get_by_role("heading", name="想分享哪个收藏夹？")).to_be_visible(timeout=10000)
                assert page.evaluate("location.hash") == ""
                assert page.evaluate("sessionStorage.getItem('setup-token')") == server.token
                expect(page.locator("#notice")).to_be_hidden()
                responses = [0]
                def transient(route):
                    responses[0] += 1
                    if responses[0] == 1:
                        route.fulfill(status=503, content_type="application/json", body='{"error":"fixture unavailable"}')
                    else:
                        route.continue_()
                page.route("**/api/status", transient)
                page.reload()
                expect(page.get_by_role("heading", name="无法读取设置")).to_be_visible()
                expect(page.get_by_role("heading", name="想分享哪个收藏夹？")).to_be_visible(timeout=10000)
                expect(page.locator("#notice")).to_be_hidden()
                page.unroute("**/api/status", transient)
                service.permission_fixture = True
                page.reload()
                expect(page.locator("#delivery-reason")).to_have_text(GROUP_PERMISSION_HINT)
                expect(page.locator("#dialog-live")).to_contain_text("主动发言")
                expect(page.locator("#sync-button")).to_be_disabled()
                expect(page.locator("#hud-live")).to_have_text("转发已暂停")
                expect(page.locator("#page-title")).to_have_text("设置完成，转发已暂停")
                page.screenshot(path=str(out / "permission-desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                expect(page.locator("#delivery-warning")).to_be_visible()
                page.screenshot(path=str(out / "permission-mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                service.wait_fixture = True
                page.reload()
                expect(page.locator("#dialog-live")).to_contain_text("分页结果前后不一致")
                expect(page.locator("#dialog-live")).to_contain_text("后台正在停止")
                expect(page.locator("#dialog-live")).not_to_contain_text("自动重试")
                expect(page.locator("#runtime-value")).to_have_text("正在停止…")
                expect(page.locator("#page-title")).to_have_text("设置完成，后台正在停止")
                expect(page.locator("#hud-live")).to_have_text("正在停止")
                expect(page.locator("#sync-button")).to_be_disabled()
                expect(page.locator("#sync-value")).to_contain_text("需启动后台")
                expect(page.locator("#delivery-warning")).to_be_hidden()
                page.screenshot(path=str(out / "stopping-mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.set_viewport_size({"width": 1280, "height": 950})
                page.screenshot(path=str(out / "stopping-desktop.png"), full_page=True)
                assert not errors, errors
                assert not external, external
                browser.close()
            print(json.dumps({"same_tab_session_recovery": "passed", "invalid_token_rejected": "passed",
                              "transient_recovery": "passed", "permission_hint": "passed", "stopping_hint": "passed",
                              "mobile_overflow": False,
                              "console_errors": errors, "external_requests": len(external)}))
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()


if __name__ == "__main__":
    main()
