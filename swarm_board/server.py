"""Localhost-only HTTP + SSE server and lifecycle CLI for Swarm Board."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from datetime import datetime, timezone
import errno
import fcntl
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import logging
import math
import mimetypes
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from .adapters import BoardRegistry
from .safety import is_path_allowed, redact_if_secret_name

SERVER_VERSION = "0.3.0"
BIND_HOST = "127.0.0.1"
TAILSCALE_CGNAT_NET = ipaddress.IPv4Network("100.64.0.0/10")
DEFAULT_PORT = 18420
PORT_SCAN_END = 18449
DEFAULT_IDLE_SECONDS = 7200.0
DEFAULT_SSE_POLL_S = 2.0
DEFAULT_SSE_HEARTBEAT_S = 20.0
_STREAM_CHUNK_BYTES = 65_536
_ENSURE_TIMEOUT_S = 5.0
_HEALTH_PROBE_TIMEOUT_S = 0.4
_HOOK_PROBE_TIMEOUT_S = 0.25
_SIOCGIFADDR = 0x8915

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_UI_DIR = Path(__file__).resolve().parent / "ui"
_DEFAULT_ENV_ID_PATH = Path.home() / ".t3" / "userdata" / "environment-id"
_DEFAULT_CONFIG_PATH = Path.home() / ".config" / "swarm-board" / "config.json"

_HOST_HEADER_RE = re.compile(r"^(?:127\.0\.0\.1|localhost|\[::1\])(?::(\d{1,5}))?$")
_BYTE_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_IP_INET_RE = re.compile(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/")

_LOGGER = logging.getLogger("swarm_board.server")

# Ensure common media MIME types are registered across minimal Linux/macOS installs.
for _ext, _mime in (
    (".webp", "image/webp"),
    (".svg", "image/svg+xml"),
    (".mp4", "video/mp4"),
    (".webm", "video/webm"),
    (".m4a", "audio/mp4"),
    (".mp3", "audio/mpeg"),
    (".wav", "audio/wav"),
    (".ogg", "audio/ogg"),
    (".jsonl", "application/x-ndjson"),
):
    mimetypes.add_type(_mime, _ext)


def get_state_dir(override: str | Path | None = None) -> Path:
    """Resolve the cross-platform runtime state directory for Swarm Board."""
    if override is not None and str(override).strip():
        return Path(override).expanduser()
    env_dir = os.environ.get("SWARM_BOARD_STATE_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "swarm-board"
    xdg_cache = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache"
    return base / "swarm-board"


def get_config_path(override: str | Path | None = None) -> Path | None:
    """Resolve user config path, or None when SWARM_BOARD_CONFIG disables lookup."""
    if override is not None:
        raw = str(override).strip()
        return Path(raw).expanduser() if raw else None
    if "SWARM_BOARD_CONFIG" in os.environ:
        env_cfg = os.environ["SWARM_BOARD_CONFIG"].strip()
        return Path(env_cfg).expanduser() if env_cfg else None
    return _DEFAULT_CONFIG_PATH


def load_user_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load optional JSON config (returns {} if absent, disabled, or invalid)."""
    path = get_config_path(config_path)
    if path is None:
        return {}
    data = _read_json_file(path)
    return data if data is not None else {}


def is_tailscale_ipv4(host: str) -> bool:
    """Return True when host is a Tailscale CGNAT IPv4 address (100.64.0.0/10)."""
    try:
        ip = ipaddress.IPv4Address((host or "").strip())
    except ValueError:
        return False
    return ip in TAILSCALE_CGNAT_NET


def is_loopback_host(host: str) -> bool:
    """Return True when host refers to loopback (127.0.0.0/8, localhost, or ::1)."""
    cleaned = (host or "").strip().lower()
    if cleaned in ("localhost", "::1", "[::1]"):
        return True
    try:
        ip = ipaddress.IPv4Address(cleaned)
    except ValueError:
        return False
    return ip.is_loopback


