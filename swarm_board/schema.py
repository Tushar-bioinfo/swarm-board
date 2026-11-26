"""Normalized read-only schema, telemetry usage indexing, and cost estimation."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

VALID_STATES = frozenset(
    {"running", "done", "killed", "error", "interrupted", "stale", "waiting", "queued"}
)

VALID_LINK_KINDS = frozenset({"thread", "resume", "session", "transcript"})

_UUID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)

_EFFORT_SUFFIX_RE = re.compile(
    r"-(?:low|medium|high|xhigh|max|ultra|ultrathink|fast|build)$",
    re.IGNORECASE,
)

_MODEL_COST_ALIASES: dict[str, str] = {
    "gemini-pro-orchestrator-neon": "gemini-4-neon",
}

_RATES_FILE_CACHE: dict[
    str, tuple[tuple[int, int], dict[str, tuple[float, float, float | None, float | None]]]
] = {}
_USAGE_FILE_CACHE: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}

REPO_RATES_OVERRIDE_PATH = Path(__file__).resolve().parent / "rates_override.json"

_DOC_OPEN_RE = re.compile(r'"document"\s*:\s*\{')
_JSON_SCAN_RE = re.compile(r'"(?:\\.|[^"\\])*"|\{|\}')
_NUM_AFTER_COLON_RE = re.compile(r"\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)")
_TARGET_RATE_TOKENS = frozenset(
    {
        '"input_cost_per_token"',
        '"output_cost_per_token"',
        '"cache_read_input_token_cost"',
        '"cache_creation_input_token_cost"',
    }
)
_STREAM_RATES_MIN_BYTES = 262_144


def default_t3_userdata_dir() -> Path:
    """Resolve T3 userdata directory across env override, Linux, and macOS paths."""
    env_dir = os.environ.get("T3_USERDATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    home = Path.home()
    linux_path = home / ".t3" / "userdata"
    if linux_path.is_dir():
        return linux_path
    macos_path = home / "Library" / "Application Support" / "T3 Code" / "userdata"
    if macos_path.is_dir():
        return macos_path
    if sys.platform == "darwin" and macos_path.parent.is_dir():
        return macos_path
    return linux_path


def _file_sig(path: Path) -> tuple[str, int | None, int | None]:
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), None, None)


def _extract_rate_tuple(val: Any) -> tuple[float, float, float | None, float | None] | None:
    if not isinstance(val, dict):
        return None
    try:
        in_rate = float(val.get("input_cost_per_token") or 0.0)
        out_rate = float(val.get("output_cost_per_token") or 0.0)
    except (TypeError, ValueError):
        return None
    if in_rate == 0.0 and out_rate == 0.0:
        return None
    cr_raw = val.get("cache_read_input_token_cost")
    cw_raw = val.get("cache_creation_input_token_cost")
    cr_rate = float(cr_raw) if isinstance(cr_raw, (int, float)) else None
    cw_rate = float(cw_raw) if isinstance(cw_raw, (int, float)) else None
    return (in_rate, out_rate, cr_rate, cw_rate)


def _parse_override_file(
    path: Path,
) -> dict[str, tuple[float, float, float | None, float | None]]:
    """Parse a rates_override.json file into compact lowercase rate tuples."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    mapping = raw.get("models")
    if not isinstance(mapping, dict):
        mapping = raw.get("document")
    if not isinstance(mapping, dict):
        mapping = raw
    out: dict[str, tuple[float, float, float | None, float | None]] = {}
    for key, val in mapping.items():
        k_str = str(key).strip().lower()
        if not k_str or k_str.startswith("_"):
            continue
        tup = _extract_rate_tuple(val)
        if tup is not None:
            out[k_str] = tup
    return out


