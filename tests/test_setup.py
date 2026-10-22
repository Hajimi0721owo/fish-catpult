import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from bili_fav_relay.config import Config
from bili_fav_relay.credentials import CredentialVault, VaultError
from bili_fav_relay.core.lock import WorkerLock
from bili_fav_relay.setup.jobs import SetupJobs
from bili_fav_relay.setup.service import SetupService
from bili_fav_relay.setup.status import setup_status
from bili_fav_relay.targets.qq_gateway import QQGroupBinding


class MemoryBackend:
    def __init__(self):
        self.values = {}

    def set_password(self, service, key, value):
        self.values[service, key] = value

    def get_password(self, service, key):
        return self.values.get((service, key))

    def delete_password(self, service, key):
        del self.values[service, key]


class FakeClient:
    def __init__(self, app_id, secret):
        self.app_id = app_id

    def get_bot_info(self):
        return {"username": "测试机器人"}


class FakeGateway:
    def __init__(self, client):
        pass

    def wait_for_binding(self, code, on_ready):
        on_ready()
        return QQGroupBinding("private-group-marker", "event", "GROUP_AT_MESSAGE_CREATE")


class FakeSource:
    title = "测试收藏夹"
    calls = 0

    def __init__(self, config):
        pass

    def snapshot(self):
        FakeSource.calls += 1
        return [{"id": "BV0000000001", "title": "历史视频"}]

    def probe(self):
        return {"title": self.title, "count": 1}


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "config.json"
        self.backend = MemoryBackend()
        self.native = patch("bili_fav_relay.credentials.native_backend", return_value=self.backend)
        self.native.start()
        self.now = time.time()
        self.service = SetupService(self.path, client_factory=FakeClient, gateway_factory=FakeGateway, source_factory=FakeSource, clock=lambda: self.now)
        self.progress = []
        FakeSource.calls = 0

    def tearDown(self):
        self.native.stop()
        self.temp.cleanup()

    def action(self, action, **payload):
        return self.service.dispatch(action, payload, lambda **value: self.progress.append(value))

    def configured(self):
        self.action("folder", folder="https://space.bilibili.com/123/favlist?fid=456")
        self.action("credentials", app_id="123", secret="secret-marker-not-in-files")
        self.action("bind", alias="测试群")

    def test_fresh_setup_full_flow_and_no_secret_in_files_or_status(self):
        self.assertEqual(setup_status(self.path)["next_step"], "folder")
        self.configured()
        self.assertTrue(any("code" in p for p in self.progress))
        self.now += 301
        self.action("baseline")
        self.now += 301
        self.action("baseline")
        self.assertEqual(FakeSource.calls, 1)
        state = setup_status(self.path)
        self.assertEqual(state["next_step"], "activate")
        self.assertFalse(state["send_enabled"])
        self.assertEqual(state["seen"], 1)
        self.assertNotIn("private-group-marker", json.dumps(state))
        self.assertEqual(Config.load(self.path).credentials(), ("123", "secret-marker-not-in-files"))
        with patch("bili_fav_relay.setup.service.start_worker", return_value={"active": True}), patch("shutil.which", return_value="/fake/tool"):
            self.action("activate", mode="manual", minutes=10)
        config = Config.load(self.path)
        self.assertTrue(config.send_enabled)
        self.assertEqual(setup_status(self.path)["mode"], "manual")
        self.assertEqual(setup_status(self.path)["next_step"], "ready")
        for file in self.root.rglob("*.json"):
            self.assertNotIn("secret-marker-not-in-files", file.read_text())
        self.assertNotIn("secret-marker-not-in-files", json.dumps(self.progress))

    def test_seconds_activation_validation_and_status(self):
        self.configured()
        self.now += 10
        self.action("baseline")
        with patch("bili_fav_relay.setup.service.start_worker", return_value={"active": True}) as start, patch("shutil.which", return_value="/fake/tool"):
            for payload in ({"seconds": 9}, {"seconds": 10, "minutes": 1}, {"seconds": True}):
                with self.assertRaises(ValueError):
                    self.action("activate", mode="auto", **payload)
                self.assertFalse(Config.load(self.path).send_enabled)
            start.assert_not_called()
            self.action("activate", mode="auto", seconds=10)
            start.assert_called_once()
        self.assertEqual(self.service.status()["interval_seconds"], 10)
        self.action("control", operation="schedule", seconds=17)
        self.assertEqual(self.service.status()["interval_seconds"], 17)
        self.action("control", operation="schedule", mode="manual")
        self.assertEqual(self.service.status()["interval_seconds"], 17)
        with self.assertRaises(ValueError):
            self.action("control", operation="schedule", mode="auto", seconds=0)
        self.assertEqual(self.service.status()["mode"], "manual")

    def test_setup_status_does_not_open_vault_or_contact_network(self):
        self.configured()
        with patch("bili_fav_relay.credentials.CredentialVault.load", side_effect=AssertionError("secret access")), patch("socket.socket", side_effect=AssertionError("network")):
            self.assertTrue(setup_status(self.path)["bot"]["verified"])

    def test_cannot_activate_without_baseline_or_binding(self):
        self.action("folder", folder="456")
        with self.assertRaises(ValueError):
            self.action("activate", mode="manual", minutes=15)
        self.assertFalse(Config.load(self.path).send_enabled)

    def test_binding_prevents_switching_bot_and_existing_state_prevents_folder_change(self):
        self.configured()
        with self.assertRaises(ValueError):
            self.action("credentials", app_id="999", secret="another-secret")
        self.now += 301
        self.action("baseline")
        with self.assertRaises(ValueError):
            self.action("folder", folder="999")
        self.assertEqual(Config.load(self.path).folder, "456")

    def test_active_worker_lock_prevents_config_mutation(self):
        self.action("folder", folder="456")
        before = self.path.read_bytes()
        with WorkerLock(self.root / "data/worker.lock"):
            with self.assertRaises(ValueError):
                self.action("folder", folder="789")
        self.assertEqual(before, self.path.read_bytes())

    def test_vault_failure_does_not_write_secret_or_plaintext_fallback(self):
        self.action("folder", folder="456")
        with patch.object(self.backend, "set_password", side_effect=RuntimeError("secret-marker-not-in-files")):
            jobs = SetupJobs(self.service)
            jobs.submit("credentials", {"app_id": "123", "secret": "secret-marker-not-in-files"})
            jobs.thread.join(timeout=3)
        self.assertEqual(jobs.snapshot()["phase"], "failed")
        self.assertNotIn("secret-marker-not-in-files", json.dumps(jobs.snapshot()))
        self.assertFalse(Config.load(self.path).credential_id)
        for file in self.root.rglob("*.json"):
            self.assertNotIn("secret-marker-not-in-files", file.read_text())

    def test_invalid_credentials_cannot_leave_partial_configuration(self):
        self.action("folder", folder="456")
        with self.assertRaises(ValueError):
            self.action("credentials", app_id="abc", secret="secret")
        self.assertFalse(Config.load(self.path).credential_id)
        self.assertFalse(self.backend.values)

    def test_native_error_is_sanitized(self):
        with patch.object(self.backend, "get_password", side_effect=RuntimeError("internal-secret")):
            with self.assertRaises(VaultError) as error:
                CredentialVault(self.backend).load("a" * 32)
        self.assertNotIn("internal-secret", str(error.exception))


if __name__ == "__main__":
    unittest.main()
