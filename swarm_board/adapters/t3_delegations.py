"""Read-only T3 Code delegation tree adapter backed by ~/.t3/userdata/statev2.sqlite."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any
import urllib.parse

from swarm_board import safety
from swarm_board.schema import (
    RateTable,
    UsageIndex,
    compute_dur_s,
    default_t3_userdata_dir,
    make_agent,
    make_flow,
    make_link,
    make_output,
    make_session_link,
    normalize_lab,
    normalize_state,
)
from .agy_swarms import default_brain_roots as _agy_default_brain_roots

_T3_USERDATA_DIR = default_t3_userdata_dir()
DEFAULT_DB_PATH = _T3_USERDATA_DIR / "statev2.sqlite"
DEFAULT_ENV_ID_PATH = _T3_USERDATA_DIR / "environment-id"
DEFAULT_AGY_PROVIDERS_ROOT = _T3_USERDATA_DIR / "providers" / "antigravity"
DEFAULT_GROK_SESSIONS_ROOT = Path.home() / ".grok" / "sessions"

_DEFAULT_RATE_TABLE = RateTable()
_DEFAULT_USAGE_INDEX = UsageIndex()

# Module-level mtime+size caches so repeated polls stay fast (< 25 ms).
_TRANSCRIPT_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}
_GROK_USAGE_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}
_GROK_INDEX_CACHE: tuple[float, str, dict[str, Path]] = (0.0, "", {})
_BRAIN_INDEX_CACHE: tuple[float, tuple[str, ...], dict[str, Path]] = (0.0, (), {})
_RESULT_FILES_CACHE: dict[tuple[str, str, tuple[str, ...]], list[dict[str, Any]]] = {}
_SUBAGENT_RESULT_CACHE: dict[tuple[str, int, str, str], str | None] = {}
_T3_FLOWS_CACHE: dict[
    tuple[Any, ...],
    tuple[
        tuple[Any, ...],
        list[tuple[str, tuple[int, int] | None]],
        list[tuple[str, tuple[int, int] | None]],
        list[dict[str, Any]],
    ],
] = {}
_RATE_LOOKUP_CACHE: dict[tuple[int, float, str], dict[str, Any] | None] = {}

_MODEL_COST_ALIASES: dict[str, str] = {
    "gemini-pro-orchestrator-neon": "gemini-4-neon",
}

_LIVE_RUN_STATUSES = ("queued", "preparing", "starting", "running", "waiting")
_MAX_TRACE_ITEMS = 30
_MAX_FILE_OUTPUTS_PER_AGENT = 12
_RESULT_SNIPPET_CHARS = 400

# Patterns to detect file paths referenced inside subagent result markdown/text.
_MD_FILE_LINK_RE = re.compile(r"file://(/[^)\s\"'`>]+)")
_ABS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_:/.-])(/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.@+-]+)+(?:\.[A-Za-z0-9]{1,10}))(?![A-Za-z0-9_.-])"
)
_REL_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_:/.-])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.@+-]+)+\.[A-Za-z0-9]{1,10})(?![A-Za-z0-9_.-])"
)
_LINE_SUFFIX_RE = re.compile(r"(?:#L\d+(?:-L?\d+)?|:\d+(?::\d+)?)$")
_PROMPT_HEAD_CHARS = 360
_LABEL_HINT_MAX_CHARS = 400
_PURE_UUID_RE = re.compile(
    r"^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32})$",
    re.IGNORECASE,
)
_GENERIC_SLUG_TOKENS = frozenset(
    {"node", "command", "mcp", "delegated-task", "delegate-task"}
)


def _bound_hint(text: Any, limit: int = _LABEL_HINT_MAX_CHARS) -> str | None:
    """Strip outer whitespace and bound a label hint string to `limit` chars."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    return cleaned[:limit]


def _extract_subagent_slug(subagent_id: str | None) -> str | None:
    """Extract a clean non-UUID trailing slug from a T3 subagent_id."""
    if not isinstance(subagent_id, str) or not subagent_id.strip():
        return None
    unquoted = urllib.parse.unquote(subagent_id)
    seg = unquoted.rsplit(":", 1)[-1].strip()
    if not seg or _PURE_UUID_RE.match(seg) or seg.lower() in _GENERIC_SLUG_TOKENS:
        return None
    return seg[:_LABEL_HINT_MAX_CHARS]


