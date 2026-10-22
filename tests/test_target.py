from pathlib import Path
import tempfile
import unittest
from bili_fav_relay.targets.qqbot import QQBotClient, QQBotError
from bili_fav_relay.targets.qq_gateway import binding_from_dispatch
from bili_fav_relay.core.lock import WorkerLock


class UploadFixture(QQBotClient):
    def __init__(self, parts):
        super().__init__("test-app", "test-secret")
        self.parts, self.calls, self.blocks = parts, [], []

    def call_api(self, method, path, *, payload=None):
        self.calls.append((path, payload))
        if path.endswith("upload_prepare"):
            return {"upload_id": "upload", "parts": self.parts, "block_size": "4"}
        if path.endswith("/files"):
            return {"file_info": "prepared"}
        return {}

    def _put_upload_part(self, url, data, **kwargs):
        self.blocks.append(data)


class TargetTest(unittest.TestCase):
    def test_upload_respects_one_based_indexes_and_no_send(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / "video.mp4"
            file.write_bytes(b"abcdefghij")
            parts = [{"index": i, "block_size": 4, "presigned_url": "https://cos.qq.com/x"} for i in [1, 2, 3]]
            client = UploadFixture(parts)
            self.assertEqual(client.upload_group_video("group", file)["file_info"], "prepared")
            self.assertEqual(client.blocks, [b"abcd", b"efgh", b"ij"])
            acknowledgements = [p["part_index"] for route, p in client.calls if route.endswith("upload_part_finish")]
            self.assertEqual(acknowledgements, [1, 2, 3])
            self.assertFalse(client.calls[-1][1]["srv_send_msg"])
            self.assertFalse(any(route.endswith("/messages") for route, _ in client.calls))

    def test_missing_or_duplicate_parts_fail(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / "video.mp4"
            file.write_bytes(b"abcdefghij")
            for indexes in [[1], [1, 1, 2]]:
                client = UploadFixture([{"index": i, "block_size": 4, "presigned_url": "https://cos.qq.com/x"} for i in indexes])
                with self.assertRaises(QQBotError):
                    client.upload_group_video("group", file)
                self.assertFalse(any(route.endswith("/files") for route, _ in client.calls))

    def test_binding_requires_command_code_and_event(self):
        payload = {"op": 0, "t": "GROUP_AT_MESSAGE_CREATE", "d": {"content": "<@bot> 绑定 123456", "group_openid": "g1"}}
        self.assertEqual(binding_from_dispatch(payload, "123456").group_openid, "g1")
        self.assertIsNone(binding_from_dispatch(payload, "999999"))
        payload["d"]["content"] = "视频 123456"
        self.assertIsNone(binding_from_dispatch(payload, "123456"))

    def test_second_worker_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            with WorkerLock(Path(root) / "worker.lock"):
                with self.assertRaises(ValueError):
                    WorkerLock(Path(root) / "worker.lock").acquire()

    def test_credentials_not_in_repr(self):
        self.assertNotIn("test-secret", repr(QQBotClient("test-app", "test-secret")))

    def test_official_api_origin_required(self):
        with self.assertRaises(ValueError):
            QQBotClient("app", "secret", api_base="http://evil.example")
