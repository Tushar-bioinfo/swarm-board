"""Read-only T3 Code delegation tree adapter backed by ~/.t3/userdata/statev2.sqlite."""

from __future__ import annotations

from collections import Counter, OrderedDict
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
    parse_iso_ts,
    to_iso_utc,
)
from .agy_swarms import default_brain_roots as _agy_default_brain_roots

_T3_USERDATA_DIR = default_t3_userdata_dir()
DEFAULT_DB_PATH = _T3_USERDATA_DIR / "statev2.sqlite"
DEFAULT_ENV_ID_PATH = _T3_USERDATA_DIR / "environment-id"
DEFAULT_AGY_PROVIDERS_ROOT = _T3_USERDATA_DIR / "providers" / "antigravity"
DEFAULT_GROK_SESSIONS_ROOT = Path.home() / ".grok" / "sessions"

_DEFAULT_RATE_TABLE = RateTable()
_DEFAULT_USAGE_INDEX = UsageIndex()

_TRANSCRIPT_CACHE_MAX = 256
_GROK_USAGE_CACHE_MAX = 256
_RESULT_FILES_CACHE_MAX = 256
_SUBAGENT_RESULT_CACHE_MAX_ENTRIES = 64
_SUBAGENT_RESULT_CACHE_MAX_BYTES = 256 * 1024
_MAX_INLINE_RESULT_BYTES = 4_096
_T3_FLOWS_CACHE_MAX = 32
_ACTIVE_THREADS_CACHE_MAX = 32
_ACTIVE_RECENT_WINDOW_S = 172_800.0
_MISSING = object()

# Bounded LRU mtime+size caches so repeated polls stay fast and memory stays flat.
_TRANSCRIPT_CACHE: OrderedDict[str, tuple[int, int, dict[str, Any]]] = OrderedDict()
_GROK_USAGE_CACHE: OrderedDict[str, tuple[int, int, dict[str, Any]]] = OrderedDict()
_GROK_INDEX_CACHE: tuple[float, str, dict[str, Path]] = (0.0, "", {})
_BRAIN_INDEX_CACHE: tuple[float, tuple[str, ...], dict[str, Path]] = (0.0, (), {})
_RESULT_FILES_CACHE: OrderedDict[tuple[str, str, tuple[str, ...]], list[dict[str, Any]]] = OrderedDict()
_SUBAGENT_RESULT_CACHE: OrderedDict[tuple[str, int, str, str], tuple[str | None, int]] = OrderedDict()
_SUBAGENT_RESULT_CACHE_BYTES: int = 0
_T3_FLOWS_CACHE: OrderedDict[tuple[Any, ...], tuple[Any, ...]] = OrderedDict()
_ACTIVE_THREADS_CACHE: OrderedDict[tuple[Any, ...], tuple[Any, ...]] = OrderedDict()


def _lru_get(cache: OrderedDict[Any, Any], key: Any) -> Any:
    val = cache.get(key, _MISSING)
    if val is not _MISSING:
        cache.move_to_end(key)
    return val


def _lru_set(cache: OrderedDict[Any, Any], key: Any, val: Any, max_size: int) -> None:
    if key in cache:
        cache.move_to_end(key)
    cache[key] = val
    while len(cache) > max_size:
        cache.popitem(last=False)


def _subagent_result_cache_get(key: tuple[str, int, str, str]) -> Any:
    item = _SUBAGENT_RESULT_CACHE.get(key, _MISSING)
    if item is _MISSING:
        return _MISSING
    _SUBAGENT_RESULT_CACHE.move_to_end(key)
    return item[0]


def _subagent_result_cache_set(key: tuple[str, int, str, str], val: str | None) -> None:
    global _SUBAGENT_RESULT_CACHE_BYTES
    vbytes = len(val.encode("utf-8")) if isinstance(val, str) else 0
    if vbytes > _SUBAGENT_RESULT_CACHE_MAX_BYTES:
        return
    old = _SUBAGENT_RESULT_CACHE.pop(key, None)
    if old is not None:
        _SUBAGENT_RESULT_CACHE_BYTES = max(0, _SUBAGENT_RESULT_CACHE_BYTES - old[1])
    while _SUBAGENT_RESULT_CACHE and (
        len(_SUBAGENT_RESULT_CACHE) >= _SUBAGENT_RESULT_CACHE_MAX_ENTRIES
        or (_SUBAGENT_RESULT_CACHE_BYTES + vbytes) > _SUBAGENT_RESULT_CACHE_MAX_BYTES
    ):
        _, (_, evicted_bytes) = _SUBAGENT_RESULT_CACHE.popitem(last=False)
        _SUBAGENT_RESULT_CACHE_BYTES = max(0, _SUBAGENT_RESULT_CACHE_BYTES - evicted_bytes)
    _SUBAGENT_RESULT_CACHE[key] = (val, vbytes)
    _SUBAGENT_RESULT_CACHE_BYTES += vbytes

_MODEL_COST_ALIASES: dict[str, str] = {
    "gemini-pro-orchestrator-neon": "gemini-4-neon",
}

_LIVE_RUN_STATUSES = ("queued", "preparing", "starting", "running", "waiting")
_MAX_TRACE_ITEMS = 30
_MAX_FILE_OUTPUTS_PER_AGENT = 12
_RESULT_SNIPPET_CHARS = 400
_TURN_ITEM_TOOL_LABELS = {
    "command_execution": "command",
    "dynamic_tool": "tool",
    "file_change": "file_change",
    "file_search": "file_search",
    "web_search": "web_search",
}

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
    cached = _lru_get(_TRANSCRIPT_CACHE, key)
    if cached is not _MISSING and cached[0] == sig[0] and cached[1] == sig[1]:
        return cached[2]

    in_tok = 0
    cache_tok = 0
    out_tok = 0
    model: str | None = None
    tool_counts: Counter[str] = Counter()
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
                            tname = str(tc.get("name") or "").strip()
                            if tname:
                                tool_counts[tname] += 1
                            item = _summarize_tool_call(tc)
                            if item is not None:
                                last_activity = f"{item[0]}: {item[1]}" if len(item) > 1 else str(item[0])
    except OSError:
        return None

    trace = [[name, count] for name, count in tool_counts.most_common()]

    has_tokens = (in_tok + cache_tok + out_tok) > 0
    parsed = {
        "tokens": {"in": in_tok, "cache": cache_tok, "out": out_tok} if has_tokens else None,
        "model": model,
        "trace": trace,
        "last": last_activity,
    }
    _lru_set(_TRANSCRIPT_CACHE, key, (sig[0], sig[1], parsed), _TRANSCRIPT_CACHE_MAX)
    return parsed


