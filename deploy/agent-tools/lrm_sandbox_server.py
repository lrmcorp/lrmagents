"""LRMagent command sandbox: runs shell commands for WhatsApp and API chats.

A separate container from the same image, holding the agents' workspace and
nothing else: no config.json, no console password, no model keys, no internal
API token. The agent's bash tool sends those chats' commands here (POST /exec,
with SANDBOX_TOKEN) instead of running them next to the secrets.
Installed as agent/lrm_sandbox_server.py; started by the compose `sandbox` service.
"""

import hmac
import json
import os
import signal
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("SANDBOX_TOKEN", "")
ROOT = os.path.realpath(os.environ.get("SANDBOX_ROOT", "/home/agent/cow"))
PORT = int(os.environ.get("SANDBOX_PORT", "8099"))
MAX_OUTPUT = 2_000_000
MAX_TIMEOUT = 3600
BASE_ENV = {
    "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/home/agent"),
    "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TERM": "dumb", "TZ": os.environ.get("TZ", "Asia/Jakarta"),
    "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""), "PYTHONUNBUFFERED": "1",
}


def _cut(data):
    text = data.decode("utf-8", "replace")
    return text if len(text) <= MAX_OUTPUT else text[-MAX_OUTPUT:]


class Handler(BaseHTTPRequestHandler):
    server_version = "lrm-sandbox"

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        given = self.headers.get("Authorization", "").replace("Bearer ", "", 1)
        if not TOKEN or not hmac.compare_digest(given, TOKEN):
            return self._send(403, {"error": "forbidden"})
        if self.path != "/exec":
            return self._send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 2_000_000)) or b"{}")
        except ValueError:
            return self._send(400, {"error": "invalid JSON"})
        command = str(body.get("command") or "")
        if not command.strip():
            return self._send(400, {"error": "command required"})
        cwd = os.path.realpath(str(body.get("cwd") or ROOT))
        if not (cwd == ROOT or cwd.startswith(ROOT + os.sep)) or not os.path.isdir(cwd):
            cwd = ROOT
        try:
            timeout = max(1, min(int(body.get("timeout") or 120), MAX_TIMEOUT))
        except (TypeError, ValueError):
            timeout = 120
        env = dict(BASE_ENV)
        env.update({str(k): str(v) for k, v in (body.get("env") or {}).items() if isinstance(k, str) and k.isidentifier()})
        proc = subprocess.Popen(["bash", "-c", command], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        timed_out = False
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            out, err = proc.communicate()
        return self._send(200, {"returncode": proc.returncode, "stdout": _cut(out), "stderr": _cut(err), "timed_out": timed_out})


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("SANDBOX_TOKEN is not set")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
