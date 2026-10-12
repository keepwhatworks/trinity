"""A command that records telemetry and exits must still deliver it (reproduced 2026-10-12: the
activation was queued on a daemon thread, marked sent, and the server received nothing).

Each test runs a real short-lived Python process against a local HTTP server, so what is counted
is what actually arrived, not what was queued.
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import subprocess
import sys
import threading
import time

import pytest

SRC = str(pathlib.Path(__file__).resolve().parent.parent / "src")


@pytest.fixture
def server():
    hits, status = [], {"code": 204, "stall": 0.0}

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            time.sleep(0.3 + status["stall"])        # a realistic round-trip, or a stalled server
            hits.append(json.loads(body))
            self.send_response(status["code"])
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/mp/collect", hits, status
    srv.shutdown()


def _child(code: str, url: str, home: pathlib.Path) -> str:
    prog = f"import sys; sys.path.insert(0, {SRC!r})\nfrom trinity_local import telemetry as T\nT.GA4_ENDPOINT = {url!r}\n{code}"
    env = dict(os.environ, TRINITY_HOME=str(home), TRINITY_GA4_MEASUREMENT_ID="G-TEST000",
               TRINITY_GA4_API_SECRET="not-a-real-key")
    r = subprocess.run([sys.executable, "-c", prog], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _names(hits):
    return [e["name"] for body in hits for e in body["events"]]


def test_a_cli_exit_still_delivers_the_activation(server, tmp_path):
    url, hits, _ = server
    out = _child('print(T.record_activation("verify"), bool(T.load_telemetry_settings().activation_sent_at))',
                 url, tmp_path)
    assert out.split() == ["True", "True"]
    assert _names(hits) == ["activation"]


def test_an_undelivered_activation_is_not_marked_sent(server, tmp_path):
    url, hits, status = server
    status["code"] = 500
    out = _child('print(T.record_activation("verify"), bool(T.load_telemetry_settings().activation_sent_at))',
                 url, tmp_path)
    assert out.split() == ["False", "False"], "a failed POST must leave the activation to be sent later"


def test_a_background_event_survives_a_prompt_exit(server, tmp_path):
    url, hits, _ = server
    _child('T.record_event("council_complete", task_type="design", winner="claude", member_count=3, mode="parallel")',
           url, tmp_path)
    assert _names(hits) == ["council_complete"]


def test_a_stalled_server_cannot_hold_the_activation_past_its_deadline(server, tmp_path):
    url, hits, status = server
    status["stall"] = 5.0
    t0 = time.monotonic()
    out = _child('T._BLOCKING_DEADLINE_S = 1.0\n'
                 'print(T.record_activation("verify"), bool(T.load_telemetry_settings().activation_sent_at))',
                 url, tmp_path)
    assert out.split()[:2] == ["False", "False"], "an undelivered activation must stay unsent"
    assert time.monotonic() - t0 < 5.0, "the caller was held past the deadline"