def _parse_compact_rates_streaming(
    text: str,
) -> dict[str, tuple[float, float, float | None, float | None]]:
    """Parse large LiteLLM rates JSON with low peak memory (no 7k temporary dict allocation)."""
    m_doc = _DOC_OPEN_RE.search(text)
    if m_doc:
        scan_start = m_doc.end()
    else:
        brace_idx = text.find("{")
        if brace_idx == -1:
            return {}
        scan_start = brace_idx + 1
    compact: dict[str, tuple[float, float, float | None, float | None]] = {}
    depth = 1
    current_key: str | None = None
    in_rate = 0.0
    out_rate = 0.0
    cr_rate: float | None = None
    cw_rate: float | None = None
    text_len = len(text)

    for m in _JSON_SCAN_RE.finditer(text, scan_start):
        tok = m.group(0)
        if tok == "{":
            if depth == 1:
                in_rate = 0.0
                out_rate = 0.0
                cr_rate = None
                cw_rate = None
            depth += 1
        elif tok == "}":
            depth -= 1
            if depth == 1 and current_key is not None:
                if in_rate != 0.0 or out_rate != 0.0:
                    compact[current_key.lower()] = (in_rate, out_rate, cr_rate, cw_rate)
                current_key = None
            elif depth <= 0:
                break
        elif depth == 1:
            j = m.end()
            while j < text_len and text[j] in " \t\r\n":
                j += 1
            if j < text_len and text[j] == ":":
                current_key = json.loads(tok) if "\\" in tok else tok[1:-1]
        elif depth == 2 and tok in _TARGET_RATE_TOKENS:
            j = m.end()
            while j < text_len and text[j] in " \t\r\n":
                j += 1
            if j < text_len and text[j] == ":":
                m_num = _NUM_AFTER_COLON_RE.match(text, j + 1)
                if m_num:
                    val = float(m_num.group(1))
                    if tok == '"input_cost_per_token"':
                        in_rate = val
                    elif tok == '"output_cost_per_token"':
                        out_rate = val
                    elif tok == '"cache_read_input_token_cost"':
                        cr_rate = val
                    elif tok == '"cache_creation_input_token_cost"':
                        cw_rate = val

    return compact


def normalize_lab(model: str | None, provider: str | None = None) -> str:
    """Map raw model or provider strings into canonical board lab identifiers."""
    m = (model or "").strip().lower()
    p = (provider or "").strip().lower()
    combined = f"{m} {p}".strip()

    if any(k in combined for k in ("gemini", "agy", "antigravity", "google")):
        return "google"
    if any(k in combined for k in ("claude", "anthropic")):
        return "anthropic"
    if any(k in combined for k in ("gpt", "codex", "openai")) or re.search(
        r"\bo[134](?:-|$)", m
    ):
        return "openai"
    if any(k in combined for k in ("grok", "xai")):
        return "xai"
    if p in ("t3", "t3-code") or m in ("t3", "t3-root"):
        return "t3"
    return "other"


def normalize_state(raw: str | None) -> str:
    """Normalize harness-specific states into the canonical 8-state enum."""
    s = (raw or "").strip().lower()
    if s in VALID_STATES:
        return s
    mapping = {
        "live": "running",
        "working": "running",
        "active": "running",
        "starting": "running",
        "preparing": "running",
        "completed": "done",
        "complete": "done",
        "success": "done",
        "succeeded": "done",
        "settled": "done",
        "idle": "done",
        "failed": "error",
        "cancelled": "interrupted",
        "canceled": "interrupted",
        "aborted": "interrupted",
        "timed_out": "error",
        "timeout": "error",
        "pending": "queued",
        "waiting_for_children": "running",
        "waiting_for_user": "waiting",
        "approval_required": "waiting",
    }
    return mapping.get(s, "done")


