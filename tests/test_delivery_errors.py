"""QQ 明确拒绝与结果不明分流；只使用虚构响应，不请求平台。"""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from bili_fav_relay.cli import main
from bili_fav_relay.config import Config
from bili_fav_relay.core.control import Control
from bili_fav_relay.core.delivery_errors import DeliveryError
from bili_fav_relay.core.pipeline import Pipeline
from bili_fav_relay.setup.qq_hints import qq_reason
from bili_fav_relay.setup.status import setup_status
from bili_fav_relay.store import Store
from bili_fav_relay.targets.errors import QQBotError
from bili_fav_relay.targets.qqbot import QQBotClient
from bili_fav_relay.targets.target import Target


class Client:
    def __init__(self, error):
        self.error, self.calls = error, 0

    def send_group_media(self, group, info):
        self.calls += 1
        if self.error:
            raise self.error
        return {"id": "fixture-receipt"}


class DeliveryErrorsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "config.json"
        self.path.write_text('{"data_dir":"data","folder":"1","send_enabled":true,"sync_mode":"manual"}')
        self.config = Config.load(self.path)
        self.store = Store(self.config.data_dir / "state.sqlite3")
        self.addCleanup(lambda: self.store.close())
        self.store.assert_folder("1")
        self.store.observe([])
        self.rows = [{"id": "BV0000000001", "title": "fixture"}]
        self.store.observe(self.rows)
        self.client = Client(QQBotError("secret-response-marker", http_status=400, code=40034105))
        self.target = Target.__new__(Target)
        self.target.client, self.target.group = self.client, "fixture-group"
        self.target.prepare = lambda _: "fixture-file"
        self.pipeline = Pipeline(self.config, self.store, None, lambda _: Path("fixture.mp4"), self.target)

    def test_permission_rejection_persists_pauses_and_requires_explicit_retry(self):
        result = self.pipeline.deliver_one()
        self.assertEqual((result["status"], result["http_status"], result["code"]), ("failed", 400, 40034105))
        self.assertIn("主动发言", result["message"])
        self.assertEqual(self.store.status()["counts"], {"failed": 1})
        self.assertTrue(self.store.get("delivery_paused"))
        self.assertEqual(self.pipeline.deliver_one()["status"], "delivery_paused")
        self.store.close()
        self.store = Store(self.config.data_dir / "state.sqlite3")
        self.pipeline.store = self.store
        self.store.recover()
        self.assertEqual(Control(self.config, self.store).summary()["delivery_problem"], result)
        self.assertEqual(self.pipeline.deliver_one()["status"], "delivery_paused")
        self.assertEqual(self.store.observe(self.rows)["queued"], 0)
        self.store.retry(self.rows[0]["id"])
        self.assertEqual(self.pipeline.deliver_one()["status"], "delivery_paused")
        self.client.error = None
        self.store.set("delivery_paused", False)
        self.assertEqual(self.pipeline.deliver_one()["status"], "sent")
        self.assertEqual(self.pipeline.deliver_one()["status"], "idle")
        self.assertEqual(self.client.calls, 2)
        self.assertIsNone(self.store.get("delivery_problem"))
        self.assertEqual(self.store.observe(self.rows)["queued"], 0)

    def test_cli_and_setup_status_show_safe_actionable_diagnostics(self):
        self.pipeline.deliver_one()
        out = io.StringIO()
        with patch("sys.argv", ["relay", "--config", str(self.path), "status"]), redirect_stdout(out), patch("socket.socket", side_effect=AssertionError("network")):
            self.assertEqual(main(), 0)
            state = setup_status(self.path)
        result = json.loads(out.getvalue())
        for control in (result["control"], state["control"]):
            self.assertEqual(control["delivery_problem"]["code"], 40034105)
            self.assertIn("主动发言", control["delivery_problem"]["message"])
        self.assertNotIn("secret-response-marker", out.getvalue() + json.dumps(state))

    def test_timeout_and_unknown_or_server_errors_remain_uncertain(self):
        errors = [TimeoutError("secret-response-marker"), QQBotError("secret-response-marker"),
                  QQBotError("secret-response-marker", http_status=503, code=40034105),
                  QQBotError("secret-response-marker", http_status=400, code=12345)]
        for error in errors:
            with self.subTest(error=type(error).__name__, status=getattr(error, "http_status", None)):
                self.store.transition(self.rows[0]["id"], "queued")
                self.store.set("delivery_paused", False)
                self.client.error = error
                result = self.pipeline.deliver_one()
                self.assertEqual(result["status"], "uncertain")
                self.assertTrue(self.store.get("delivery_paused"))
                with self.assertRaises(ValueError):
                    self.store.retry(self.rows[0]["id"])
                self.assertNotIn("secret-response-marker", json.dumps(result))

    def test_no_receipt_remains_uncertain(self):
        self.client.send_group_media = lambda *_: {}
        self.assertEqual(self.pipeline.deliver_one()["status"], "uncertain")

    def test_http_error_retains_only_numeric_fields(self):
        client = QQBotClient("fixture-app", "fixture-secret")
        response = HTTPError("https://api.bot.qq.com/fixture", 400, "secret-response-marker", {},
                             io.BytesIO(b'{"code":40034105,"message":"secret-response-marker"}'))
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = response
            with self.assertRaises(QQBotError) as caught:
                client._request_json("POST", "https://api.bot.qq.com/fixture")
        error = caught.exception
        self.assertEqual((error.http_status, error.code), (400, 40034105))
        self.assertNotIn("secret-response-marker", str(error))
        self.assertIn("主动发言", qq_reason(error))
        self.client.error = error
        with self.assertRaises(DeliveryError) as caught:
            self.target.send("fixture-file")
        self.assertTrue(caught.exception.rejected)

    def test_success_http_with_business_error_is_also_classified(self):
        client = QQBotClient("fixture-app", "fixture-secret")
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"code":"40034105","message":"secret-response-marker"}'
            with self.assertRaises(QQBotError) as caught:
                client._request_json("POST", "https://api.bot.qq.com/fixture")
        self.client.error = caught.exception
        self.assertEqual(self.pipeline.deliver_one()["status"], "failed")

    def test_untrusted_business_error_values_never_become_public_messages(self):
        client = QQBotClient("fixture-app", "fixture-secret")
        for code in ("secret-response-marker", {"token": "secret-response-marker"}, ["secret-response-marker"]):
            with patch("urllib.request.build_opener") as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps({"code": code}).encode()
                with self.assertRaises(QQBotError) as caught:
                    client._request_json("POST", "https://api.bot.qq.com/fixture")
            self.assertIsNone(caught.exception.code)
            self.assertNotIn("secret-response-marker", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
