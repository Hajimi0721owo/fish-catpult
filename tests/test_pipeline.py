from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from bili_fav_relay.config import Config
from bili_fav_relay.store import Store
from bili_fav_relay.core.pipeline import Pipeline
from bili_fav_relay.vendor.bili_api import BiliError


def item(n):
    return {"id": f"BV{n:010d}", "title": f"video {n}", "fav_time": n}


class Source:
    rows = []
    error = None
    calls = 0

    def snapshot(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.rows


class Target:
    calls = 0
    fail_prepare = False
    fail_send = False

    def prepare(self, path):
        if self.fail_prepare:
            raise RuntimeError("prepare failed")
        return "file-info"

    def send(self, prepared):
        self.calls += 1
        if self.fail_send:
            raise TimeoutError("secret-token-not-for-logs")
        return "receipt-1"


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = Config(self.root, "1", send_enabled=True)
        self.store = Store(self.root / "state.sqlite3")
        self.source, self.target = Source(), Target()
        self.now = 10000
        self.pipeline = Pipeline(self.config, self.store, self.source, lambda _: Path("test.mp4"), self.target, lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def scan(self, rows):
        self.now += 1000
        self.source.rows = rows
        return self.pipeline.scan()

    def pending(self):
        self.scan([item(1)])
        self.scan([item(1), item(2)])

    def test_baseline_new_readd_and_restart(self):
        self.assertTrue(self.scan([item(1)])["baseline_created"])
        self.assertEqual(self.store.status()["counts"], {})
        self.assertEqual(self.scan([item(1), item(2)])["queued"], 1)
        self.assertEqual(self.pipeline.deliver_one()["status"], "sent")
        self.scan([item(1)])
        self.store.close()
        self.store = Store(self.root / "state.sqlite3")
        self.pipeline.store = self.store
        self.store.recover()
        self.assertEqual(self.scan([item(1), item(2)])["queued"], 0)
        self.assertEqual(self.target.calls, 1)

    def test_empty_baseline_detects_first_video(self):
        self.scan([])
        self.assertEqual(self.scan([item(1)])["queued"], 1)

    def test_poll_cooldown_survives_restart(self):
        self.scan([])
        self.assertEqual(self.pipeline.scan()["status"], "cooldown")
        self.assertEqual(self.source.calls, 1)
        self.store.close()
        self.store = Store(self.root / "state.sqlite3")
        self.pipeline.store = self.store
        self.assertEqual(self.pipeline.scan()["status"], "cooldown")

    def test_failed_snapshot_not_committed_and_risk_pauses(self):
        self.source.error = BiliError("rate_or_risk_control", "412")
        self.assertEqual(self.pipeline.scan()["status"], "failed")
        self.assertFalse(self.store.status()["initialized"])
        self.now += 100000
        self.assertEqual(self.pipeline.scan()["status"], "paused")
        self.assertEqual(self.source.calls, 1)

    def test_send_timeout_never_automatically_retried(self):
        self.pending()
        self.target.fail_send = True
        self.assertEqual(self.pipeline.deliver_one()["status"], "uncertain")
        self.assertEqual(self.pipeline.deliver_one()["status"], "delivery_paused")
        self.store.recover()
        self.assertEqual(self.target.calls, 1)
        self.assertNotIn("secret-token", str(self.store.status()))
        with self.assertRaises(ValueError):
            self.store.retry(item(2)["id"])
        self.store.resolve(item(2)["id"], True)
        self.assertEqual(self.store.status()["counts"], {"sent": 1})

    def test_crash_recovery_stages(self):
        self.pending()
        self.store.next_job()
        self.store.recover()
        self.assertEqual(self.store.status()["counts"], {"queued": 1})
        self.store.transition(item(2)["id"], "sending")
        self.store.recover()
        self.assertEqual(self.store.status()["counts"], {"uncertain": 1})

    def test_prepare_failure_retains_queue_record(self):
        self.pending()
        self.target.fail_prepare = True
        self.assertEqual(self.pipeline.deliver_one()["status"], "failed")
        self.assertEqual(self.target.calls, 0)
        self.store.retry(item(2)["id"])
        self.assertEqual(self.store.status()["counts"], {"queued": 1})

    def test_disabled_never_downloads_or_sends(self):
        self.pending()
        self.pipeline.config = replace(self.config, send_enabled=False)
        self.assertEqual(self.pipeline.deliver_one()["status"], "sending_disabled")
        self.assertEqual(self.store.status()["counts"], {"queued": 1})

    def test_backup_restore_preserves_dedup(self):
        self.pending()
        self.pipeline.deliver_one()
        backup = self.root / "backup.sqlite3"
        self.store.backup(backup)
        restored = Store(backup)
        try:
            self.assertEqual(restored.status()["seen"], 2)
            self.assertEqual(restored.observe([item(1), item(2)])["queued"], 0)
        finally:
            restored.close()