def parse_iso_ts(value: str | int | float | None) -> float | None:
    """Parse ISO-8601 UTC strings, local wall-clock strings, or epoch numbers to epoch seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        # Heuristic: values > 1e11 are epoch milliseconds.
        return v / 1000.0 if v > 1e11 else v
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        # Assume local wall-clock when timezone offset is absent (e.g. board.jsonl).
        dt = dt.astimezone()
    return dt.timestamp()


def to_iso_utc(ts: float | None) -> str | None:
    """Format epoch seconds as ISO-8601 UTC string."""
    if ts is None:
        return None
    return (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def compute_dur_s(
    started_at: str | int | float | None,
    ended_at: str | int | float | None,
    is_running: bool = False,
    now_ts: float | None = None,
) -> float | None:
    """Compute elapsed seconds between started_at and ended_at (or now if running)."""
    start_ts = parse_iso_ts(started_at)
    if start_ts is None:
        return None
    end_ts = parse_iso_ts(ended_at)
    if end_ts is None:
        if not is_running:
            return None
        end_ts = now_ts if now_ts is not None else datetime.now(tz=timezone.utc).timestamp()
    return round(max(0.0, end_ts - start_ts), 2)


def make_session_link(cli: str | None, session_id: str | None) -> dict[str, Any] | None:
    """Create a verified CLI session link without inventing unverified resume commands."""
    sid = (session_id or "").strip()
    if not sid:
        return None
    kind_cli = (cli or "").strip().lower()
    if kind_cli in ("claude", "claudeagent"):
        return {
            "kind": "resume",
            "label": "Resume in Claude",
            "href": None,
            "cmd": f"claude --resume {sid}",
            "id": sid,
        }
    if kind_cli == "codex":
        return {
            "kind": "resume",
            "label": "Resume in Codex",
            "href": None,
            "cmd": f"codex resume {sid}",
            "id": sid,
        }
    # Per spec: never invent unverified resume commands for agy, grok, cursor, muse, etc.
    return {
        "kind": "session",
        "label": "Copy session ID",
        "href": None,
        "cmd": None,
        "id": sid,
    }


def make_link(
    kind: str,
    label: str,
    *,
    href: str | None = None,
    cmd: str | None = None,
    link_id: str | None = None,
) -> dict[str, Any]:
    """Construct a normalized Link object."""
    k = kind if kind in VALID_LINK_KINDS else "session"
    return {
        "kind": k,
        "label": label,
        "href": href,
        "cmd": cmd,
        "id": link_id,
    }


def make_output(
    out_id: str,
    kind: str,
    title: str,
    *,
    path: str | None = None,
    size: int | None = None,
    by: str = "root",
    at: str | None = None,
    body: str | None = None,
    src: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a normalized Output object."""
    return {
        "id": out_id,
        "kind": kind,
        "title": title,
        "path": path,
        "size": size,
        "by": by,
        "at": at,
        "body": body,
        "src": src,
        "meta": meta or {},
    }


def make_post(
    post_id: int | str,
    author: str,
    at: str | None,
    text: str,
    mentions: list[str] | None = None,
) -> dict[str, Any]:
    """Construct a normalized Post object."""
    return {
        "id": post_id,
        "author": author,
        "at": at,
        "mentions": mentions or [],
        "text": text,
    }