def _open_ro(db_path: Path) -> sqlite3.Connection:
    """Open a strictly read-only, WAL-safe SQLite connection. Caller must close it."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON;")
    conn.execute("PRAGMA cache_size = -2000;")
    conn.execute("PRAGMA busy_timeout = 1000;")
    return conn


def _read_env_id(env_id: str | None = None) -> str | None:
    """Resolve the T3 environment ID from argument or T3 userdata environment-id."""
    if env_id is not None:
        cleaned = env_id.strip()
        return cleaned or None
    env_dir = os.environ.get("T3_USERDATA_DIR", "").strip()
    env_path = Path(env_dir).expanduser() / "environment-id" if env_dir else DEFAULT_ENV_ID_PATH
    try:
        text = env_path.read_text(encoding="utf-8").strip()
        return text or None
    except OSError:
        return None


def _discover_brain_roots(brain_roots: list[Path] | None) -> list[Path]:
    """Return existing Antigravity brain root directories across ACP and CLI roots."""
    if brain_roots is not None:
        return [p for p in brain_roots if p.is_dir()]
    candidates: list[Path] = []
    if DEFAULT_AGY_PROVIDERS_ROOT.is_dir():
        try:
            for child in DEFAULT_AGY_PROVIDERS_ROOT.iterdir():
                candidate = child / "antigravity-acp" / "brain"
                if candidate.is_dir():
                    candidates.append(candidate)
        except OSError:
            pass
    candidates.extend(_agy_default_brain_roots())
    seen: set[str] = set()
    roots: list[Path] = []
    for p in candidates:
        if not p.is_dir():
            continue
        try:
            key = os.path.realpath(p)
        except OSError:
            key = str(p)
        if key not in seen:
            seen.add(key)
            roots.append(p)
    return roots


def _get_brain_transcript_map(brain_roots: list[Path]) -> dict[str, Path]:
    """Map Antigravity conversation UUID -> transcript.jsonl path (TTL-cached 5s)."""
    global _BRAIN_INDEX_CACHE
    key = tuple(sorted(str(p) for p in brain_roots))
    now = time.monotonic()
    cached_ts, cached_key, cached_map = _BRAIN_INDEX_CACHE
    if cached_key == key and (now - cached_ts) < 5.0:
        return cached_map

    mapping: dict[str, Path] = {}
    for root in brain_roots:
        try:
            with os.scandir(root) as it:
                for entry in it:
                    if not entry.is_dir():
                        continue
                    tr = Path(entry.path) / ".system_generated" / "logs" / "transcript.jsonl"
                    if tr.is_file():
                        mapping[entry.name] = tr
        except OSError:
            continue
    _BRAIN_INDEX_CACHE = (now, key, mapping)
    return mapping


def _get_grok_usage_map(grok_root: Path) -> dict[str, Path]:
    """Map Grok session UUID -> usage.json path (TTL-cached 5s)."""
    global _GROK_INDEX_CACHE
    key = str(grok_root)
    now = time.monotonic()
    cached_ts, cached_key, cached_map = _GROK_INDEX_CACHE
    if cached_key == key and (now - cached_ts) < 5.0:
        return cached_map

    mapping: dict[str, Path] = {}
    if grok_root.is_dir():
        try:
            with os.scandir(grok_root) as proj_it:
                for proj_entry in proj_it:
                    if not proj_entry.is_dir():
                        continue
                    try:
                        with os.scandir(proj_entry.path) as sess_it:
                            for sess_entry in sess_it:
                                if not sess_entry.is_dir():
                                    continue
                                ufile = Path(sess_entry.path) / "usage.json"
                                if ufile.is_file():
                                    mapping[sess_entry.name] = ufile
                    except OSError:
                        continue
        except OSError:
            pass
    _GROK_INDEX_CACHE = (now, key, mapping)
    return mapping


def _summarize_tool_call(tc: dict[str, Any]) -> list[Any] | None:
    """Convert an Antigravity transcript tool_call dict into a compact [name, target] pair."""
    name = tc.get("name")
    if not isinstance(name, str) or not name:
        return None
    args = tc.get("args")
    target = ""
    if isinstance(args, dict):
        for k in ("toolSummary", "Path", "path", "CommandLine", "query", "url", "title", "action"):
            val = args.get(k)
            if isinstance(val, str) and val.strip():
                cleaned = val.strip().strip('"').replace("\n", " ")
                target = cleaned[:80]
                break
    return [name, target] if target else [name]


def _parse_agy_transcript(path: Path) -> dict[str, Any] | None:
    """Parse Antigravity transcript.jsonl for token sums, model, and tool trace, cached by (mtime_ns, size)."""
    key = str(path)
    try:
        st = os.stat(key)
    except OSError:
        return None
    sig = (st.st_mtime_ns, st.st_size)
    cached = _TRANSCRIPT_CACHE.get(key)
    if cached is not None and cached[0] == sig[0] and cached[1] == sig[1]:
        return cached[2]

    in_tok = 0
    cache_tok = 0
    out_tok = 0
    model: str | None = None
    trace: list[list[Any]] = []
    last_activity: str | None = None

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"PLANNER_RESPONSE"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if obj.get("type") != "PLANNER_RESPONSE":
                    continue
                in_tok += int(obj.get("input_tokens") or 0)
                cache_tok += int(obj.get("cache_read_tokens") or 0)
                out_tok += int(obj.get("output_tokens") or 0)
                m = obj.get("model")
                if isinstance(m, str) and m.strip():
                    model = m.strip()
                tcs = obj.get("tool_calls")
                if isinstance(tcs, list):
                    for tc in tcs:
                        if isinstance(tc, dict):
                            item = _summarize_tool_call(tc)
                            if item is not None:
                                trace.append(item)
                                last_activity = f"{item[0]}: {item[1]}" if len(item) > 1 else str(item[0])
    except OSError:
        return None

    if len(trace) > _MAX_TRACE_ITEMS:
        trace = trace[-_MAX_TRACE_ITEMS:]

    has_tokens = (in_tok + cache_tok + out_tok) > 0
    parsed = {
        "tokens": {"in": in_tok, "cache": cache_tok, "out": out_tok} if has_tokens else None,
        "model": model,
        "trace": trace,
        "last": last_activity,
    }
    _TRANSCRIPT_CACHE[key] = (sig[0], sig[1], parsed)
    return parsed


def _parse_grok_usage(path: Path) -> dict[str, Any] | None:
    """Parse Grok ~/.grok/sessions/*/<sid>/usage.json, cached by (mtime_ns, size)."""
    key = str(path)
    try:
        st = os.stat(key)
    except OSError:
        return None
    sig = (st.st_mtime_ns, st.st_size)
    cached = _GROK_USAGE_CACHE.get(key)
    if cached is not None and cached[0] == sig[0] and cached[1] == sig[1]:
        return cached[2]

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    sess = raw.get("session") if isinstance(raw, dict) else None
    if not isinstance(sess, dict):
        return None

    total_in = int(sess.get("inputTokens") or 0)
    cr = int(sess.get("cachedReadTokens") or 0)
    cw = int(sess.get("cacheCreationTokens") or 0)
    out_tok = int(sess.get("outputTokens") or 0)
    non_cached_in = max(0, total_in - cr - cw)
    ticks = sess.get("costUsdTicks")
    cost_usd = round(float(ticks) / 1e10, 4) if isinstance(ticks, (int, float)) and ticks > 0 else None
    model = sess.get("primaryModelId") if isinstance(sess.get("primaryModelId"), str) else None

    has_tokens = (non_cached_in + cr + cw + out_tok) > 0
    parsed = {
        "tokens": {"in": non_cached_in, "cache": cr + cw, "out": out_tok} if has_tokens else None,
        "cacheRead": cr,
        "cacheWrite": cw,
        "costUsd": cost_usd,
        "model": model,
    }
    _GROK_USAGE_CACHE[key] = (sig[0], sig[1], parsed)
    return parsed


def _clip_snippet(text: str | None, limit: int = _RESULT_SNIPPET_CHARS) -> str | None:
    """Return a clean, single-block clipped snippet of at most `limit` characters."""
    if not text:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def _estimate_cost_fast(
    rate_table: RateTable,
    model: str | None,
    tokens: dict[str, int] | None,
    *,
    cache_read: int | None = None,
    cache_write: int | None = None,
) -> float | None:
    """Delegate cost estimation to RateTable (which caches lookups and honors overrides)."""
    if not tokens or not model:
        return None
    m_raw = model.strip()
    if not m_raw:
        return None
    m_lookup = _MODEL_COST_ALIASES.get(m_raw.lower(), m_raw)
    return rate_table.estimate_cost_usd(
        m_lookup,
        tokens,
        cache_read=cache_read,
        cache_write=cache_write,
    )


def _extract_result_files(
    result_text: str | None,
    allowed_roots: list[str],
    *,
    subagent_id: str,
    updated_at: str | None,
    agent_id: str,
    at: str | None,
) -> list[dict[str, Any]]:
    """Detect file paths referenced in `result_text` that safely resolve inside `allowed_roots`."""
    if not result_text or not allowed_roots:
        return []

    cache_key = (subagent_id, updated_at or "", tuple(allowed_roots))
    cached = _RESULT_FILES_CACHE.get(cache_key)
    if cached is not None:
        return cached

    candidates: list[str] = []
    seen_raw: set[str] = set()

    for pattern in (_MD_FILE_LINK_RE, _ABS_PATH_RE, _REL_PATH_RE):
        for m in pattern.finditer(result_text):
            raw = _LINE_SUFFIX_RE.sub("", m.group(1)).rstrip(".,;:!?`'\"")
            if not raw or raw in seen_raw:
                continue
            seen_raw.add(raw)
            candidates.append(raw)
            if len(candidates) >= 48:
                break
        if len(candidates) >= 48:
            break

    outputs: list[dict[str, Any]] = []
    seen_resolved: set[str] = set()
    for raw in candidates:
        p = Path(raw)
        resolved: str | None = None
        if p.is_absolute():
            if os.path.isfile(raw):
                ok, res = safety.is_path_allowed(raw, allowed_roots)
                if ok and res and os.path.isfile(res):
                    resolved = res
        else:
            for root in allowed_roots:
                if not root:
                    continue
                cand = os.path.join(root, raw)
                if os.path.isfile(cand):
                    ok, res = safety.is_path_allowed(cand, [root])
                    if ok and res and os.path.isfile(res):
                        resolved = res
                        break
        if not resolved or resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)
        try:
            fsize = os.path.getsize(resolved)
        except OSError:
            fsize = None
        idx = len(outputs) + 1
        outputs.append(
            make_output(
                f"file-{agent_id}-{idx}",
                "file",
                Path(resolved).name,
                path=resolved,
                size=fsize,
                by=agent_id,
                at=at,
            )
        )
        if len(outputs) >= _MAX_FILE_OUTPUTS_PER_AGENT:
            break
    _RESULT_FILES_CACHE[cache_key] = outputs
    return outputs


def _resolve_telemetry(
    *,
    provider: str | None,
    model: str | None,
    native_id: str | None,
    rate_table: RateTable,
    usage_index: UsageIndex,
    brain_map: dict[str, Path],
    grok_map: dict[str, Path],
) -> tuple[
    dict[str, int] | None,
    float | None,
    list[list[Any]],
    str | None,
    str | None,
    Path | None,
    bool,
]:
    """Resolve real tokens, USD cost, trace, last activity, model, transcript path, and cost_estimated."""
    tokens: dict[str, int] | None = None
    cost_usd: float | None = None
    trace: list[list[Any]] = []
    last_act: str | None = None
    resolved_model = model
    transcript_path: Path | None = None
    cost_estimated = True

    if not native_id:
        return tokens, cost_usd, trace, last_act, resolved_model, transcript_path, cost_estimated

    prov_lower = (provider or "").strip().lower()

    # 1. Antigravity: read transcript.jsonl (cached by mtime_ns + size).
    if prov_lower in ("antigravity", "agy") or native_id in brain_map:
        tr_path = brain_map.get(native_id)
        if tr_path is not None:
            transcript_path = tr_path
            parsed = _parse_agy_transcript(tr_path)
            if parsed is not None:
                tokens = parsed["tokens"]
                trace = parsed["trace"]
                last_act = parsed["last"]
                tr_model = parsed["model"]
                if not resolved_model and tr_model:
                    resolved_model = tr_model
                if tokens is not None:
                    cost_usd = _estimate_cost_fast(rate_table, resolved_model, tokens)
                    if cost_usd is None and tr_model and tr_model != resolved_model:
                        cost_usd = _estimate_cost_fast(rate_table, tr_model, tokens)

    # 2. Grok: check ~/.grok/sessions/*/<sid>/usage.json (has exact costUsdTicks).
    if tokens is None and (prov_lower == "grok" or native_id in grok_map):
        g_path = grok_map.get(native_id)
        if g_path is not None:
            parsed_g = _parse_grok_usage(g_path)
            if parsed_g is not None:
                tokens = parsed_g["tokens"]
                cost_usd = parsed_g["costUsd"]
                if cost_usd is not None:
                    cost_estimated = False
                if not resolved_model and parsed_g["model"]:
                    resolved_model = parsed_g["model"]
                if cost_usd is None and tokens is not None:
                    cost_usd = _estimate_cost_fast(
                        rate_table,
                        resolved_model or parsed_g["model"],
                        tokens,
                        cache_read=parsed_g["cacheRead"],
                        cache_write=parsed_g["cacheWrite"],
                    )

    # 3. UsageIndex (Claude, Codex, Grok fallback).
    if tokens is None:
        rec = usage_index.get(native_id)
        if rec is not None:
            tokens = {
                "in": rec["in"],
                "cache": rec["cache"],
                "out": rec["out"],
            }
            if not resolved_model and rec.get("model"):
                resolved_model = rec["model"]
            cost_usd = rec.get("costUsd")
            if cost_usd is not None:
                cost_estimated = False
            else:
                cost_usd = _estimate_cost_fast(
                    rate_table,
                    resolved_model or rec.get("model"),
                    tokens,
                    cache_read=rec.get("cacheRead"),
                    cache_write=rec.get("cacheWrite"),
                )

    return tokens, cost_usd, trace, last_act, resolved_model, transcript_path, cost_estimated


