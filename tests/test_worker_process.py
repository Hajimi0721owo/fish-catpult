"""两个真实 CLI 进程共享控制状态；外部网络在 worker 内禁用。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from bili_fav_relay.store import Store


WORKER = r'''
import socket
from pathlib import Path
from bili_fav_relay import cli, worker
from bili_fav_relay.core.pipeline import Pipeline
def no_network(*args, **kwargs):
    raise AssertionError("offline test: network prohibited")
socket.socket = no_network
class Source:
    def snapshot(self):
        with Path("source-calls.txt").open("a") as handle:
            handle.write("scan\n")
        return [{"id": "BV0000000001", "title": "old"},
                {"id": "BV0000000002", "title": "new"}]
class Target:
    def prepare(self, path): return path
    def send(self, prepared): return "offline-receipt"
worker.make_pipeline = lambda config, store: Pipeline(config, store, Source(), lambda bv: Path(bv), Target())
raise SystemExit(cli.main())
'''


class WorkerProcessTest(unittest.TestCase):
    def test_live_worker_accepts_cli_sync_and_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            config.write_text(json.dumps({"folder": "1", "data_dir": "data", "sync_mode": "manual", "send_enabled": True}))
            store = Store(root / "data/state.sqlite3")
            store.assert_folder("1")
            store.observe([{"id": "BV0000000001", "title": "old"}])
            store.close()
            env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
            def command(*args):
                run = subprocess.run([sys.executable, "-m", "bili_fav_relay", "--config", str(config), *args],
                                     cwd=root, env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                return [json.loads(line) for line in run.stdout.splitlines()]
            def state():
                current = Store(root / "data/state.sqlite3")
                try:
                    return current.get("sync_request")
                finally:
                    current.close()
            def await_status(expected):
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    observed = state()
                    if observed and observed["status"] == expected:
                        return observed
                    time.sleep(0.05)
                self.fail(f"worker did not reach {expected}: {state()}")
            with (root / "worker.log").open("w+") as log:
                proc = subprocess.Popen([sys.executable, "-u", "-c", WORKER, "--config", str(config), "run"],
                                        cwd=root, env=env, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 5
                    while "manual_idle" not in (root / "worker.log").read_text():
                        self.assertIsNone(proc.poll(), (root / "worker.log").read_text())
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(0.05)
                    self.assertEqual(command("schedule", "--minutes", "5")[-1]["interval_minutes"], 5)
                    self.assertEqual(command("sync")[-1]["status"], "queued")
                    self.assertEqual(await_status("completed")["counts"], {"sent": 1})
                    command("sync")
                    self.assertTrue(command("sync")[0]["coalesced"])
                    await_status("waiting")
                    command("sync", "--cancel")
                    await_status("cancelled")
                    status = command("status")[-1]
                    self.assertEqual(status["counts"], {"sent": 1})
                    self.assertEqual(status["control"]["mode"], "manual")
                    self.assertEqual((root / "source-calls.txt").read_text(), "scan\n")
                finally:
                    try:
                        # Stop the actual worker, including when Python uses a launcher process.
                        command("stop")
                        proc.wait(timeout=8)
                    finally:
                        if proc.poll() is None:
                            proc.terminate()
                            try:
                                proc.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                                proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
