import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from bili_fav_relay.config import Config
from bili_fav_relay.core.lock import WorkerLock
from bili_fav_relay.runtime import _windows_children, runtime_status, start_worker, stop_worker
from bili_fav_relay.store import Store


class RuntimeTest(unittest.TestCase):
    def test_detached_worker_start_handshake_dedup_and_graceful_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "config.json"
            path.write_text(json.dumps({"folder": "1", "data_dir": "data", "sync_mode": "manual", "send_enabled": False}))
            config = Config.load(path)
            child_count = len(_windows_children)
            started = start_worker(path)
            pid = started["pid"]
            try:
                self.assertTrue(started["active"])
                again = start_worker(path)
                self.assertTrue(again["already_running"])
                self.assertEqual(again["pid"], pid)
                self.assertEqual(runtime_status(config)["last_result"], {"status": "manual_idle"})
                self.assertEqual(stop_worker(config)["status"], "stop_requested")
                deadline = time.monotonic() + 6
                while runtime_status(config)["active"] and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertEqual(runtime_status(config)["status"], "stopped")
                self.assertIn('"status": "stopped"', (root / "data/worker.log").read_text())
            finally:
                if runtime_status(config)["active"]:
                    stop_worker(config)
                    time.sleep(2.2)
                if os.name != "nt":
                    os.waitpid(pid, 0)
                else:
                    for child in _windows_children[child_count:]:
                        child.wait(timeout=5)

    def test_stale_worker_metadata_cannot_claim_an_unrelated_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(Path(tmp), "1")
            store = Store(config.data_dir / "state.sqlite3")
            store.set("worker_session", {"active": True, "id": "stale", "pid": 123})
            store.close()
            with WorkerLock(config.data_dir / "worker.lock"):
                self.assertEqual(runtime_status(config)["status"], "busy")
                self.assertFalse(stop_worker(config)["active"])


if __name__ == "__main__":
    unittest.main()
