import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from bili_fav_relay.setup.server import SetupServer
from bili_fav_relay.setup.service import SetupService
from test_setup import FakeSource


class SetupServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.json"
        self.server = SetupServer(SetupService(self.path, source_factory=FakeSource))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        self.temp.cleanup()

    def request(self, method="GET", path="/api/status", headers=None, payload=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            conn.request(method, path, body=payload, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode()
        finally:
            conn.close()

    def auth(self):
        return {"X-Setup-Token": self.server.token, "Origin": self.server.origin, "Content-Type": "application/json"}

    def test_token_required_and_no_cross_origin_or_rebinding(self):
        self.assertEqual(self.request()[0], 401)
        self.assertEqual(self.request(headers={**self.auth(), "Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request(headers={**self.auth(), "Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/action", {"X-Setup-Token": self.server.token}, '{}')[0], 403)
        self.assertFalse(self.path.exists())

    def test_valid_status_and_no_token_in_html(self):
        code, headers, body = self.request(headers=self.auth())
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["next_step"], "folder")
        code, headers, body = self.request(path="/")
        self.assertEqual(code, 200)
        self.assertNotIn(self.server.token, body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_page_assets_are_whitelisted_and_art_is_served_as_png(self):
        def get(path):
            conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
            try:
                conn.request("GET", path)
                response = conn.getresponse()
                return response.status, response.getheader("Content-Type"), response.read()
            finally:
                conn.close()
        for path, mime in [("/room.css", "text/css"), ("/cast.js", "text/javascript"), ("/art/dsf-happy.png", "image/png")]:
            code, content_type, body = get(path)
            self.assertEqual((code, content_type.split(";")[0]), (200, mime), path)
        self.assertTrue(get("/art/hachi-calm.png")[2].startswith(b"\x89PNG"))
        for path in ["/art/../server.py", "/../service.py", "/art/missing.png", "/server.py", "/%2e%2e/service.py", "/art/a/b.png"]:
            self.assertEqual(get(path)[0], 404, path)

    def test_request_size_and_bad_json_do_not_echo_secret(self):
        headers = self.auth()
        self.assertEqual(self.request("POST", "/api/action", headers, "x" * 16385)[0], 413)
        code, _, body = self.request("POST", "/api/action", headers, '{"secret":"private-marker"')
        self.assertEqual(code, 400)
        self.assertNotIn("private-marker", body)
        self.assertEqual(self.request("POST", "/api/action", {**headers, "Content-Type": "text/plain"}, '{}')[0], 415)

    def test_async_folder_action_and_rate_limit(self):
        payload = json.dumps({"action": "folder", "payload": {"folder": "456"}})
        self.assertEqual(self.request("POST", "/api/action", self.auth(), payload)[0], 202)
        self.server.jobs.thread.join(timeout=3)
        result = json.loads(self.request(headers=self.auth())[2])
        self.assertEqual(result["folder"], "456")
        self.assertEqual(result["job"]["phase"], "done")
        import time
        self.server.requests = [time.monotonic()] * 30
        self.assertEqual(self.request("POST", "/api/action", self.auth(), payload)[0], 429)


if __name__ == "__main__":
    unittest.main()