def _parse_grok_usage(path: Path) -> dict[str, Any] | None:
    """Parse Grok ~/.grok/sessions/*/<sid>/usage.json, cached by (mtime_ns, size)."""
    key = str(path)
    try:
        st = os.stat(key)
    except OSError:
        return None
    sig = (st.st_mtime_ns, st.st_size)
    cached = _lru_get(_GROK_USAGE_CACHE, key)
    if cached is not _MISSING and cached[0] == sig[0] and cached[1] == sig[1]:
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
    _lru_set(_GROK_USAGE_CACHE, key, (sig[0], sig[1], parsed), _GROK_USAGE_CACHE_MAX)
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
    cached = _lru_get(_RESULT_FILES_CACHE, cache_key)
    if cached is not _MISSING:
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
    _lru_set(_RESULT_FILES_CACHE, cache_key, outputs, _RESULT_FILES_CACHE_MAX)
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
    int | None,
]:
    """Resolve real tokens, USD cost, trace, last activity, model, transcript path, cost_estimated, and cache_read."""
    tokens: dict[str, int] | None = None
    cost_usd: float | None = None
    trace: list[list[Any]] = []
    last_act: str | None = None
    resolved_model = model
    transcript_path: Path | None = None
    cost_estimated = True
    cache_read: int | None = None

    if not native_id:
        return tokens, cost_usd, trace, last_act, resolved_model, transcript_path, cost_estimated, cache_read

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
                    cache_read = tokens["cache"]
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
                if tokens is not None:
                    cache_read = parsed_g["cacheRead"]
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
            cache_read = rec.get("cacheRead")
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

    return tokens, cost_usd, trace, last_act, resolved_model, transcript_path, cost_estimated, cache_read


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


def _refresh_active_thread_durations(
    items: list[dict[str, Any]],
    now_ts: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if item.get("isLive"):
            copy_item = dict(item)
            copy_item["durS"] = compute_dur_s(
                copy_item.get("startedAt"),
                None,
                is_running=True,
                now_ts=now_ts,
            )
            out.append(copy_item)
        else:
            out.append(item)
    return out


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table_name})")}
    except sqlite3.Error:
        return set()


def _thread_select_sql(thread_cols: set[str]) -> str:
    """Build a schema-resilient SELECT projection for orchestration_v2_projection_threads."""
    parts = [
        "thread_id",
        "project_id" if "project_id" in thread_cols else "NULL AS project_id",
        "title" if "title" in thread_cols else "NULL AS title",
        "default_provider" if "default_provider" in thread_cols else "NULL AS default_provider",
        "provider_instance_id" if "provider_instance_id" in thread_cols else "NULL AS provider_instance_id",
        "active_provider_thread_id" if "active_provider_thread_id" in thread_cols else "NULL AS active_provider_thread_id",
        "created_at" if "created_at" in thread_cols else "NULL AS created_at",
        "updated_at" if "updated_at" in thread_cols else "NULL AS updated_at",
        "archived_at" if "archived_at" in thread_cols else "NULL AS archived_at",
        "deleted_at" if "deleted_at" in thread_cols else "NULL AS deleted_at",
    ]
    if "payload_json" in thread_cols:
        parts.extend(
            [
                "json_extract(payload_json, '$.lineage.rootThreadId') AS root_thread_id",
                "json_extract(payload_json, '$.lineage.parentThreadId') AS parent_thread_id",
                "json_extract(payload_json, '$.lineage.relationshipToParent') AS rel_to_parent",
                "json_extract(payload_json, '$.modelSelection.model') AS model",
                "json_extract(payload_json, '$.worktreePath') AS worktree_path",
                "json_extract(payload_json, '$.settledAt') AS settled_at",
            ]
        )
    else:
        parts.extend(
            [
                "NULL AS root_thread_id",
                "NULL AS parent_thread_id",
                "NULL AS rel_to_parent",
                "NULL AS model",
                "NULL AS worktree_path",
                "NULL AS settled_at",
            ]
        )
    return ", ".join(parts)


def _fetch_live_runs(
    conn: sqlite3.Connection,
    run_cols: set[str],
) -> tuple[dict[str, dict[str, Any]], tuple[tuple[str, str, str], ...]]:
    """Fetch live runs and a deterministic watermark tuple."""
    if "thread_id" not in run_cols or "status" not in run_cols:
        return {}, ()
    run_id_expr = "run_id" if "run_id" in run_cols else "CAST(rowid AS TEXT) AS run_id"
    req_expr = "requested_at" if "requested_at" in run_cols else "NULL AS requested_at"
    rows = conn.execute(
        f"""
        SELECT {run_id_expr}, thread_id, status, {req_expr}
        FROM orchestration_v2_projection_runs
        WHERE status IN ('queued', 'preparing', 'starting', 'running', 'waiting')
        ORDER BY thread_id ASC, run_id ASC
        """
    ).fetchall()
    live_info: dict[str, dict[str, Any]] = {}
    sig_items: list[tuple[str, str, str]] = []
    for r in rows:
        tid = str(r["thread_id"] or "")
        st = str(r["status"] or "")
        rid = str(r["run_id"] or "")
        sig_items.append((tid, st, rid))
        if tid and (tid not in live_info or st == "running"):
            live_info[tid] = {
                "status": st,
                "run_id": rid,
                "requested_at": r["requested_at"],
            }
    return live_info, tuple(sig_items)


def _query_subagent_watermark(conn: sqlite3.Connection, sub_cols: set[str]) -> tuple[int, str]:
    if not sub_cols:
        return (0, "")
    upd_expr = "COALESCE(MAX(updated_at), '')" if "updated_at" in sub_cols else "''"
    row = conn.execute(
        f"SELECT COUNT(*), {upd_expr} FROM orchestration_v2_projection_subagents"
    ).fetchone()
    return (int(row[0] or 0), str(row[1] or "")) if row else (0, "")


def _query_roots_watermark(
    conn: sqlite3.Connection,
    thread_cols: set[str],
    root_ids: tuple[str, ...],
) -> tuple[int, str, int, int]:
    if not root_ids or "thread_id" not in thread_cols:
        return (0, "", 0, 0)
    upd_expr = "COALESCE(MAX(updated_at), '')" if "updated_at" in thread_cols else "''"
    del_expr = "SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END)" if "deleted_at" in thread_cols else "0"
    arch_expr = "SUM(CASE WHEN archived_at IS NOT NULL THEN 1 ELSE 0 END)" if "archived_at" in thread_cols else "0"
    total_cnt = 0
    max_upd = ""
    del_cnt = 0
    arch_cnt = 0
    for i in range(0, len(root_ids), 400):
        chunk = root_ids[i : i + 400]
        ph = ",".join("?" * len(chunk))
        row = conn.execute(
            f"""
            SELECT COUNT(*), {upd_expr}, {del_expr}, {arch_expr}
            FROM orchestration_v2_projection_threads
            WHERE thread_id IN ({ph})
            """,
            chunk,
        ).fetchone()
        if row:
            total_cnt += int(row[0] or 0)
            upd = str(row[1] or "")
            if upd > max_upd:
                max_upd = upd
            del_cnt += int(row[2] or 0)
            arch_cnt += int(row[3] or 0)
    return (total_cnt, max_upd, del_cnt, arch_cnt)