def make_agent(
    agent_id: str,
    name: str,
    *,
    parent: str | None = None,
    role: str = "worker",
    workstream: str | None = None,
    lab: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    state: str = "done",
    started_at: str | None = None,
    ended_at: str | None = None,
    dur_s: float | None = None,
    tokens: dict[str, int] | None = None,
    cost_usd: float | None = None,
    errors: list[dict[str, Any]] | None = None,
    trace: list[list[Any]] | None = None,
    last: str | None = None,
    result: str | None = None,
    weight: float | None = None,
    verdict: str | None = None,
    links: list[dict[str, Any]] | None = None,
    output_ids: list[str] | None = None,
    label: str | None = None,
    task: str | None = None,
) -> dict[str, Any]:
    """Construct a normalized Agent object."""
    norm_state = normalize_state(state)
    norm_lab = lab if lab in {"google", "anthropic", "openai", "xai", "t3", "other"} else normalize_lab(model, provider)
    computed_dur = (
        dur_s
        if dur_s is not None
        else compute_dur_s(started_at, ended_at, is_running=(norm_state == "running"))
    )
    tok = None
    if tokens is not None:
        tok = {
            "in": int(tokens.get("in", 0) or 0),
            "cache": int(tokens.get("cache", 0) or 0),
            "out": int(tokens.get("out", 0) or 0),
        }
    return {
        "id": agent_id,
        "parent": parent,
        "name": name,
        "label": label,
        "task": task,
        "role": role,
        "workstream": workstream,
        "lab": norm_lab,
        "model": model,
        "state": norm_state,
        "startedAt": started_at,
        "endedAt": ended_at,
        "durS": computed_dur,
        "tokens": tok,
        "costUsd": round(float(cost_usd), 4) if cost_usd is not None else None,
        "errors": errors or [],
        "trace": trace or [],
        "last": last,
        "result": result,
        "weight": weight,
        "verdict": verdict,
        "links": links or [],
        "outputIds": output_ids or [],
    }