def _t3_thread_link(env_id: str | None, thread_id: str | None) -> dict[str, Any] | None:
    if not env_id or not thread_id:
        return None
    return make_link(
        "thread",
        "Open chat",
        href=f"t3-thread://v2/{env_id}/{thread_id}",
        link_id=thread_id,
    )


def _compute_tree_shape(root_id: str, agents: list[dict[str, Any]]) -> tuple[int, int]:
    """Return (max_depth, leaf_count) for the tree rooted at `root_id`."""
    children_by_parent: dict[str, list[str]] = {}
    agent_ids = {a["id"] for a in agents}
    for a in agents:
        p = a.get("parent")
        if p and p in agent_ids:
            children_by_parent.setdefault(p, []).append(a["id"])

    depths: dict[str, int] = {root_id: 0}
    queue = [root_id]
    while queue:
        cur = queue.pop(0)
        d = depths[cur]
        for ch in children_by_parent.get(cur, ()):
            if ch not in depths:
                depths[ch] = d + 1
                queue.append(ch)

    max_depth = max(depths.values(), default=0)
    leaf_count = sum(
        1
        for a in agents
        if a["id"] != root_id and not children_by_parent.get(a["id"])
    )
    return max_depth, leaf_count


def _derive_root_state(
    root_id: str,
    live_runs: dict[str, str],
    child_states: list[str],
) -> str:
    if root_id in live_runs:
        return normalize_state(live_runs[root_id])
    if any(s == "running" for s in child_states):
        return "running"
    if any(s == "waiting" for s in child_states):
        return "waiting"
    if any(s == "queued" for s in child_states):
        return "queued"
    return "done"


