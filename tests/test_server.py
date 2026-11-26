"""Unit and lifecycle tests for swarm_board.server and scripts/ensure-server.sh."""

from __future__ import annotations

from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import quote

from swarm_board.adapters import BoardRegistry
from swarm_board.server import (
    DEFAULT_IDLE_SECONDS,
    REPO_ROOT,
    SERVER_VERSION,
    _acquire_state_owner_lock,
    _start_systemd_unit,
    _use_systemd_unit,
    create_server,
    detect_tailscale_ipv4,
    ensure_server,
    get_state_dir,
    hook_ensure_server,
    is_loopback_host,
    is_tailscale_ipv4,
    is_valid_host_header,
    parse_byte_range,
    resolve_bind_host,
    resolve_idle_seconds,
    resolve_open_in_preview,
)


def _read_sse_event(sock: socket.socket, buf: bytearray, timeout: float = 5.0) -> tuple[str | None, dict | None, str]:
    """Read a single SSE frame (comment or event+data) from a raw socket."""
    deadline = time.monotonic() + timeout
    while True:
        sep = buf.find(b"\n\n")
        if sep != -1:
            raw_frame = bytes(buf[:sep]).decode("utf-8", errors="replace")
            del buf[: sep + 2]
            event_name: str | None = None
            data_lines: list[str] = []
            for line in raw_frame.splitlines():
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
            parsed_data = json.loads("\n".join(data_lines)) if data_lines else None
            return event_name, parsed_data, raw_frame
        rem = deadline - time.monotonic()
        if rem <= 0:
            raise TimeoutError("Timed out waiting for SSE frame")
        sock.settimeout(rem)
        chunk = sock.recv(4096)
        if not chunk:
            raise EOFError("SSE stream closed before complete frame")
        buf.extend(chunk)


class ServerUnitAndApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        self.proj = self.root / "project"
        self.proj.mkdir()
        (self.proj / "src").mkdir()
        (self.proj / "src" / "app.py").write_text(
            "\n".join(f"line_{i} = {i}" for i in range(1, 21)) + "\n",
            encoding="utf-8",
        )
        (self.proj / "media").mkdir()
        self.sample_bytes = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        (self.proj / "media" / "clip.bin").write_bytes(self.sample_bytes)
        (self.proj / "media" / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"ABCD" * 5)
        (self.proj / ".env").write_text("API_SECRET=top-secret\n", encoding="utf-8")

        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("outside-secret\n", encoding="utf-8")
        os.symlink(self.outside / "secret.txt", self.proj / "escape.txt")

        self.dropin_dir = self.root / "dropin"
        self.dropin_dir.mkdir()
        self.flow_id = "dropin:demo/1"
        self.thread_id = "thread/abc:1"
        self.big_body = "Big output header\n" + ("Z" * 20_000)

        self.brain_dir = self.root / "brain"
        conv_dir = self.brain_dir / self.thread_id / ".system_generated" / "logs"
        conv_dir.mkdir(parents=True)
        (conv_dir / "transcript.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "USER_INPUT",
                            "created_at": "2026-11-25T10:00:00Z",
                            "content": "Start task",
                        }
                    ),
                    json.dumps(
                        {
                            "type": "PLANNER_RESPONSE",
                            "status": "DONE",
                            "created_at": "2026-11-25T10:00:05Z",
                            "content": "Completed step 1",
                        }
                    ),
                    json.dumps(
                        {
                            "type": "PLANNER_RESPONSE",
                            "status": "DONE",
                            "created_at": "2026-11-25T10:00:10Z",
                            "content": "Completed step 2",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        self.flow_file = self.dropin_dir / "demo.json"
        self._write_flow(state="done", last="All clear")

        self.ui_dir = self.root / "ui"
        self.ui_dir.mkdir()

        self.registry = BoardRegistry(
            {
                "committee_dir": self.root / "no-com",
                "orchestrate_dir": self.root / "no-orch",
                "sessions_ledger": self.root / "no-ledger",
                "grok_sessions_root": self.root / "no-grok",
                "codex_db": self.root / "no-codex",
                "claude_root": self.root / "no-claude",
                "brain_roots": [self.brain_dir],
                "statev2_db": self.root / "no-t3.sqlite",
                "dropin_dir": self.dropin_dir,
                "rates_path": self.root / "no-rates",
                "usage_cache_path": self.root / "no-usage",
                "env_id": "env-test-123",
                "now_ts": datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc).timestamp(),
            },
            cache_ttl_s=0.0,
        )

        self.server = create_server(
            self.registry,
            port=0,
            ui_dir=self.ui_dir,
            idle_seconds=0,
            sse_poll_interval_s=0.08,
            sse_heartbeat_interval_s=0.25,
        )
        self.port = self.server.bound_port
        self.server_thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self.server_thread.start()

    def tearDown(self) -> None:
        self.server.request_shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=3.0)
        self.tmp.cleanup()

    def _write_flow(self, *, state: str, last: str) -> None:
        self.flow_file.write_text(
            json.dumps(
                {
                    "id": self.flow_id,
                    "title": "Demo Flow",
                    "project": "project",
                    "projectRoot": str(self.proj),
                    "harness": "dropin",
                    "kind": "flat",
                    "startedAt": "2026-11-25T10:00:00Z",
                    "updatedAt": "2026-11-25T10:05:00Z",
                    "agents": [
                        {
                            "id": "worker-1",
                            "name": "worker-1",
                            "lab": "google",
                            "model": "gemini-4-neon",
                            "state": state,
                            "last": last,
                            "tokens": {"in": 100, "cache": 20, "out": 50},
                            "costUsd": 0.0123,
                            "outputIds": ["out/big"],
                        }
                    ],
                    "outputs": [
                        {
                            "id": "out/big",
                            "kind": "report",
                            "title": "Report",
                            "by": "worker-1",
                            "body": self.big_body,
                        },
                        {
                            "id": "out/file",
                            "kind": "file",
                            "title": "App",
                            "path": "src/app.py",
                            "by": "worker-1",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

    def _request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5.0)
        try:
            req_headers = {"Host": f"127.0.0.1:{self.port}"}
            if headers is not None:
                req_headers = headers
            conn.request(method, path, headers=req_headers)
            resp = conn.getresponse()
            body = resp.read()
            resp_headers = {k.lower(): v for k, v in resp.getheaders()}
            return resp.status, resp_headers, body
        finally:
            conn.close()

    def test_helpers_host_range_and_state_dir(self) -> None:
        self.assertTrue(is_valid_host_header(None))
        self.assertTrue(is_valid_host_header("127.0.0.1:18420"))
        self.assertTrue(is_valid_host_header("localhost:18420"))
        self.assertTrue(is_valid_host_header("[::1]:18420"))
        self.assertFalse(is_valid_host_header("evil.example:18420"))
        self.assertFalse(is_valid_host_header("127.0.0.1.evil.com:18420"))
        self.assertFalse(is_valid_host_header("localhost:99999"))
        self.assertFalse(is_valid_host_header(""))

        self.assertEqual(parse_byte_range("bytes=0-9", 36), (0, 9))
        self.assertEqual(parse_byte_range("bytes=10-", 36), (10, 35))
        self.assertEqual(parse_byte_range("bytes=-5", 36), (31, 35))
        self.assertEqual(parse_byte_range("bytes=0-999", 36), (0, 35))
        for bad in ("bytes=50-60", "bytes=10-5", "bytes=-0", "bytes=0-1,2-3", "items=0-5", "bytes=-"):
            with self.assertRaises(ValueError):
                parse_byte_range(bad, 36)

        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("SWARM_BOARD_STATE_DIR")
            try:
                os.environ["SWARM_BOARD_STATE_DIR"] = tmp
                self.assertEqual(get_state_dir(), Path(tmp))
            finally:
                if old is None:
                    os.environ.pop("SWARM_BOARD_STATE_DIR", None)
                else:
                    os.environ["SWARM_BOARD_STATE_DIR"] = old

    def test_ui_missing_503_then_200_and_health(self) -> None:
        st, hdrs, body = self._request("GET", "/")
        self.assertEqual(st, 503)
        self.assertIn(b"index.html", body)

        (self.ui_dir / "index.html").write_text("<html><body>Swarm Board</body></html>", encoding="utf-8")
        for route in ("/", "/index.html"):
            st2, hdrs2, body2 = self._request("GET", route)
            self.assertEqual(st2, 200)
            self.assertEqual(hdrs2["content-type"], "text/html; charset=utf-8")
            self.assertEqual(hdrs2["cache-control"], "no-store")
            self.assertIn(b"Swarm Board", body2)

        st_h, _, body_h = self._request("GET", "/health")
        self.assertEqual(st_h, 200)
        health = json.loads(body_h.decode("utf-8"))
        self.assertTrue(health["ok"])
        self.assertEqual(health["pid"], os.getpid())
        self.assertEqual(health["port"], self.port)
        self.assertGreaterEqual(health["uptime_s"], 0.0)
        self.assertEqual(health["idle_seconds"], 0.0)
        self.assertEqual(health["clients"], 0)
        self.assertEqual(health["version"], SERVER_VERSION)
        self.assertIn("flows_count", health)

    def test_flows_single_flow_stats_outputs_and_thread_messages(self) -> None:
        st, _, body = self._request("GET", "/api/flows?force=1")
        self.assertEqual(st, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(data["envId"], "env-test-123")
        self.assertTrue(data["snapshot"])
        self.assertEqual(data["errors"], {})
        self.assertEqual(len(data["flows"]), 1)
        # List endpoint compacts >16KB body
        big_in_list = next(o for o in data["flows"][0]["outputs"] if o["id"] == "out/big")
        self.assertIsNone(big_in_list["body"])

        enc_flow = quote(self.flow_id, safe="")
        st_f, _, body_f = self._request("GET", f"/api/flow/{enc_flow}")
        self.assertEqual(st_f, 200)
        flow_res = json.loads(body_f.decode("utf-8"))
        self.assertTrue(flow_res["ok"])
        self.assertEqual(flow_res["flow"]["id"], self.flow_id)

        st_miss, _, _ = self._request("GET", "/api/flow/does-not-exist")
        self.assertEqual(st_miss, 404)

        # Stats fallback + guarded delegation check
        st_s, _, body_s = self._request("GET", "/api/stats?scope=all&rng=24h")
        self.assertEqual(st_s, 200)
        stats_res = json.loads(body_s.decode("utf-8"))
        self.assertTrue(stats_res["ok"])
        self.assertEqual(stats_res["stats"]["flowsCount"], 1)

        setattr(self.registry, "get_stats", lambda **kw: {"delegated": True, **kw})
        try:
            st_s2, _, body_s2 = self._request(
                "GET", f"/api/stats?scope=flow&flow_id={enc_flow}&rng=7d&lab=google"
            )
            self.assertEqual(st_s2, 200)
            s2 = json.loads(body_s2.decode("utf-8"))
            self.assertTrue(s2["stats"]["delegated"])
            self.assertEqual(s2["stats"]["flow_id"], self.flow_id)
            self.assertEqual(s2["stats"]["rng"], "7d")
            self.assertEqual(s2["stats"]["lab"], "google")
        finally:
            delattr(self.registry, "get_stats")

        # Output lazy loader
        enc_out = quote("out/big", safe="")
        st_o, _, body_o = self._request("GET", f"/api/output/{enc_flow}/{enc_out}")
        self.assertEqual(st_o, 200)
        out_res = json.loads(body_o.decode("utf-8"))
        self.assertTrue(out_res["ok"])
        self.assertEqual(out_res["body"], self.big_body)

        st_om, _, _ = self._request("GET", f"/api/output/{enc_flow}/missing")
        self.assertEqual(st_om, 404)

        # Thread messages
        enc_tid = quote(self.thread_id, safe="")
        st_m, _, body_m = self._request("GET", f"/api/thread/{enc_tid}/messages?limit=2")
        self.assertEqual(st_m, 200)
        msg_res = json.loads(body_m.decode("utf-8"))
        self.assertTrue(msg_res["ok"])
        self.assertEqual(len(msg_res["messages"]), 2)

    def test_citation_file_and_raw_media_security_and_ranges(self) -> None:
        enc_flow = quote(self.flow_id, safe="")
        # Populate registry cache first
        self._request("GET", "/api/flows?force=1")

        # Valid citation
        st, _, body = self._request(
            "GET", f"/api/file?flow={enc_flow}&path={quote('src/app.py')}&line=5&ctx=2"
        )
        self.assertEqual(st, 200)
        cit = json.loads(body.decode("utf-8"))
        self.assertTrue(cit["ok"])
        self.assertEqual([ln["num"] for ln in cit["lines"]], [3, 4, 5, 6, 7])
        self.assertEqual([ln["num"] for ln in cit["lines"] if ln["cited"]], [5])

        # Bad params -> 400
        self.assertEqual(self._request("GET", f"/api/file?flow={enc_flow}&path=src/app.py")[0], 400)
        self.assertEqual(self._request("GET", f"/api/file?flow={enc_flow}&path=src/app.py&line=abc")[0], 400)

        # Missing file -> 404
        self.assertEqual(
            self._request("GET", f"/api/file?flow={enc_flow}&path=src/missing.py&line=1")[0], 404
        )

        # Traversal, outside, symlink escape, and secret files -> 403
        for bad_path in (
            "../outside/secret.txt",
            str(self.outside / "secret.txt"),
            "escape.txt",
            ".env",
            ".git/config",
        ):
            st_bad, _, _ = self._request(
                "GET", f"/api/file?flow={enc_flow}&path={quote(bad_path)}&line=1"
            )
            self.assertEqual(st_bad, 403, f"/api/file expected 403 for {bad_path}")

        # Raw full GET
        st_raw, hdrs_raw, body_raw = self._request(
            "GET", f"/api/raw?flow={enc_flow}&path={quote('media/clip.bin')}"
        )
        self.assertEqual(st_raw, 200)
        self.assertEqual(hdrs_raw["accept-ranges"], "bytes")
        self.assertEqual(body_raw, self.sample_bytes)

        # Raw PNG MIME detection
        st_png, hdrs_png, _ = self._request(
            "GET", f"/api/raw?flow={enc_flow}&path={quote('media/pic.png')}"
        )
        self.assertEqual(st_png, 200)
        self.assertEqual(hdrs_png["content-type"], "image/png")

        # Range: bytes=0-9 -> 206 Partial Content
        st_r1, hdrs_r1, body_r1 = self._request(
            "GET",
            f"/api/raw?flow={enc_flow}&path={quote('media/clip.bin')}",
            headers={"Host": f"127.0.0.1:{self.port}", "Range": "bytes=0-9"},
        )
        self.assertEqual(st_r1, 206)
        self.assertEqual(hdrs_r1["accept-ranges"], "bytes")
        self.assertEqual(hdrs_r1["content-range"], f"bytes 0-9/{len(self.sample_bytes)}")
        self.assertEqual(body_r1, self.sample_bytes[:10])

        # Suffix Range: bytes=-6 -> 206
        st_r2, hdrs_r2, body_r2 = self._request(
            "GET",
            f"/api/raw?flow={enc_flow}&path={quote('media/clip.bin')}",
            headers={"Host": f"127.0.0.1:{self.port}", "Range": "bytes=-6"},
        )
        self.assertEqual(st_r2, 206)
        self.assertEqual(body_r2, self.sample_bytes[-6:])
        self.assertEqual(
            hdrs_r2["content-range"],
            f"bytes {len(self.sample_bytes) - 6}-{len(self.sample_bytes) - 1}/{len(self.sample_bytes)}",
        )

        # Out-of-bounds Range -> 416
        st_416, hdrs_416, _ = self._request(
            "GET",
            f"/api/raw?flow={enc_flow}&path={quote('media/clip.bin')}",
            headers={"Host": f"127.0.0.1:{self.port}", "Range": "bytes=999-1000"},
        )
        self.assertEqual(st_416, 416)
        self.assertEqual(hdrs_416["content-range"], f"bytes */{len(self.sample_bytes)}")

        # Raw security rejections -> 403 and missing -> 404
        for bad_path in ("../outside/secret.txt", "escape.txt", ".env"):
            st_rbad, _, _ = self._request(
                "GET", f"/api/raw?flow={enc_flow}&path={quote(bad_path)}"
            )
            self.assertEqual(st_rbad, 403, f"/api/raw expected 403 for {bad_path}")
        self.assertEqual(
            self._request("GET", f"/api/raw?flow={enc_flow}&path=media/missing.png")[0], 404
        )

        # Guarded registry.get_raw_file_path delegation
        setattr(
            self.registry,
            "get_raw_file_path",
            lambda _fid, p: {"ok": True, "path": str(self.proj / p)},
        )
        try:
            st_del, _, body_del = self._request(
                "GET", f"/api/raw?flow={enc_flow}&path={quote('media/clip.bin')}"
            )
            self.assertEqual(st_del, 200)
            self.assertEqual(body_del, self.sample_bytes)
            # Secret check still holds even if delegated method resolves path
            st_sec, _, _ = self._request(
                "GET", f"/api/raw?flow={enc_flow}&path=.env"
            )
            self.assertEqual(st_sec, 403)
        finally:
            delattr(self.registry, "get_raw_file_path")

    def test_host_header_and_method_restrictions(self) -> None:
        # Allowed loopback host headers
        for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"):
            st, _, _ = self._request("GET", "/health", headers={"Host": host})
            self.assertEqual(st, 200, host)

        # Forbidden host headers (DNS rebinding protection)
        for bad_host in ("evil.com:18420", "127.0.0.1.evil.com", "localhost.attacker:80"):
            st, _, _ = self._request("GET", "/health", headers={"Host": bad_host})
            self.assertEqual(st, 403, bad_host)

        # Non-GET/HEAD rejected with 405
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            st_m, hdrs_m, _ = self._request(method, "/api/flows")
            self.assertEqual(st_m, 405)
            self.assertEqual(hdrs_m["allow"], "GET, HEAD")

    def test_tailscale_host_resolution_header_validation_and_preview_policy(self) -> None:
        self.assertTrue(is_tailscale_ipv4("100.73.34.46"))
        self.assertTrue(is_tailscale_ipv4("100.64.0.1"))
        self.assertFalse(is_tailscale_ipv4("192.168.1.5"))
        self.assertFalse(is_tailscale_ipv4("0.0.0.0"))
        self.assertTrue(is_loopback_host("127.0.0.1"))
        self.assertTrue(is_loopback_host("localhost"))
        self.assertFalse(is_loopback_host("100.73.34.46"))

        # Allowed hosts validation alongside loopback
        allowed = ["100.73.34.46", "vmi3773409.tail4e88e4.ts.net."]
        for ok_host in (
            "100.73.34.46",
            "100.73.34.46:18420",
            "vmi3773409.tail4e88e4.ts.net",
            "vmi3773409.tail4e88e4.ts.net:18420",
            "VMI3773409.TAIL4E88E4.TS.NET.:18420",
        ):
            self.assertTrue(is_valid_host_header(ok_host, allowed_hosts=allowed), ok_host)
        for bad_host in (
            "evil.com:18420",
            "100.73.34.46.evil.com:18420",
            "100.73.34.46:99999",
            "100.73.34.46:0",
            "100.73.34.46:notaport",
        ):
            self.assertFalse(is_valid_host_header(bad_host, allowed_hosts=allowed), bad_host)

        # Reject unsafe bind hosts
        for bad_bind in ("0.0.0.0", "80.190.78.82", "192.168.1.5", "evil.com"):
            with self.assertRaises(ValueError):
                resolve_bind_host(bad_bind, use_env_and_config=False)

        self.assertEqual(resolve_bind_host("100.73.34.46", use_env_and_config=False), "100.73.34.46")
        with mock.patch("swarm_board.server.detect_tailscale_ipv4", return_value="100.73.34.46"):
            self.assertEqual(resolve_bind_host("tailscale", use_env_and_config=False), "100.73.34.46")

        # Precedence: explicit > env > config > default
        with mock.patch.dict(
            os.environ,
            {"SWARM_BOARD_HOST": "100.73.34.46"},
            clear=False,
        ):
            self.assertEqual(resolve_bind_host(None, config={"host": "127.0.0.1"}), "100.73.34.46")
            self.assertEqual(resolve_bind_host("127.0.0.1", config={"host": "100.73.34.46"}), "127.0.0.1")

        clean_env = {k: v for k, v in os.environ.items() if k not in ("SWARM_BOARD_HOST", "SWARM_BOARD_OPEN_IN_PREVIEW")}
        with mock.patch.dict(os.environ, clean_env, clear=True):
            self.assertEqual(resolve_bind_host(None, config={}), "127.0.0.1")
            self.assertEqual(resolve_bind_host(None, config={"host": "100.73.34.46"}), "100.73.34.46")
            self.assertTrue(resolve_open_in_preview("127.0.0.1", config={}))
            self.assertFalse(resolve_open_in_preview("100.73.34.46", config={}))
            self.assertFalse(resolve_open_in_preview("127.0.0.1", config={"open_in_preview": False}))
            self.assertTrue(resolve_open_in_preview("100.73.34.46", config={"open_in_preview": True}))

        with mock.patch.dict(os.environ, {"SWARM_BOARD_OPEN_IN_PREVIEW": "0"}, clear=False):
            self.assertFalse(resolve_open_in_preview("127.0.0.1", config={"open_in_preview": True}))

        # Idle timeout default and resolution (explicit > env > config > default)
        self.assertEqual(DEFAULT_IDLE_SECONDS, 7200.0)
        clean_idle_env = {k: v for k, v in os.environ.items() if k != "SWARM_BOARD_IDLE_SECONDS"}
        with mock.patch.dict(os.environ, clean_idle_env, clear=True):
            self.assertEqual(resolve_idle_seconds(None, config={}), 7200.0)
            self.assertEqual(resolve_idle_seconds(None, config={"idle_seconds": 3600}), 3600.0)
            self.assertEqual(resolve_idle_seconds(None, config={"idle_seconds": 10800}), 10800.0)
            self.assertEqual(resolve_idle_seconds(None, config={"idle_seconds": 0}), 0.0)
            self.assertEqual(resolve_idle_seconds(None, config={"idle_seconds": -5}), 0.0)
            for bad_cfg in (True, False, "bad", "", float("nan"), float("inf")):
                self.assertEqual(
                    resolve_idle_seconds(None, config={"idle_seconds": bad_cfg}),
                    7200.0,
                    f"bad_cfg={bad_cfg!r}",
                )

        with mock.patch.dict(os.environ, {"SWARM_BOARD_IDLE_SECONDS": "5400"}, clear=False):
            self.assertEqual(resolve_idle_seconds(None, config={"idle_seconds": 3600}), 5400.0)
            self.assertEqual(resolve_idle_seconds(10800, config={"idle_seconds": 3600}), 10800.0)
            self.assertEqual(
                resolve_idle_seconds(None, use_env_and_config=False, config={"idle_seconds": 3600}),
                7200.0,
            )

        for bad_explicit in (True, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                resolve_idle_seconds(bad_explicit)  # type: ignore[arg-type]

    def test_sse_events_ready_update_heartbeat_and_zero_idle_poller(self) -> None:
        # Before connecting, zero SSE clients and no background poller thread active.
        self.assertEqual(self.server.sse_hub.client_count(), 0)
        self.assertFalse(self.server.sse_hub.poller_active())

        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5.0)
        try:
            req = (
                f"GET /events HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{self.port}\r\n"
                f"Accept: text/event-stream\r\n\r\n"
            ).encode("ascii")
            sock.sendall(req)

            buf = bytearray()
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(4096)
                self.assertTrue(chunk)
                buf.extend(chunk)
            hdr_end = buf.find(b"\r\n\r\n")
            hdr_text = bytes(buf[:hdr_end]).decode("ascii")
            del buf[: hdr_end + 4]

            self.assertIn("200 OK", hdr_text)
            self.assertIn("text/event-stream", hdr_text.lower())
            self.assertIn("no-cache", hdr_text.lower())
            self.assertIn("x-accel-buffering: no", hdr_text.lower())

            ev1, data1, _ = _read_sse_event(sock, buf, timeout=3.0)
            self.assertEqual(ev1, "ready")
            self.assertIsNotNone(data1)
            self.assertIn("digest", data1)
            self.assertIn("ts", data1)
            self.assertEqual(self.server.sse_hub.client_count(), 1)
            self.assertTrue(self.server.sse_hub.poller_active())

            # Trigger heartbeat ping
            ev_ping, _, raw_ping = _read_sse_event(sock, buf, timeout=2.0)
            self.assertIsNone(ev_ping)
            self.assertEqual(raw_ping, ": ping")

            # Mutate flow state -> SSE poller emits `update` with new digest
            self._write_flow(state="running", last="Working now")
            got_update = None
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                ev, payload, _ = _read_sse_event(sock, buf, timeout=2.0)
                if ev == "update":
                    got_update = payload
                    break
            self.assertIsNotNone(got_update)
            self.assertNotEqual(got_update["digest"], data1["digest"])
        finally:
            sock.close()

        # Wait briefly for server handler to see EOF and unregister client
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and self.server.sse_hub.client_count() > 0:
            time.sleep(0.03)
        self.assertEqual(self.server.sse_hub.client_count(), 0)

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and self.server.sse_hub.poller_active():
            time.sleep(0.03)
        self.assertFalse(self.server.sse_hub.poller_active())


class ServerLifecycleSubprocessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name) / "state"
        self.state_dir.mkdir(parents=True)
        self.script_path = REPO_ROOT / "scripts" / "ensure-server.sh"
        self._pids_to_cleanup: list[int] = []

    def tearDown(self) -> None:
        for pid in self._pids_to_cleanup:
            self._kill_pid(pid)
        self.tmp.cleanup()

    def _kill_pid(self, pid: int) -> None:
        if pid <= 0:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.05)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["SWARM_BOARD_STATE_DIR"] = str(self.state_dir)
        env["SWARM_BOARD_CONFIG"] = str(Path(self.tmp.name) / "no-config.json")
        env.pop("SWARM_BOARD_HOST", None)
        env.pop("SWARM_BOARD_OPEN_IN_PREVIEW", None)
        env.pop("SWARM_BOARD_IDLE_SECONDS", None)
        return env

    def test_ensure_script_idempotent_and_hook_mode(self) -> None:
        # 1. First --ensure via portable shell launcher starts detached server
        p1 = subprocess.run(
            [str(self.script_path), "--ensure", "--port", "0", "--idle-seconds", "30"],
            cwd=str(REPO_ROOT),
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=10.0,
            check=True,
        )
        res1 = json.loads(p1.stdout.strip())
        self.assertTrue(res1["ok"])
        self.assertTrue(res1["started"])
        pid1 = int(res1["pid"])
        port1 = int(res1["port"])
        self._pids_to_cleanup.append(pid1)

        # 2. Second --ensure reuses the exact same server instance
        p2 = subprocess.run(
            [str(self.script_path), "--port", "0"],
            cwd=str(REPO_ROOT),
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=10.0,
            check=True,
        )
        res2 = json.loads(p2.stdout.strip())
        self.assertTrue(res2["ok"])
        self.assertFalse(res2["started"])
        self.assertEqual(res2["pid"], pid1)
        self.assertEqual(res2["port"], port1)

        # 3. Verify /health over HTTP
        conn = http.client.HTTPConnection("127.0.0.1", port1, timeout=2.0)
        try:
            conn.request("GET", "/health", headers={"Host": f"127.0.0.1:{port1}"})
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            h = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(h["pid"], pid1)
        finally:
            conn.close()

        # 4. --hook returns {} quickly on existing server
        t0 = time.monotonic()
        ph_reuse = subprocess.run(
            [str(self.script_path), "--hook", "--port", "0"],
            cwd=str(REPO_ROOT),
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=5.0,
            check=True,
        )
        self.assertEqual(json.loads(ph_reuse.stdout.strip()), {})
        self.assertLess(time.monotonic() - t0, 2.0)

        # 5. Stop first server cleanly, then verify --hook spawns a fresh server non-blockingly
        self._kill_pid(pid1)
        state_file = self.state_dir / "server.json"
        if state_file.exists():
            state_file.unlink()

        t1 = time.monotonic()
        ph_spawn = subprocess.run(
            [str(self.script_path), "--hook", "--port", "0", "--idle-seconds", "30"],
            cwd=str(REPO_ROOT),
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=5.0,
            check=True,
        )
        self.assertEqual(json.loads(ph_spawn.stdout.strip()), {})
        self.assertLess(time.monotonic() - t1, 2.0)

        # Wait for the hook-spawned server to become healthy and track its PID for cleanup
        deadline = time.monotonic() + 5.0
        spawned_data = None
        while time.monotonic() < deadline:
            if state_file.is_file():
                try:
                    data = json.loads(state_file.read_text(encoding="utf-8"))
                    if isinstance(data.get("pid"), int) and isinstance(data.get("port"), int):
                        conn2 = http.client.HTTPConnection("127.0.0.1", data["port"], timeout=0.5)
                        try:
                            conn2.request("GET", "/health", headers={"Host": f"127.0.0.1:{data['port']}"})
                            r2 = conn2.getresponse()
                            if r2.status == 200:
                                spawned_data = data
                                break
                        finally:
                            conn2.close()
                except (OSError, ValueError, http.client.HTTPException):
                    pass
            time.sleep(0.05)

        self.assertIsNotNone(spawned_data)
        self._pids_to_cleanup.append(int(spawned_data["pid"]))
        self.assertNotEqual(int(spawned_data["pid"]), pid1)

    def test_idle_auto_exit(self) -> None:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "swarm_board.server",
                "--port",
                "0",
                "--idle-seconds",
                "0.5",
            ],
            cwd=str(REPO_ROOT),
            env=self._env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._pids_to_cleanup.append(proc.pid)

        state_file = self.state_dir / "server.json"
        deadline = time.monotonic() + 5.0
        port = None
        while time.monotonic() < deadline:
            if state_file.is_file():
                try:
                    data = json.loads(state_file.read_text(encoding="utf-8"))
                    if data.get("pid") == proc.pid and isinstance(data.get("port"), int):
                        port = int(data["port"])
                        break
                except (OSError, ValueError):
                    pass
            time.sleep(0.05)
        self.assertIsNotNone(port, "Server did not write server.json in time")

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
        try:
            conn.request("GET", "/health", headers={"Host": f"127.0.0.1:{port}"})
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
        finally:
            conn.close()

        # Server must auto-exit cleanly after ~0.5s of inactivity and remove server.json
        ret = proc.wait(timeout=5.0)
        self.assertEqual(ret, 0)
        self.assertFalse(state_file.exists())

    def test_status_and_stop_cli_and_script(self) -> None:
        # 1. --status before start reports running=False
        p0 = subprocess.run(
            [str(self.script_path), "--status"],
            cwd=str(REPO_ROOT),
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=10.0,
            check=True,
        )
        st0 = json.loads(p0.stdout.strip())
        self.assertTrue(st0["ok"])
        self.assertFalse(st0["running"])

        # 2. Mutually exclusive lifecycle flags return exit 2
        p_bad = subprocess.run(
            [sys.executable, "-m", "swarm_board.server", "--status", "--stop"],
            cwd=str(REPO_ROOT),
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
        self.assertEqual(p_bad.returncode, 2)

        # 3. Start via ensure, verify --status reports running=True, then --stop cleanly
        p_ens = subprocess.run(
            [str(self.script_path), "--ensure", "--port", "0"],
            cwd=str(REPO_ROOT),
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=10.0,
            check=True,
        )
        res_ens = json.loads(p_ens.stdout.strip())
        pid = int(res_ens["pid"])
        self._pids_to_cleanup.append(pid)

        p_up = subprocess.run(
            [str(self.script_path), "--status"],
            cwd=str(REPO_ROOT),
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=10.0,
            check=True,
        )
        st_up = json.loads(p_up.stdout.strip())
        self.assertTrue(st_up["running"])
        self.assertEqual(st_up["pid"], pid)
        self.assertEqual(st_up["port"], res_ens["port"])
        self.assertTrue(st_up["health"]["ok"])

        p_stop = subprocess.run(
            [str(self.script_path), "--stop"],
            cwd=str(REPO_ROOT),
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=10.0,
            check=True,
        )
        st_stop = json.loads(p_stop.stdout.strip())
        self.assertTrue(st_stop["ok"])
        self.assertTrue(st_stop["stopped"])
        self.assertEqual(st_stop["pid"], pid)

        p_after = subprocess.run(
            [str(self.script_path), "--status"],
            cwd=str(REPO_ROOT),
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=10.0,
            check=True,
        )
        self.assertFalse(json.loads(p_after.stdout.strip())["running"])
        self.assertFalse((self.state_dir / "server.json").exists())

        p_stop2 = subprocess.run(
            [str(self.script_path), "--stop"],
            cwd=str(REPO_ROOT),
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=10.0,
            check=True,
        )
        st_stop2 = json.loads(p_stop2.stdout.strip())
        self.assertTrue(st_stop2["ok"])
        self.assertFalse(st_stop2["stopped"])

    def test_config_file_open_in_preview_and_tailscale_lifecycle(self) -> None:
        cfg_path = Path(self.tmp.name) / "swarm-config.json"
        cfg_path.write_text(
            json.dumps({"host": "127.0.0.1", "open_in_preview": False}),
            encoding="utf-8",
        )
        env = self._env()
        env["SWARM_BOARD_CONFIG"] = str(cfg_path)

        p1 = subprocess.run(
            [str(self.script_path), "--ensure", "--port", "0", "--idle-seconds", "30"],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=10.0,
            check=True,
        )
        res1 = json.loads(p1.stdout.strip())
        self.assertTrue(res1["ok"])
        self.assertEqual(res1["host"], "127.0.0.1")
        self.assertEqual(res1["local_url"], f"http://127.0.0.1:{res1['port']}")
        self.assertFalse(res1["open_in_preview"])
        pid1 = int(res1["pid"])
        self._pids_to_cleanup.append(pid1)

        # Bad explicit host fails cleanly without starting
        p_bad = subprocess.run(
            [str(self.script_path), "--ensure", "--host", "0.0.0.0", "--port", "0"],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
        self.assertEqual(p_bad.returncode, 1)
        self.assertFalse(json.loads(p_bad.stdout.strip())["ok"])

        try:
            ts_ip = detect_tailscale_ipv4()
        except RuntimeError:
            return

        # Switching same state_dir to --host tailscale stops loopback server and binds ts_ip
        p_ts = subprocess.run(
            [str(self.script_path), "--ensure", "--host", "tailscale", "--port", "0", "--idle-seconds", "30"],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=10.0,
            check=True,
        )
        res_ts = json.loads(p_ts.stdout.strip())
        self.assertTrue(res_ts["ok"])
        self.assertTrue(res_ts["started"])
        self.assertEqual(res_ts["host"], ts_ip)
        self.assertFalse(res_ts["open_in_preview"])
        self.assertEqual(res_ts["url"], f"http://{ts_ip}:{res_ts['port']}")
        self.assertEqual(res_ts["local_url"], f"http://127.0.0.1:{res_ts['port']}")
        ts_pid = int(res_ts["pid"])
        ts_port = int(res_ts["port"])
        self._pids_to_cleanup.append(ts_pid)
        self.assertNotEqual(ts_pid, pid1)

        # Previous loopback server should be stopped
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid1, 0)
                time.sleep(0.05)
            except OSError:
                break
        with self.assertRaises(OSError):
            os.kill(pid1, 0)

        # Health over both Tailscale IP and companion 127.0.0.1 accepts valid Hosts and rejects evil.com
        for probe_host in (ts_ip, "127.0.0.1"):
            conn = http.client.HTTPConnection(probe_host, ts_port, timeout=3.0)
            try:
                conn.request("GET", "/health", headers={"Host": f"{probe_host}:{ts_port}"})
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200, probe_host)
                h_data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(h_data["host"], ts_ip)
                self.assertEqual(h_data["hosts"], [ts_ip, "127.0.0.1"])
                self.assertEqual(h_data["local_url"], f"http://127.0.0.1:{ts_port}")
                self.assertEqual(h_data["pid"], ts_pid)

                conn.request("GET", "/health", headers={"Host": f"evil.com:{ts_port}"})
                resp_bad = conn.getresponse()
                resp_bad.read()
                self.assertEqual(resp_bad.status, 403, probe_host)
            finally:
                conn.close()

        # --status and --stop work cleanly for Tailscale server
        p_st = subprocess.run(
            [str(self.script_path), "--status"],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=10.0,
            check=True,
        )
        st_data = json.loads(p_st.stdout.strip())
        self.assertTrue(st_data["running"])
        self.assertEqual(st_data["host"], ts_ip)
        self.assertEqual(st_data["local_url"], f"http://127.0.0.1:{ts_port}")
        self.assertFalse(st_data["open_in_preview"])

        p_sp = subprocess.run(
            [str(self.script_path), "--stop"],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=10.0,
            check=True,
        )
        sp_data = json.loads(p_sp.stdout.strip())
        self.assertTrue(sp_data["stopped"])
        self.assertEqual(sp_data["host"], ts_ip)

        # Both listeners must be closed after --stop
        for probe_host in (ts_ip, "127.0.0.1"):
            with self.assertRaises(OSError):
                socket.create_connection((probe_host, ts_port), timeout=1.0).close()

    def test_config_file_idle_seconds_lifecycle(self) -> None:
        cfg_path = Path(self.tmp.name) / "idle-config.json"
        cfg_path.write_text(
            json.dumps({"host": "127.0.0.1", "idle_seconds": 3600}),
            encoding="utf-8",
        )
        env = self._env()
        env["SWARM_BOARD_CONFIG"] = str(cfg_path)

        p1 = subprocess.run(
            [str(self.script_path), "--ensure", "--port", "0"],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=10.0,
            check=True,
        )
        res1 = json.loads(p1.stdout.strip())
        self.assertTrue(res1["ok"])
        pid1 = int(res1["pid"])
        port1 = int(res1["port"])
        self._pids_to_cleanup.append(pid1)

        conn = http.client.HTTPConnection("127.0.0.1", port1, timeout=2.0)
        try:
            conn.request("GET", "/health", headers={"Host": f"127.0.0.1:{port1}"})
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            health = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(health["idle_seconds"], 3600.0)
        finally:
            conn.close()


class SystemdUnitModeTests(unittest.TestCase):
    """Default launches use the persistent unit; any override keeps the direct spawn path."""

    # Every key _use_systemd_unit checks. The baseline must clear all of them, or an inherited
    # override would hide a broken per-key check.
    _ENV_KEYS = (
        "SWARM_BOARD_HOST",
        "SWARM_BOARD_PORT",
        "SWARM_BOARD_IDLE_SECONDS",
        "SWARM_BOARD_STATE_DIR",
        "SWARM_BOARD_CONFIG",
        "XDG_CACHE_HOME",
    )

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name) / "state"
        self.state_dir.mkdir()
        self.clean_env = {k: v for k, v in os.environ.items() if k not in self._ENV_KEYS}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_unit_mode_selection(self) -> None:
        def pick(**overrides: object) -> bool:
            kwargs: dict[str, object] = {
                "host": None,
                "port": None,
                "idle_seconds": None,
                "state_dir": None,
                "ui_dir": None,
            }
            kwargs.update(overrides)
            return _use_systemd_unit(**kwargs)  # type: ignore[arg-type]

        with (
            mock.patch.dict(os.environ, self.clean_env, clear=True),
            mock.patch("swarm_board.server.sys.platform", "linux"),
            mock.patch("swarm_board.server.shutil.which", return_value="/usr/bin/systemctl"),
        ):
            self.assertTrue(pick())
            self.assertFalse(pick(port=0))
            self.assertFalse(pick(host="127.0.0.1"))
            self.assertFalse(pick(idle_seconds=30.0))
            self.assertFalse(pick(state_dir=self.state_dir))
            self.assertFalse(pick(ui_dir=self.state_dir))

        with (
            mock.patch.dict(os.environ, {**self.clean_env, "SWARM_BOARD_HOST": "tailscale"}, clear=True),
            mock.patch("swarm_board.server.sys.platform", "linux"),
            mock.patch("swarm_board.server.shutil.which", return_value="/usr/bin/systemctl"),
        ):
            self.assertFalse(pick())

        with (
            mock.patch.dict(os.environ, self.clean_env, clear=True),
            mock.patch("swarm_board.server.sys.platform", "darwin"),
            mock.patch("swarm_board.server.shutil.which", return_value="/usr/bin/systemctl"),
        ):
            self.assertFalse(pick())

        with (
            mock.patch.dict(os.environ, self.clean_env, clear=True),
            mock.patch("swarm_board.server.sys.platform", "linux"),
            mock.patch("swarm_board.server.shutil.which", return_value=None),
        ):
            self.assertFalse(pick())

    def test_start_unit_command_and_failure_text(self) -> None:
        ok = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        missing = subprocess.CompletedProcess(
            [], 5, stdout="", stderr="Unit swarm-board.service not found."
        )
        with (
            mock.patch("swarm_board.server.shutil.which", return_value="/usr/bin/systemctl"),
            mock.patch("swarm_board.server.subprocess.run", return_value=ok) as run,
        ):
            _start_systemd_unit()
        self.assertEqual(
            run.call_args.args[0], ["/usr/bin/systemctl", "--user", "start", "swarm-board.service"]
        )

        with (
            mock.patch("swarm_board.server.shutil.which", return_value="/usr/bin/systemctl"),
            mock.patch("swarm_board.server.subprocess.run", return_value=missing),
        ):
            with self.assertRaisesRegex(RuntimeError, "not found"):
                _start_systemd_unit()

    def test_ensure_unit_mode_starts_unit_and_never_spawns_child(self) -> None:
        with (
            mock.patch("swarm_board.server._use_systemd_unit", return_value=True),
            mock.patch("swarm_board.server._start_systemd_unit") as start,
            mock.patch("swarm_board.server._spawn_detached_server") as spawn,
        ):
            # No server ever writes server.json here, so ensure must time out after starting the unit.
            with self.assertRaises(TimeoutError):
                ensure_server(state_dir=self.state_dir, timeout_s=0.5)
        start.assert_called_once()
        spawn.assert_not_called()

    def test_each_override_env_key_keeps_direct_path(self) -> None:
        with (
            mock.patch("swarm_board.server.sys.platform", "linux"),
            mock.patch("swarm_board.server.shutil.which", return_value="/usr/bin/systemctl"),
        ):
            for key in (
                "SWARM_BOARD_HOST",
                "SWARM_BOARD_PORT",
                "SWARM_BOARD_IDLE_SECONDS",
                "SWARM_BOARD_STATE_DIR",
                "SWARM_BOARD_CONFIG",
                "XDG_CACHE_HOME",
            ):
                # Presence counts, even when empty: the server reads an empty SWARM_BOARD_CONFIG as "no config".
                with mock.patch.dict(os.environ, {**self.clean_env, key: ""}, clear=True):
                    self.assertFalse(
                        _use_systemd_unit(
                            host=None, port=None, idle_seconds=None, state_dir=None, ui_dir=None
                        ),
                        key,
                    )

    def test_ensure_unit_mode_returns_healthy_server_after_unit_start(self) -> None:
        # The fake start writes the record a real unit would write after binding; ensure must then
        # find it healthy through the normal wait loop, not by timing out.
        live = create_server(host="127.0.0.1", port=0, idle_seconds=30.0)
        threading.Thread(target=live.serve_forever, daemon=True).start()
        self.addCleanup(live.server_close)
        self.addCleanup(live.shutdown)

        def fake_start() -> None:
            record = {
                "pid": os.getpid(),
                "host": "127.0.0.1",
                "port": live.bound_port,
                "url": f"http://127.0.0.1:{live.bound_port}",
                "local_url": f"http://127.0.0.1:{live.bound_port}",
            }
            (self.state_dir / "server.json").write_text(json.dumps(record), encoding="utf-8")

        with (
            mock.patch("swarm_board.server.load_user_config", return_value={}),
            mock.patch.dict(os.environ, self.clean_env, clear=True),
            mock.patch("swarm_board.server._use_systemd_unit", return_value=True),
            mock.patch("swarm_board.server._start_systemd_unit", side_effect=fake_start) as start,
            mock.patch("swarm_board.server._spawn_detached_server") as spawn,
        ):
            result = ensure_server(state_dir=self.state_dir, timeout_s=5.0)
        start.assert_called_once()
        spawn.assert_not_called()
        self.assertTrue(result["started"])
        self.assertEqual(result["port"], live.bound_port)

    def test_ensure_unit_mode_keeps_server_record_when_probe_fails(self) -> None:
        record = self.state_dir / "server.json"
        # Port 1 refuses connections, so the probe fails just like a missed probe on a live server.
        record.write_text(
            json.dumps({"pid": 999999, "host": "127.0.0.1", "port": 1}), encoding="utf-8"
        )
        with (
            mock.patch("swarm_board.server.load_user_config", return_value={}),
            mock.patch.dict(os.environ, self.clean_env, clear=True),
            mock.patch("swarm_board.server._use_systemd_unit", return_value=True),
            mock.patch("swarm_board.server._start_systemd_unit"),
        ):
            with self.assertRaises(TimeoutError):
                ensure_server(state_dir=self.state_dir, timeout_s=0.5)
        self.assertTrue(record.exists())

    def test_hook_unit_mode_keeps_server_record(self) -> None:
        record = self.state_dir / "server.json"
        record.write_text(
            json.dumps({"pid": 999999, "host": "127.0.0.1", "port": 1}), encoding="utf-8"
        )
        with (
            mock.patch("swarm_board.server.load_user_config", return_value={}),
            mock.patch.dict(os.environ, self.clean_env, clear=True),
            mock.patch("swarm_board.server._use_systemd_unit", return_value=True),
            mock.patch("swarm_board.server._start_systemd_unit"),
            mock.patch("swarm_board.server._spawn_detached_server") as spawn,
        ):
            self.assertEqual(hook_ensure_server(state_dir=self.state_dir), {})
        self.assertTrue(record.exists())
        spawn.assert_not_called()

    def test_state_owner_lock_allows_one_owner(self) -> None:
        first = _acquire_state_owner_lock(self.state_dir)
        self.assertIsNotNone(first)
        assert first is not None
        self.assertIsNone(_acquire_state_owner_lock(self.state_dir))
        first.close()
        third = _acquire_state_owner_lock(self.state_dir)
        self.assertIsNotNone(third)
        assert third is not None
        third.close()

    def test_hook_unit_mode_starts_unit_twice_without_spawning(self) -> None:
        with (
            mock.patch("swarm_board.server._use_systemd_unit", return_value=True),
            mock.patch("swarm_board.server._start_systemd_unit") as start,
            mock.patch("swarm_board.server._spawn_detached_server") as spawn,
        ):
            self.assertEqual(hook_ensure_server(state_dir=self.state_dir), {})
            self.assertEqual(hook_ensure_server(state_dir=self.state_dir), {})
        self.assertEqual(start.call_count, 2)
        spawn.assert_not_called()

    def test_hook_unit_start_failure_is_logged_not_raised(self) -> None:
        with (
            mock.patch("swarm_board.server._use_systemd_unit", return_value=True),
            mock.patch(
                "swarm_board.server._start_systemd_unit",
                side_effect=RuntimeError("systemctl --user start swarm-board.service failed: not found"),
            ),
            self.assertLogs(level="WARNING") as logs,
        ):
            self.assertEqual(hook_ensure_server(state_dir=self.state_dir), {})
        self.assertIn("not found", "\n".join(logs.output))

    def test_hook_unit_start_is_queued_with_short_timeout(self) -> None:
        # The hook holds ensure.lock while it starts the unit, so it must queue the job and not wait.
        with (
            mock.patch("swarm_board.server.load_user_config", return_value={}),
            mock.patch.dict(os.environ, self.clean_env, clear=True),
            mock.patch("swarm_board.server._use_systemd_unit", return_value=True),
            mock.patch("swarm_board.server._start_systemd_unit") as start,
        ):
            self.assertEqual(hook_ensure_server(state_dir=self.state_dir), {})
        start.assert_called_once()
        self.assertIs(start.call_args.kwargs["block"], False)
        self.assertLessEqual(start.call_args.kwargs["timeout_s"], 2.0)

    def test_nonblocking_start_passes_no_block_and_timeout(self) -> None:
        with (
            mock.patch("swarm_board.server.shutil.which", return_value="/usr/bin/systemctl"),
            mock.patch(
                "swarm_board.server.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ) as run,
        ):
            _start_systemd_unit(block=False, timeout_s=2.0)
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/systemctl", "--user", "start", "--no-block", "swarm-board.service"],
        )
        self.assertEqual(run.call_args.kwargs["timeout"], 2.0)

    def test_direct_ensure_keeps_record_when_child_loses_state_lock(self) -> None:
        # Sol round 4 repro: a live owner's record must survive a new child that exits 1 at the lock.
        record = self.state_dir / "server.json"
        record.write_text(json.dumps({"pid": 999999, "host": "127.0.0.1", "port": 1}), encoding="utf-8")
        fake_child = mock.Mock(pid=424242, returncode=1)
        fake_child.poll.return_value = 1
        with (
            mock.patch("swarm_board.server.load_user_config", return_value={}),
            mock.patch.dict(os.environ, self.clean_env, clear=True),
            mock.patch("swarm_board.server._use_systemd_unit", return_value=False),
            mock.patch("swarm_board.server._spawn_detached_server", return_value=fake_child) as spawn,
        ):
            with self.assertRaises(RuntimeError):
                ensure_server(state_dir=self.state_dir, port=1, timeout_s=5.0)
        spawn.assert_called_once()
        self.assertTrue(record.exists())

    def test_direct_hook_keeps_record_when_spawning(self) -> None:
        record = self.state_dir / "server.json"
        record.write_text(json.dumps({"pid": 999999, "host": "127.0.0.1", "port": 1}), encoding="utf-8")
        with (
            mock.patch("swarm_board.server.load_user_config", return_value={}),
            mock.patch.dict(os.environ, self.clean_env, clear=True),
            mock.patch("swarm_board.server._use_systemd_unit", return_value=False),
            mock.patch(
                "swarm_board.server._spawn_detached_server", return_value=mock.Mock(pid=424242)
            ) as spawn,
        ):
            self.assertEqual(hook_ensure_server(state_dir=self.state_dir, port=1), {})
        spawn.assert_called_once()
        self.assertTrue(record.exists())


if __name__ == "__main__":
    unittest.main()