def make_flow(
    flow_id: str,
    title: str,
    *,
    project: str = "",
    project_root: str | None = None,
    harness: str = "t3",
    kind: str = "flat",
    started_at: str | None = None,
    updated_at: str | None = None,
    goal: str | None = None,
    decision: dict[str, Any] | None = None,
    workstreams: list[dict[str, str]] | None = None,
    agents: list[dict[str, Any]] | None = None,
    posts: list[dict[str, Any]] | None = None,
    outputs: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Construct a normalized Flow object."""
    return {
        "id": flow_id,
        "title": title,
        "project": project or (Path(project_root).name if project_root else ""),
        "projectRoot": project_root,
        "harness": harness,
        "kind": kind,
        "startedAt": started_at,
        "updatedAt": updated_at or started_at,
        "goal": goal,
        "decision": decision,
        "workstreams": workstreams or [],
        "agents": agents or [],
        "posts": posts or [],
        "outputs": outputs or [],
        "links": links or [],
    }


class RateTable:
    """Mtime-cached read-only lookup for LiteLLM and layered override model pricing rates."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        override_paths: list[str | Path] | None = None,
        fallback_path: str | Path | None = None,
    ) -> None:
        self._explicit_path = path is not None
        self.path = Path(path) if path else default_t3_userdata_dir() / "usage-model-rates.json"
        if fallback_path is not None:
            self.fallback_path: Path | None = Path(fallback_path)
        elif self._explicit_path:
            self.fallback_path = None
        else:
            self.fallback_path = Path.home() / ".config" / "swarm-board" / "litellm-rates.json"
        self._explicit_override_paths = (
            [Path(p) for p in override_paths] if override_paths is not None else None
        )
        self._mtime: float = -1.0
        self._sig: tuple[Any, ...] | None = None
        self._rates: dict[str, tuple[float, float, float | None, float | None]] = {}
        self._override_rates: dict[str, tuple[float, float, float | None, float | None]] = {}
        self._lookup_cache: dict[str, dict[str, Any] | None] = {}

    def _resolve_override_paths(self) -> list[Path]:
        if self._explicit_override_paths is not None:
            return self._explicit_override_paths
        paths: list[Path] = []
        env_ov = os.environ.get("SWARM_BOARD_RATES_OVERRIDE", "").strip()
        if env_ov:
            paths.append(Path(env_ov).expanduser())
        paths.append(Path.home() / ".config" / "swarm-board" / "rates_override.json")
        paths.append(REPO_RATES_OVERRIDE_PATH)
        return paths

    def _active_base_path(self) -> Path:
        if self.path.is_file():
            return self.path
        if self.fallback_path is not None and self.fallback_path.is_file():
            return self.fallback_path
        return self.path

    def cache_signature(self) -> tuple[Any, ...]:
        """Return a stat signature tuple across active base catalog and override layers."""
        return (
            _file_sig(self._active_base_path()),
            tuple(_file_sig(p) for p in self._resolve_override_paths()),
        )

    def _ensure_loaded(self) -> None:
        sig = self.cache_signature()
        if sig == self._sig:
            return

        base_path = self._active_base_path()
        base_rates: dict[str, tuple[float, float, float | None, float | None]] = {}
        max_mtime = -1.0
        try:
            st = base_path.stat()
            max_mtime = max(max_mtime, st.st_mtime)
        except OSError:
            st = None

        if st is not None:
            try:
                cache_key = os.path.realpath(base_path)
            except OSError:
                cache_key = str(base_path)
            file_sig = (st.st_mtime_ns, st.st_size)
            shared = _RATES_FILE_CACHE.get(cache_key)
            if shared is not None and shared[0] == file_sig:
                base_rates = shared[1]
            else:
                try:
                    text = base_path.read_text(encoding="utf-8")
                    if st.st_size >= _STREAM_RATES_MIN_BYTES:
                        compact = _parse_compact_rates_streaming(text)
                    else:
                        raw = json.loads(text)
                        doc = raw.get("document") if isinstance(raw, dict) else None
                        if not isinstance(doc, dict) and isinstance(raw, dict):
                            doc = raw
                        compact = {}
                        if isinstance(doc, dict):
                            for key, val in doc.items():
                                tup = _extract_rate_tuple(val)
                                if tup is not None:
                                    compact[str(key).lower()] = tup
                        del raw, doc
                    del text
                    base_rates = compact
                    _RATES_FILE_CACHE[cache_key] = (file_sig, compact)
                except (OSError, ValueError, TypeError):
                    base_rates = {}

        override_rates: dict[str, tuple[float, float, float | None, float | None]] = {}
        for ov_path in reversed(self._resolve_override_paths()):
            try:
                ov_st = ov_path.stat()
                max_mtime = max(max_mtime, ov_st.st_mtime)
            except OSError:
                continue
            parsed = _parse_override_file(ov_path)
            if parsed:
                override_rates.update(parsed)

        self._rates = base_rates
        self._override_rates = override_rates
        self._lookup_cache.clear()
        self._sig = sig
        self._mtime = max_mtime if max_mtime >= 0.0 else -1.0

    def _tuple_to_dict(self, tup: tuple[float, float, float | None, float | None]) -> dict[str, Any]:
        return {
            "input_cost_per_token": tup[0],
            "output_cost_per_token": tup[1],
            "cache_read_input_token_cost": tup[2],
            "cache_creation_input_token_cost": tup[3],
        }

    @staticmethod
    def _find_in_table(
        table: dict[str, tuple[float, float, float | None, float | None]],
        stripped: str,
        candidates: list[str],
    ) -> tuple[float, float, float | None, float | None] | None:
        if not table:
            return None
        for key in candidates:
            tup = table.get(key)
            if tup is not None:
                return tup
        suffix = f"/{stripped}"
        dot_suffix = f".{stripped}"
        for key, tup in table.items():
            if key.endswith(suffix) or key.endswith(dot_suffix):
                return tup
        return None

    def lookup(self, model: str | None) -> dict[str, Any] | None:
        """Find the best matching rate entry for a model slug (overrides checked first)."""
        if not model:
            return None
        self._ensure_loaded()
        if not self._rates and not self._override_rates:
            return None
        m_raw = model.strip().lower()
        if not m_raw:
            return None
        if m_raw in self._lookup_cache:
            return self._lookup_cache[m_raw]

        m = _MODEL_COST_ALIASES.get(m_raw, m_raw)
        stripped = _EFFORT_SUFFIX_RE.sub("", m)
        stripped_raw = _EFFORT_SUFFIX_RE.sub("", m_raw)
        candidates = [
            m,
            stripped,
            f"gemini/{stripped}",
            f"vertex_ai/{stripped}",
            f"anthropic.{stripped}",
            f"global.anthropic.{stripped}",
            f"xai/{stripped}",
            f"openai/{stripped}",
            f"azure_ai/{stripped}",
            f"perplexity/google/{stripped}",
            f"perplexity/openai/{stripped}",
            f"perplexity/xai/{stripped}",
        ]
        found_tup: tuple[float, float, float | None, float | None] | None = None
        if self._override_rates:
            ov_candidates = [m_raw, stripped_raw] + candidates if m_raw != m else candidates
            found_tup = self._find_in_table(self._override_rates, stripped_raw, ov_candidates)
        if found_tup is None:
            found_tup = self._find_in_table(self._rates, stripped, candidates)

        result = self._tuple_to_dict(found_tup) if found_tup is not None else None
        self._lookup_cache[m_raw] = result
        return result

    def cost_breakdown(
        self,
        model: str | None,
        tokens: dict[str, int] | None,
        cost_usd: float | None = None,
    ) -> tuple[dict[str, float] | None, float | None]:
        """Split cost into {cached, uncached, output} and compute cache savings USD."""
        if not tokens:
            return None, None
        entry = self.lookup(model)
        if not entry:
            return None, None

        in_tok = max(0, int(tokens.get("in", 0) or 0))
        cache_tok = max(0, int(tokens.get("cache", 0) or 0))
        out_tok = max(0, int(tokens.get("out", 0) or 0))

        ir = float(entry.get("input_cost_per_token") or 0.0)
        orr = float(entry.get("output_cost_per_token") or 0.0)
        if ir == 0.0 and orr == 0.0:
            return None, None

        cr_raw = entry.get("cache_read_input_token_cost")
        cr = float(cr_raw) if isinstance(cr_raw, (int, float)) else (ir * 0.1)

        parts = [cache_tok * cr, in_tok * ir, out_tok * orr]
        total_est = sum(parts)
        if cost_usd is not None and total_est > 0.0:
            scale = float(cost_usd) / total_est
            parts = [x * scale for x in parts]

        cost_split = {
            "cached": round(parts[0], 4),
            "uncached": round(parts[1], 4),
            "output": round(parts[2], 4),
        }
        cache_savings = round(cache_tok * max(0.0, ir - cr), 4)
        return cost_split, cache_savings

    def estimate_cost_usd(
        self,
        model: str | None,
        tokens: dict[str, int] | None,
        *,
        cache_read: int | None = None,
        cache_write: int | None = None,
    ) -> float | None:
        """Estimate USD cost for normalized token counts using local rate metadata."""
        if not tokens:
            return None
        entry = self.lookup(model)
        if not entry:
            return None
        in_tok = max(0, int(tokens.get("in", 0) or 0))
        out_tok = max(0, int(tokens.get("out", 0) or 0))
        if cache_read is not None or cache_write is not None:
            cr = max(0, int(cache_read or 0))
            cw = max(0, int(cache_write or 0))
        else:
            cr = max(0, int(tokens.get("cache", 0) or 0))
            cw = 0

        in_rate = float(entry.get("input_cost_per_token") or 0.0)
        out_rate = float(entry.get("output_cost_per_token") or 0.0)
        cr_rate = float(entry.get("cache_read_input_token_cost") or (in_rate * 0.1))
        cw_rate = float(entry.get("cache_creation_input_token_cost") or (in_rate * 1.25))

        if in_rate == 0.0 and out_rate == 0.0:
            return None
        total = in_tok * in_rate + out_tok * out_rate + cr * cr_rate + cw * cw_rate
        return round(total, 4)