def detect_tailscale_ipv4() -> str:
    """Detect the local Tailscale IPv4 address without ever falling back to 0.0.0.0."""
    if sys.platform.startswith("linux"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                ifreq = fcntl.ioctl(
                    sock.fileno(),
                    _SIOCGIFADDR,
                    struct.pack("256s", b"tailscale0"),
                )
                ip_str = socket.inet_ntoa(ifreq[20:24])
                if is_tailscale_ipv4(ip_str):
                    return ip_str
        except OSError:
            pass

    try:
        proc = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                cand = line.strip()
                if is_tailscale_ipv4(cand):
                    return cand
    except (OSError, subprocess.SubprocessError):
        pass

    if sys.platform.startswith("linux"):
        try:
            proc_ip = subprocess.run(
                ["ip", "-4", "-o", "addr", "show"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if proc_ip.returncode == 0:
                for match in _IP_INET_RE.finditer(proc_ip.stdout):
                    cand = match.group(1)
                    if is_tailscale_ipv4(cand):
                        return cand
        except (OSError, subprocess.SubprocessError):
            pass

    raise RuntimeError(
        "Tailscale IPv4 address (100.64.0.0/10) not found; refusing to bind non-Tailscale interface."
    )


def detect_tailscale_hostnames() -> list[str]:
    """Return normalized local Tailscale MagicDNS / HostName entries (best-effort)."""
    try:
        proc = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            return []
        data = json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []

    self_info = data.get("Self") if isinstance(data, dict) else None
    if not isinstance(self_info, dict):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for key in ("DNSName", "HostName"):
        raw = self_info.get(key)
        if isinstance(raw, str):
            norm = raw.strip().lower().rstrip(".")
            if norm and norm not in seen:
                seen.add(norm)
                out.append(norm)
    return out


def resolve_bind_host(
    host: str | None = None,
    *,
    use_env_and_config: bool = True,
    config: dict[str, Any] | None = None,
) -> str:
    """Resolve and strictly validate a loopback or Tailscale-only bind host."""
    raw: str
    if host is not None:
        raw = str(host).strip()
    elif use_env_and_config:
        env_host = os.environ.get("SWARM_BOARD_HOST", "").strip()
        if env_host:
            raw = env_host
        else:
            cfg = config if config is not None else load_user_config()
            cfg_host = cfg.get("host")
            raw = cfg_host.strip() if isinstance(cfg_host, str) and cfg_host.strip() else BIND_HOST
    else:
        raw = BIND_HOST

    low = raw.lower()
    if low in ("", "127.0.0.1", "localhost", "loopback"):
        return BIND_HOST
    if low == "tailscale":
        return detect_tailscale_ipv4()

    try:
        ip = ipaddress.IPv4Address(raw)
    except ValueError as exc:
        raise ValueError(
            f"Unsupported bind host {raw!r}; expected '127.0.0.1', 'tailscale', or a loopback/100.64.0.0/10 IPv4."
        ) from exc

    if ip.is_loopback or ip in TAILSCALE_CGNAT_NET:
        return str(ip)
    raise ValueError(
        f"Refusing to bind non-loopback, non-Tailscale IPv4 address: {raw}"
    )


def resolve_open_in_preview(
    bind_host: str,
    *,
    config: dict[str, Any] | None = None,
) -> bool:
    """Determine whether agents should open Swarm Board in T3 preview or return the URL."""
    env_raw = os.environ.get("SWARM_BOARD_OPEN_IN_PREVIEW", "").strip().lower()
    if env_raw in ("1", "true", "yes"):
        return True
    if env_raw in ("0", "false", "no"):
        return False

    cfg = config if config is not None else load_user_config()
    cfg_val = cfg.get("open_in_preview")
    if isinstance(cfg_val, bool):
        return cfg_val
    return is_loopback_host(bind_host)


def _parse_idle_seconds(raw: Any) -> float | None:
    """Parse a finite non-negative idle timeout in seconds, rejecting booleans."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
    elif isinstance(raw, str):
        cleaned = raw.strip()
        if not cleaned:
            return None
        try:
            val = float(cleaned)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(val):
        return None
    return max(0.0, val)


def resolve_idle_seconds(
    idle_seconds: float | None = None,
    *,
    use_env_and_config: bool = True,
    config: dict[str, Any] | None = None,
) -> float:
    """Resolve idle auto-exit timeout in seconds (explicit > env > config > default)."""
    if idle_seconds is not None:
        parsed = _parse_idle_seconds(idle_seconds)
        if parsed is None:
            raise ValueError(f"Invalid idle_seconds value: {idle_seconds!r}")
        return parsed

    if use_env_and_config:
        env_raw = os.environ.get("SWARM_BOARD_IDLE_SECONDS", "").strip()
        if env_raw:
            parsed = _parse_idle_seconds(env_raw)
            if parsed is not None:
                return parsed

        cfg = config if config is not None else load_user_config()
        if "idle_seconds" in cfg and cfg["idle_seconds"] is not None:
            parsed = _parse_idle_seconds(cfg["idle_seconds"])
            if parsed is not None:
                return parsed

    return DEFAULT_IDLE_SECONDS


def is_valid_host_header(
    host: str | None,
    *,
    allowed_hosts: Iterable[str] | None = None,
) -> bool:
    """Allow loopback Host headers plus explicit bound Tailscale IPs/MagicDNS hostnames."""
    if host is None:
        return True
    cleaned = host.strip().lower()
    if not cleaned:
        return False
    m = _HOST_HEADER_RE.match(cleaned)
    if m:
        port_str = m.group(1)
        if port_str is not None:
            port_num = int(port_str)
            if port_num < 1 or port_num > 65535:
                return False
        return True

    if allowed_hosts is None:
        return False

    allowed_set = {str(h).strip().lower().rstrip(".") for h in allowed_hosts if str(h).strip()}
    if not allowed_set or cleaned.startswith("["):
        return False

    if ":" in cleaned:
        host_part, port_str = cleaned.rsplit(":", 1)
        if ":" in host_part or not port_str.isdigit():
            return False
        port_num = int(port_str)
        if port_num < 1 or port_num > 65535:
            return False
    else:
        host_part = cleaned

    norm_host = host_part.rstrip(".")
    return bool(norm_host) and norm_host in allowed_set


def parse_byte_range(header: str, total_size: int) -> tuple[int, int]:
    """Parse a single RFC 9110 `bytes=...` Range header or raise ValueError for 416."""
    if total_size <= 0:
        raise ValueError("Cannot satisfy byte range for empty resource")
    spec = header.strip()
    if "," in spec:
        raise ValueError("Multiple byte ranges are not supported")
    m = _BYTE_RANGE_RE.match(spec)
    if not m:
        raise ValueError("Malformed byte range")
    start_s, end_s = m.group(1), m.group(2)
    if not start_s and not end_s:
        raise ValueError("Empty byte range")
    if not start_s:
        suffix = int(end_s)
        if suffix <= 0:
            raise ValueError("Suffix byte range must be positive")
        start = max(0, total_size - suffix)
        end = total_size - 1
        return start, end
    start = int(start_s)
    if start >= total_size:
        raise ValueError("Byte range start past EOF")
    if not end_s:
        return start, total_size - 1
    end = int(end_s)
    if end < start:
        raise ValueError("Byte range end precedes start")
    return start, min(end, total_size - 1)


def _format_snapshot_time(dt: datetime | None = None) -> str:
    """Format local wall-clock timestamp like '25 Nov 19:55'."""
    now = dt if dt is not None else datetime.now().astimezone()
    return f"{now.day} {now.strftime('%b %H:%M')}"


def _format_sse_event(event_name: str, payload: dict[str, Any]) -> bytes:
    """Serialize an SSE event frame as UTF-8 bytes."""
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return f"event: {event_name}\ndata: {data}\n\n".encode("utf-8")


def _error_looks_denied(msg: str | None) -> bool:
    """Return True when an error string describes a path-security or secret denial."""
    low = (msg or "").lower()
    return any(
        tok in low
        for tok in ("denied", "outside", "secret", "sensitive", "forbidden", "traversal", "escape")
    )


def _allowed_roots_for_flow(registry: Any, flow_id: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Collect [projectRoot, *extra_allowed_roots] for a flow if known."""
    flow = registry.get_flow(flow_id)
    if not isinstance(flow, dict):
        return None, []
    roots: list[str] = []
    proj_root = flow.get("projectRoot")
    if isinstance(proj_root, str) and proj_root:
        roots.append(proj_root)
    extra_fn = getattr(registry, "_extra_allowed_roots", None)
    if callable(extra_fn):
        for r in extra_fn() or ():
            if r:
                roots.append(str(r))
    else:
        cfg = getattr(registry, "config", None)
        if isinstance(cfg, dict):
            raw_extra = cfg.get("extra_allowed_roots")
            if isinstance(raw_extra, (str, Path)):
                roots.append(str(raw_extra))
            elif isinstance(raw_extra, (list, tuple, set)):
                roots.extend(str(v) for v in raw_extra if v)
    return flow, roots


def _classify_file_error(
    registry: Any,
    flow_id: str,
    raw_path: str,
    error_msg: str | None,
) -> tuple[int, str]:
    """Distinguish 403 security denials from 404 missing resources for /api/file and /api/raw."""
    secret_reason = redact_if_secret_name(raw_path)
    if secret_reason:
        return 403, secret_reason
    msg = error_msg or "Not found"
    if _error_looks_denied(msg):
        return 403, msg

    flow, roots = _allowed_roots_for_flow(registry, flow_id)
    if flow is None:
        return 404, msg
    if not roots:
        return 403, "Flow has no allowed project root"

    p = Path(raw_path)
    if p.is_absolute():
        ok, reason = is_path_allowed(raw_path, roots)
        if not ok:
            return 403, reason or "Access denied"
        return 404, msg

    last_reason: str | None = None
    for root in roots:
        candidate = str(Path(root) / p)
        ok, reason = is_path_allowed(candidate, [root])
        if ok:
            return 404, msg
        if reason:
            last_reason = reason
    return 403, last_reason or "Path is outside allowed project roots"


def _resolve_raw_file_path(
    registry: Any,
    flow_id: str,
    raw_path: str,
) -> tuple[int, str | None, str | None]:
    """Resolve and validate a raw file path via registry.get_raw_file_path or safety fallback.

    Returns (http_status, resolved_path_or_none, error_message_or_none).
    """
    secret_reason = redact_if_secret_name(raw_path)
    if secret_reason:
        return 403, None, secret_reason

    resolved: str | None = None
    get_raw = getattr(registry, "get_raw_file_path", None)
    if callable(get_raw):
        try:
            res = get_raw(flow_id, raw_path)
        except PermissionError as exc:
            return 403, None, str(exc) or "Access denied"
        except FileNotFoundError as exc:
            return 404, None, str(exc) or "File not found"
        if isinstance(res, dict):
            if res.get("ok") and res.get("path"):
                resolved = str(res["path"])
            else:
                code, err = _classify_file_error(registry, flow_id, raw_path, res.get("error"))
                return code, None, err
        elif isinstance(res, tuple) and len(res) == 2:
            first, second = res
            if isinstance(first, bool):
                if first and second:
                    resolved = str(second)
                else:
                    code, err = _classify_file_error(
                        registry, flow_id, raw_path, str(second) if second else None
                    )
                    return code, None, err
            else:
                if first:
                    resolved = str(first)
                else:
                    code, err = _classify_file_error(
                        registry, flow_id, raw_path, str(second) if second else None
                    )
                    return code, None, err
        elif isinstance(res, (str, Path)) and str(res):
            resolved = str(res)

    if resolved is None:
        flow, roots = _allowed_roots_for_flow(registry, flow_id)
        if flow is None:
            return 404, None, f"Unknown flow: {flow_id}"
        if not roots:
            return 403, None, "Flow has no allowed project root"

        p = Path(raw_path)
        if p.is_absolute():
            ok, res_or_err = is_path_allowed(raw_path, roots)
            if not ok or not res_or_err:
                return 403, None, res_or_err or "Access denied"
            resolved = res_or_err
        else:
            denied_reason: str | None = None
            any_allowed_syntax = False
            for root in roots:
                candidate = str(Path(root) / p)
                ok, res_or_err = is_path_allowed(candidate, [root])
                if ok and res_or_err:
                    any_allowed_syntax = True
                    if os.path.exists(res_or_err):
                        resolved = res_or_err
                        break
                elif res_or_err:
                    denied_reason = res_or_err
            if resolved is None:
                if not any_allowed_syntax:
                    return 403, None, denied_reason or "Path is outside allowed project roots"
                return 404, None, "File not found under this project"

    resolved_secret = redact_if_secret_name(resolved)
    if resolved_secret:
        return 403, None, resolved_secret
    if not os.path.isfile(resolved):
        return 404, None, "File not found"
    return 200, resolved, None


def _compute_fallback_stats(
    registry: Any,
    *,
    scope: str,
    flow_id: str | None,
    rng: str,
    lab: str | None,
) -> dict[str, Any]:
    """Compute a minimal stats payload when registry.get_stats is not implemented."""
    flows = registry.list_flows(force=False)
    if scope == "flow" and flow_id:
        flows = [f for f in flows if isinstance(f, dict) and f.get("id") == flow_id]
    by_state: dict[str, int] = {}
    by_lab: dict[str, int] = {}
    tok_in = 0
    tok_cache = 0
    tok_out = 0
    total_cost = 0.0
    agents_count = 0
    for flow in flows:
        if not isinstance(flow, dict):
            continue
        for agent in flow.get("agents") or ():
            if not isinstance(agent, dict):
                continue
            a_lab = str(agent.get("lab") or "other")
            if lab and lab != "all" and a_lab != lab:
                continue
            agents_count += 1
            st = str(agent.get("state") or "done")
            by_state[st] = by_state.get(st, 0) + 1
            by_lab[a_lab] = by_lab.get(a_lab, 0) + 1
            tok = agent.get("tokens")
            if isinstance(tok, dict):
                tok_in += int(tok.get("in", 0) or 0)
                tok_cache += int(tok.get("cache", 0) or 0)
                tok_out += int(tok.get("out", 0) or 0)
            cost = agent.get("costUsd")
            if isinstance(cost, (int, float)):
                total_cost += float(cost)
    return {
        "scope": scope,
        "flowId": flow_id,
        "rng": rng,
        "lab": lab,
        "flowsCount": len(flows),
        "agentsCount": agents_count,
        "byState": by_state,
        "byLab": by_lab,
        "tokens": {"in": tok_in, "cache": tok_cache, "out": tok_out},
        "costUsd": round(total_cost, 4),
    }


def _resolve_env_id(registry: Any) -> str | None:
    """Safely read environment ID from registry.get_env_id() or ~/.t3/userdata/environment-id."""
    get_env = getattr(registry, "get_env_id", None)
    if callable(get_env):
        val = get_env()
        return str(val).strip() or None if val is not None else None
    cfg = getattr(registry, "config", None)
    if isinstance(cfg, dict) and cfg.get("env_id"):
        return str(cfg["env_id"]).strip() or None
    try:
        text = _DEFAULT_ENV_ID_PATH.read_text(encoding="utf-8").strip()
        return text or None
    except OSError:
        return None


class SSEHub:
    """Single shared background digest poller active ONLY while >= 1 SSE client is connected."""

    def __init__(
        self,
        registry: Any,
        *,
        poll_interval_s: float = DEFAULT_SSE_POLL_S,
        on_empty_callback: Callable[[], None] | None = None,
    ) -> None:
        self.registry = registry
        self.poll_interval_s = max(0.05, float(poll_interval_s))
        self._on_empty_callback = on_empty_callback
        self._lock = threading.Lock()
        self._clients: set[queue.Queue[bytes | None]] = set()
        self._poller_thread: threading.Thread | None = None
        self._poller_stop: threading.Event | None = None
        self._last_digest: str | None = None
        self._closed = False

    def client_count(self) -> int:
        """Return the number of currently connected SSE clients."""
        with self._lock:
            return len(self._clients)

    def poller_active(self) -> bool:
        """Return True if the shared background digest-poller thread is currently alive."""
        with self._lock:
            return self._poller_thread is not None and self._poller_thread.is_alive()

    def register(self, initial_digest: str) -> queue.Queue[bytes | None] | None:
        """Register a new SSE client queue and start the shared poller if transitioning 0 -> 1."""
        client_q: queue.Queue[bytes | None] = queue.Queue(maxsize=64)
        with self._lock:
            if self._closed:
                return None
            was_empty = len(self._clients) == 0
            self._clients.add(client_q)
            if was_empty or self._last_digest is None:
                self._last_digest = initial_digest
            if self._poller_thread is None or not self._poller_thread.is_alive():
                stop_ev = threading.Event()
                self._poller_stop = stop_ev
                thread = threading.Thread(
                    target=self._poll_loop,
                    args=(stop_ev,),
                    name="swarm-board-sse-poller",
                    daemon=True,
                )
                self._poller_thread = thread
                thread.start()
        return client_q

    def unregister(self, client_q: queue.Queue[bytes | None]) -> None:
        """Unregister an SSE client and stop the shared poller immediately when count hits 0."""
        became_empty = False
        with self._lock:
            if client_q in self._clients:
                self._clients.remove(client_q)
                became_empty = len(self._clients) == 0
            if became_empty:
                if self._poller_stop is not None:
                    self._poller_stop.set()
                    self._poller_stop = None
                self._poller_thread = None
                self._last_digest = None
        if became_empty and self._on_empty_callback is not None:
            self._on_empty_callback()

    def _enqueue_to_clients(self, payload: bytes | None) -> None:
        with self._lock:
            targets = list(self._clients)
        for q in targets:
            try:
                q.put_nowait(payload)
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    pass

    def _poll_loop(self, stop_ev: threading.Event) -> None:
        while not stop_ev.wait(self.poll_interval_s):
            with self._lock:
                if self._closed or not self._clients or self._poller_stop is not stop_ev:
                    return
                last_digest = self._last_digest
            try:
                new_digest = str(self.registry.snapshot_digest())
            except Exception as exc:  # noqa: BLE001 - keep SSE hub resilient
                _LOGGER.debug("SSE snapshot_digest error: %s", exc)
                continue

            if new_digest != last_digest:
                with self._lock:
                    if self._closed or not self._clients or self._poller_stop is not stop_ev:
                        return
                    self._last_digest = new_digest
                frame = _format_sse_event("update", {"digest": new_digest, "ts": time.time()})
                self._enqueue_to_clients(frame)

    def close(self) -> None:
        """Stop the poller and wake all connected SSE streams for immediate server shutdown."""
        with self._lock:
            self._closed = True
            if self._poller_stop is not None:
                self._poller_stop.set()
                self._poller_stop = None
            thread = self._poller_thread
            self._poller_thread = None
            targets = list(self._clients)
        for q in targets:
            while True:
                try:
                    q.put_nowait(None)
                    break
                except queue.Full:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)


class SwarmBoardRequestHandler(BaseHTTPRequestHandler):
    """Read-only HTTP + SSE handler strictly bound to loopback."""

    protocol_version = "HTTP/1.1"
    server: SwarmBoardHTTPServer  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:
        _LOGGER.debug("%s - %s", self.address_string(), format % args)

    def _send_json(self, status: int, payload: Any, *, head_only: bool = False) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _send_text(
        self,
        status: int,
        text: str,
        *,
        content_type: str = "text/plain; charset=utf-8",
        head_only: bool = False,
    ) -> None:
        body = text.encode("utf-8")
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _check_host(self) -> bool:
        if not is_valid_host_header(
            self.headers.get("Host"),
            allowed_hosts=self.server.allowed_hosts,
        ):
            self._send_json(403, {"ok": False, "error": "Forbidden host"})
            return False
        return True

    def _reject_method(self) -> None:
        self.server.touch_activity()
        if not self._check_host():
            return
        body = json.dumps({"ok": False, "error": "Method not allowed"}).encode("utf-8")
        self.close_connection = True
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        self._reject_method()

    def do_PUT(self) -> None:  # noqa: N802
        self._reject_method()

    def do_PATCH(self) -> None:  # noqa: N802
        self._reject_method()

    def do_DELETE(self) -> None:  # noqa: N802
        self._reject_method()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._reject_method()

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle_get(head_only=True)

    def do_GET(self) -> None:  # noqa: N802
        self._handle_get(head_only=False)

    def _handle_get(self, *, head_only: bool) -> None:
        self.server.touch_activity()
        if not self._check_host():
            return

        parsed = urlsplit(self.path)
        raw_path = parsed.path or "/"
        params = parse_qs(parsed.query, keep_blank_values=True)
        segments = [unquote(seg) for seg in raw_path.split("/") if seg != ""]

        try:
            if raw_path in ("/", "/index.html"):
                self._serve_index(head_only=head_only)
            elif raw_path == "/health":
                self._serve_health(head_only=head_only)
            elif raw_path == "/api/flows":
                self._serve_flows(params, head_only=head_only)
            elif len(segments) == 3 and segments[0] == "api" and segments[1] == "flow":
                self._serve_single_flow(segments[2], head_only=head_only)
            elif raw_path == "/api/stats":
                self._serve_stats(params, head_only=head_only)
            elif len(segments) == 4 and segments[0] == "api" and segments[1] == "output":
                self._serve_output(segments[2], segments[3], head_only=head_only)
            elif raw_path == "/api/file":
                self._serve_citation(params, head_only=head_only)
            elif raw_path == "/api/raw":
                self._serve_raw(params, head_only=head_only)
            elif (
                len(segments) == 4
                and segments[0] == "api"
                and segments[1] == "thread"
                and segments[3] == "messages"
            ):
                self._serve_thread_messages(segments[2], params, head_only=head_only)
            elif raw_path == "/events":
                self._serve_sse(head_only=head_only)
            else:
                self._send_json(404, {"ok": False, "error": "Not found"}, head_only=head_only)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001 - prevent silent 500 crashes
            _LOGGER.exception("Unhandled error for %s: %s", raw_path, exc)
            try:
                self._send_json(
                    500,
                    {"ok": False, "error": f"Internal server error: {type(exc).__name__}"},
                    head_only=head_only,
                )
            except OSError:
                self.close_connection = True

    def _serve_index(self, *, head_only: bool) -> None:
        index_path = self.server.ui_dir / "index.html"
        if not index_path.is_file():
            self._send_text(
                503,
                "Swarm Board UI is not built yet (missing swarm_board/ui/index.html).\n",
                head_only=head_only,
            )
            return
        try:
            data = index_path.read_bytes()
        except OSError as exc:
            self._send_text(503, f"Unable to read UI index: {exc}\n", head_only=head_only)
            return
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def _serve_health(self, *, head_only: bool) -> None:
        hosts = (
            [self.server.bind_host, BIND_HOST]
            if self.server.bind_host != BIND_HOST
            else [self.server.bind_host]
        )
        payload = {
            "ok": True,
            "pid": os.getpid(),
            "host": self.server.bind_host,
            "hosts": hosts,
            "port": self.server.bound_port,
            "local_url": f"http://{BIND_HOST}:{self.server.bound_port}",
            "uptime_s": round(max(0.0, time.monotonic() - self.server.started_monotonic), 3),
            "idle_seconds": self.server.idle_seconds,
            "clients": self.server.sse_hub.client_count(),
            "flows_count": self.server.get_flows_count(),
            "version": SERVER_VERSION,
        }
        self._send_json(200, payload, head_only=head_only)

    def _serve_flows(self, params: dict[str, list[str]], *, head_only: bool) -> None:
        force_raw = (params.get("force", [""])[0] or "").strip().lower()
        force = force_raw in ("1", "true", "yes")
        reg = self.server.registry
        flows = reg.list_flows(force=force)
        payload = {
            "ok": True,
            "envId": _resolve_env_id(reg),
            "snapshot": _format_snapshot_time(),
            "flows": flows,
            "errors": dict(getattr(reg, "last_errors", {}) or {}),
        }
        self._send_json(200, payload, head_only=head_only)

    def _serve_single_flow(self, flow_id: str, *, head_only: bool) -> None:
        if not flow_id:
            self._send_json(404, {"ok": False, "error": "Flow not found"}, head_only=head_only)
            return
        flow = self.server.registry.get_flow(flow_id)
        if flow is None:
            self._send_json(404, {"ok": False, "error": "Flow not found"}, head_only=head_only)
            return
        self._send_json(200, {"ok": True, "flow": flow}, head_only=head_only)

    def _serve_stats(self, params: dict[str, list[str]], *, head_only: bool) -> None:
        scope = (params.get("scope", ["all"])[0] or "all").strip() or "all"
        flow_id = (params.get("flow_id", [""])[0] or "").strip() or None
        rng = (params.get("rng", ["all"])[0] or "all").strip() or "all"
        lab = (params.get("lab", [""])[0] or "").strip() or None

        reg = self.server.registry
        get_stats = getattr(reg, "get_stats", None)
        if callable(get_stats):
            res = get_stats(scope=scope, flow_id=flow_id, rng=rng, lab=lab)
            if isinstance(res, dict) and res.get("ok") is False:
                self._send_json(404, res, head_only=head_only)
                return
            if isinstance(res, dict) and "ok" in res and "stats" in res:
                self._send_json(200, res, head_only=head_only)
                return
            self._send_json(200, {"ok": True, "stats": res}, head_only=head_only)
            return

        stats = _compute_fallback_stats(reg, scope=scope, flow_id=flow_id, rng=rng, lab=lab)
        self._send_json(200, {"ok": True, "stats": stats}, head_only=head_only)

    def _serve_output(self, flow_id: str, output_id: str, *, head_only: bool) -> None:
        res = self.server.registry.get_output(flow_id, output_id)
        if res is None:
            self._send_json(404, {"ok": False, "error": "Output not found"}, head_only=head_only)
            return
        if res.get("ok"):
            self._send_json(200, res, head_only=head_only)
            return
        status = 403 if _error_looks_denied(res.get("error")) else 404
        self._send_json(status, res, head_only=head_only)

    def _serve_citation(self, params: dict[str, list[str]], *, head_only: bool) -> None:
        flow_id = (params.get("flow", [""])[0] or "").strip()
        raw_path = params.get("path", [""])[0] or ""
        line_raw = params.get("line", [""])[0]
        ctx_raw = params.get("ctx", ["4"])[0]

        if not flow_id or not raw_path or not line_raw:
            self._send_json(
                400,
                {"ok": False, "error": "Missing required parameters: flow, path, line"},
                head_only=head_only,
            )
            return
        try:
            line = int(line_raw)
        except ValueError:
            self._send_json(400, {"ok": False, "error": "Invalid line parameter"}, head_only=head_only)
            return
        if line < 1:
            self._send_json(400, {"ok": False, "error": "line must be >= 1"}, head_only=head_only)
            return

        try:
            ctx = int(ctx_raw) if ctx_raw != "" else 4
        except ValueError:
            self._send_json(400, {"ok": False, "error": "Invalid ctx parameter"}, head_only=head_only)
            return
        if ctx < 0 or ctx > 500:
            self._send_json(400, {"ok": False, "error": "ctx must be between 0 and 500"}, head_only=head_only)
            return

        secret_reason = redact_if_secret_name(raw_path)
        if secret_reason:
            self._send_json(403, {"ok": False, "error": secret_reason}, head_only=head_only)
            return

        res = self.server.registry.get_citation(flow_id, raw_path, line, ctx=ctx)
        if res.get("ok"):
            self._send_json(200, res, head_only=head_only)
            return

        status, err_msg = _classify_file_error(
            self.server.registry, flow_id, raw_path, res.get("error")
        )
        out = dict(res)
        out["ok"] = False
        out["error"] = err_msg
        self._send_json(status, out, head_only=head_only)

    def _serve_raw(self, params: dict[str, list[str]], *, head_only: bool) -> None:
        flow_id = (params.get("flow", [""])[0] or "").strip()
        raw_path = params.get("path", [""])[0] or ""
        if not flow_id or not raw_path:
            self._send_json(
                400,
                {"ok": False, "error": "Missing required parameters: flow, path"},
                head_only=head_only,
            )
            return

        status, resolved, err_msg = _resolve_raw_file_path(self.server.registry, flow_id, raw_path)
        if status != 200 or not resolved:
            self._send_json(status, {"ok": False, "error": err_msg or "Not found"}, head_only=head_only)
            return

        try:
            total_size = os.path.getsize(resolved)
        except OSError as exc:
            self._send_json(404, {"ok": False, "error": f"Stat failed: {exc}"}, head_only=head_only)
            return

        mime_type, _ = mimetypes.guess_type(resolved)
        content_type = mime_type or "application/octet-stream"
        range_hdr = self.headers.get("Range")

        if range_hdr is not None:
            try:
                start, end = parse_byte_range(range_hdr, total_size)
            except ValueError:
                self.close_connection = True
                self.send_response(416)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes */{total_size}")
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                return

            length = end - start + 1
            self.close_connection = True
            self.send_response(206)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{total_size}")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            if head_only:
                return
            self._stream_file_slice(resolved, start, length)
            return

        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(total_size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        if head_only:
            return
        self._stream_file_slice(resolved, 0, total_size)

    def _stream_file_slice(self, file_path: str, offset: int, length: int) -> None:
        remaining = length
        with open(file_path, "rb") as fh:
            if offset > 0:
                fh.seek(offset)
            while remaining > 0:
                chunk = fh.read(min(_STREAM_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _serve_thread_messages(
        self,
        thread_id: str,
        params: dict[str, list[str]],
        *,
        head_only: bool,
    ) -> None:
        if not thread_id:
            self._send_json(400, {"ok": False, "error": "Missing thread ID"}, head_only=head_only)
            return
        limit_raw = params.get("limit", ["50"])[0]
        try:
            limit = int(limit_raw) if limit_raw != "" else 50
        except ValueError:
            self._send_json(400, {"ok": False, "error": "Invalid limit parameter"}, head_only=head_only)
            return
        if limit < 1 or limit > 1000:
            self._send_json(
                400,
                {"ok": False, "error": "limit must be between 1 and 1000"},
                head_only=head_only,
            )
            return
        before = (params.get("before", [""])[0] or "").strip() or None
        messages = self.server.registry.get_thread_messages(thread_id, limit=limit, before=before)
        self._send_json(200, {"ok": True, "messages": messages}, head_only=head_only)

    def _serve_sse(self, *, head_only: bool) -> None:
        if head_only:
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            return

        try:
            initial_digest = str(self.server.registry.snapshot_digest())
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Initial SSE snapshot_digest error: %s", exc)
            initial_digest = ""

        client_q = self.server.sse_hub.register(initial_digest)
        if client_q is None:
            self._send_json(503, {"ok": False, "error": "Server is shutting down"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.close_connection = True

        try:
            ready_frame = _format_sse_event("ready", {"digest": initial_digest, "ts": time.time()})
            self.wfile.write(ready_frame)
            self.wfile.flush()

            heartbeat_s = self.server.sse_heartbeat_interval_s
            while not self.server.is_shutting_down():
                try:
                    frame = client_q.get(timeout=heartbeat_s)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if frame is None:
                    break
                self.wfile.write(frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            self.server.sse_hub.unregister(client_q)


class _LoopbackCompanionServer(ThreadingHTTPServer):
    """Secondary 127.0.0.1 listener sharing registry, SSE hub, and idle state with primary."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        primary: SwarmBoardHTTPServer,
    ) -> None:
        self._primary = primary
        self._serving_started = threading.Event()
        self.registry = primary.registry
        self.ui_dir = primary.ui_dir
        self.idle_seconds = primary.idle_seconds
        self.sse_heartbeat_interval_s = primary.sse_heartbeat_interval_s
        self.started_monotonic = primary.started_monotonic
        self.sse_hub = primary.sse_hub
        self.allowed_hosts = primary.allowed_hosts
        self.bind_host = primary.bind_host
        self.bound_port = primary.bound_port
        super().__init__(server_address, SwarmBoardRequestHandler)

    def touch_activity(self) -> None:
        self._primary.touch_activity()

    def is_shutting_down(self) -> bool:
        return self._primary.is_shutting_down()

    def get_flows_count(self) -> int:
        return self._primary.get_flows_count()

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        self._serving_started.set()
        super().serve_forever(poll_interval=poll_interval)


class SwarmBoardHTTPServer(ThreadingHTTPServer):
    """Multi-threaded loopback/Tailscale HTTP server with SSE hub and idle auto-exit watchdog."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        registry: Any,
        *,
        ui_dir: Path | None = None,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        sse_poll_interval_s: float = DEFAULT_SSE_POLL_S,
        sse_heartbeat_interval_s: float = DEFAULT_SSE_HEARTBEAT_S,
        allowed_hosts: Iterable[str] | None = None,
    ) -> None:
        self.registry = registry
        self.ui_dir = Path(ui_dir) if ui_dir is not None else DEFAULT_UI_DIR
        self.idle_seconds = max(0.0, float(idle_seconds))
        self.sse_heartbeat_interval_s = max(0.05, float(sse_heartbeat_interval_s))
        self.started_monotonic = time.monotonic()
        self._activity_lock = threading.Lock()
        self._last_activity = self.started_monotonic
        self._shutdown_event = threading.Event()
        self._watchdog_thread: threading.Thread | None = None
        self._companion: _LoopbackCompanionServer | None = None
        self._companion_thread: threading.Thread | None = None

        bind_ip = str(server_address[0]).strip() or BIND_HOST
        hosts: set[str] = {bind_ip.lower()}
        if allowed_hosts is not None:
            for item in allowed_hosts:
                cleaned = str(item).strip().lower().rstrip(".")
                if cleaned:
                    hosts.add(cleaned)
        elif is_tailscale_ipv4(bind_ip):
            for name in detect_tailscale_hostnames():
                cleaned = str(name).strip().lower().rstrip(".")
                if cleaned:
                    hosts.add(cleaned)
            cfg_hosts = load_user_config().get("allowed_hosts")
            if isinstance(cfg_hosts, (list, tuple, set)):
                for item in cfg_hosts:
                    if isinstance(item, str) and item.strip():
                        hosts.add(item.strip().lower().rstrip("."))
        self.allowed_hosts: frozenset[str] = frozenset(hosts)

        self.sse_hub = SSEHub(
            registry,
            poll_interval_s=sse_poll_interval_s,
            on_empty_callback=self.touch_activity,
        )
        super().__init__(server_address, SwarmBoardRequestHandler)
        self.bind_host = str(self.server_address[0])
        self.bound_port = int(self.server_address[1])

        if self.idle_seconds > 0:
            self._watchdog_thread = threading.Thread(
                target=self._idle_watchdog_loop,
                name="swarm-board-idle-watchdog",
                daemon=True,
            )
            self._watchdog_thread.start()

    def attach_loopback_companion(self, companion: _LoopbackCompanionServer) -> None:
        """Attach a 127.0.0.1 companion listener to share serve/shutdown lifecycle."""
        self._companion = companion

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        if self._companion is not None:
            comp = self._companion
            thread = threading.Thread(
                target=comp.serve_forever,
                kwargs={"poll_interval": poll_interval},
                name="swarm-board-loopback-companion",
                daemon=True,
            )
            self._companion_thread = thread
            thread.start()
            comp._serving_started.wait(timeout=1.0)
        try:
            super().serve_forever(poll_interval=poll_interval)
        finally:
            if self._companion is not None:
                if self._companion._serving_started.is_set():
                    self._companion.shutdown()
                if (
                    self._companion_thread is not None
                    and self._companion_thread.is_alive()
                    and self._companion_thread is not threading.current_thread()
                ):
                    self._companion_thread.join(timeout=2.0)

    def touch_activity(self) -> None:
        """Record activity timestamp for idle auto-exit tracking."""
        with self._activity_lock:
            self._last_activity = time.monotonic()

    def is_shutting_down(self) -> bool:
        """Return True once server shutdown has begun."""
        return self._shutdown_event.is_set()

    def get_flows_count(self) -> int:
        """Return current known flow count without stalling /health on a cold adapter scan."""
        reg = self.registry
        if hasattr(reg, "_has_snapshot"):
            if not getattr(reg, "_has_snapshot", False):
                return 0
            lock = getattr(reg, "_lock", None)
            if lock is not None:
                with lock:
                    return len(getattr(reg, "_cached_flows", []) or ())
            return len(getattr(reg, "_cached_flows", []) or ())
        try:
            return len(reg.list_flows(force=False))
        except Exception:  # noqa: BLE001
            return 0

    def request_shutdown(self) -> None:
        """Trigger graceful server shutdown safely from another thread."""
        if self._shutdown_event.is_set():
            return
        self._shutdown_event.set()
        self.sse_hub.close()
        self.shutdown()

    def _idle_watchdog_loop(self) -> None:
        interval = max(0.05, min(5.0, self.idle_seconds / 4.0))
        while not self._shutdown_event.wait(interval):
            if self.sse_hub.client_count() > 0:
                continue
            with self._activity_lock:
                elapsed = time.monotonic() - self._last_activity
            if elapsed >= self.idle_seconds:
                self._shutdown_event.set()
                self.sse_hub.close()
                threading.Thread(
                    target=self.shutdown,
                    name="swarm-board-idle-shutdown",
                    daemon=True,
                ).start()
                return

    def server_close(self) -> None:
        self._shutdown_event.set()
        self.sse_hub.close()
        if (
            self._watchdog_thread is not None
            and self._watchdog_thread.is_alive()
            and self._watchdog_thread is not threading.current_thread()
        ):
            self._watchdog_thread.join(timeout=1.0)
        if self._companion is not None:
            try:
                self._companion.server_close()
            except OSError:
                pass
        super().server_close()


def _bind_server_socket(
    port: int | None,
    *,
    host: str | None = None,
    registry: Any,
    ui_dir: Path | None = None,
    idle_seconds: float = DEFAULT_IDLE_SECONDS,
    sse_poll_interval_s: float = DEFAULT_SSE_POLL_S,
    sse_heartbeat_interval_s: float = DEFAULT_SSE_HEARTBEAT_S,
    allowed_hosts: Iterable[str] | None = None,
) -> SwarmBoardHTTPServer:
    """Create and bind SwarmBoardHTTPServer strictly on loopback or Tailscale+loopback."""
    bind_host = resolve_bind_host(host, use_env_and_config=False)
    if port is not None:
        candidate_ports = [int(port)]
    else:
        env_port = os.environ.get("SWARM_BOARD_PORT", "").strip()
        if env_port:
            candidate_ports = [int(env_port)]
        else:
            candidate_ports = list(range(DEFAULT_PORT, PORT_SCAN_END + 1))

    last_err: OSError | None = None
    for candidate in candidate_ports:
        if candidate == 0 and bind_host != BIND_HOST:
            for attempt in range(10):
                primary = SwarmBoardHTTPServer(
                    (bind_host, 0),
                    registry,
                    ui_dir=ui_dir,
                    idle_seconds=idle_seconds,
                    sse_poll_interval_s=sse_poll_interval_s,
                    sse_heartbeat_interval_s=sse_heartbeat_interval_s,
                    allowed_hosts=allowed_hosts,
                )
                try:
                    companion = _LoopbackCompanionServer((BIND_HOST, primary.bound_port), primary)
                except OSError as exc:
                    last_err = exc
                    primary.server_close()
                    if exc.errno == errno.EADDRINUSE and attempt < 9:
                        continue
                    raise
                primary.attach_loopback_companion(companion)
                return primary
            continue

        primary: SwarmBoardHTTPServer | None = None
        try:
            primary = SwarmBoardHTTPServer(
                (bind_host, candidate),
                registry,
                ui_dir=ui_dir,
                idle_seconds=idle_seconds,
                sse_poll_interval_s=sse_poll_interval_s,
                sse_heartbeat_interval_s=sse_heartbeat_interval_s,
                allowed_hosts=allowed_hosts,
            )
            if bind_host != BIND_HOST:
                companion = _LoopbackCompanionServer((BIND_HOST, primary.bound_port), primary)
                primary.attach_loopback_companion(companion)
            return primary
        except OSError as exc:
            if primary is not None:
                primary.server_close()
            last_err = exc
            if len(candidate_ports) == 1 or exc.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
    raise RuntimeError(
        f"No available port in {candidate_ports[0]}..{candidate_ports[-1]} on {bind_host}: {last_err}"
    )


def create_server(
    registry: Any | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    ui_dir: Path | None = None,
    idle_seconds: float = DEFAULT_IDLE_SECONDS,
    sse_poll_interval_s: float = DEFAULT_SSE_POLL_S,
    sse_heartbeat_interval_s: float = DEFAULT_SSE_HEARTBEAT_S,
    allowed_hosts: Iterable[str] | None = None,
) -> SwarmBoardHTTPServer:
    """Build a ready-to-serve SwarmBoardHTTPServer instance bound to loopback or Tailscale."""
    reg = registry if registry is not None else BoardRegistry()
    bind_host = resolve_bind_host(host, use_env_and_config=False)
    return _bind_server_socket(
        port,
        host=bind_host,
        registry=reg,
        ui_dir=ui_dir,
        idle_seconds=idle_seconds,
        sse_poll_interval_s=sse_poll_interval_s,
        sse_heartbeat_interval_s=sse_heartbeat_interval_s,
        allowed_hosts=allowed_hosts,
    )


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _remove_server_json_if_owner(state_file: Path, pid: int) -> None:
    data = _read_json_file(state_file)
    if data is not None and data.get("pid") == pid:
        try:
            state_file.unlink()
        except OSError:
            pass


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _probe_health(
    port: int,
    *,
    host: str = BIND_HOST,
    timeout: float = _HEALTH_PROBE_TIMEOUT_S,
) -> dict[str, Any] | None:
    """Probe http://<host>:<port>/health without honoring external proxy env vars."""
    if port <= 0 or port > 65535:
        return None
    target_host = (host or BIND_HOST).strip() or BIND_HOST
    conn: http.client.HTTPConnection | None = None
    try:
        conn = http.client.HTTPConnection(target_host, int(port), timeout=timeout)
        conn.request("GET", "/health", headers={"Host": f"{target_host}:{int(port)}"})
        resp = conn.getresponse()
        raw = resp.read(16_384)
        if resp.status != 200:
            return None
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict) and data.get("ok") is True and data.get("port") and data.get("pid"):
            return data
        return None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass


# Persistent user unit (~/.config/systemd/user/swarm-board.service) that runs the server outside any
# agent's systemd scope. Starting a unit that is already running does nothing, so launches cannot
# duplicate. The unit takes its host, idle time, and state directory from config.json and the
# defaults, so only launches without per-call overrides use it.
SYSTEMD_UNIT = "swarm-board.service"
_SYSTEMCTL_TIMEOUT_S = 8.0
# The hook holds ensure.lock while it starts the unit, so it queues the start job and waits only briefly.
_HOOK_SYSTEMCTL_TIMEOUT_S = 2.0
# Any of these set in the caller's environment (even empty) keeps the direct child-process path,
# because the unit cannot see them. The server treats an empty SWARM_BOARD_CONFIG as "no config".
_UNIT_ENV_OVERRIDES = (
    "SWARM_BOARD_HOST",
    "SWARM_BOARD_PORT",
    "SWARM_BOARD_IDLE_SECONDS",
    "SWARM_BOARD_STATE_DIR",
    "SWARM_BOARD_CONFIG",
    "XDG_CACHE_HOME",
)


def _use_systemd_unit(
    *,
    host: str | None,
    port: int | None,
    idle_seconds: float | None,
    state_dir: Path | None,
    ui_dir: Path | None,
) -> bool:
    """True for default launches on Linux with systemctl; overrides keep the direct spawn path."""
    if not sys.platform.startswith("linux") or shutil.which("systemctl") is None:
        return False
    if any(v is not None for v in (host, port, idle_seconds, state_dir, ui_dir)):
        return False
    return not any(key in os.environ for key in _UNIT_ENV_OVERRIDES)


def _start_systemd_unit(*, block: bool = True, timeout_s: float = _SYSTEMCTL_TIMEOUT_S) -> None:
    """Start the persistent server unit. block=False queues the start job and returns at once.

    Raises RuntimeError if systemctl refuses (e.g. the unit is not installed).
    """
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        raise RuntimeError("systemctl not found")
    cmd = [systemctl, "--user", "start"]
    if not block:
        cmd.append("--no-block")
    cmd.append(SYSTEMD_UNIT)
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()
        raise RuntimeError(
            f"systemctl --user start {SYSTEMD_UNIT} failed: {detail}. "
            "Install the unit first: see INSTALL.md, 'Linux: run the board as its own systemd user service'."
        )


def _spawn_detached_server(
    state_dir: Path,
    *,
    host: str = BIND_HOST,
    port: int | None,
    idle_seconds: float,
    explicit_state_dir: Path | None,
    ui_dir: Path | None,
) -> subprocess.Popen[bytes]:
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "server.log"
    cmd = [sys.executable, "-m", "swarm_board.server", "--host", host]
    if port is not None:
        cmd.extend(["--port", str(port)])
    cmd.extend(["--idle-seconds", str(idle_seconds)])
    if explicit_state_dir is not None:
        cmd.extend(["--state-dir", str(explicit_state_dir)])
    if ui_dir is not None:
        cmd.extend(["--ui-dir", str(ui_dir)])

    env = os.environ.copy()
    env["SWARM_BOARD_STATE_DIR"] = str(state_dir)
    repo_str = str(REPO_ROOT)
    existing_pp = env.get("PYTHONPATH", "")
    if repo_str not in existing_pp.split(os.pathsep):
        env["PYTHONPATH"] = f"{repo_str}{os.pathsep}{existing_pp}" if existing_pp else repo_str

    with open(log_path, "ab") as log_fh:
        return subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,
            close_fds=True,
        )


def _state_entry_host(data: dict[str, Any]) -> str:
    raw = data.get("host")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return BIND_HOST


def _try_reuse_existing(
    state_file: Path,
    *,
    requested_host: str = BIND_HOST,
    requested_port: int | None,
    timeout: float,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Check server.json (and explicit port if > 0) for a live healthy server on requested_host."""
    data = _read_json_file(state_file)
    if data is not None:
        entry_port = data.get("port")
        entry_host = _state_entry_host(data)
        if (
            isinstance(entry_port, int)
            and entry_host == requested_host
            and (requested_port is None or requested_port <= 0 or entry_port == requested_port)
        ):
            health = _probe_health(entry_port, host=entry_host, timeout=timeout)
            if health is not None:
                port = int(health["port"])
                resp_host = str(health.get("host") or entry_host)
                return {
                    "ok": True,
                    "host": resp_host,
                    "port": port,
                    "url": f"http://{resp_host}:{port}",
                    "local_url": f"http://{BIND_HOST}:{port}",
                    "pid": int(health["pid"]),
                    "started": False,
                    "open_in_preview": resolve_open_in_preview(resp_host, config=config),
                }

    if requested_port is not None and requested_port > 0:
        health = _probe_health(requested_port, host=requested_host, timeout=timeout)
        if health is not None:
            port = int(health["port"])
            resp_host = str(health.get("host") or requested_host)
            return {
                "ok": True,
                "host": resp_host,
                "port": port,
                "url": f"http://{resp_host}:{port}",
                "local_url": f"http://{BIND_HOST}:{port}",
                "pid": int(health["pid"]),
                "started": False,
                "open_in_preview": resolve_open_in_preview(resp_host, config=config),
            }
    return None


def _stop_mismatched_host_if_verified(state_file: Path, target_host: str, wait_s: float) -> None:
    """Stop an existing verified Swarm Board server if it is bound to a different host."""
    data = _read_json_file(state_file)
    if data is None:
        return
    entry_port = data.get("port")
    entry_pid = data.get("pid")
    entry_host = _state_entry_host(data)
    if (
        not isinstance(entry_port, int)
        or not isinstance(entry_pid, int)
        or entry_host == target_host
    ):
        return

    health = _probe_health(entry_port, host=entry_host, timeout=_HOOK_PROBE_TIMEOUT_S)
    if health is None or int(health.get("pid", -1)) != entry_pid:
        return

    try:
        os.kill(entry_pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return

    deadline = time.monotonic() + max(0.0, float(wait_s))
    while _is_pid_alive(entry_pid) and time.monotonic() < deadline:
        time.sleep(0.05)

    if not _is_pid_alive(entry_pid):
        _remove_server_json_if_owner(state_file, entry_pid)


def _active_starting_pid(starting_file: Path, max_age_s: float = _ENSURE_TIMEOUT_S) -> int | None:
    info = _read_json_file(starting_file)
    if info is None:
        return None
    pid = info.get("pid")
    ts = info.get("started_at")
    if not isinstance(pid, int) or not isinstance(ts, (int, float)):
        return None
    if (time.time() - float(ts)) > max_age_s:
        return None
    if not _is_pid_alive(pid):
        return None
    return pid


def ensure_server(
    *,
    state_dir: Path | None = None,
    host: str | None = None,
    port: int | None = None,
    idle_seconds: float | None = None,
    ui_dir: Path | None = None,
    timeout_s: float = _ENSURE_TIMEOUT_S,
) -> dict[str, Any]:
    """Ensure a single Swarm Board server instance is healthy, spawning detached if needed."""
    cfg = load_user_config()
    target_host = resolve_bind_host(host, use_env_and_config=True, config=cfg)
    resolved_idle = resolve_idle_seconds(idle_seconds, use_env_and_config=True, config=cfg)

    resolved_state = get_state_dir(state_dir)
    resolved_state.mkdir(parents=True, exist_ok=True)
    lock_path = resolved_state / "ensure.lock"
    state_file = resolved_state / "server.json"
    starting_file = resolved_state / "starting.json"

    with open(lock_path, "a+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            reused = _try_reuse_existing(
                state_file,
                requested_host=target_host,
                requested_port=port,
                timeout=_HEALTH_PROBE_TIMEOUT_S,
                config=cfg,
            )
            if reused is not None:
                return reused

            _stop_mismatched_host_if_verified(state_file, target_host, wait_s=2.0)

            proc: subprocess.Popen[bytes] | None = None
            watch_pid: int | None = None
            if _use_systemd_unit(
                host=host, port=port, idle_seconds=idle_seconds, state_dir=state_dir, ui_dir=ui_dir
            ):
                # Starting a running unit is a no-op, so this cannot launch a second server.
                # server.json is left alone: a failed probe must not erase the record of a live server.
                _start_systemd_unit()
            else:
                watch_pid = _active_starting_pid(starting_file, max_age_s=timeout_s)
                if watch_pid is None:
                    # No unlink of server.json here. A child that loses the state lock exits 1 and must
                    # leave the live owner's record intact; a new owner replaces the record after it binds.
                    proc = _spawn_detached_server(
                        resolved_state,
                        host=target_host,
                        port=port,
                        idle_seconds=resolved_idle,
                        explicit_state_dir=state_dir,
                        ui_dir=ui_dir,
                    )
                    watch_pid = proc.pid
                    _atomic_write_json(
                        starting_file,
                        {"pid": proc.pid, "started_at": time.time()},
                    )

            deadline = time.monotonic() + max(0.5, float(timeout_s))
            while time.monotonic() < deadline:
                if proc is not None and proc.poll() is not None:
                    # Another concurrent instance may have won the port race just before exit.
                    race_reused = _try_reuse_existing(
                        state_file,
                        requested_host=target_host,
                        requested_port=port,
                        timeout=_HEALTH_PROBE_TIMEOUT_S,
                        config=cfg,
                    )
                    if race_reused is not None:
                        return race_reused
                    raise RuntimeError(
                        f"Swarm Board server exited prematurely with code {proc.returncode}"
                    )

                data = _read_json_file(state_file)
                if data is not None and isinstance(data.get("port"), int):
                    candidate_port = int(data["port"])
                    data_host = _state_entry_host(data)
                    if data_host == target_host and (port is None or port <= 0 or candidate_port == port):
                        health = _probe_health(
                            candidate_port,
                            host=target_host,
                            timeout=_HEALTH_PROBE_TIMEOUT_S,
                        )
                        if health is not None:
                            try:
                                starting_file.unlink()
                            except OSError:
                                pass
                            out_port = int(health["port"])
                            resp_host = str(health.get("host") or target_host)
                            return {
                                "ok": True,
                                "host": resp_host,
                                "port": out_port,
                                "url": f"http://{resp_host}:{out_port}",
                                "local_url": f"http://{BIND_HOST}:{out_port}",
                                "pid": int(health["pid"]),
                                "started": True,
                                "open_in_preview": resolve_open_in_preview(resp_host, config=cfg),
                            }

                if proc is None and watch_pid is not None and not _is_pid_alive(watch_pid):
                    # Previously starting process died before writing server.json; spawn fresh.
                    proc = _spawn_detached_server(
                        resolved_state,
                        host=target_host,
                        port=port,
                        idle_seconds=resolved_idle,
                        explicit_state_dir=state_dir,
                        ui_dir=ui_dir,
                    )
                    watch_pid = proc.pid
                    _atomic_write_json(
                        starting_file,
                        {"pid": proc.pid, "started_at": time.time()},
                    )

                time.sleep(0.05)

            raise TimeoutError(
                f"Timed out after {timeout_s:.1f}s waiting for Swarm Board server health check"
            )
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def hook_ensure_server(
    *,
    state_dir: Path | None = None,
    host: str | None = None,
    port: int | None = None,
    idle_seconds: float | None = None,
    ui_dir: Path | None = None,
) -> dict[str, Any]:
    """Fast non-blocking hook entrypoint: spawns server if needed and returns {} immediately."""
    cfg = load_user_config()
    try:
        target_host = resolve_bind_host(host, use_env_and_config=True, config=cfg)
        resolved_idle = resolve_idle_seconds(idle_seconds, use_env_and_config=True, config=cfg)
    except (ValueError, RuntimeError) as exc:
        _LOGGER.debug("Hook host/idle resolution failed: %s", exc)
        return {}

    resolved_state = get_state_dir(state_dir)
    resolved_state.mkdir(parents=True, exist_ok=True)
    lock_path = resolved_state / "ensure.lock"
    state_file = resolved_state / "server.json"
    starting_file = resolved_state / "starting.json"

    with open(lock_path, "a+", encoding="utf-8") as lock_fh:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {}
        try:
            if _try_reuse_existing(
                state_file,
                requested_host=target_host,
                requested_port=port,
                timeout=_HOOK_PROBE_TIMEOUT_S,
                config=cfg,
            ) is not None:
                return {}
            _stop_mismatched_host_if_verified(state_file, target_host, wait_s=0.2)
            if _use_systemd_unit(
                host=host, port=port, idle_seconds=idle_seconds, state_dir=state_dir, ui_dir=ui_dir
            ):
                # Queued with --no-block (this hook holds ensure.lock). server.json is left alone: a failed
                # probe must not erase the record of a live server.
                try:
                    _start_systemd_unit(block=False, timeout_s=_HOOK_SYSTEMCTL_TIMEOUT_S)
                except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                    _LOGGER.warning("hook could not start %s: %s", SYSTEMD_UNIT, exc)
                return {}
            if _active_starting_pid(starting_file) is not None:
                return {}
            # No unlink of server.json here, for the same reason as in ensure_server.
            proc = _spawn_detached_server(
                resolved_state,
                host=target_host,
                port=port,
                idle_seconds=resolved_idle,
                explicit_state_dir=state_dir,
                ui_dir=ui_dir,
            )
            _atomic_write_json(
                starting_file,
                {"pid": proc.pid, "started_at": time.time()},
            )
            return {}
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def server_status(
    *,
    state_dir: Path | None = None,
    host: str | None = None,
    port: int | None = None,
    timeout: float = 1.0,
) -> dict[str, Any]:
    """Check whether a Swarm Board server is alive for state_dir/host/port."""
    cfg = load_user_config()
    resolved_state = get_state_dir(state_dir)
    state_file = resolved_state / "server.json"
    data = _read_json_file(state_file)

    explicit_host: str | None = None
    if host is not None:
        try:
            explicit_host = resolve_bind_host(host, use_env_and_config=False)
        except (ValueError, RuntimeError):
            explicit_host = None

    candidates: list[tuple[str, int]] = []
    if data is not None and isinstance(data.get("port"), int):
        entry_port = int(data["port"])
        entry_host = _state_entry_host(data)
        if (
            (explicit_host is None or entry_host == explicit_host)
            and (port is None or port <= 0 or entry_port == port)
        ):
            candidates.append((entry_host, entry_port))

    if port is not None and port > 0:
        try:
            probe_host = resolve_bind_host(host, use_env_and_config=True, config=cfg)
        except (ValueError, RuntimeError):
            probe_host = BIND_HOST
        cand = (probe_host, int(port))
        if cand not in candidates:
            candidates.append(cand)

    for cand_host, cand_port in candidates:
        health = _probe_health(cand_port, host=cand_host, timeout=timeout)
        if health is not None:
            bound_port = int(health["port"])
            bound_host = str(health.get("host") or cand_host)
            return {
                "ok": True,
                "running": True,
                "pid": int(health["pid"]),
                "host": bound_host,
                "port": bound_port,
                "url": f"http://{bound_host}:{bound_port}",
                "local_url": f"http://{BIND_HOST}:{bound_port}",
                "open_in_preview": resolve_open_in_preview(bound_host, config=cfg),
                "health": health,
            }

    return {
        "ok": True,
        "running": False,
        "state_dir": str(resolved_state),
    }


def stop_server(
    *,
    state_dir: Path | None = None,
    host: str | None = None,
    port: int | None = None,
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    """Stop a verified Swarm Board server process cleanly."""
    resolved_state = get_state_dir(state_dir)
    state_file = resolved_state / "server.json"
    status = server_status(state_dir=resolved_state, host=host, port=port, timeout=1.0)
    if not status.get("running"):
        return {
            "ok": True,
            "stopped": False,
            "running": False,
        }

    pid = int(status["pid"])
    bound_port = int(status["port"])
    bound_host = str(status.get("host") or BIND_HOST)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

    deadline = time.monotonic() + max(0.2, float(timeout_s))
    while _is_pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)

    stopped = not _is_pid_alive(pid)
    if stopped:
        _remove_server_json_if_owner(state_file, pid)

    return {
        "ok": True,
        "stopped": stopped,
        "pid": pid,
        "host": bound_host,
        "port": bound_port,
    }


def _acquire_state_owner_lock(state_dir: Path) -> Any | None:
    """Hold an exclusive lock on state_dir/server.lock for the server's whole lifetime.

    Only one server may own a state directory. Every launch path (the unit, a child process,
    a manual run) goes through this, so a second server exits at once instead of overwriting
    server.json. Returns the open file (keep it alive), or None when another server holds the lock.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    fh = open(state_dir / "server.lock", "a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def run_server(
    *,
    host: str | None = None,
    port: int | None = None,
    idle_seconds: float | None = None,
    state_dir: Path | None = None,
    ui_dir: Path | None = None,
    registry: Any | None = None,
    force_refresh: bool = False,
    allowed_hosts: Iterable[str] | None = None,
) -> int:
    """Run the Swarm Board HTTP/SSE server until signal or idle auto-exit."""
    cfg = load_user_config()
    bind_host = resolve_bind_host(host, use_env_and_config=True, config=cfg)
    resolved_idle = resolve_idle_seconds(idle_seconds, use_env_and_config=True, config=cfg)

    resolved_state = get_state_dir(state_dir)
    state_file = resolved_state / "server.json"
    starting_file = resolved_state / "starting.json"

    owner_lock = _acquire_state_owner_lock(resolved_state)
    if owner_lock is None:
        _LOGGER.error(
            "Another Swarm Board server already owns %s; not starting a second one.", resolved_state
        )
        return 1

    server = create_server(
        registry=registry,
        host=bind_host,
        port=port,
        ui_dir=ui_dir,
        idle_seconds=resolved_idle,
        allowed_hosts=allowed_hosts,
    )
    if force_refresh:
        try:
            server.registry.list_flows(force=True)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Initial force refresh failed: %s", exc)

    my_pid = os.getpid()
    _atomic_write_json(
        state_file,
        {
            "pid": my_pid,
            "host": server.bind_host,
            "port": server.bound_port,
            "url": f"http://{server.bind_host}:{server.bound_port}",
            "local_url": f"http://{BIND_HOST}:{server.bound_port}",
            "started_at": datetime.now(tz=timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "repo_root": str(REPO_ROOT),
        },
    )
    try:
        starting_info = _read_json_file(starting_file)
        if starting_info is not None and starting_info.get("pid") == my_pid:
            starting_file.unlink()
    except OSError:
        pass

    prev_handlers: dict[int, Any] = {}
    if threading.current_thread() is threading.main_thread():
        def _handle_signal(_signum: int, _frame: Any) -> None:
            threading.Thread(
                target=server.request_shutdown,
                name="swarm-board-signal-shutdown",
                daemon=True,
            ).start()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                prev_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, _handle_signal)
            except (ValueError, OSError):
                pass

    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        try:
            server.server_close()
        finally:
            _remove_server_json_if_owner(state_file, my_pid)
            for sig, prev in prev_handlers.items():
                try:
                    signal.signal(sig, prev)
                except (ValueError, OSError):
                    pass
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m swarm_board.server",
        description="Read-only local HTTP + SSE server for Swarm Board.",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Bind host ('127.0.0.1', 'tailscale', or a loopback/100.64.0.0/10 Tailscale IPv4).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="TCP port (0 for ephemeral, default scans 18420..18449).",
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=None,
        help="Auto-exit after N idle seconds with 0 SSE clients (0 disables, default 7200 / 2h).",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Override runtime state directory (defaults to SWARM_BOARD_STATE_DIR or OS cache).",
    )
    parser.add_argument(
        "--ui-dir",
        default=None,
        help="Override UI directory containing index.html.",
    )
    parser.add_argument(
        "--ensure",
        action="store_true",
        help="Ensure server is running (reuse or spawn detached) and print JSON summary.",
    )
    parser.add_argument(
        "--hook",
        action="store_true",
        help="Fast non-blocking hook mode: spawn server if stopped and print {} immediately.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Check whether the detached Swarm Board server is running and print JSON summary.",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop a running Swarm Board server cleanly and print JSON summary.",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Warm initial BoardRegistry flow snapshot on startup.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for `python3 -m swarm_board.server`."""
    args = _build_arg_parser().parse_args(argv)
    if sum(bool(x) for x in (args.ensure, args.hook, args.status, args.stop)) > 1:
        sys.stderr.write("--ensure, --hook, --status, and --stop are mutually exclusive\n")
        return 2

    state_dir = Path(args.state_dir).expanduser() if args.state_dir else None
    ui_dir = Path(args.ui_dir).expanduser() if args.ui_dir else None

    if args.status:
        res = server_status(state_dir=state_dir, host=args.host, port=args.port)
        sys.stdout.write(json.dumps(res) + "\n")
        sys.stdout.flush()
        return 0

    if args.stop:
        res = stop_server(state_dir=state_dir, host=args.host, port=args.port)
        sys.stdout.write(json.dumps(res) + "\n")
        sys.stdout.flush()
        return 0 if (res.get("stopped") or not res.get("pid")) else 1

    if args.hook:
        res = hook_ensure_server(
            state_dir=state_dir,
            host=args.host,
            port=args.port,
            idle_seconds=args.idle_seconds,
            ui_dir=ui_dir,
        )
        sys.stdout.write(json.dumps(res) + "\n")
        sys.stdout.flush()
        return 0

    if args.ensure:
        try:
            res = ensure_server(
                state_dir=state_dir,
                host=args.host,
                port=args.port,
                idle_seconds=args.idle_seconds,
                ui_dir=ui_dir,
            )
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()
            return 0
        except Exception as exc:  # noqa: BLE001
            sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
            sys.stdout.flush()
            return 1

    try:
        return run_server(
            host=args.host,
            port=args.port,
            idle_seconds=args.idle_seconds,
            state_dir=state_dir,
            ui_dir=ui_dir,
            force_refresh=bool(args.force_refresh),
        )
    except (ValueError, RuntimeError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