def _compute_roots_watermark_from_map(
    threads_by_id: dict[str, dict[str, Any]],
    thread_cols: set[str],
    root_ids: tuple[str, ...],
) -> tuple[int, str, int, int]:
    if not root_ids or "thread_id" not in thread_cols:
        return (0, "", 0, 0)
    has_upd = "updated_at" in thread_cols
    has_del = "deleted_at" in thread_cols
    has_arch = "archived_at" in thread_cols
    total_cnt = 0
    max_upd = ""
    del_cnt = 0
    arch_cnt = 0
    for rtid in root_ids:
        t = threads_by_id.get(rtid)
        if t is None:
            continue
        total_cnt += 1
        if has_upd:
            upd = str(t.get("updated_at") or "")
            if upd > max_upd:
                max_upd = upd
        if has_del and t.get("deleted_at") is not None:
            del_cnt += 1
        if has_arch and t.get("archived_at") is not None:
            arch_cnt += 1
    return (total_cnt, max_upd, del_cnt, arch_cnt)


def _query_active_threads_watermark(
    conn: sqlite3.Connection,
    thread_cols: set[str],
) -> tuple[int, str]:
    if not thread_cols:
        return (0, "")
    where_parts = []
    if "deleted_at" in thread_cols:
        where_parts.append("deleted_at IS NULL")
    if "archived_at" in thread_cols:
        where_parts.append("archived_at IS NULL")
    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    upd_expr = "COALESCE(MAX(updated_at), '')" if "updated_at" in thread_cols else "''"
    row = conn.execute(
        f"SELECT COUNT(*), {upd_expr} FROM orchestration_v2_projection_threads {where_sql}"
    ).fetchone()
    return (int(row[0] or 0), str(row[1] or "")) if row else (0, "")


