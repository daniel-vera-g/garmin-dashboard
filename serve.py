#!/usr/bin/env python3
"""Serve dashboard.html locally and let the page trigger a fresh sync.

A file:// page cannot run Python or reach Garmin, so the Sync button needs
something local to talk to. This is that, and nothing more: stdlib only,
bound to localhost, and it serves the three pages plus a few endpoints:
sync, its status, and the coach update (preview / apply an answer from the
coaching session into season.toml / athlete.toml). data/ is never exposed.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import settings

ROOT = Path(__file__).parent
PAGE = ROOT / "dashboard.html"
# The only files ever served. data/ and everything else stay unreachable.
PAGES = {"/": "dashboard.html", "/dashboard.html": "dashboard.html",
         "/season.html": "season.html", "/health.html": "health.html"}
PY = ROOT / ".venv" / "bin" / "python"
_APP = settings.load_athlete()["app"]
HOST, PORT = _APP["host"], int(_APP["port"])
LOOPBACK = {"127.0.0.1", "localhost", "::1"}

_state: dict[str, object] = {"running": False, "error": None, "finished_at": None}
_lock = threading.Lock()


def _age_seconds() -> float | None:
    return time.time() - PAGE.stat().st_mtime if PAGE.exists() else None


def _sync_worker() -> None:
    error = None
    try:
        for script in ("garmin_sync.py", "build_dashboard.py"):
            proc = subprocess.run([str(PY), str(ROOT / script)], cwd=ROOT,
                                  capture_output=True, text=True, timeout=600)
            print(proc.stdout, end="")
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
                raise RuntimeError(f"{script}: " + " ".join(tail))
    except Exception as exc:
        error = str(exc)
        print(f"sync failed: {error}")
    finally:
        with _lock:
            _state.update(running=False, error=error, finished_at=time.time())


def watch_own_code(interval: float = 5.0) -> None:
    """Exit when serve.py changes on disk, so launchd starts the new code.
    A running server keeps the old code in memory otherwise - a security fix
    sat inactive for days that way. The exit code is non-zero on purpose:
    the agent's KeepAlive restarts only on unsuccessful exits."""
    me = Path(__file__)
    seen = me.stat().st_mtime

    def loop() -> None:
        while True:
            time.sleep(interval)
            try:
                changed = me.stat().st_mtime != seen
            except OSError:
                continue
            with _lock:
                busy = _state["running"]
            if changed and not busy:
                print("serve.py changed on disk - exiting so launchd restarts the new version", flush=True)
                os._exit(75)

    threading.Thread(target=loop, daemon=True).start()


def run_coach(mode: str, text: str) -> dict:
    """coach.py in a subprocess, like the sync: the long-running server never
    holds stale copies of the code that edits the config."""
    proc = subprocess.run([str(PY), str(ROOT / "coach.py"), mode], cwd=ROOT, input=text,
                          capture_output=True, text=True, timeout=60)
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["no output"]
        return {"ok": False, "error": f"coach.py failed: {tail[0]}", "changes": [], "warnings": []}
    if mode == "apply" and out.get("ok") and out.get("changes"):
        build = subprocess.run([str(PY), str(ROOT / "build_dashboard.py")], cwd=ROOT,
                               capture_output=True, text=True, timeout=120)
        if build.returncode != 0:
            out.update(ok=False, error="saved, but the rebuild failed: "
                       + " ".join((build.stderr or build.stdout).strip().splitlines()[-2:]))
    return out


def start_sync() -> bool:
    with _lock:
        if _state["running"]:
            return False
        _state.update(running=True, error=None)
    threading.Thread(target=_sync_worker, daemon=True).start()
    return True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if code >= 400:            # an unread request body must not become the next request
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict) -> None:
        self._send(200, json.dumps(payload).encode(), "application/json")

    def _valid_host(self) -> bool:
        host = self.headers.get("Host", "")
        port = self.server.server_port
        expected = {f"{HOST}:{port}", f"localhost:{port}"}
        if port == 80:
            expected.update({HOST, "localhost"})
        return host in expected

    def do_GET(self) -> None:
        if not self._valid_host():
            self._send(421, b"misdirected request", "text/plain")
            return
        path = self.path.split("?")[0]
        if path in PAGES:
            page = ROOT / PAGES[path]
            if not page.exists():
                self._send(404, f"{page.name} not built yet".encode(), "text/plain")
                return
            self._send(200, page.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/status":
            with _lock:
                self._json({"running": _state["running"], "error": _state["error"],
                            "age_seconds": _age_seconds()})
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        # Read the body before answering, even to reject it: a reply that leaves
        # it unread makes the close reset the connection under the client.
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        body = self.rfile.read(length) if 0 < length <= 1024 * 1024 else b""
        if not self._valid_host():
            self._send(421, b"misdirected request", "text/plain")
            return
        path = self.path.split("?")[0]
        if path not in ("/api/sync", "/api/coach/preview", "/api/coach/apply"):
            self._send(404, b"not found", "text/plain")
            return
        # A cross-origin page cannot set a custom header without a CORS
        # preflight, so requiring one keeps other sites off these endpoints.
        if self.headers.get("X-Sync") != "1":
            self._send(403, b"forbidden", "text/plain")
            return
        if path == "/api/sync":
            self._json({"started": start_sync(), "running": True})
            return
        if not 0 < length <= 64 * 1024:
            self._send(413, b"answer missing or too long", "text/plain")
            return
        try:
            text = json.loads(body).get("text", "")
        except (ValueError, AttributeError):
            self._send(400, b"bad request", "text/plain")
            return
        self._json(run_coach(path.rsplit("/", 1)[1], str(text)))

    def log_message(self, *args) -> None:  # keep the console readable
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Serve the Garmin dashboard locally.")
    ap.add_argument("--no-browser", action="store_true",
                    help="do not open a browser (used when running as a login agent)")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--restart-on-change", action="store_true",
                    help="exit when serve.py changes so launchd restarts it. The login agent sets "
                         "DASHBOARD_RESTART_ON_CHANGE=1 instead: an environment variable, unlike a "
                         "flag, doesn't crash an older serve.py after a rollback")
    args = ap.parse_args()
    port = args.port
    # The pages hold health data and the endpoints can trigger a sync and edit the
    # config: this server is for this machine only.
    if HOST not in LOOPBACK:
        print(f"refusing to serve on {HOST!r}: [app] host must be one of {sorted(LOOPBACK)}")
        return 2

    if not PAGE.exists():
        print("dashboard.html not built yet - running an initial build...")
        subprocess.run([str(PY), str(ROOT / "build_dashboard.py")], cwd=ROOT)

    url = f"http://{HOST}:{port}/"
    try:
        server = ThreadingHTTPServer((HOST, port), Handler)
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, errno.EACCES):
            # Almost always our own agent already serving. Exit cleanly so
            # launchd's KeepAlive (SuccessfulExit=false) does not respawn us.
            print(f"port {port} already in use - assuming the dashboard is already "
                  f"running at {url}")
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        raise

    print(f"Dashboard: {url}")
    if args.restart_on_change or os.environ.get("DASHBOARD_RESTART_ON_CHANGE") == "1":
        watch_own_code()
    if not args.no_browser:
        print("Press Ctrl+C to stop.")
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