class UsageIndex:
    """Mtime-cached read-only index of CLI session usage from usage-scan-cache-v17.json."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = (
            Path(path)
            if path
            else default_t3_userdata_dir() / "usage-scan-cache-v17.json"
        )
        self._mtime: float = -1.0
        self._by_session: dict[str, dict[str, Any]] = {}

    def _ensure_loaded(self) -> None:
        try:
            st = self.path.stat()
        except OSError:
            self._by_session = {}
            self._mtime = -1.0
            return
        if st.st_mtime == self._mtime and self._by_session:
            return
        try:
            cache_key = os.path.realpath(self.path)
        except OSError:
            cache_key = str(self.path)
        shared = _USAGE_FILE_CACHE.get(cache_key)
        if shared is not None and shared[0] == st.st_mtime:
            self._by_session = shared[1]
            self._mtime = st.st_mtime
            return
        index: dict[str, dict[str, Any]] = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            files = raw.get("files") if isinstance(raw, dict) else None
            if not isinstance(files, dict):
                self._by_session = {}
                self._mtime = st.st_mtime
                _USAGE_FILE_CACHE[cache_key] = (st.st_mtime, {})
                return
            for file_path, entry in files.items():
                if not isinstance(entry, dict):
                    continue
                cs = entry.get("cs") if isinstance(entry.get("cs"), dict) else None
                sid = (cs.get("sessionId") if cs else None) or None
                if not sid:
                    m = _UUID_RE.search(Path(file_path).name)
                    if not m:
                        m = _UUID_RE.search(Path(file_path).parent.name)
                    if m:
                        sid = m.group(1)
                if not sid:
                    continue
                rows = entry.get("r") if isinstance(entry.get("r"), list) else []
                in_sum = 0
                cr_sum = 0
                cw_sum = 0
                out_sum = 0
                cost_sum = 0.0
                has_cost = False
                for row in rows:
                    if not isinstance(row, list) or len(row) < 7:
                        continue
                    in_sum += int(row[3] or 0)
                    cr_sum += int(row[4] or 0)
                    cw_sum += int(row[5] or 0)
                    out_sum += int(row[6] or 0)
                    if len(row) > 9 and isinstance(row[9], (int, float)) and row[9] > 0:
                        cost_sum += float(row[9])
                        has_cost = True
                if cs and isinstance(cs.get("lastCumulativeUsage"), dict) and in_sum == 0 and out_sum == 0:
                    cu = cs["lastCumulativeUsage"]
                    cr_sum = int(cu.get("cached_input_tokens") or 0)
                    cw_sum = int(cu.get("cache_write_input_tokens") or 0)
                    total_in = int(cu.get("input_tokens") or 0)
                    in_sum = max(0, total_in - cr_sum)
                    out_sum = int(cu.get("output_tokens") or 0)
                if in_sum or cr_sum or cw_sum or out_sum:
                    rec = {
                        "in": in_sum,
                        "cache": cr_sum + cw_sum,
                        "cacheRead": cr_sum,
                        "cacheWrite": cw_sum,
                        "out": out_sum,
                        "model": cs.get("model") if cs else None,
                        "costUsd": round(cost_sum, 4) if has_cost else None,
                    }
                    prev = index.get(sid)
                    if prev is None:
                        index[sid] = rec
                    else:
                        # Combine multiple entries for the same session ID safely.
                        prev["in"] += rec["in"]
                        prev["cache"] += rec["cache"]
                        prev["cacheRead"] += rec["cacheRead"]
                        prev["cacheWrite"] += rec["cacheWrite"]
                        prev["out"] += rec["out"]
                        if rec["costUsd"] is not None:
                            prev["costUsd"] = round((prev["costUsd"] or 0.0) + rec["costUsd"], 4)
            del raw, files
            self._by_session = index
            self._mtime = st.st_mtime
            _USAGE_FILE_CACHE[cache_key] = (st.st_mtime, index)
        except (OSError, ValueError, TypeError):
            self._by_session = {}

    def get(self, session_id: str | None) -> dict[str, Any] | None:
        """Return aggregated token/cost record for a CLI session ID, if known."""
        if not session_id:
            return None
        self._ensure_loaded()
        return self._by_session.get(session_id.strip())
