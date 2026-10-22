"""收藏夹预检与错误说明：离线替身，不请求 B 站或 QQ。"""
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import patch
from bili_fav_relay.config import Config, folder_id
from bili_fav_relay.setup.folder_check import extract_link, pages_for
from bili_fav_relay.setup.qq_hints import qq_reason
from bili_fav_relay.setup.service import SetupService
from bili_fav_relay.setup.status import setup_status
from bili_fav_relay.store import Store
from bili_fav_relay.targets.errors import QQBotError
from bili_fav_relay.targets.qqbot import _body_code
from bili_fav_relay.vendor.bili_api import BiliError
from test_setup import FakeClient, FakeGateway, FakeSource, MemoryBackend


class Probe(FakeSource):
    result = {"title": "测试收藏夹", "count": 49}
    error = None
    probes = 0

    def probe(self):
        Probe.probes += 1
        if Probe.error:
            raise Probe.error
        return dict(Probe.result)


class FolderCheckTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.json"
        self.native = patch("bili_fav_relay.credentials.native_backend", return_value=MemoryBackend())
        self.native.start()
        Probe.result, Probe.error, Probe.probes = {"title": "测试收藏夹", "count": 49}, None, 0
        self.now = time.time()
        self.service = SetupService(self.path, client_factory=FakeClient, gateway_factory=FakeGateway, source_factory=Probe, clock=lambda: self.now)

    def tearDown(self):
        self.native.stop()
        self.temp.cleanup()

    def action(self, action, **payload):
        return self.service.dispatch(action, payload, lambda **_: None)

    def test_share_text_and_mobile_links(self):
        self.assertEqual(extract_link("【我的收藏】 https://space.bilibili.com/1/favlist?fid=456 来看看"),
                         "https://space.bilibili.com/1/favlist?fid=456")
        self.assertEqual(extract_link(" 456 "), "456")
        self.assertEqual(folder_id("https://m.bilibili.com/medialist/detail/ml789?x=1"), "789")
        self.assertEqual(folder_id("https://www.bilibili.com/list/ml321"), "321")
        result = self.action("folder", folder="复制这段 https://space.bilibili.com/1/favlist?fid=456，打开看")
        self.assertIn("「测试收藏夹」", result["message"])
        state = setup_status(self.path)
        self.assertEqual((state["folder"], state["folder_title"], state["folder_count"]), ("456", "测试收藏夹", 49))

    def test_private_folder_is_rejected_before_saving(self):
        Probe.error = BiliError("api_error", "denied", api_code=-403)
        with self.assertRaisesRegex(ValueError, "私密"):
            self.action("folder", folder="456")
        self.assertFalse(self.path.exists())

    def test_page_budget_follows_folder_size_and_huge_folder_is_refused(self):
        Probe.result = {"title": "大收藏夹", "count": 230}
        self.action("folder", folder="456")
        self.assertEqual(Config.load(self.path).max_pages, 13)
        self.assertEqual((pages_for(0), pages_for(999), pages_for(2000)), (5, 50, 50))
        self.now += 301
        Probe.result = {"title": "太大", "count": 1001}
        with self.assertRaisesRegex(ValueError, "专门用来分享"):
            self.action("folder", folder="789")
        self.assertEqual(Config.load(self.path).folder, "456")

    def test_unchanged_folder_after_baseline_does_not_probe_again(self):
        self.action("folder", folder="456")
        self.action("credentials", app_id="123", secret="s")
        self.action("bind", alias="群")
        self.now += 301
        self.action("baseline")
        self.action("folder", folder="456")
        self.assertEqual(Probe.probes, 1)

    def failed_store(self, error):
        self.action("folder", folder="456")
        config = Config.load(self.path)
        store = Store(config.data_dir / "state.sqlite3")
        store.assert_folder("456")
        store.set("source_paused", True)
        store.set("poll_error", error)
        store.set("next_poll_at", 4_000_000_000)
        store.close()
        return config

    def test_failed_first_read_can_switch_folder_only_after_cooldown(self):
        config = self.failed_store("api_error")
        before = Probe.probes
        result = self.action("folder", folder="789")
        self.assertEqual(result["status"], "cooldown")
        self.assertEqual(Probe.probes, before)
        self.assertEqual(Config.load(self.path).folder, "456")
        self.now = 4_000_000_001
        self.action("folder", folder="789")
        store = Store(config.data_dir / "state.sqlite3")
        try:
            self.assertEqual(store.get("folder"), "789")
            self.assertFalse(store.get("source_paused"))
            self.assertGreater(store.get("next_poll_at"), self.now)
        finally:
            store.close()

    def test_risk_control_pause_blocks_probe_and_short_link_resolution(self):
        config = self.failed_store("rate_or_risk_control")
        before = Probe.probes
        with patch("bili_fav_relay.setup.folder_setup.resolve_folder", side_effect=AssertionError("network")):
            with self.assertRaisesRegex(ValueError, "暂停"):
                self.action("folder", folder="https://b23.tv/test")
        self.assertEqual(Probe.probes, before)
        store = Store(config.data_dir / "state.sqlite3")
        try:
            self.assertTrue(store.get("source_paused"))
            self.assertEqual(store.get("folder"), "456")
        finally:
            store.close()


class QQHintTest(unittest.TestCase):
    def test_ip_whitelist_code_is_read_from_http_error_body(self):
        body = io.BytesIO(json.dumps({"code": 11298, "message": "token-like-text"}).encode())
        error = urllib.error.HTTPError("https://api.sgroup.qq.com", 401, "x", {}, body)
        self.assertEqual(_body_code(error), 11298)
        reason = qq_reason(QQBotError("QQ API HTTP 401 rejected: 11298"))
        self.assertIn("IP 白名单", reason)
        self.assertNotIn("token-like-text", reason)

    def test_other_errors_have_actionable_text(self):
        self.assertIn("AppSecret", qq_reason(QQBotError("QQ API rejected: 100016")))
        self.assertIn("网络", qq_reason(QQBotError("QQ API network error")))
        self.assertIn("重新生成", qq_reason(QQBotError("等待绑定超时（300 秒），没有收到正确的群消息"), "绑定"))
        self.assertIsNone(qq_reason(RuntimeError("other")))


if __name__ == "__main__":
    unittest.main()
