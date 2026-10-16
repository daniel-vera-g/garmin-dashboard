"""serve.py: Host-header check (DNS rebinding), page allow-list, sync guard."""
import http.client
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

import serve


class Serve(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        cls.port = cls.server.server_port
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method: str, path: str, host: str | None, headers: dict | None = None,
                body: bytes | None = None) -> int:
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest(method, path, skip_host=True)
        if host is not None:
            c.putheader("Host", host)
        for k, v in (headers or {}).items():
            c.putheader(k, v)
        c.endheaders(body)
        status = c.getresponse().status
        c.close()
        return status

    def test_loopback_hosts_are_accepted(self):
        for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            self.assertEqual(self.request("GET", "/api/status", host), 200, host)

    def test_foreign_or_missing_host_is_rejected(self):
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1:1", "localhost", None):
            self.assertEqual(self.request("GET", "/api/status", host), 421, host)
            self.assertEqual(self.request("POST", "/api/sync", host, {"X-Sync": "1"}), 421, host)

    def test_only_listed_pages_are_served(self):
        host = f"127.0.0.1:{self.port}"
        for path in ("/data/cache/sleep.json", "/private/config.toml", "/../private/config.toml", "/config.example.toml", "/serve.py"):
            self.assertEqual(self.request("GET", path, host), 404, path)

    def test_sync_needs_the_custom_header(self):
        host = f"127.0.0.1:{self.port}"
        with mock.patch.object(serve, "start_sync", return_value=True) as started:
            self.assertEqual(self.request("POST", "/api/sync", host), 403)
            started.assert_not_called()
            self.assertEqual(self.request("POST", "/api/sync", host, {"X-Sync": "1"}), 200)
            started.assert_called_once()


    def test_coach_endpoints_are_guarded_and_size_limited(self):
        host = f"127.0.0.1:{self.port}"
        with mock.patch.object(serve, "run_coach", return_value={"ok": True, "changes": [], "warnings": []}) as rc:
            self.assertEqual(self.request("POST", "/api/coach/apply", host,
                                          {"Content-Length": "2"}, b"{}"), 403)
            self.assertEqual(self.request("POST", "/api/coach/apply", "evil.example",
                                          {"X-Sync": "1", "Content-Length": "2"}, b"{}"), 421)
            big = b'{"text": "' + b"x" * 70000 + b'"}'
            self.assertEqual(self.request("POST", "/api/coach/preview", host,
                                          {"X-Sync": "1", "Content-Length": str(len(big))}, big), 413)
            rc.assert_not_called()
            body = b'{"text": "n = 1"}'
            self.assertEqual(self.request("POST", "/api/coach/preview", host,
                                          {"X-Sync": "1", "Content-Length": str(len(body))}, body), 200)
            rc.assert_called_once_with("preview", "n = 1")


class RestartSwitch(unittest.TestCase):
    def test_login_agent_uses_an_env_var_not_a_flag(self):
        # a flag in the plist would crash an older serve.py after a rollback
        from pathlib import Path
        run = (Path(serve.__file__).parent / "run.sh").read_text()
        self.assertIn("DASHBOARD_RESTART_ON_CHANGE", run)
        self.assertNotIn("<string>--restart-on-change</string>", run)


class LoopbackOnly(unittest.TestCase):
    def test_refuses_a_non_loopback_host(self):
        import sys
        with mock.patch.object(serve, "HOST", "0.0.0.0"), mock.patch.object(sys, "argv", ["serve.py", "--no-browser"]), \
                mock.patch("builtins.print"), mock.patch.object(serve, "ThreadingHTTPServer") as srv:
            self.assertEqual(serve.main(), 2)
            srv.assert_not_called()


if __name__ == "__main__":
    unittest.main()
