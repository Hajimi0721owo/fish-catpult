"""候选失败不得改变订阅；预检预算跨服务重启，风控只能显式恢复。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from bili_fav_relay.config import Config
from bili_fav_relay.setup.service import SetupService
from bili_fav_relay.setup.status import setup_status
from bili_fav_relay.store import Store
from bili_fav_relay.vendor.bili_api import BiliError


class Source:
    calls = 0
    error = None
    title = "fixture"

    def __init__(self, config):
        pass

    def probe(self):
        Source.calls += 1
        if Source.error:
            raise Source.error
        return {"title": self.title, "count": 1}

    def snapshot(self):
        Source.calls += 1
        return [{"id": "BV0000000001", "title": "old"}]


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"
        self.now = 1_000_000_000
        Source.calls, Source.error = 0, None
        self.service = self.reopen()

    def reopen(self):
        return SetupService(self.path, source_factory=Source, clock=lambda: self.now)

    def action(self, name="folder", **payload):
        return self.service.dispatch(name, payload, lambda **_: None)

    def db(self):
        store = Store(self.path.parent / "data/state.sqlite3")
        self.addCleanup(store.close)
        return store

    def saved(self):
        self.action(folder="456")
        self.now += 301

    def test_failed_candidate_preserves_config_folder_metadata_and_pause(self):
        self.saved()
        store = self.db()
        store.set("source_paused", True)
        store.set("poll_error", "api_error")
        info = self.path.parent / "data/setup-info.json"
        before = self.path.read_bytes(), info.read_bytes()
        Source.error = BiliError("api_error", "private", api_code=-403)
        with self.assertRaisesRegex(ValueError, "私密"):
            self.action(folder="789")
        self.assertEqual((self.path.read_bytes(), info.read_bytes()), before)
        self.assertEqual(store.get("folder"), "456")
        self.assertTrue(store.get("source_paused"))
        self.assertFalse(store.get("initialized", False))

    def test_config_write_failure_rolls_back_folder_and_pause(self):
        self.saved()
        store = self.db()
        store.set("source_paused", True)
        store.set("poll_error", "api_error")
        with patch("bili_fav_relay.setup.folder_setup.private_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.action(folder="789")
        self.assertEqual(Config.load(self.path).folder, "456")
        self.assertEqual(store.get("folder"), "456")
        self.assertTrue(store.get("source_paused"))

    def test_successful_switch_preserves_bot_binding_and_records_no_history(self):
        self.saved()
        binding = self.path.parent / "data/binding.json"
        binding.write_text('{"app_id":"123","group_openid":"fixture-group"}')
        before = binding.read_bytes()
        self.action(folder="789")
        self.assertEqual(Config.load(self.path).folder, "789")
        self.assertEqual(self.db().get("folder"), "789")
        self.assertEqual(binding.read_bytes(), before)
        self.assertFalse(self.db().get("initialized", False))
        self.assertEqual(self.db().status()["seen"], 0)

    def test_precheck_budget_survives_restart_and_folder_change(self):
        self.action(folder="456")
        self.service = self.reopen()
        result = self.action(folder="789")
        self.assertEqual(result["status"], "cooldown")
        self.assertEqual(Source.calls, 1)
        self.assertEqual(Config.load(self.path).folder, "456")
        self.now += 300
        self.action(folder="789")
        self.assertEqual(Source.calls, 2)

    def test_probe_then_baseline_obeys_shared_minimum_interval(self):
        self.action(folder="456")
        self.assertEqual(self.action("baseline")["status"], "cooldown")
        self.assertEqual(Source.calls, 1)
        self.now += 9
        self.assertEqual(self.action("baseline")["status"], "cooldown")
        self.assertEqual(Source.calls, 1)
        self.now += 1
        self.action("baseline")
        self.assertEqual(Source.calls, 2)
        self.assertEqual(self.db().status()["seen"], 1)
        self.assertEqual(self.db().status()["counts"].get("queued", 0), 0)

    def test_first_probe_risk_pause_visible_without_config_and_recovers_explicitly(self):
        Source.error = BiliError("rate_or_risk_control", "fake risk")
        with self.assertRaisesRegex(ValueError, "暂停"):
            self.action(folder="456")
        self.assertFalse(self.path.exists())
        state = setup_status(self.path, clock=lambda: self.now)
        self.assertTrue(state["source_access"]["paused"])
        self.assertTrue(state["source_access"]["cooling"])
        self.service = self.reopen()
        self.assertEqual(self.action("resume_source")["status"], "cooldown")
        Source.error = None
        self.now = state["source_access"]["next_poll_at"]
        with self.assertRaisesRegex(ValueError, "暂停"):
            self.action(folder="789")
        self.assertEqual(Source.calls, 1)
        self.db().set("delivery_paused", True)
        self.action("resume_source")
        self.assertTrue(self.db().get("delivery_paused"))
        self.assertEqual(Source.calls, 1)
        self.action(folder="789")
        self.assertEqual(Source.calls, 2)

    def test_network_failure_backoff_is_not_shortened_by_reopening(self):
        Source.error = BiliError("network", "fixture")
        with self.assertRaises(ValueError):
            self.action(folder="456")
        due = self.db().get("next_poll_at")
        self.now += 301
        self.service = self.reopen()
        result = self.action(folder="789")
        self.assertEqual(result["next_poll_at"], due)
        self.assertEqual(Source.calls, 1)

    def test_existing_baseline_cannot_resume_or_switch_via_setup(self):
        self.saved()
        self.action("baseline")
        calls = Source.calls
        for name, payload in [("folder", {"folder": "789"}), ("resume_source", {})]:
            with self.assertRaises(ValueError):
                self.action(name, **payload)
        self.assertEqual(Source.calls, calls)