def _collect_t3_cache_sigs(
    flows: list[dict[str, Any]],
    grok_map: dict[str, Path],
) -> tuple[list[tuple[str, tuple[int, int] | None]], list[tuple[str, tuple[int, int] | None]]]:
    tr_sigs: list[tuple[str, tuple[int, int] | None]] = []
    seen_tr: set[str] = set()
    grok_sigs: list[tuple[str, tuple[int, int] | None]] = []
    seen_grok: set[str] = set()
    for f in flows:
        for o in f.get("outputs") or ():
            if not isinstance(o, dict):
                continue
            if o.get("kind") == "transcript" and isinstance(o.get("path"), str):
                trp = o["path"]
                if trp not in seen_tr:
                    seen_tr.add(trp)
                    tr_sigs.append((trp, _stat_mtime_size(trp)))
            nid = (o.get("meta") or {}).get("nativeId")
            if isinstance(nid, str) and nid in grok_map:
                gp = str(grok_map[nid])
                if gp not in seen_grok:
                    seen_grok.add(gp)
                    grok_sigs.append((gp, _stat_mtime_size(gp)))
    return tr_sigs, grok_sigs


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
    """Discover recent T3 delegation trees and standalone live threads as normalized Flow objects."""
    resolved_db = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if not resolved_db.is_file():
        return []

    resolved_env_id = _read_env_id(env_id)
    rt = rate_table if rate_table is not None else _DEFAULT_RATE_TABLE
    ui = usage_index if usage_index is not None else _DEFAULT_USAGE_INDEX
    b_roots = _discover_brain_roots(brain_roots)
    g_root = grok_sessions_root if grok_sessions_root is not None else DEFAULT_GROK_SESSIONS_ROOT

    use_cache = now_ts is None
    effective_now = now_ts if now_ts is not None else time.time()
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
    db_sig = _stat_ino_sig(resolved_db)
    wal_sig = _stat_ino_sig(wal_path)
    rt_sig = rt.cache_signature()
    ui_sig = _stat_ino_sig(ui.path)
    fast_sig = (db_sig, wal_sig, rt_sig, ui_sig)
    meta_sig = (db_sig[0] if db_sig else None, rt_sig, ui_sig)

    cached = _lru_get(_T3_FLOWS_CACHE, cache_key) if use_cache else _MISSING
    if cached is not _MISSING:
        c_fast_sig, c_meta_sig, c_watermark, c_tracked_roots, c_tr_sigs, c_grok_sigs, c_flows = cached
        if (
            c_fast_sig == fast_sig
            and all(_stat_mtime_size(p) == s for p, s in c_tr_sigs)
            and all(_stat_mtime_size(p) == s for p, s in c_grok_sigs)
        ):
            return _refresh_running_durations(c_flows, effective_now)

    try:
        conn = _open_ro(resolved_db)
    except sqlite3.Error:
        return []

    try:
        thread_cols = _table_columns(conn, "orchestration_v2_projection_threads")
        run_cols = _table_columns(conn, "orchestration_v2_projection_runs")
        sub_cols = _table_columns(conn, "orchestration_v2_projection_subagents")
        pt_cols = _table_columns(conn, "orchestration_v2_projection_provider_threads")
        proj_cols = _table_columns(conn, "projection_projects")

        live_runs_info, live_runs_sig = _fetch_live_runs(conn, run_cols)
        sub_count, sub_max_upd = _query_subagent_watermark(conn, sub_cols)

        # Tier-2 watermark check: skips full scan when WAL/DB moved due to unrelated writes.
        if cached is not _MISSING:
            _, c_meta_sig, c_watermark, c_tracked_roots, c_tr_sigs, c_grok_sigs, c_flows = cached
            if c_meta_sig == meta_sig:
                roots_wm = _query_roots_watermark(conn, thread_cols, c_tracked_roots)
                watermark = (live_runs_sig, sub_count, sub_max_upd, roots_wm)
                if (
                    watermark == c_watermark
                    and all(_stat_mtime_size(p) == s for p, s in c_tr_sigs)
                    and all(_stat_mtime_size(p) == s for p, s in c_grok_sigs)
                ):
                    _lru_set(
                        _T3_FLOWS_CACHE,
                        cache_key,
                        (fast_sig, meta_sig, watermark, c_tracked_roots, c_tr_sigs, c_grok_sigs, c_flows),
                        _T3_FLOWS_CACHE_MAX,
                    )
                    return _refresh_running_durations(c_flows, effective_now)

        # Phase 1: Lightweight subagent scan WITHOUT json_extract(payload_json, ...).
        if sub_cols:
            slim_sub_rows = conn.execute(
                """
                SELECT
                    subagent_id,
                    thread_id,
                    child_thread_id,
                    status,
                    started_at,
                    completed_at,
                    updated_at
                FROM orchestration_v2_projection_subagents
                ORDER BY COALESCE(started_at, updated_at) ASC, subagent_id ASC
                """
            ).fetchall()
        else:
            slim_sub_rows = []

        if not slim_sub_rows and not live_runs_info:
            if use_cache:
                watermark = (live_runs_sig, sub_count, sub_max_upd, (0, "", 0, 0))
                _lru_set(
                    _T3_FLOWS_CACHE,
                    cache_key,
                    (fast_sig, meta_sig, watermark, (), [], [], []),
                    _T3_FLOWS_CACHE_MAX,
                )
            return []

        brain_map = _get_brain_transcript_map(b_roots) if b_roots else {}
        grok_map = _get_grok_usage_map(g_root)

        # 3. Load thread lineage + metadata for subagents AND live runs (and walk up ancestors).
        referenced_tids: set[str] = set(live_runs_info.keys())
        for s in slim_sub_rows:
            if s["thread_id"]:
                referenced_tids.add(s["thread_id"])
            if s["child_thread_id"]:
                referenced_tids.add(s["child_thread_id"])

        threads_by_id: dict[str, dict[str, Any]] = {}
        thread_select = _thread_select_sql(thread_cols) if thread_cols else ""

        def _fetch_threads(tids: set[str]) -> None:
            if not thread_select:
                return
            missing = [t for t in tids if t and t not in threads_by_id]
            for i in range(0, len(missing), 400):
                chunk = missing[i : i + 400]
                ph = ",".join("?" * len(chunk))
                q = f"""
                    SELECT {thread_select}
                    FROM orchestration_v2_projection_threads
                    WHERE thread_id IN ({ph})
                """
                for row in conn.execute(q, chunk):
                    threads_by_id[row["thread_id"]] = dict(row)

        _fetch_threads(referenced_tids)
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

        live_runs: dict[str, str] = {tid: info["status"] for tid, info in live_runs_info.items()}

        # 4. Group slim subagents under rootThreadId; prioritize live/active trees before truncation.
        slim_trees_subs: dict[str, list[sqlite3.Row]] = {}
        for s in slim_sub_rows:
            root_tid = _resolve_root_thread_id(s["thread_id"])
            slim_trees_subs.setdefault(root_tid, []).append(s)

        tree_activity: list[tuple[int, str, str]] = []
        for root_tid, subs in slim_trees_subs.items():
            root_info = threads_by_id.get(root_tid)
            if root_info and root_info.get("deleted_at"):
                continue
            latest = (root_info.get("updated_at") if root_info else "") or ""
            is_live_tree = root_tid in live_runs
            for s in subs:
                for ts_col in (s["updated_at"], s["completed_at"], s["started_at"]):
                    if ts_col and ts_col > latest:
                        latest = ts_col
                if not is_live_tree:
                    if s["thread_id"] in live_runs or (s["child_thread_id"] and s["child_thread_id"] in live_runs):
                        is_live_tree = True
                    elif normalize_state(s["status"]) in ("running", "waiting", "queued"):
                        is_live_tree = True
            tree_activity.append((1 if is_live_tree else 0, latest, root_tid))

        tree_activity.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected_roots = [rtid for _, _, rtid in tree_activity[: max(1, int(limit_trees))]]
        selected_roots_set = set(selected_roots)

        # Standalone live threads whose root is not already covered by a selected delegation tree.
        standalone_live_roots: list[str] = []
        standalone_live_status: dict[str, str] = {}
        standalone_live_requested_at: dict[str, str | None] = {}
        for tid, run_info in live_runs_info.items():
            tinfo = threads_by_id.get(tid)
            if tinfo is None or tinfo.get("deleted_at") or tinfo.get("archived_at"):
                continue
            root_tid = _resolve_root_thread_id(tid)
            if root_tid in selected_roots_set:
                continue
            rinfo = threads_by_id.get(root_tid)
            if rinfo is None or rinfo.get("deleted_at") or rinfo.get("archived_at"):
                continue
            if root_tid not in standalone_live_status:
                standalone_live_roots.append(root_tid)
                standalone_live_status[root_tid] = run_info["status"]
                standalone_live_requested_at[root_tid] = run_info.get("requested_at")
            elif run_info["status"] == "running" or tid == root_tid:
                standalone_live_status[root_tid] = run_info["status"]
                if run_info.get("requested_at"):
                    standalone_live_requested_at[root_tid] = run_info["requested_at"]

        standalone_live_roots.sort(
            key=lambda rtid: (threads_by_id.get(rtid) or {}).get("updated_at") or "",
            reverse=True,
        )

        if not selected_roots and not standalone_live_roots:
            if use_cache:
                watermark = (live_runs_sig, sub_count, sub_max_upd, (0, "", 0, 0))
                _lru_set(
                    _T3_FLOWS_CACHE,
                    cache_key,
                    (fast_sig, meta_sig, watermark, (), [], [], []),
                    _T3_FLOWS_CACHE_MAX,
                )
            return []

        # Phase 2: Extract payload_json fields ONLY for subagents of selected_roots.
        selected_sub_ids: list[str] = []
        for root_tid in selected_roots:
            for s in slim_trees_subs[root_tid]:
                selected_sub_ids.append(s["subagent_id"])

        full_sub_by_id: dict[str, sqlite3.Row] = {}
        for i in range(0, len(selected_sub_ids), 400):
            chunk = selected_sub_ids[i : i + 400]
            ph = ",".join("?" * len(chunk))
            for row in conn.execute(
                f"""
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
                WHERE subagent_id IN ({ph})
                """,
                chunk,
            ):
                full_sub_by_id[row["subagent_id"]] = row

        trees_subs: dict[str, list[sqlite3.Row]] = {}
        sub_updated_by_id: dict[str, str] = {}
        selected_thread_ids: set[str] = set(selected_roots) | set(standalone_live_roots)
        selected_pt_ids: set[str] = set()
        selected_project_ids: set[str] = set()

        for root_tid in selected_roots:
            rinfo = threads_by_id.get(root_tid)
            if rinfo:
                if rinfo.get("project_id"):
                    selected_project_ids.add(rinfo["project_id"])
                if rinfo.get("active_provider_thread_id"):
                    selected_pt_ids.add(rinfo["active_provider_thread_id"])
            tree_full: list[sqlite3.Row] = []
            for slim_s in slim_trees_subs[root_tid]:
                sid = slim_s["subagent_id"]
                s = full_sub_by_id.get(sid)
                if s is None:
                    continue
                tree_full.append(s)
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
            trees_subs[root_tid] = tree_full

        for root_tid in standalone_live_roots:
            rinfo = threads_by_id.get(root_tid)
            if rinfo:
                if rinfo.get("project_id"):
                    selected_project_ids.add(rinfo["project_id"])
                if rinfo.get("active_provider_thread_id"):
                    selected_pt_ids.add(rinfo["active_provider_thread_id"])

        # Fetch projects.
        projects_by_id: dict[str, dict[str, Any]] = {}
        if selected_project_ids and proj_cols:
            pids = list(selected_project_ids)
            for i in range(0, len(pids), 400):
                chunk = pids[i : i + 400]
                ph = ",".join("?" * len(chunk))
                for row in conn.execute(
                    f"SELECT project_id, title, workspace_root FROM projection_projects WHERE project_id IN ({ph})",
                    chunk,
                ):
                    projects_by_id[row["project_id"]] = dict(row)

        # Fetch provider_threads nativeId mappings for selected trees and standalone live threads.
        pt_native_by_ptid: dict[str, tuple[str | None, str | None]] = {}
        pt_native_by_tid: dict[str, tuple[str | None, str | None]] = {}

        if selected_pt_ids and pt_cols:
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

        if selected_thread_ids and pt_cols:
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

        # Fetch full $.result ONLY for uncached selected trees' subagents (bounded LRU <=128 entries / 2 MB).
        db_ino = db_sig[0] if db_sig else 0
        db_key = str(resolved_db)

        full_results: dict[str, str] = {}
        uncached_sub_ids: list[str] = []
        for sid in selected_sub_ids:
            ckey = (db_key, db_ino, sid, sub_updated_by_id.get(sid, ""))
            cached_res = _subagent_result_cache_get(ckey)
            if cached_res is not _MISSING:
                if isinstance(cached_res, str) and cached_res.strip():
                    full_results[sid] = cached_res
            else:
                uncached_sub_ids.append(sid)

        for i in range(0, len(uncached_sub_ids), 32):
            chunk = uncached_sub_ids[i : i + 32]
            ph = ",".join("?" * len(chunk))
            seen_in_chunk: set[str] = set()
            for row in conn.execute(
                f"""
                SELECT subagent_id, json_extract(payload_json, '$.result') AS full_result
                FROM orchestration_v2_projection_subagents
                WHERE subagent_id IN ({ph})
                  AND json_extract(payload_json, '$.result') IS NOT NULL
                """,
                chunk,
            ):
                sid = row["subagent_id"]
                val = row["full_result"]
                if isinstance(val, str) and val.strip():
                    seen_in_chunk.add(sid)
                    full_results[sid] = val
                    _subagent_result_cache_set((db_key, db_ino, sid, sub_updated_by_id.get(sid, "")), val)
            for sid in chunk:
                if sid not in seen_in_chunk:
                    _subagent_result_cache_set((db_key, db_ino, sid, sub_updated_by_id.get(sid, "")), None)

        # Fetch tool call counts per thread via covering index (type, status) without reading payload_json.
        turn_item_trace_by_tid: dict[str, list[list[Any]]] = {}
        if selected_thread_ids:
            try:
                tids = list(selected_thread_ids)
                raw_counts: dict[str, list[list[Any]]] = {}
                for i in range(0, len(tids), 400):
                    chunk = tids[i : i + 400]
                    ph = ",".join("?" * len(chunk))
                    for row in conn.execute(
                        f"""
                        SELECT thread_id, type, COUNT(*) AS cnt
                        FROM orchestration_v2_projection_turn_items
                        WHERE thread_id IN ({ph})
                          AND type IN ('command_execution', 'file_change', 'file_search', 'web_search', 'dynamic_tool')
                        GROUP BY thread_id, type
                        """,
                        chunk,
                    ):
                        label = _TURN_ITEM_TOOL_LABELS.get(row["type"])
                        cnt = int(row["cnt"] or 0)
                        if label and cnt > 0:
                            raw_counts.setdefault(row["thread_id"], []).append([label, cnt])
                for tid, items in raw_counts.items():
                    items.sort(key=lambda x: (-x[1], x[0]))
                    turn_item_trace_by_tid[tid] = items
            except sqlite3.Error:
                turn_item_trace_by_tid = {}
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    # 6. Assemble normalized Flow objects for each selected delegation tree.
    flows: list[dict[str, Any]] = []
    for root_tid in selected_roots:
        subs = trees_subs.get(root_tid, [])
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

        root_tokens, root_cost, root_trace, root_last, root_res_model, root_tr_path, root_cost_est, root_cache_read = _resolve_telemetry(
            provider=root_drv or root_provider,
            model=root_model,
            native_id=root_nid,
            rate_table=rt,
            usage_index=ui,
            brain_map=brain_map,
            grok_map=grok_map,
        )
        if not root_trace:
            root_trace = turn_item_trace_by_tid.get(root_tid, [])

        seen_native_ids: set[str] = set()
        seen_trace_native_ids: set[str] = set()
        seen_usage_keys: set[tuple[tuple[tuple[str, int], ...], float | None]] = set()
        if root_nid and root_trace:
            seen_trace_native_ids.add(root_nid)
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
            itok, icost, itrace, ilast, ires_model, itr_path, icost_est, icache_read = _resolve_telemetry(
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
            if i_deduped:
                icache_read = None
                itrace = []
            elif inid and inid in seen_trace_native_ids:
                itrace = []
            elif not itrace:
                itrace = turn_item_trace_by_tid.get(itid, [])
            if inid and itrace:
                seen_trace_native_ids.add(inid)
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
                dur_s=compute_dur_s(istart, iend, is_running=(istate == "running"), now_ts=effective_now),
                tokens=itok,
                cost_usd=icost,
                trace=itrace,
                last=ilast,
                links=ilinks,
                output_ids=iout_ids,
            )
            i_agent["costEstimated"] = icost_est
            if itok is not None and icache_read is not None:
                i_agent["cacheRead"] = icache_read
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
            dur_s = compute_dur_s(started_at, ended_at, is_running=(norm_state == "running"), now_ts=effective_now)

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

            tokens, cost_usd, trace, last_act, resolved_model, tr_path, cost_est, cache_read = _resolve_telemetry(
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
            if usage_deduped:
                cache_read = None
                trace = []
            elif native_id and native_id in seen_trace_native_ids:
                trace = []
            elif not trace and ctid and agent_id == ctid:
                trace = turn_item_trace_by_tid.get(ctid, [])
            if native_id and trace:
                seen_trace_native_ids.add(native_id)

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
                res_bytes = len(full_res.encode("utf-8"))
                res_body = full_res if res_bytes <= _MAX_INLINE_RESULT_BYTES else None
                outputs.append(
                    make_output(
                        res_out_id,
                        "result",
                        f"{name} result",
                        by=agent_id,
                        at=ended_at or s["updated_at"],
                        body=res_body,
                        size=res_bytes,
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
            if tokens is not None and cache_read is not None:
                sub_agent["cacheRead"] = cache_read
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
        root_dur = compute_dur_s(root_started, root_ended, is_running=(root_state == "running"), now_ts=effective_now)

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
        if root_tokens is not None and root_cache_read is not None:
            root_agent["cacheRead"] = root_cache_read
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

    # 7. Assemble normalized Flow objects for standalone live T3 threads.
    for root_tid in standalone_live_roots:
        root_info = threads_by_id.get(root_tid) or {}
        project_id = root_info.get("project_id")
        proj_info = projects_by_id.get(project_id) if project_id else None
        project_root = proj_info.get("workspace_root") if proj_info else None
        project_title = (proj_info.get("title") if proj_info else None) or ""

        root_title = root_info.get("title") or root_tid
        root_model = root_info.get("model")
        root_provider = root_info.get("default_provider") or root_info.get("provider_instance_id")
        root_nid, root_drv = pt_native_by_tid.get(root_tid, (None, None))
        if not root_nid and root_info.get("active_provider_thread_id"):
            root_nid, root_drv = pt_native_by_ptid.get(root_info["active_provider_thread_id"], (None, None))

        root_tokens, root_cost, root_trace, root_last, root_res_model, root_tr_path, root_cost_est, root_cache_read = _resolve_telemetry(
            provider=root_drv or root_provider,
            model=root_model,
            native_id=root_nid,
            rate_table=rt,
            usage_index=ui,
            brain_map=brain_map,
            grok_map=grok_map,
        )
        if not root_trace:
            root_trace = turn_item_trace_by_tid.get(root_tid, [])

        root_state = normalize_state(standalone_live_status[root_tid])
        root_started = root_info.get("created_at") or standalone_live_requested_at.get(root_tid)
        latest_updated = root_info.get("updated_at") or root_started
        root_ended = None if root_state == "running" else latest_updated
        root_dur = compute_dur_s(root_started, root_ended, is_running=(root_state == "running"), now_ts=effective_now)

        root_lab = normalize_lab(root_res_model, root_drv or root_provider)
        if root_lab == "other" and not root_res_model and not root_provider:
            root_lab = "t3"

        root_links = []
        rt_link = _t3_thread_link(resolved_env_id, root_tid)
        if rt_link:
            root_links.append(rt_link)
        rs_link = make_session_link(root_drv or root_provider, root_nid)
        if rs_link:
            root_links.append(rs_link)

        root_tr_id = f"tr-{root_tid}"
        outputs = [
            make_output(
                root_tr_id,
                "transcript",
                f"{root_title} transcript",
                path=str(root_tr_path) if root_tr_path else None,
                by=root_tid,
                at=root_ended or root_started,
                meta={"threadId": root_tid, "nativeId": root_nid},
            )
        ]

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
            output_ids=[root_tr_id],
        )
        root_agent["costEstimated"] = root_cost_est
        if root_tokens is not None and root_cache_read is not None:
            root_agent["cacheRead"] = root_cache_read
        root_raw_title = root_info.get("title") or (
            root_title if not _PURE_UUID_RE.match(str(root_title)) else None
        )
        root_agent["_labelHint"] = {
            "prompt": None,
            "slug": None,
            "planTask": None,
            "rawTitle": _bound_hint(root_raw_title, _LABEL_HINT_MAX_CHARS),
        }

        flow_links = [rt_link] if rt_link else []
        flows.append(
            make_flow(
                f"t3:{root_tid}",
                root_title,
                project=project_title,
                project_root=project_root,
                harness="t3",
                kind="thread",
                started_at=root_started,
                updated_at=latest_updated or root_started,
                agents=[root_agent],
                outputs=outputs,
                links=flow_links,
            )
        )

    if use_cache:
        tr_sigs, grok_sigs = _collect_t3_cache_sigs(flows, grok_map)
        tracked_roots = tuple(dict.fromkeys(list(selected_roots) + list(standalone_live_roots)))
        roots_wm = _compute_roots_watermark_from_map(threads_by_id, thread_cols, tracked_roots)
        watermark = (live_runs_sig, sub_count, sub_max_upd, roots_wm)
        _lru_set(
            _T3_FLOWS_CACHE,
            cache_key,
            (fast_sig, meta_sig, watermark, tracked_roots, tr_sigs, grok_sigs, flows),
            _T3_FLOWS_CACHE_MAX,
        )

    return flows


def discover_active_threads(
    db_path: Path | None = None,
    *,
    env_id: str | None = None,
    limit_recent: int = 20,
    rate_table: RateTable | None = None,
    usage_index: UsageIndex | None = None,
    brain_roots: list[Path] | None = None,
    grok_sessions_root: Path | None = None,
    now_ts: float | None = None,
) -> list[dict[str, Any]]:
    """Discover live T3 threads and recent unsettled top-level threads (last 48h)."""
    resolved_db = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if not resolved_db.is_file():
        return []

    resolved_env_id = _read_env_id(env_id)
    rt = rate_table if rate_table is not None else _DEFAULT_RATE_TABLE
    ui = usage_index if usage_index is not None else _DEFAULT_USAGE_INDEX
    b_roots = _discover_brain_roots(brain_roots)
    g_root = grok_sessions_root if grok_sessions_root is not None else DEFAULT_GROK_SESSIONS_ROOT

    use_cache = now_ts is None
    effective_now = float(now_ts) if now_ts is not None else time.time()
    lim_recent = max(0, int(limit_recent))
    cache_key = (
        str(resolved_db),
        resolved_env_id,
        lim_recent,
        str(rt.path),
        str(ui.path),
        str(g_root),
        tuple(str(p) for p in b_roots),
    )
    wal_path = f"{resolved_db}-wal"
    db_sig = _stat_ino_sig(resolved_db)
    wal_sig = _stat_ino_sig(wal_path)
    rt_sig = rt.cache_signature()
    ui_sig = _stat_ino_sig(ui.path)
    fast_sig = (db_sig, wal_sig, rt_sig, ui_sig)
    meta_sig = (db_sig[0] if db_sig else None, rt_sig, ui_sig)

    cached = _lru_get(_ACTIVE_THREADS_CACHE, cache_key) if use_cache else _MISSING
    if cached is not _MISSING:
        c_fast_sig, c_meta_sig, c_watermark, c_tr_sigs, c_oldest_recent_ts, c_items = cached
        window_ok = c_oldest_recent_ts is None or (effective_now - c_oldest_recent_ts) <= _ACTIVE_RECENT_WINDOW_S
        if (
            c_fast_sig == fast_sig
            and window_ok
            and all(_stat_mtime_size(p) == s for p, s in c_tr_sigs)
        ):
            return _refresh_active_thread_durations(c_items, effective_now)

    try:
        conn = _open_ro(resolved_db)
    except sqlite3.Error:
        return []

    try:
        thread_cols = _table_columns(conn, "orchestration_v2_projection_threads")
        if not thread_cols:
            return []
        run_cols = _table_columns(conn, "orchestration_v2_projection_runs")
        sub_cols = _table_columns(conn, "orchestration_v2_projection_subagents")
        pt_cols = _table_columns(conn, "orchestration_v2_projection_provider_threads")
        proj_cols = _table_columns(conn, "projection_projects")

        live_runs_info, live_runs_sig = _fetch_live_runs(conn, run_cols)
        sub_count, sub_max_upd = _query_subagent_watermark(conn, sub_cols)
        threads_wm = _query_active_threads_watermark(conn, thread_cols)
        watermark = (live_runs_sig, sub_count, sub_max_upd, threads_wm)

        if cached is not _MISSING:
            _, c_meta_sig, c_watermark, c_tr_sigs, c_oldest_recent_ts, c_items = cached
            window_ok = c_oldest_recent_ts is None or (effective_now - c_oldest_recent_ts) <= _ACTIVE_RECENT_WINDOW_S
            if (
                c_meta_sig == meta_sig
                and c_watermark == watermark
                and window_ok
                and all(_stat_mtime_size(p) == s for p, s in c_tr_sigs)
            ):
                _lru_set(
                    _ACTIVE_THREADS_CACHE,
                    cache_key,
                    (fast_sig, meta_sig, watermark, c_tr_sigs, c_oldest_recent_ts, c_items),
                    _ACTIVE_THREADS_CACHE_MAX,
                )
                return _refresh_active_thread_durations(c_items, effective_now)

        brain_map = _get_brain_transcript_map(b_roots) if b_roots else {}
        grok_map = _get_grok_usage_map(g_root)

        thread_select = _thread_select_sql(thread_cols)
        threads_by_id: dict[str, dict[str, Any]] = {}

        def _fetch_threads(tids: set[str]) -> None:
            missing = [t for t in tids if t and t not in threads_by_id]
            for i in range(0, len(missing), 400):
                chunk = missing[i : i + 400]
                ph = ",".join("?" * len(chunk))
                for row in conn.execute(
                    f"SELECT {thread_select} FROM orchestration_v2_projection_threads WHERE thread_id IN ({ph})",
                    chunk,
                ):
                    threads_by_id[row["thread_id"]] = dict(row)

        # 1. Fetch all threads referenced by live runs.
        if live_runs_info:
            _fetch_threads(set(live_runs_info.keys()))

        # 2. Fetch up to `limit_recent` recent unsettled top-level threads updated within 48h.
        cutoff_ts = effective_now - _ACTIVE_RECENT_WINDOW_S
        recent_top_ids: list[str] = []
        oldest_recent_ts: float | None = None
        if lim_recent > 0:
            where_parts: list[str] = []
            params: list[Any] = []
            if "deleted_at" in thread_cols:
                where_parts.append("deleted_at IS NULL")
            if "archived_at" in thread_cols:
                where_parts.append("archived_at IS NULL")
            if "updated_at" in thread_cols:
                probe_iso = to_iso_utc(cutoff_ts - 86_400.0)
                if probe_iso:
                    where_parts.append("updated_at >= ?")
                    params.append(probe_iso)
            if "payload_json" in thread_cols:
                where_parts.append("json_extract(payload_json, '$.settledAt') IS NULL")
                where_parts.append("json_extract(payload_json, '$.lineage.parentThreadId') IS NULL")
            where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
            order_sql = "ORDER BY updated_at DESC" if "updated_at" in thread_cols else ""
            fetch_lim = lim_recent * 3 + 20
            q_recent = f"SELECT {thread_select} FROM orchestration_v2_projection_threads {where_sql} {order_sql} LIMIT ?"
            params.append(fetch_lim)
            recent_rows: list[tuple[float, dict[str, Any]]] = []
            for row in conn.execute(q_recent, params):
                d = dict(row)
                if d.get("deleted_at") or d.get("archived_at") or d.get("settled_at"):
                    continue
                if d.get("parent_thread_id"):
                    continue
                upd_ts = parse_iso_ts(d.get("updated_at") or d.get("created_at"))
                if upd_ts is None or upd_ts < cutoff_ts:
                    continue
                threads_by_id[d["thread_id"]] = d
                recent_rows.append((upd_ts, d))
            recent_rows.sort(key=lambda pair: pair[0], reverse=True)
            for upd_ts, d in recent_rows[:lim_recent]:
                recent_top_ids.append(d["thread_id"])
                if oldest_recent_ts is None or upd_ts < oldest_recent_ts:
                    oldest_recent_ts = upd_ts

        # Collect included thread IDs (all non-deleted/non-archived live threads + recent top-level threads).
        included_ids: list[str] = []
        included_set: set[str] = set()
        for tid in live_runs_info:
            tinfo = threads_by_id.get(tid)
            if tinfo is None or tinfo.get("deleted_at") or tinfo.get("archived_at"):
                continue
            if tid not in included_set:
                included_set.add(tid)
                included_ids.append(tid)
        for tid in recent_top_ids:
            if tid not in included_set:
                included_set.add(tid)
                included_ids.append(tid)

        if not included_ids:
            if use_cache:
                _lru_set(
                    _ACTIVE_THREADS_CACHE,
                    cache_key,
                    (fast_sig, meta_sig, watermark, [], None, []),
                    _ACTIVE_THREADS_CACHE_MAX,
                )
            return []

        # 3. Fetch lightweight subagent rows to map delegation roots & child counts.
        slim_sub_rows: list[sqlite3.Row] = []
        if sub_cols:
            slim_sub_rows = conn.execute(
                "SELECT subagent_id, thread_id, child_thread_id, status FROM orchestration_v2_projection_subagents"
            ).fetchall()
            sub_tids = {str(s["thread_id"]) for s in slim_sub_rows if s["thread_id"]}
            if sub_tids:
                _fetch_threads(sub_tids)

        # Resolve ancestors for all loaded threads (up to 4 hops).
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

        # Track delegation roots, live roots, and deduplicated active/total child counts.
        delegation_roots: set[str] = set()
        direct_children_state: dict[str, dict[str, bool]] = {}
        tree_children_state: dict[str, dict[str, bool]] = {}
        for s in slim_sub_rows:
            ptid = s["thread_id"]
            if not ptid:
                continue
            rtid = _resolve_root_thread_id(ptid)
            rinfo = threads_by_id.get(rtid)
            if not (rinfo and rinfo.get("deleted_at")):
                delegation_roots.add(rtid)
            ctid = s["child_thread_id"]
            child_key = str(ctid or s["subagent_id"])
            st_norm = normalize_state(s["status"])
            is_child_active = bool(
                (ctid and ctid in live_runs_info)
                or (st_norm in ("running", "waiting", "queued"))
            )
            dc_map = direct_children_state.setdefault(str(ptid), {})
            dc_map[child_key] = dc_map.get(child_key, False) or is_child_active
            tc_map = tree_children_state.setdefault(rtid, {})
            tc_map[child_key] = tc_map.get(child_key, False) or is_child_active

        live_roots: set[str] = set()
        for tid in live_runs_info:
            tinfo = threads_by_id.get(tid)
            if tinfo is None or tinfo.get("deleted_at") or tinfo.get("archived_at"):
                continue
            rtid = _resolve_root_thread_id(tid)
            rinfo = threads_by_id.get(rtid)
            if not (rinfo and (rinfo.get("deleted_at") or rinfo.get("archived_at"))):
                live_roots.add(rtid)

        # 4. Fetch latest run status for non-live included threads.
        non_live_ids = [tid for tid in included_ids if tid not in live_runs_info]
        latest_run_by_tid: dict[str, dict[str, Any]] = {}
        if non_live_ids and "thread_id" in run_cols and "status" in run_cols:
            order_col = "ordinal DESC" if "ordinal" in run_cols else "rowid DESC"
            req_col = "requested_at" if "requested_at" in run_cols else "NULL AS requested_at"
            for i in range(0, len(non_live_ids), 400):
                chunk = non_live_ids[i : i + 400]
                ph = ",".join("?" * len(chunk))
                for row in conn.execute(
                    f"""
                    SELECT thread_id, status, {req_col}
                    FROM orchestration_v2_projection_runs
                    WHERE thread_id IN ({ph})
                    ORDER BY thread_id ASC, {order_col}
                    """,
                    chunk,
                ):
                    tid = row["thread_id"]
                    if tid not in latest_run_by_tid:
                        latest_run_by_tid[tid] = dict(row)

        # 5. Fetch projects & provider_threads mappings for included threads.
        project_ids: set[str] = set()
        pt_ids: set[str] = set()
        for tid in included_ids:
            tinfo = threads_by_id.get(tid)
            if tinfo:
                if tinfo.get("project_id"):
                    project_ids.add(tinfo["project_id"])
                if tinfo.get("active_provider_thread_id"):
                    pt_ids.add(tinfo["active_provider_thread_id"])

        projects_by_id: dict[str, dict[str, Any]] = {}
        if project_ids and proj_cols:
            pids = list(project_ids)
            for i in range(0, len(pids), 400):
                chunk = pids[i : i + 400]
                ph = ",".join("?" * len(chunk))
                for row in conn.execute(
                    f"SELECT project_id, title, workspace_root FROM projection_projects WHERE project_id IN ({ph})",
                    chunk,
                ):
                    projects_by_id[row["project_id"]] = dict(row)

        pt_native_by_ptid: dict[str, tuple[str | None, str | None]] = {}
        pt_native_by_tid: dict[str, tuple[str | None, str | None]] = {}
        if pt_ids and pt_cols:
            pids = list(pt_ids)
            for i in range(0, len(pids), 400):
                chunk = pids[i : i + 400]
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

        if included_ids and pt_cols:
            for i in range(0, len(included_ids), 400):
                chunk = included_ids[i : i + 400]
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
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    items: list[dict[str, Any]] = []
    tr_sigs: list[tuple[str, tuple[int, int] | None]] = []
    seen_tr: set[str] = set()

    for tid in included_ids:
        tinfo = threads_by_id.get(tid) or {}
        root_tid = _resolve_root_thread_id(tid)
        parent_tid = tinfo.get("parent_thread_id") or None
        proj_info = projects_by_id.get(tinfo.get("project_id")) if tinfo.get("project_id") else None
        project_root = proj_info.get("workspace_root") if proj_info else None
        project_title = (
            (proj_info.get("title") if proj_info else None)
            or (Path(project_root).name if project_root else "")
        )

        raw_model = tinfo.get("model")
        prov = tinfo.get("default_provider") or tinfo.get("provider_instance_id")
        nid, drv = pt_native_by_tid.get(tid, (None, None))
        if not nid and tinfo.get("active_provider_thread_id"):
            nid, drv = pt_native_by_ptid.get(tinfo["active_provider_thread_id"], (None, None))

        _, _, _, last_act, resolved_model, tr_path, _, _ = _resolve_telemetry(
            provider=drv or prov,
            model=raw_model,
            native_id=nid,
            rate_table=rt,
            usage_index=ui,
            brain_map=brain_map,
            grok_map=grok_map,
        )
        if tr_path is not None:
            trp_str = str(tr_path)
            if trp_str not in seen_tr:
                seen_tr.add(trp_str)
                tr_sigs.append((trp_str, _stat_mtime_size(trp_str)))

        if tid in live_runs_info:
            run_info = live_runs_info[tid]
            state = normalize_state(run_info["status"])
            started_at = run_info.get("requested_at") or tinfo.get("created_at")
        else:
            latest_run = latest_run_by_tid.get(tid)
            state = normalize_state(latest_run["status"]) if latest_run else "done"
            started_at = tinfo.get("created_at") or (latest_run.get("requested_at") if latest_run else None)

        is_live = state in ("running", "waiting", "queued")
        updated_at = tinfo.get("updated_at") or started_at
        dur_s = compute_dur_s(
            started_at,
            None if is_live else updated_at,
            is_running=is_live,
            now_ts=effective_now,
        )

        lab = normalize_lab(resolved_model, drv or prov)
        if lab == "other" and not resolved_model and not prov:
            lab = "t3"

        child_map = tree_children_state.get(tid, {}) if parent_tid is None else direct_children_state.get(tid, {})
        total_children = len(child_map)
        active_children = sum(1 for act in child_map.values() if act)

        has_flow = (root_tid in delegation_roots) or (root_tid in live_roots)
        flow_id = f"t3:{root_tid}" if has_flow else None
        href = f"t3-thread://v2/{resolved_env_id}/{tid}" if resolved_env_id else None

        links: list[dict[str, Any]] = []
        tlink = _t3_thread_link(resolved_env_id, tid)
        if tlink:
            links.append(tlink)
        slink = make_session_link(drv or prov, nid)
        if slink:
            links.append(slink)

        items.append(
            {
                "id": tid,
                "flowId": flow_id,
                "rootThreadId": root_tid,
                "parentThreadId": parent_tid,
                "title": tinfo.get("title") or tid,
                "project": project_title,
                "projectRoot": project_root,
                "harness": "t3",
                "state": state,
                "isLive": is_live,
                "settled": bool(tinfo.get("settled_at")),
                "model": resolved_model,
                "lab": lab,
                "startedAt": started_at,
                "updatedAt": updated_at,
                "durS": dur_s,
                "activeChildren": active_children,
                "totalChildren": total_children,
                "last": last_act,
                "href": href,
                "links": links,
            }
        )

    items.sort(
        key=lambda item: (
            0 if item["isLive"] else 1,
            0 if item["state"] == "running" else 1,
            -(parse_iso_ts(item.get("updatedAt")) or 0.0),
        )
    )

    if use_cache:
        _lru_set(
            _ACTIVE_THREADS_CACHE,
            cache_key,
            (fast_sig, meta_sig, watermark, tr_sigs, oldest_recent_ts, items),
            _ACTIVE_THREADS_CACHE_MAX,
        )

    return items


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
