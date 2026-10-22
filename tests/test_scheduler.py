from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from bili_fav_relay.config import Config
from bili_fav_relay.core.control import Control
from bili_fav_relay.core.lock import WorkerLock
from bili_fav_relay.core.pipeline import Pipeline
from bili_fav_relay.core.scheduler import Scheduler
from bili_fav_relay.store import Store
from bili_fav_relay.vendor.bili_api import BiliError
from bili_fav_relay.worker import run_worker


def item(n):
    return {"id": f"BV{n:010d}", "title": str(n), "fav_time": n}


class Source:
    def __init__(self):
        self.rows, self.calls, self.error = [], 0, None

    def snapshot(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.rows


class Target:
    def __init__(self):
        self.calls, self.error, self.on_prepare = 0, None, None

    def prepare(self, path):
        if self.on_prepare:
            self.on_prepare()
        return path

    def send(self, prepared):
        self.calls += 1
        if self.error:
            raise self.error
        return "receipt"


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = Config(self.root, "1", send_enabled=True)
        self.store = Store(self.root / "state.sqlite3")
        self.now = 10000
        self.source, self.target = Source(), Target()
        self.control = Control(self.config, self.store, lambda: self.now)
        self.scheduler = Scheduler(self.config, self.store, self.factory, lambda: self.now)

    def factory(self, config):
        return Pipeline(config, self.store, self.source, lambda bv: Path(bv), self.target, lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def prepare_new(self):
        self.store.observe([item(1)])
        self.source.rows = [item(1), item(2), item(3)]

    def test_manual_mode_does_no_network_or_delivery(self):
        self.prepare_new()
        self.store.observe(self.source.rows)
        self.control.schedule(mode="manual")
        self.assertEqual(self.scheduler.tick()["status"], "manual_idle")
        self.assertEqual((self.source.calls, self.target.calls), (0, 0))

    def test_manual_sync_drains_finite_batch_once_and_preserves_mode(self):
        self.prepare_new()
        self.control.schedule(mode="manual")
        request = self.control.request_sync()
        self.scheduler.tick()
        self.assertTrue(self.control.request_sync()["coalesced"])
        self.assertEqual(self.scheduler.tick()["status"], "waiting")
        self.source.rows.append(item(4))
        self.now += 10
        self.assertEqual(self.scheduler.tick()["status"], "completed")
        self.assertEqual((self.source.calls, self.target.calls), (1, 2))
        self.assertEqual(self.control.summary()["mode"], "manual")
        self.assertEqual(self.store.get("sync_request")["id"], request["request_id"])
        self.assertEqual(self.scheduler.tick()["status"], "manual_idle")

    def test_initial_sync_only_establishes_baseline(self):
        self.source.rows = [item(1)]
        self.control.request_sync()
        self.assertEqual(self.scheduler.tick()["status"], "completed")
        self.assertEqual(self.target.calls, 0)
        self.assertTrue(self.store.get("sync_request")["scan"]["baseline_created"])

    def test_manual_respects_minimum_and_existing_failure_backoff(self):
        self.scheduler.tick()
        self.now += 10
        self.control.request_sync()
        result = self.scheduler.tick()
        self.assertEqual(result["next_action_at"], 10300)
        self.assertEqual(self.source.calls, 1)
        self.store.set("poll_failures", 1)
        self.store.set("next_poll_at", 13000)
        self.now = 10300
        self.assertEqual(self.scheduler.tick()["next_action_at"], 13000)
        self.assertEqual(self.source.calls, 1)

    def test_new_interval_applies_to_running_scheduler(self):
        self.scheduler.tick()
        self.control.schedule(minutes=5)
        self.now += 299
        self.scheduler.tick()
        self.assertEqual(self.source.calls, 1)
        self.now += 1
        self.scheduler.tick()
        self.assertEqual(self.source.calls, 2)
        self.control.schedule(minutes=30)
        self.now += 300
        self.scheduler.tick()
        self.assertEqual(self.source.calls, 2)

    def test_cancel_during_current_item_preserves_remaining_queue(self):
        self.prepare_new()
        self.control.schedule(mode="manual")
        self.control.request_sync()
        self.target.on_prepare = self.control.cancel_sync
        self.scheduler.tick()
        self.assertEqual(self.scheduler.tick()["status"], "cancelled")
        self.assertEqual(self.target.calls, 1)
        self.assertEqual(self.store.status()["counts"], {"sent": 1, "queued": 1})

    def test_timeout_stops_batch_and_cannot_be_retried_via_sync(self):
        self.prepare_new()
        self.control.request_sync()
        self.target.error = TimeoutError("secret")
        self.assertEqual(self.scheduler.tick()["status"], "blocked")
        with self.assertRaises(ValueError):
            self.control.request_sync()
        self.store.recover()
        self.scheduler.tick()
        self.assertEqual(self.target.calls, 1)

    def test_risk_control_preserves_state_and_stops_sync(self):
        self.control.request_sync()
        self.source.error = BiliError("rate_or_risk_control", "412")
        self.assertEqual(self.scheduler.tick()["status"], "failed")
        self.assertFalse(self.store.get("initialized", False))
        self.now += 100000
        self.scheduler.tick()
        self.assertEqual(self.source.calls, 1)

    def test_switch_manual_stops_automatic_batch_after_current_item(self):
        self.prepare_new()
        self.scheduler.control.config = replace(self.config, max_sends_per_cycle=2)
        self.target.on_prepare = lambda: self.control.schedule(mode="manual")
        self.scheduler.tick()
        self.now += 900
        self.assertEqual(self.scheduler.tick()["status"], "manual_idle")
        self.assertEqual((self.source.calls, self.target.calls), (1, 1))

    def test_fast_ticks_do_not_multiply_automatic_quota(self):
        self.prepare_new()
        self.scheduler.tick()
        for _ in range(20):
            self.now += 10
            self.scheduler.tick()
        self.assertEqual((self.source.calls, self.target.calls), (1, 1))

    def test_restart_resumes_remaining_explicit_task_without_rescan(self):
        self.prepare_new()
        self.control.request_sync()
        self.scheduler.tick()
        self.store.close()
        self.store = Store(self.root / "state.sqlite3")
        self.store.recover()
        self.scheduler = Scheduler(self.config, self.store, self.factory, lambda: self.now)
        self.now += 10
        self.assertEqual(self.scheduler.tick()["status"], "completed")
        self.assertEqual((self.source.calls, self.target.calls), (1, 2))

    def test_foreground_interrupt_marks_uncertain_without_extra_tick(self):
        self.prepare_new()
        request = self.control.request_sync()
        self.target.error = KeyboardInterrupt()
        with WorkerLock(self.root / "worker.lock"), patch("bili_fav_relay.worker.make_pipeline", side_effect=lambda cfg, store: self.factory(cfg)):
            with self.assertRaises(KeyboardInterrupt):
                run_worker(self.config, self.store, lambda _: None, request_id=request["request_id"])
        self.assertEqual(self.store.get("sync_request")["status"], "cancelled")
        self.assertEqual(self.store.status()["counts"], {"queued": 1, "uncertain": 1})
        self.assertTrue(self.store.get("delivery_paused"))
        self.assertEqual((self.source.calls, self.target.calls), (1, 1))


if __name__ == "__main__":
    unittest.main()