def _stat_ino_sig(path: Path | str) -> tuple[int, int, int] | None:
    try:
        st = os.stat(path)
        return (st.st_ino, st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _stat_mtime_size(path_str: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path_str)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _dedup_tree_usage(
    *,
    agent_id: str,
    native_id: str | None,
    tokens: dict[str, int] | None,
    cost_usd: float | None,
    seen_native_ids: set[str],
    seen_usage_keys: set[tuple[tuple[tuple[str, int], ...], float | None]],
) -> tuple[dict[str, int] | None, float | None, bool]:
    """Clear duplicate cumulative session usage on continuation/node:* rows within a tree."""
    if tokens is None and cost_usd is None:
        return None, None, False
    tok_key = (tuple(sorted(tokens.items())), cost_usd) if tokens is not None else None
    is_shared_native = bool(native_id and native_id in seen_native_ids)
    is_node_dup_usage = bool(
        str(agent_id).startswith("node:")
        and tok_key is not None
        and tok_key in seen_usage_keys
    )
    if is_shared_native or is_node_dup_usage:
        return None, None, True
    if native_id:
        seen_native_ids.add(native_id)
    if tok_key is not None:
        seen_usage_keys.add(tok_key)
    return tokens, cost_usd, False


def _refresh_running_durations(
    flows: list[dict[str, Any]],
    now_ts: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in flows:
        agents = f.get("agents") or ()
        if not any(isinstance(a, dict) and a.get("state") == "running" for a in agents):
            out.append(f)
            continue
        new_agents = []
        for a in agents:
            if isinstance(a, dict) and a.get("state") == "running":
                a_copy = dict(a)
                a_copy["durS"] = compute_dur_s(a.get("startedAt"), None, is_running=True, now_ts=now_ts)
                new_agents.append(a_copy)
            else:
                new_agents.append(a)
        f_copy = dict(f)
        f_copy["agents"] = new_agents
        out.append(f_copy)
    return out


def discover_t3_flows(
    db_path: Path | None = None,
    *,
    env_id: str | None = None,
    limit_trees: int = 25,
    rate_table: RateTable | None = None,
    usage_index: UsageIndex | None = None,
    brain_roots: list[Path] | None = None,
    grok_sessions_root: Path | None = None,
    now_ts: float | None = None,
) -> list[dict[str, Any]]:
    """Discover recent T3 delegation trees as normalized read-only Flow objects."""
    resolved_db = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if not resolved_db.is_file():
        return []

    resolved_env_id = _read_env_id(env_id)
    rt = rate_table if rate_table is not None else _DEFAULT_RATE_TABLE
    ui = usage_index if usage_index is not None else _DEFAULT_USAGE_INDEX
    b_roots = _discover_brain_roots(brain_roots)
    g_root = grok_sessions_root if grok_sessions_root is not None else DEFAULT_GROK_SESSIONS_ROOT

    use_cache = now_ts is None
    cache_key = (
        str(resolved_db),
        resolved_env_id,
        int(limit_trees),
        str(rt.path),
        str(ui.path),
        str(g_root),
        tuple(str(p) for p in b_roots),
    )
    wal_path = f"{resolved_db}-wal"
    sig = (
        _stat_ino_sig(resolved_db),
        _stat_ino_sig(wal_path),
        rt.cache_signature(),
        _stat_ino_sig(ui.path),
    )
    if use_cache:
        cached = _T3_FLOWS_CACHE.get(cache_key)
        if cached is not None:
            c_sig, c_tr_sigs, c_grok_sigs, c_flows = cached
            if (
                c_sig == sig
                and all(_stat_mtime_size(p) == s for p, s in c_tr_sigs)
                and all(_stat_mtime_size(p) == s for p, s in c_grok_sigs)
            ):
                return _refresh_running_durations(c_flows, time.time())

    brain_map = _get_brain_transcript_map(b_roots) if b_roots else {}
    grok_map = _get_grok_usage_map(g_root)

    try:
        conn = _open_ro(resolved_db)
    except sqlite3.Error:
        return []

    try:
        # 1. Fetch slim subagent rows (NEVER pull full $.prompt or full $.result on list scan).
        sub_rows = conn.execute(
            """
            SELECT
                subagent_id,
                thread_id,
                run_id,
                parent_node_id,
                provider,
                driver,
                provider_instance_id,
                provider_thread_id,
                child_thread_id,
                origin,
                status,
                started_at,
                completed_at,
                updated_at,
                json_extract(payload_json, '$.title') AS title,
                json_extract(payload_json, '$.role') AS role,
                json_extract(payload_json, '$.model') AS model,
                json_extract(payload_json, '$.modelSelection.model') AS ms_model,
                substr(json_extract(payload_json, '$.result'), 1, 400) AS result_snippet,
                substr(json_extract(payload_json, '$.prompt'), 1, 360) AS prompt_head
            FROM orchestration_v2_projection_subagents
            ORDER BY COALESCE(started_at, updated_at) ASC, subagent_id ASC
            """
        ).fetchall()

        if not sub_rows:
            return []

        # 2. Fetch live runs via partial recovery_idx (< 0.3 ms).
        live_runs: dict[str, str] = {}
        for r in conn.execute(
            """
            SELECT thread_id, status
            FROM orchestration_v2_projection_runs
            WHERE status IN ('queued', 'preparing', 'starting', 'running', 'waiting')
            """
        ):
            tid = r["thread_id"]
            st = r["status"]
            # Prefer 'running' over 'queued'/'waiting' if multiple active runs exist.
            if tid not in live_runs or st == "running":
                live_runs[tid] = st

        # 3. Load thread lineage + metadata for all referenced threads (and walk up ancestors).
        referenced_tids: set[str] = set()
        for s in sub_rows:
            if s["thread_id"]:
                referenced_tids.add(s["thread_id"])
            if s["child_thread_id"]:
                referenced_tids.add(s["child_thread_id"])

        threads_by_id: dict[str, dict[str, Any]] = {}

        def _fetch_threads(tids: set[str]) -> None:
            missing = [t for t in tids if t and t not in threads_by_id]
            for i in range(0, len(missing), 400):
                chunk = missing[i : i + 400]
                ph = ",".join("?" * len(chunk))
                q = f"""
                    SELECT
                        thread_id,
                        project_id,
                        title,
                        default_provider,
                        provider_instance_id,
                        active_provider_thread_id,
                        created_at,
                        updated_at,
                        archived_at,
                        deleted_at,
                        json_extract(payload_json, '$.lineage.rootThreadId') AS root_thread_id,
                        json_extract(payload_json, '$.lineage.parentThreadId') AS parent_thread_id,
                        json_extract(payload_json, '$.lineage.relationshipToParent') AS rel_to_parent,
                        json_extract(payload_json, '$.modelSelection.model') AS model,
                        json_extract(payload_json, '$.worktreePath') AS worktree_path
                    FROM orchestration_v2_projection_threads
                    WHERE thread_id IN ({ph})
                """
                for row in conn.execute(q, chunk):
                    threads_by_id[row["thread_id"]] = dict(row)

        _fetch_threads(referenced_tids)
        # Fetch any ancestor rootThreadId / parentThreadId not yet in threads_by_id.
        for _ in range(4):
            ancestors: set[str] = set()
            for t in threads_by_id.values():
                if t.get("root_thread_id") and t["root_thread_id"] not in threads_by_id:
                    ancestors.add(t["root_thread_id"])
                if t.get("parent_thread_id") and t["parent_thread_id"] not in threads_by_id:
                    ancestors.add(t["parent_thread_id"])
            if not ancestors:
                break
            _fetch_threads(ancestors)

        def _resolve_root_thread_id(tid: str) -> str:
            t = threads_by_id.get(tid)
            if t and t.get("root_thread_id"):
                return str(t["root_thread_id"])
            cur = tid
            visited: set[str] = set()
            while cur and cur not in visited:
                visited.add(cur)
                info = threads_by_id.get(cur)
                if not info:
                    break
                if info.get("root_thread_id"):
                    return str(info["root_thread_id"])
                parent = info.get("parent_thread_id")
                if not parent:
                    break
                cur = str(parent)
            return cur or tid

        # 4. Group subagents under their rootThreadId.
        trees_subs: dict[str, list[sqlite3.Row]] = {}
        for s in sub_rows:
            root_tid = _resolve_root_thread_id(s["thread_id"])
            trees_subs.setdefault(root_tid, []).append(s)

        # Compute latest activity per root tree and keep the most recent `limit_trees`.
        tree_activity: list[tuple[str, str]] = []
        for root_tid, subs in trees_subs.items():
            root_info = threads_by_id.get(root_tid)
            # Skip deleted root threads if marked deleted.
            if root_info and root_info.get("deleted_at"):
                continue
            latest = (root_info.get("updated_at") if root_info else "") or ""
            for s in subs:
                for ts_col in (s["updated_at"], s["completed_at"], s["started_at"]):
                    if ts_col and ts_col > latest:
                        latest = ts_col
            tree_activity.append((latest, root_tid))

        tree_activity.sort(key=lambda item: item[0], reverse=True)
        selected_roots = [rtid for _, rtid in tree_activity[: max(1, int(limit_trees))]]
        if not selected_roots:
            return []

        # 5. Collect IDs for selected trees to fetch projects, provider_threads, and full results.
        selected_sub_ids: list[str] = []
        sub_updated_by_id: dict[str, str] = {}
        selected_thread_ids: set[str] = set(selected_roots)
        selected_pt_ids: set[str] = set()
        selected_project_ids: set[str] = set()

        for root_tid in selected_roots:
            rinfo = threads_by_id.get(root_tid)
            if rinfo:
                if rinfo.get("project_id"):
                    selected_project_ids.add(rinfo["project_id"])
                if rinfo.get("active_provider_thread_id"):
                    selected_pt_ids.add(rinfo["active_provider_thread_id"])
            for s in trees_subs[root_tid]:
                sid = s["subagent_id"]
                selected_sub_ids.append(sid)
                sub_updated_by_id[sid] = str(s["updated_at"] or "")
                if s["thread_id"]:
                    selected_thread_ids.add(s["thread_id"])
                    tinfo = threads_by_id.get(s["thread_id"])
                    if tinfo and tinfo.get("project_id"):
                        selected_project_ids.add(tinfo["project_id"])
                    if tinfo and tinfo.get("active_provider_thread_id"):
                        selected_pt_ids.add(tinfo["active_provider_thread_id"])
                if s["child_thread_id"]:
                    selected_thread_ids.add(s["child_thread_id"])
                    cinfo = threads_by_id.get(s["child_thread_id"])
                    if cinfo and cinfo.get("active_provider_thread_id"):
                        selected_pt_ids.add(cinfo["active_provider_thread_id"])
                if s["provider_thread_id"]:
                    selected_pt_ids.add(s["provider_thread_id"])

        # Fetch projects.
        projects_by_id: dict[str, dict[str, Any]] = {}
        if selected_project_ids:
            pids = list(selected_project_ids)
            for i in range(0, len(pids), 400):
                chunk = pids[i : i + 400]
                ph = ",".join("?" * len(chunk))
                for row in conn.execute(
                    f"SELECT project_id, title, workspace_root FROM projection_projects WHERE project_id IN ({ph})",
                    chunk,
                ):
                    projects_by_id[row["project_id"]] = dict(row)

        # Fetch provider_threads nativeId mappings for selected trees.
        pt_native_by_ptid: dict[str, tuple[str | None, str | None]] = {}
        pt_native_by_tid: dict[str, tuple[str | None, str | None]] = {}

        if selected_pt_ids:
            ptids = list(selected_pt_ids)
            for i in range(0, len(ptids), 400):
                chunk = ptids[i : i + 400]
                ph = ",".join("?" * len(chunk))
                for row in conn.execute(
                    f"""
                    SELECT
                        provider_thread_id,
                        thread_id,
                        COALESCE(driver, provider) AS drv,
                        json_extract(payload_json, '$.nativeThreadRef.nativeId') AS native_id
                    FROM orchestration_v2_projection_provider_threads
                    WHERE provider_thread_id IN ({ph})
                    """,
                    chunk,
                ):
                    nid = row["native_id"]
                    drv = row["drv"]
                    if nid:
                        pt_native_by_ptid[row["provider_thread_id"]] = (nid, drv)
                        if row["thread_id"] and row["thread_id"] not in pt_native_by_tid:
                            pt_native_by_tid[row["thread_id"]] = (nid, drv)

        if selected_thread_ids:
            tids = list(selected_thread_ids)
            for i in range(0, len(tids), 400):
                chunk = tids[i : i + 400]
                ph = ",".join("?" * len(chunk))
                for row in conn.execute(
                    f"""
                    SELECT
                        provider_thread_id,
                        thread_id,
                        COALESCE(driver, provider) AS drv,
                        json_extract(payload_json, '$.nativeThreadRef.nativeId') AS native_id
                    FROM orchestration_v2_projection_provider_threads
                    WHERE thread_id IN ({ph})
                    ORDER BY updated_at DESC
                    """,
                    chunk,
                ):
                    nid = row["native_id"]
                    drv = row["drv"]
                    if nid:
                        if row["thread_id"] and row["thread_id"] not in pt_native_by_tid:
                            pt_native_by_tid[row["thread_id"]] = (nid, drv)
                        if row["provider_thread_id"] and row["provider_thread_id"] not in pt_native_by_ptid:
                            pt_native_by_ptid[row["provider_thread_id"]] = (nid, drv)

        # Fetch full $.result ONLY for uncached selected trees' subagents (still NEVER pulling $.prompt).
        try:
            db_ino = resolved_db.stat().st_ino
        except OSError:
            db_ino = 0
        db_key = str(resolved_db)

        if len(_SUBAGENT_RESULT_CACHE) > 4096:
            _SUBAGENT_RESULT_CACHE.clear()

        full_results: dict[str, str] = {}
        uncached_sub_ids: list[str] = []
        for sid in selected_sub_ids:
            ckey = (db_key, db_ino, sid, sub_updated_by_id.get(sid, ""))
            if ckey in _SUBAGENT_RESULT_CACHE:
                cached_res = _SUBAGENT_RESULT_CACHE[ckey]
                if isinstance(cached_res, str) and cached_res.strip():
                    full_results[sid] = cached_res
            else:
                uncached_sub_ids.append(sid)

        fetched_map: dict[str, str] = {}
        for i in range(0, len(uncached_sub_ids), 400):
            chunk = uncached_sub_ids[i : i + 400]
            ph = ",".join("?" * len(chunk))
            for row in conn.execute(
                f"""
                SELECT subagent_id, json_extract(payload_json, '$.result') AS full_result
                FROM orchestration_v2_projection_subagents
                WHERE subagent_id IN ({ph})
                  AND json_extract(payload_json, '$.result') IS NOT NULL
                """,
                chunk,
            ):
                if isinstance(row["full_result"], str) and row["full_result"].strip():
                    fetched_map[row["subagent_id"]] = row["full_result"]

        for sid in uncached_sub_ids:
            val = fetched_map.get(sid)
            ckey = (db_key, db_ino, sid, sub_updated_by_id.get(sid, ""))
            _SUBAGENT_RESULT_CACHE[ckey] = val
            if val is not None:
                full_results[sid] = val
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    # 6. Assemble normalized Flow objects for each selected root tree.
    flows: list[dict[str, Any]] = []
    for root_tid in selected_roots:
        subs = trees_subs[root_tid]
        root_info = threads_by_id.get(root_tid) or {}
        project_id = root_info.get("project_id")
        if not project_id:
            for s in subs:
                tinfo = threads_by_id.get(s["thread_id"])
                if tinfo and tinfo.get("project_id"):
                    project_id = tinfo["project_id"]
                    break
        proj_info = projects_by_id.get(project_id) if project_id else None
        project_root = proj_info.get("workspace_root") if proj_info else None
        project_title = (proj_info.get("title") if proj_info else None) or ""
        root_worktree = root_info.get("worktree_path")

        allowed_roots = [r for r in (project_root, root_worktree) if r]

        # Build subagent nodes first so we know all child_thread_ids and child states.
        sub_agents: list[dict[str, Any]] = []
        outputs: list[dict[str, Any]] = []
        known_node_ids: set[str] = {root_tid}
        child_tid_used: set[str] = set()

        for s in subs:
            ctid = s["child_thread_id"]
            if ctid and ctid not in child_tid_used:
                agent_id = ctid
                child_tid_used.add(ctid)
            else:
                agent_id = s["subagent_id"]
            known_node_ids.add(agent_id)

        # Create intermediate nodes for forked/side threads that spawned subagents
        # without themselves being a subagent row, so every `parent = subagent.thread_id` resolves.
        intermediate_agents: list[dict[str, Any]] = []
        intermediate_tids: set[str] = set()
        for s in subs:
            ptid = s["thread_id"]
            if ptid and ptid not in known_node_ids and ptid not in intermediate_tids:
                intermediate_tids.add(ptid)
                known_node_ids.add(ptid)

        # Resolve root telemetry first so per-tree usage deduplication claims root session keys.
        root_title = root_info.get("title") or root_tid
        root_model = root_info.get("model")
        root_provider = root_info.get("default_provider") or root_info.get("provider_instance_id")
        root_nid, root_drv = pt_native_by_tid.get(root_tid, (None, None))
        if not root_nid and root_info.get("active_provider_thread_id"):
            root_nid, root_drv = pt_native_by_ptid.get(root_info["active_provider_thread_id"], (None, None))

        root_tokens, root_cost, root_trace, root_last, root_res_model, root_tr_path, root_cost_est = _resolve_telemetry(
            provider=root_drv or root_provider,
            model=root_model,
            native_id=root_nid,
            rate_table=rt,
            usage_index=ui,
            brain_map=brain_map,
            grok_map=grok_map,
        )

        seen_native_ids: set[str] = set()
        seen_usage_keys: set[tuple[tuple[tuple[str, int], ...], float | None]] = set()
        if root_tokens is not None or root_cost is not None:
            if root_nid:
                seen_native_ids.add(root_nid)
            if root_tokens is not None:
                seen_usage_keys.add((tuple(sorted(root_tokens.items())), root_cost))

        for itid in sorted(intermediate_tids):
            tinfo = threads_by_id.get(itid) or {}
            iparent = tinfo.get("parent_thread_id")
            if not iparent or iparent not in known_node_ids:
                iparent = root_tid
            imodel = tinfo.get("model")
            iprov = tinfo.get("default_provider") or tinfo.get("provider_instance_id")
            istate = normalize_state(live_runs[itid]) if itid in live_runs else "done"
            istart = tinfo.get("created_at")
            iend = None if istate == "running" else tinfo.get("updated_at")
            inid, idrv = pt_native_by_tid.get(itid, (None, None))
            if not inid and tinfo.get("active_provider_thread_id"):
                inid, idrv = pt_native_by_ptid.get(tinfo["active_provider_thread_id"], (None, None))
            itok, icost, itrace, ilast, ires_model, itr_path, icost_est = _resolve_telemetry(
                provider=idrv or iprov,
                model=imodel,
                native_id=inid,
                rate_table=rt,
                usage_index=ui,
                brain_map=brain_map,
                grok_map=grok_map,
            )
            itok, icost, i_deduped = _dedup_tree_usage(
                agent_id=itid,
                native_id=inid,
                tokens=itok,
                cost_usd=icost,
                seen_native_ids=seen_native_ids,
                seen_usage_keys=seen_usage_keys,
            )
            ilinks: list[dict[str, Any]] = []
            tlink = _t3_thread_link(resolved_env_id, itid)
            if tlink:
                ilinks.append(tlink)
            slink = make_session_link(idrv or iprov, inid)
            if slink:
                ilinks.append(slink)

            iout_ids: list[str] = []
            tr_out_id = f"tr-{itid}"
            outputs.append(
                make_output(
                    tr_out_id,
                    "transcript",
                    f"{tinfo.get('title') or itid} transcript",
                    path=str(itr_path) if itr_path else None,
                    by=itid,
                    at=iend or istart,
                    meta={"threadId": itid, "nativeId": inid},
                )
            )
            iout_ids.append(tr_out_id)

            i_agent = make_agent(
                itid,
                tinfo.get("title") or itid,
                parent=iparent,
                role=tinfo.get("rel_to_parent") or "fork",
                model=ires_model,
                provider=idrv or iprov,
                state=istate,
                started_at=istart,
                ended_at=iend,
                dur_s=compute_dur_s(istart, iend, is_running=(istate == "running"), now_ts=now_ts),
                tokens=itok,
                cost_usd=icost,
                trace=itrace,
                last=ilast,
                links=ilinks,
                output_ids=iout_ids,
            )
            i_agent["costEstimated"] = icost_est
            if i_deduped:
                i_agent["usageDeduped"] = True
                i_agent["meta"] = {"usageDeduped": True}
            i_raw_title = tinfo.get("title") or (
                itid if not _PURE_UUID_RE.match(str(itid)) else None
            )
            i_agent["_labelHint"] = {
                "prompt": None,
                "slug": None,
                "planTask": None,
                "rawTitle": _bound_hint(i_raw_title, _LABEL_HINT_MAX_CHARS),
            }
            intermediate_agents.append(i_agent)

        # Now assemble every subagent node.
        seen_sub_agent_ids: set[str] = set()
        for s in subs:
            ctid = s["child_thread_id"]
            if ctid and ctid not in seen_sub_agent_ids:
                agent_id = ctid
            else:
                agent_id = s["subagent_id"]
            seen_sub_agent_ids.add(agent_id)

            parent_tid = s["thread_id"] if s["thread_id"] in known_node_ids else root_tid
            cinfo = threads_by_id.get(ctid) if ctid else None

            prov = s["driver"] or s["provider"] or (cinfo.get("default_provider") if cinfo else None)
            raw_model = s["model"] or s["ms_model"] or (cinfo.get("model") if cinfo else None)
            name = (
                s["title"]
                or (cinfo.get("title") if cinfo else None)
                or s["subagent_id"]
            )
            role = s["role"] or "worker"

            # Determine live state.
            raw_status = s["status"]
            norm_state = normalize_state(raw_status)
            if ctid and ctid in live_runs and norm_state not in ("done", "error", "killed", "interrupted"):
                norm_state = normalize_state(live_runs[ctid])

            started_at = s["started_at"] or (cinfo.get("created_at") if cinfo else None)
            ended_at = None if norm_state == "running" else (s["completed_at"] or s["updated_at"])
            dur_s = compute_dur_s(started_at, ended_at, is_running=(norm_state == "running"), now_ts=now_ts)

            # Resolve native session ID via provider_thread_id or child_thread_id.
            native_id: str | None = None
            native_drv: str | None = None
            if s["provider_thread_id"] and s["provider_thread_id"] in pt_native_by_ptid:
                native_id, native_drv = pt_native_by_ptid[s["provider_thread_id"]]
            if not native_id and ctid and ctid in pt_native_by_tid:
                native_id, native_drv = pt_native_by_tid[ctid]
            if not native_id and cinfo and cinfo.get("active_provider_thread_id"):
                apt = cinfo["active_provider_thread_id"]
                if apt in pt_native_by_ptid:
                    native_id, native_drv = pt_native_by_ptid[apt]

            tokens, cost_usd, trace, last_act, resolved_model, tr_path, cost_est = _resolve_telemetry(
                provider=native_drv or prov,
                model=raw_model,
                native_id=native_id,
                rate_table=rt,
                usage_index=ui,
                brain_map=brain_map,
                grok_map=grok_map,
            )
            tokens, cost_usd, usage_deduped = _dedup_tree_usage(
                agent_id=agent_id,
                native_id=native_id,
                tokens=tokens,
                cost_usd=cost_usd,
                seen_native_ids=seen_native_ids,
                seen_usage_keys=seen_usage_keys,
            )

            links: list[dict[str, Any]] = []
            tlink = _t3_thread_link(resolved_env_id, ctid)
            if tlink:
                links.append(tlink)
            slink = make_session_link(native_drv or prov, native_id)
            if slink:
                links.append(slink)

            out_ids: list[str] = []
            tr_out_id = f"tr-{agent_id}"
            outputs.append(
                make_output(
                    tr_out_id,
                    "transcript",
                    f"{name} transcript",
                    path=str(tr_path) if tr_path else None,
                    by=agent_id,
                    at=ended_at or started_at,
                    meta={
                        "threadId": ctid,
                        "subagentId": s["subagent_id"],
                        "nativeId": native_id,
                    },
                )
            )
            out_ids.append(tr_out_id)

            full_res = full_results.get(s["subagent_id"])
            snippet = _clip_snippet(s["result_snippet"] or full_res)
            if full_res:
                res_out_id = f"res-{agent_id}"
                outputs.append(
                    make_output(
                        res_out_id,
                        "result",
                        f"{name} result",
                        by=agent_id,
                        at=ended_at or s["updated_at"],
                        body=full_res,
                        size=len(full_res.encode("utf-8")),
                        meta={"subagentId": s["subagent_id"]},
                    )
                )
                out_ids.append(res_out_id)

                child_wt = cinfo.get("worktree_path") if cinfo else None
                agent_roots = allowed_roots + ([child_wt] if child_wt and child_wt not in allowed_roots else [])
                for f_out in _extract_result_files(
                    full_res,
                    agent_roots,
                    subagent_id=s["subagent_id"],
                    updated_at=s["updated_at"],
                    agent_id=agent_id,
                    at=ended_at or s["updated_at"],
                ):
                    outputs.append(f_out)
                    out_ids.append(f_out["id"])

            sub_agent = make_agent(
                agent_id,
                name,
                parent=parent_tid,
                role=role,
                model=resolved_model,
                provider=native_drv or prov,
                state=norm_state,
                started_at=started_at,
                ended_at=ended_at,
                dur_s=dur_s,
                tokens=tokens,
                cost_usd=cost_usd,
                trace=trace,
                last=last_act,
                result=snippet,
                links=links,
                output_ids=out_ids,
            )
            sub_agent["costEstimated"] = cost_est
            if usage_deduped:
                sub_agent["usageDeduped"] = True
                sub_agent["meta"] = {"usageDeduped": True}
            raw_title = s["title"] or (cinfo.get("title") if cinfo else None)
            if (
                not raw_title
                and isinstance(name, str)
                and not name.startswith("node:")
                and not _PURE_UUID_RE.match(name)
            ):
                raw_title = name
            sub_agent["_labelHint"] = {
                "prompt": _bound_hint(s["prompt_head"], _PROMPT_HEAD_CHARS),
                "slug": _extract_subagent_slug(s["subagent_id"]),
                "planTask": None,
                "rawTitle": _bound_hint(raw_title, _LABEL_HINT_MAX_CHARS),
            }
            sub_agents.append(sub_agent)

        # Assemble root Agent.

        non_root_agents = intermediate_agents + sub_agents
        root_state = _derive_root_state(root_tid, live_runs, [a["state"] for a in non_root_agents])

        root_started = root_info.get("created_at")
        if not root_started:
            starts = [a["startedAt"] for a in non_root_agents if a.get("startedAt")]
            root_started = min(starts) if starts else None

        latest_updated = root_info.get("updated_at") or ""
        for a in non_root_agents:
            for ts_val in (a.get("endedAt"), a.get("startedAt")):
                if ts_val and ts_val > latest_updated:
                    latest_updated = ts_val
        root_ended = None if root_state == "running" else (latest_updated or None)
        root_dur = compute_dur_s(root_started, root_ended, is_running=(root_state == "running"), now_ts=now_ts)

        root_lab = normalize_lab(root_res_model, root_drv or root_provider)
        if root_lab == "other" and not root_res_model and not root_provider:
            root_lab = "t3"

        root_links: list[dict[str, Any]] = []
        rt_link = _t3_thread_link(resolved_env_id, root_tid)
        if rt_link:
            root_links.append(rt_link)
        rs_link = make_session_link(root_drv or root_provider, root_nid)
        if rs_link:
            root_links.append(rs_link)

        root_out_ids: list[str] = []
        root_tr_id = f"tr-{root_tid}"
        outputs.insert(
            0,
            make_output(
                root_tr_id,
                "transcript",
                f"{root_title} transcript",
                path=str(root_tr_path) if root_tr_path else None,
                by=root_tid,
                at=root_ended or root_started,
                meta={"threadId": root_tid, "nativeId": root_nid},
            ),
        )
        root_out_ids.append(root_tr_id)

        root_agent = make_agent(
            root_tid,
            root_title,
            parent=None,
            role="root",
            lab=root_lab,
            model=root_res_model,
            provider=root_drv or root_provider,
            state=root_state,
            started_at=root_started,
            ended_at=root_ended,
            dur_s=root_dur,
            tokens=root_tokens,
            cost_usd=root_cost,
            trace=root_trace,
            last=root_last,
            links=root_links,
            output_ids=root_out_ids,
        )
        root_agent["costEstimated"] = root_cost_est
        root_raw_title = root_info.get("title") or (
            root_title if not _PURE_UUID_RE.match(str(root_title)) else None
        )
        root_agent["_labelHint"] = {
            "prompt": None,
            "slug": None,
            "planTask": None,
            "rawTitle": _bound_hint(root_raw_title, _LABEL_HINT_MAX_CHARS),
        }

        all_agents = [root_agent] + non_root_agents
        max_depth, leaf_count = _compute_tree_shape(root_tid, all_agents)
        distinct_labs = {a["lab"] for a in non_root_agents if a.get("lab")}

        if max_depth >= 2 and leaf_count > 12:
            flow_kind = "tree"
        elif len(distinct_labs) > 1:
            flow_kind = "mixed"
        else:
            flow_kind = "flat"

        flow_links = [rt_link] if rt_link else []
        flows.append(
            make_flow(
                f"t3:{root_tid}",
                root_title,
                project=project_title,
                project_root=project_root,
                harness="t3",
                kind=flow_kind,
                started_at=root_started,
                updated_at=latest_updated or root_started,
                agents=all_agents,
                outputs=outputs,
                links=flow_links,
            )
        )

    if use_cache:
        tr_sigs = [
            (str(tr_path), _stat_mtime_size(str(tr_path)))
            for f in flows
            for o in f.get("outputs") or ()
            if o.get("kind") == "transcript" and isinstance(o.get("path"), str)
            for tr_path in (o["path"],)
        ]
        grok_sigs: list[tuple[str, tuple[int, int] | None]] = []
        seen_grok: set[str] = set()
        for f in flows:
            for o in f.get("outputs") or ():
                nid = (o.get("meta") or {}).get("nativeId") if isinstance(o, dict) else None
                if isinstance(nid, str) and nid in grok_map:
                    gp = str(grok_map[nid])
                    if gp not in seen_grok:
                        seen_grok.add(gp)
                        grok_sigs.append((gp, _stat_mtime_size(gp)))
        if len(_T3_FLOWS_CACHE) > 32:
            _T3_FLOWS_CACHE.clear()
        _T3_FLOWS_CACHE[cache_key] = (sig, tr_sigs, grok_sigs, flows)

    return flows


def load_subagent_detail(db_path: Path, subagent_id: str) -> dict[str, Any]:
    """Load full prompt and result for one subagent via indexed single-row lookup."""
    resolved_db = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if not subagent_id or not resolved_db.is_file():
        return {}
    try:
        conn = _open_ro(resolved_db)
    except sqlite3.Error:
        return {}
    try:
        row = conn.execute(
            """
            SELECT
                subagent_id,
                thread_id,
                child_thread_id,
                provider,
                driver,
                status,
                started_at,
                completed_at,
                updated_at,
                json_extract(payload_json, '$.title') AS title,
                json_extract(payload_json, '$.role') AS role,
                COALESCE(
                    json_extract(payload_json, '$.model'),
                    json_extract(payload_json, '$.modelSelection.model')
                ) AS model,
                json_extract(payload_json, '$.prompt') AS prompt,
                json_extract(payload_json, '$.result') AS result
            FROM orchestration_v2_projection_subagents
            WHERE subagent_id = ?
            LIMIT 1
            """,
            (subagent_id,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """
                SELECT
                    subagent_id,
                    thread_id,
                    child_thread_id,
                    provider,
                    driver,
                    status,
                    started_at,
                    completed_at,
                    updated_at,
                    json_extract(payload_json, '$.title') AS title,
                    json_extract(payload_json, '$.role') AS role,
                    COALESCE(
                        json_extract(payload_json, '$.model'),
                        json_extract(payload_json, '$.modelSelection.model')
                    ) AS model,
                    json_extract(payload_json, '$.prompt') AS prompt,
                    json_extract(payload_json, '$.result') AS result
                FROM orchestration_v2_projection_subagents
                WHERE child_thread_id = ?
                LIMIT 1
                """,
                (subagent_id,),
            ).fetchone()
        if row is None:
            return {}
        return {
            "subagentId": row["subagent_id"],
            "threadId": row["thread_id"],
            "childThreadId": row["child_thread_id"],
            "provider": row["driver"] or row["provider"],
            "model": row["model"],
            "title": row["title"],
            "role": row["role"] or "worker",
            "status": normalize_state(row["status"]),
            "startedAt": row["started_at"],
            "completedAt": row["completed_at"],
            "updatedAt": row["updated_at"],
            "prompt": row["prompt"],
            "result": row["result"],
        }
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def load_thread_messages(
    db_path: Path,
    thread_id: str,
    limit: int = 50,
    before_created_at: str | None = None,
) -> list[dict[str, Any]]:
    """Load recent messages for a thread using the (thread_id, created_at, message_id) index."""
    resolved_db = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if not thread_id or not resolved_db.is_file():
        return []
    lim = max(1, min(int(limit), 500))
    try:
        conn = _open_ro(resolved_db)
    except sqlite3.Error:
        return []
    try:
        if before_created_at:
            rows = conn.execute(
                """
                SELECT
                    message_id,
                    thread_id,
                    run_id,
                    node_id,
                    role,
                    streaming,
                    created_at,
                    updated_at,
                    json_extract(payload_json, '$.text') AS text,
                    json_extract(payload_json, '$.createdBy') AS created_by,
                    json_extract(payload_json, '$.creationSource') AS creation_source
                FROM orchestration_v2_projection_messages
                WHERE thread_id = ? AND created_at < ?
                ORDER BY created_at DESC, message_id DESC
                LIMIT ?
                """,
                (thread_id, before_created_at, lim),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    message_id,
                    thread_id,
                    run_id,
                    node_id,
                    role,
                    streaming,
                    created_at,
                    updated_at,
                    json_extract(payload_json, '$.text') AS text,
                    json_extract(payload_json, '$.createdBy') AS created_by,
                    json_extract(payload_json, '$.creationSource') AS creation_source
                FROM orchestration_v2_projection_messages
                WHERE thread_id = ?
                ORDER BY created_at DESC, message_id DESC
                LIMIT ?
                """,
                (thread_id, lim),
            ).fetchall()

        messages: list[dict[str, Any]] = []
        for row in reversed(rows):
            messages.append(
                {
                    "id": row["message_id"],
                    "threadId": row["thread_id"],
                    "runId": row["run_id"],
                    "nodeId": row["node_id"],
                    "role": row["role"],
                    "streaming": bool(row["streaming"]),
                    "createdAt": row["created_at"],
                    "updatedAt": row["updated_at"],
                    "text": row["text"] or "",
                    "createdBy": row["created_by"],
                    "creationSource": row["creation_source"],
                }
            )
        return messages
    except sqlite3.Error:
        return []
    finally:
        conn.close()
