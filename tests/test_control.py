from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from bili_fav_relay.cli import main
from bili_fav_relay.config import Config
from bili_fav_relay.core.control import Control
from bili_fav_relay.core.lock import WorkerLock
from bili_fav_relay.store import Store


class ControlTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = Config(self.root / "data", "1", send_enabled=True)
        self.store = Store(self.config.data_dir / "state.sqlite3")
        self.store.assert_folder("1")
        self.control = Control(self.config, self.store)
        self.path = self.root / "config.json"
        self.path.write_text(json.dumps({"data_dir": "data", "folder": "1", "send_enabled": True}))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def invoke(self, *args):
        output = io.StringIO()
        with patch("sys.argv", ["bili-fav-relay", "--config", str(self.path), *args]), redirect_stdout(output):
            code = main()
        return code, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_settings_persist_without_rewriting_config(self):
        before = self.path.read_bytes()
        self.control.schedule(mode="manual", minutes=5)
        second = Store(self.config.data_dir / "state.sqlite3")
        try:
            summary = Control(self.config, second).summary()
            self.assertEqual(summary["mode"], "manual")
            self.assertEqual(summary["interval_minutes"], 5)
            self.assertIsNone(summary["next_auto_scan_at"])
        finally:
            second.close()
        self.assertEqual(before, self.path.read_bytes())

    def test_invalid_interval_does_not_partially_change_mode(self):
        for value in (0, 1, 4, 1441, True, 5.5):
            with self.assertRaises(ValueError):
                self.control.schedule(mode="manual", minutes=value)
        self.assertEqual(self.control.summary()["mode"], "auto")

    def test_simultaneous_requests_coalesce(self):
        def request(_):
            store = Store(self.config.data_dir / "state.sqlite3")
            try:
                return Control(self.config, store).request_sync()
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(request, range(8)))
        self.assertEqual(len({r["request_id"] for r in results}), 1)
        self.assertEqual(sum(not r["coalesced"] for r in results), 1)

    def test_disabled_and_paused_do_not_enqueue_or_enable(self):
        with self.assertRaises(ValueError):
            Control(replace(self.config, send_enabled=False), self.store).request_sync()
        self.assertIsNone(self.store.get("sync_request"))
        self.store.set("delivery_paused", True)
        with self.assertRaises(ValueError):
            self.control.request_sync()
        self.assertIsNone(self.store.get("sync_request"))

    def test_live_lock_does_not_block_controls_or_recover_sending(self):
        self.store.observe([])
        self.store.observe([{"id": "BV0000000001", "title": "test"}])
        self.store.transition("BV0000000001", "sending")
        with WorkerLock(self.config.data_dir / "worker.lock"), patch("socket.socket", side_effect=AssertionError("network")):
            self.assertEqual(self.invoke("schedule", "--mode", "manual", "--minutes", "5")[0], 0)
            code, output = self.invoke("sync")
            self.assertEqual(code, 0)
            self.assertEqual(output[-1]["status"], "queued")
            self.assertEqual(self.invoke("status")[0], 0)
        self.assertEqual(self.store.status()["counts"], {"sending": 1})

    def test_summary_does_not_expose_environment_secrets(self):
        with patch.dict("os.environ", {"BILI_RELAY_QQ_APP_SECRET": "secret-marker"}):
            self.assertNotIn("secret-marker", json.dumps(self.control.summary()))

    def test_other_folder_cannot_control_existing_state(self):
        with self.assertRaises(ValueError):
            self.store.assert_folder("2")
        self.assertEqual(self.store.get("folder"), "1")


if __name__ == "__main__":
    unittest.main()
