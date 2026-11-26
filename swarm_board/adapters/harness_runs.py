"""Read-only adapter for local committee, orchestrate, and agent-delegate harness runs."""

from __future__ import annotations

from collections import Counter, OrderedDict, deque
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any

from ..safety import is_path_allowed
from ..schema import (
    RateTable,
    UsageIndex,
    make_agent,
    make_flow,
    make_output,
    make_session_link,
    normalize_lab,
    parse_iso_ts,
    to_iso_utc,
)
from .agy_swarms import default_brain_roots as _agy_default_brain_roots

DEFAULT_COMMITTEE_DIR = Path("/tmp/committee")
DEFAULT_ORCHESTRATE_DIR = Path("/tmp/orchestrate")
DEFAULT_SESSIONS_LEDGER = Path.home() / ".agent-delegate" / "sessions.jsonl"
DEFAULT_GROK_SESSIONS_ROOT = Path.home() / ".grok" / "sessions"
DEFAULT_CLAUDE_ROOT = Path.home() / ".claude"

# Patterns for extracting concise verdict labels from committee responses.
_VERDICT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bcond(?:ition(?:al(?:ly)?)?)?\.?\s+pass\b", re.IGNORECASE), "COND PASS"),
    (re.compile(r"\bcond(?:ition(?:al(?:ly)?)?)?\.?\s+go\b", re.IGNORECASE), "COND GO"),
    (re.compile(r"\bcond(?:ition(?:al(?:ly)?)?)?\.?\s+approve(?:d)?\b", re.IGNORECASE), "COND APPROVE"),
    (re.compile(r"\bcond(?:ition(?:al(?:ly)?)?)?\.?\s+ship\b", re.IGNORECASE), "COND SHIP"),
    (re.compile(r"\bno[\s-]+go\b", re.IGNORECASE), "NO-GO"),
    (re.compile(r"\b(?:do\s+not\s+ship|no[\s-]+ship)\b", re.IGNORECASE), "NO-SHIP"),
    (re.compile(r"\breject(?:ed)?\b", re.IGNORECASE), "REJECT"),
    (re.compile(r"\bfail(?:ed)?\b", re.IGNORECASE), "FAIL"),
    (re.compile(r"\bblock(?:ed)?\b", re.IGNORECASE), "BLOCK"),
    (re.compile(r"\bpass(?:ed)?\b", re.IGNORECASE), "PASS"),
    (re.compile(r"\bapprove(?:d)?\b", re.IGNORECASE), "APPROVE"),
    (re.compile(r"\bship\b", re.IGNORECASE), "SHIP"),
    (re.compile(r"\bhold\b|\bdefer(?:red)?\b", re.IGNORECASE), "HOLD"),
    (re.compile(r"\bgo\b", re.IGNORECASE), "GO"),
    (re.compile(r"\bconditional\b", re.IGNORECASE), "CONDITIONAL"),
]

_BOLD_RE = re.compile(r"\*\*([^*]{2,120})\*\*")
_MD_LINK_RE = re.compile(r"\[[^\]]+\]\((?:file://)?([^)\s#]+)(?:#[^)]+)?\)")
_BACKTICK_PATH_RE = re.compile(
    r"`([A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,8})(?::\d+(?:-\d+)?)?`"
)
_INPUT_BULLET_RE = re.compile(r"^\s*-\s+([A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,8})\s*$")
_COMMITTEE_FILE_RE = re.compile(r"^(committee-(\d+))\.(?:out\.json|md)$")

HARNESS_STALE_THRESHOLD_S = 1800.0
_MISSING = object()
_DIR_RUN_CACHE_MAX = 256
_AGY_TRANSCRIPT_CACHE_MAX = 256
_GROK_INDEX_CACHE_MAX = 16
_GROK_INDEX_TTL_S = 5.0
_GROK_USAGE_FILE_CACHE_MAX = 256
_LEDGER_PARSED_CACHE_MAX = 32
_LEDGER_FLOWS_CACHE_MAX = 32

_DEFAULT_RATE_TABLE = RateTable()
_DEFAULT_USAGE_INDEX = UsageIndex()
_DIR_RUN_CACHE: OrderedDict[tuple[str, str], tuple[Any, ...]] = OrderedDict()
_AGY_TRANSCRIPT_CACHE: OrderedDict[str, tuple[int, int, dict[str, Any]]] = OrderedDict()
_GROK_INDEX_CACHE: OrderedDict[str, tuple[float, dict[str, Path]]] = OrderedDict()
_GROK_USAGE_FILE_CACHE: OrderedDict[str, tuple[int, int, dict[str, Any]]] = OrderedDict()
_LEDGER_PARSED_CACHE: OrderedDict[str, tuple[int, int, int, list[dict[str, Any]]]] = OrderedDict()
_LEDGER_FLOWS_CACHE: OrderedDict[tuple[Any, ...], tuple[Any, ...]] = OrderedDict()


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


def _dir_latest_mtime(run_dir: Path) -> float | None:
    latest: float | None = None
    try:
        with os.scandir(run_dir) as it:
            for e in it:
                try:
                    m = e.stat().st_mtime
                    if latest is None or m > latest:
                        latest = m
                except OSError:
                    continue
    except OSError:
        pass
    if latest is not None:
        return latest
    try:
        return run_dir.stat().st_mtime
    except OSError:
        return None


def _refresh_harness_flow(
    flow: dict[str, Any] | None,
    live_mtimes: dict[str, float],
    now_ts: float,
) -> dict[str, Any] | None:
    if flow is None or not live_mtimes:
        return flow
    agents = flow.get("agents") or ()
    new_agents = []
    for a in agents:
        if isinstance(a, dict) and a.get("state") == "running" and a.get("id") in live_mtimes:
            a_copy = dict(a)
            a_copy["durS"] = round(max(0.0, now_ts - live_mtimes[a["id"]]), 2)
            new_agents.append(a_copy)
        else:
            new_agents.append(a)
    f_copy = dict(flow)
    f_copy["agents"] = new_agents
    return f_copy


def _resolve_harness_dirs(
    explicit: Path | str | None,
    default_dir: Path,
    subdir_name: str,
) -> list[Path]:
    """Return unique candidate directories, supporting /tmp and tempfile.gettempdir()."""
    if explicit is not None:
        return [Path(explicit)]
    candidates = [default_dir, Path(tempfile.gettempdir()) / subdir_name]
    out: list[Path] = []
    seen: set[str] = set()
    for p in candidates:
        try:
            key = os.path.realpath(p)
        except OSError:
            key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _stat_sig(path: Path | str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _dir_sig(
    run_dir: Path,
    rate_table: RateTable | None,
    usage_index: UsageIndex | None,
    grok_lookup: _GrokUsageLookup | None,
    agy_lookup: _AgyTranscriptLookup | None = None,
) -> tuple[Any, ...]:
    entries: list[tuple[str, int, int]] = []
    try:
        with os.scandir(run_dir) as it:
            for e in it:
                try:
                    st = e.stat()
                    entries.append((e.name, st.st_mtime_ns, st.st_size))
                except OSError:
                    continue
    except OSError:
        pass
    entries.sort()
    return (
        tuple(entries),
        rate_table.cache_signature() if rate_table else None,
        str(usage_index.path) if usage_index else "",
        _stat_sig(usage_index.path) if usage_index else None,
        str(grok_lookup.root) if grok_lookup else "",
        tuple(str(p) for p in agy_lookup.roots) if agy_lookup else (),
    )


def _safe_read_json(path: Path) -> Any:
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _safe_stat_mtime_size(path: Path) -> tuple[float | None, int | None]:
    try:
        st = path.stat()
        return st.st_mtime, st.st_size
    except OSError:
        return None, None


def _clean_excerpt(text: str | None, limit: int = 220) -> str | None:
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)
        cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if len(cleaned) > limit:
            return cleaned[: limit - 1].rstrip() + "…"
        return cleaned
    return None


def _bound_hint(text: Any, limit: int = 400) -> str | None:
    """Strip outer whitespace and bound a label hint string to `limit` chars."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    return cleaned[:limit]


def _extract_verdict(response: str | None) -> str | None:
    """Extract a concise verdict label from the opening header/bold line of a committee response."""
    if not response or not isinstance(response, str):
        return None
    # Focus on the first non-empty line / opening sentence so body prose doesn't false-match.
    first_lines: list[str] = []
    for raw_line in response.splitlines():
        s = raw_line.strip()
        if s:
            first_lines.append(s)
            if len(first_lines) >= 2:
                break
    if not first_lines:
        return None
    head = first_lines[0]
    # Strip leading list markers like "1. " or "- " before splitting sentences.
    head_clean = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s+", "", head)
    # Narrow to the first sentence or verdict clause if long.
    clause = re.split(r"(?<!\d)(?<=[.!?])\s+|\n", head_clean, maxsplit=1)[0][:220]

    for pattern, label in _VERDICT_PATTERNS:
        if pattern.search(clause):
            return label

    # Fallback: extract first bold segment that isn't just "Verdict" / "Verdict:".
    for match in _BOLD_RE.finditer(clause):
        candidate = match.group(1).strip().rstrip(":.")
        if candidate.lower() in {"verdict", "1. verdict", "board vote", "summary"}:
            continue
        short = re.split(r"[.;:(]", candidate, maxsplit=1)[0].strip()
        if short:
            return short[:28]

    # Secondary fallback: text immediately after "Verdict:"
    m_after = re.search(r"verdict\s*[:—-]\s*([^\n.;]{2,40})", clause, re.IGNORECASE)
    if m_after:
        cleaned = re.sub(r"[*`]", "", m_after.group(1)).strip()
        if cleaned:
            return cleaned[:28]

    return None


def _verdict_bucket(verdict: str | None) -> str | None:
    if not verdict:
        return None
    u = verdict.upper()
    if u.startswith("COND") or u == "CONDITIONAL":
        return "CONDITIONAL"
    if u in {"NO-GO", "NO-SHIP", "FAIL", "REJECT", "BLOCK"}:
        return "FAIL"
    if u in {"PASS", "GO", "APPROVE", "SHIP"}:
        return "PASS"
    if u == "HOLD":
        return "HOLD"
    return "OTHER"


def _derive_decision(
    seat_verdicts: list[tuple[str | None, float]],
    running_count: int,
) -> dict[str, Any] | None:
    """Derive a flow-level decision {word, qualifier} across committee seats."""
    total = len(seat_verdicts)
    if total == 0:
        return None
    completed = [(v, w) for v, w in seat_verdicts if v is not None]
    if not completed:
        if running_count > 0:
            return {"word": "PENDING", "qualifier": f"{running_count}/{total} running"}
        return None

    bucket_counts: dict[str, int] = {}
    bucket_weights: dict[str, float] = {}
    for verdict, weight in completed:
        bucket = _verdict_bucket(verdict) or "OTHER"
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        bucket_weights[bucket] = bucket_weights.get(bucket, 0.0) + float(weight or 1.0)

    ranked = sorted(
        bucket_counts.keys(),
        key=lambda b: (bucket_weights.get(b, 0.0), bucket_counts[b]),
        reverse=True,
    )
    top_bucket = ranked[0]
    top_count = bucket_counts[top_bucket]

    if top_bucket == "OTHER":
        word = "RANKED" if len(completed) == total else "IN PROGRESS"
    elif len(bucket_counts) == 1:
        word = top_bucket
    elif top_count > len(completed) / 2:
        word = top_bucket
    else:
        word = "SPLIT"

    if running_count > 0:
        qualifier = f"{len(completed)}/{total} in · {running_count} running"
    elif len(bucket_counts) == 1 and len(completed) == total:
        qualifier = f"{top_count}/{total} unanimous"
    else:
        qualifier = f"{top_count}/{total} seats"

    return {"word": word, "qualifier": qualifier}


class _GrokUsageLookup:
    """Lazy read-only lookup for ~/.grok/sessions/<project>/<session_id>/usage.json."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._loaded = False
        self._index: dict[str, Path] = {}

    def _ensure_indexed(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        root_key = str(self.root)
        now_mono = time.monotonic()
        cached = _lru_get(_GROK_INDEX_CACHE, root_key)
        if cached is not _MISSING and (now_mono - cached[0]) < _GROK_INDEX_TTL_S:
            self._index = cached[1]
            return
        index: dict[str, Path] = {}
        if self.root.is_dir():
            try:
                with os.scandir(self.root) as proj_it:
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
                                        index[sess_entry.name] = ufile
                        except OSError:
                            continue
            except OSError:
                pass
        self._index = index
        _lru_set(_GROK_INDEX_CACHE, root_key, (now_mono, index), _GROK_INDEX_CACHE_MAX)

    def get(self, session_id: str | None) -> dict[str, Any] | None:
        if not session_id:
            return None
        self._ensure_indexed()
        usage_file = self._index.get(session_id.strip())
        if not usage_file:
            return None
        key = str(usage_file)
        try:
            st = os.stat(key)
        except OSError:
            return None
        cached = _lru_get(_GROK_USAGE_FILE_CACHE, key)
        if cached is not _MISSING and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            return cached[2]

        doc = _safe_read_json(usage_file)
        if not isinstance(doc, dict):
            return None
        sess = doc.get("session")
        if not isinstance(sess, dict):
            return None
        total_in = int(sess.get("inputTokens") or 0)
        cached_read = int(sess.get("cachedReadTokens") or 0)
        cache_create = int(sess.get("cacheCreationTokens") or 0)
        out_tok = int(sess.get("outputTokens") or 0)
        non_cached_in = max(0, total_in - cached_read - cache_create)
        ticks = sess.get("costUsdTicks")
        cost_usd = (
            round(float(ticks) / 1e10, 4)
            if isinstance(ticks, (int, float)) and ticks > 0
            else None
        )
        rec = {
            "in": non_cached_in,
            "cache": cached_read + cache_create,
            "cacheRead": cached_read,
            "cacheWrite": cache_create,
            "out": out_tok,
            "model": sess.get("primaryModelId"),
            "costUsd": cost_usd,
        }
        _lru_set(
            _GROK_USAGE_FILE_CACHE,
            key,
            (st.st_mtime_ns, st.st_size, rec),
            _GROK_USAGE_FILE_CACHE_MAX,
        )
        return rec


class _AgyTranscriptLookup:
    """Lazy mtime+size-cached lookup for standalone Antigravity CLI transcript.jsonl files."""

    def __init__(self, brain_roots: list[Path] | None = None) -> None:
        raw_roots = brain_roots if brain_roots is not None else _agy_default_brain_roots()
        self.roots = [p for p in raw_roots if p.is_dir()]

    def get(self, session_id: str | None) -> tuple[dict[str, Any] | None, Path | None]:
        sid = (session_id or "").strip()
        if not sid or not self.roots:
            return None, None
        for root in self.roots:
            tr_path = root / sid / ".system_generated" / "logs" / "transcript.jsonl"
            if not tr_path.is_file():
                continue
            key = str(tr_path)
            try:
                st = os.stat(key)
            except OSError:
                continue
            sig = (st.st_mtime_ns, st.st_size)
            cached = _lru_get(_AGY_TRANSCRIPT_CACHE, key)
            if cached is not _MISSING and cached[0] == sig[0] and cached[1] == sig[1]:
                rec = cached[2]
                return (rec if (rec["in"] or rec["cache"] or rec["out"] or rec.get("trace")) else None), tr_path

            in_tok = 0
            cache_tok = 0
            out_tok = 0
            model: str | None = None
            tool_counts: Counter[str] = Counter()
            try:
                with open(tr_path, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if '"PLANNER_RESPONSE"' not in line:
                            continue
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(obj, dict) or obj.get("type") != "PLANNER_RESPONSE":
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
            except OSError:
                continue

            trace = [[name, count] for name, count in tool_counts.most_common()]
            rec = {
                "in": in_tok,
                "cache": cache_tok,
                "out": out_tok,
                "model": model,
                "costUsd": None,
                "trace": trace,
            }
            _lru_set(
                _AGY_TRANSCRIPT_CACHE,
                key,
                (sig[0], sig[1], rec),
                _AGY_TRANSCRIPT_CACHE_MAX,
            )
            if in_tok or cache_tok or out_tok or trace:
                return rec, tr_path
            return None, tr_path
        return None, None


def _resolve_usage_and_cost(
    session_id: str | None,
    model: str | None,
    *,
    cli: str | None = None,
    lab: str | None = None,
    reported_cost: float | None = None,
    fallback_cost: float | None,
    usage_index: UsageIndex | None,
    grok_lookup: _GrokUsageLookup | None,
    agy_lookup: _AgyTranscriptLookup | None = None,
    rate_table: RateTable | None,
) -> tuple[dict[str, int] | None, float | None, bool, Path | None, int | None, list[list[Any]]]:
    """Join session_id to real tokens/cost/cacheRead/trace via UsageIndex -> Grok usage.json -> AGY transcript -> fallback."""
    tokens: dict[str, int] | None = None
    cost_usd: float | None = None
    cost_estimated = True
    usage_rec: dict[str, Any] | None = None
    transcript_path: Path | None = None
    cache_read: int | None = None
    trace: list[list[Any]] = []

    if reported_cost is not None:
        try:
            cost_usd = round(float(reported_cost), 4)
            cost_estimated = False
        except (TypeError, ValueError):
            cost_usd = None

    if session_id and usage_index is not None:
        usage_rec = usage_index.get(session_id)
    if usage_rec is None and session_id and grok_lookup is not None:
        usage_rec = grok_lookup.get(session_id)

    if usage_rec is not None:
        tokens = {
            "in": int(usage_rec.get("in", 0) or 0),
            "cache": int(usage_rec.get("cache", 0) or 0),
            "out": int(usage_rec.get("out", 0) or 0),
        }
        cache_read = usage_rec.get("cacheRead")
        if cost_usd is None:
            rec_cost = usage_rec.get("costUsd")
            if rec_cost is not None:
                cost_usd = round(float(rec_cost), 4)
                cost_estimated = False
            elif rate_table is not None:
                cost_usd = rate_table.estimate_cost_usd(
                    usage_rec.get("model") or model,
                    tokens,
                    cache_read=usage_rec.get("cacheRead"),
                    cache_write=usage_rec.get("cacheWrite"),
                )
                cost_estimated = True

    if cost_usd is None and fallback_cost is not None:
        try:
            cost_usd = round(float(fallback_cost), 4)
            cost_estimated = True
        except (TypeError, ValueError):
            cost_usd = None

    # Standalone AGY CLI transcript join for google/agy agents lacking tokens (even if costUsd exists).
    cli_lower = (cli or "").strip().lower()
    is_google_agy = (lab == "google") or (cli_lower in ("agy", "antigravity", "gemini"))
    if tokens is None and session_id and agy_lookup is not None and is_google_agy:
        agy_rec, transcript_path = agy_lookup.get(session_id)
        if agy_rec is not None:
            trace = list(agy_rec.get("trace") or [])
            if agy_rec.get("in") or agy_rec.get("cache") or agy_rec.get("out"):
                tokens = {
                    "in": int(agy_rec.get("in", 0) or 0),
                    "cache": int(agy_rec.get("cache", 0) or 0),
                    "out": int(agy_rec.get("out", 0) or 0),
                }
                cache_read = tokens["cache"]
                if cost_usd is None and rate_table is not None:
                    est = rate_table.estimate_cost_usd(
                        agy_rec.get("model") or model,
                        tokens,
                    )
                    if est is not None:
                        cost_usd = est
                        cost_estimated = True

    return tokens, cost_usd, cost_estimated, transcript_path, cache_read, trace


def _extract_referenced_files(
    texts: list[str],
    workspace: str | None,
    *,
    max_files: int = 20,
) -> list[tuple[str, str, int | None, float | None]]:
    """Find workspace-local files referenced in briefs/responses, strictly verified by safety."""
    if not workspace:
        return []
    ws_path = Path(workspace)
    if not ws_path.is_dir():
        return []

    raw_candidates: list[str] = []
    seen_raw: set[str] = set()
    for text in texts:
        if not text:
            continue
        for m in _MD_LINK_RE.finditer(text):
            raw = m.group(1)
            # Strip trailing :line or :line-line from markdown link targets
            raw = re.sub(r":\d+(?:-\d+)?$", "", raw).strip()
            if raw and not raw.startswith(("http://", "https://", "mailto:")) and raw not in seen_raw:
                seen_raw.add(raw)
                raw_candidates.append(raw)
        for m in _BACKTICK_PATH_RE.finditer(text):
            raw = m.group(1).strip()
            if raw and raw not in seen_raw:
                seen_raw.add(raw)
                raw_candidates.append(raw)
        for line in text.splitlines():
            m_in = _INPUT_BULLET_RE.match(line)
            if m_in:
                raw = m_in.group(1).strip()
                if raw and raw not in seen_raw:
                    seen_raw.add(raw)
                    raw_candidates.append(raw)

    seen: set[str] = set()
    verified: list[tuple[str, str, int | None, float | None]] = []
    allowed = [workspace]

    for cand in raw_candidates:
        if len(verified) >= max_files:
            break
        cand = cand.strip()
        if not cand or cand.startswith(("http://", "https://")):
            continue
        p = Path(cand)
        full_cand = str(p) if p.is_absolute() else str(ws_path / p)
        ok, resolved = is_path_allowed(full_cand, allowed)
        if not ok or not resolved or resolved in seen:
            continue
        if not os.path.isfile(resolved):
            continue
        seen.add(resolved)
        mtime, size = _safe_stat_mtime_size(Path(resolved))
        try:
            rel_title = os.path.relpath(resolved, os.path.realpath(workspace))
        except ValueError:
            rel_title = Path(resolved).name
        verified.append((rel_title, resolved, size, mtime))

    return verified


def _parse_committee_dir(
    run_dir: Path,
    *,
    usage_index: UsageIndex | None,
    grok_lookup: _GrokUsageLookup | None,
    agy_lookup: _AgyTranscriptLookup | None,
    rate_table: RateTable | None,
    now_ts: float,
    seen_session_ids: set[str],
    use_cache: bool = True,
) -> dict[str, Any] | None:
    cache_key = ("committee", str(run_dir))
    sig = _dir_sig(run_dir, rate_table, usage_index, grok_lookup, agy_lookup) if use_cache else None
    if use_cache and sig is not None:
        cached = _lru_get(_DIR_RUN_CACHE, cache_key)
        if cached is not _MISSING:
            c_sig, c_ref_sigs, c_flow, c_sids, c_live_mtimes, c_has_queued, c_dir_mtime = cached
            if c_sig == sig and all(_stat_sig(p) == s for p, s in c_ref_sigs):
                stale_aged = any(
                    (now_ts - md_m) > HARNESS_STALE_THRESHOLD_S
                    for md_m in c_live_mtimes.values()
                )
                if not stale_aged and c_has_queued and c_dir_mtime is not None:
                    stale_aged = (now_ts - c_dir_mtime) > HARNESS_STALE_THRESHOLD_S
                if not stale_aged:
                    seen_session_ids.update(c_sids)
                    return _refresh_harness_flow(c_flow, c_live_mtimes, now_ts)

    resolved_path = run_dir / "committee.resolved.json"
    resolved_doc = _safe_read_json(resolved_path)

    # Check if directory actually looks like a committee run.
    member_entries: list[dict[str, Any]] = []
    if isinstance(resolved_doc, dict) and isinstance(resolved_doc.get("members"), list):
        member_entries = [m for m in resolved_doc["members"] if isinstance(m, dict)]
    else:
        # Synthesize from committee-*.out.json / committee-*.md files.
        discovered_ids: dict[int, str] = {}
        try:
            for child in run_dir.iterdir():
                m = _COMMITTEE_FILE_RE.match(child.name)
                if m:
                    discovered_ids[int(m.group(2))] = m.group(1)
        except OSError:
            return None
        if not discovered_ids:
            return None
        for seat_num in sorted(discovered_ids):
            member_entries.append({"id": discovered_ids[seat_num], "seat": seat_num})

    if not member_entries:
        return None

    workspace = resolved_doc.get("workspace") if isinstance(resolved_doc, dict) else None
    question = resolved_doc.get("question") if isinstance(resolved_doc, dict) else None
    tier = resolved_doc.get("tier") if isinstance(resolved_doc, dict) else None

    agents: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    seat_verdicts: list[tuple[str | None, float]] = []
    texts_for_refs: list[str] = [question or ""]
    timestamps: list[float] = []
    run_session_ids: list[str] = []
    matched_tr_paths: list[str] = []
    running_count = 0
    live_md_mtimes: dict[str, float] = {}
    has_queued = False
    dir_latest_mtime = _dir_latest_mtime(run_dir)

    if resolved_path.is_file():
        res_mtime, res_size = _safe_stat_mtime_size(resolved_path)
        if res_mtime is not None:
            timestamps.append(res_mtime)
        outputs.append(
            make_output(
                "committee-resolved",
                "json",
                "committee.resolved.json",
                path=str(resolved_path),
                size=res_size,
                by="root",
                at=to_iso_utc(res_mtime),
                meta={"tier": tier} if tier else {},
            )
        )

    for idx, member in enumerate(member_entries, start=1):
        seat_num = int(member.get("seat") or idx)
        member_id = str(member.get("id") or f"committee-{seat_num}")
        md_path = run_dir / f"{member_id}.md"
        out_path = run_dir / f"{member_id}.out.json"

        md_mtime, md_size = _safe_stat_mtime_size(md_path)
        out_mtime, out_size = _safe_stat_mtime_size(out_path)
        out_data = _safe_read_json(out_path) if (out_size and out_size > 0) else None
        if not isinstance(out_data, dict):
            out_data = None

        if md_mtime is not None:
            timestamps.append(md_mtime)
        if out_mtime is not None and out_size and out_size > 0:
            timestamps.append(out_mtime)

        # Live detection: brief exists and output is missing or empty.
        if md_path.is_file() and (not out_path.is_file() or not out_size):
            if md_mtime is not None and (now_ts - md_mtime) > HARNESS_STALE_THRESHOLD_S:
                state = "stale"
                started_ts = md_mtime
                ended_ts = None
                dur_s = None
            else:
                state = "running"
                running_count += 1
                started_ts = md_mtime
                ended_ts = None
                dur_s = round(max(0.0, now_ts - md_mtime), 2) if md_mtime is not None else None
                if md_mtime is not None:
                    live_md_mtimes[member_id] = md_mtime
        elif out_data is not None:
            raw_status = str(out_data.get("status") or "").lower()
            state = "done" if raw_status == "success" else "error"
            raw_dur = out_data.get("duration_seconds")
            dur_s = round(float(raw_dur), 2) if isinstance(raw_dur, (int, float)) else None
            ended_ts = out_mtime
            if md_mtime is not None:
                started_ts = md_mtime
            elif ended_ts is not None and dur_s is not None:
                started_ts = ended_ts - dur_s
            else:
                started_ts = ended_ts
        else:
            if dir_latest_mtime is not None and (now_ts - dir_latest_mtime) > HARNESS_STALE_THRESHOLD_S:
                state = "stale"
            else:
                state = "queued"
                has_queued = True
            started_ts = md_mtime
            ended_ts = None
            dur_s = None

        route = member.get("route") if isinstance(member.get("route"), dict) else {}
        cli = (out_data.get("cli") if out_data else None) or route.get("cli")
        model = (
            (out_data.get("actual_model") or out_data.get("model") if out_data else None)
            or member.get("model")
            or route.get("model")
        )
        role = (out_data.get("role") if out_data else None) or member.get("role") or "reviewer"
        session_id = out_data.get("session_id") if out_data else None
        if session_id:
            sid_clean = str(session_id).strip()
            seen_session_ids.add(sid_clean)
            run_session_ids.append(sid_clean)

        response = out_data.get("response") if out_data else None
        if isinstance(response, str) and response:
            texts_for_refs.append(response)

        # Read brief text for referenced project files (only first brief needed if identical,
        # but reading small .md briefs is cheap and handles per-seat differences).
        brief_text: str | None = None
        if md_path.is_file() and md_size and md_size <= 65_536:
            try:
                brief_text = md_path.read_text(encoding="utf-8", errors="replace")
                texts_for_refs.append(brief_text)
            except OSError:
                pass

        norm_weight = member.get("normalized_weight")
        raw_weight = member.get("weight")
        weight = (
            float(norm_weight)
            if isinstance(norm_weight, (int, float))
            else (float(raw_weight) if isinstance(raw_weight, (int, float)) else None)
        )

        verdict = _extract_verdict(response)
        seat_verdicts.append((verdict, weight if weight is not None else 1.0))

        agent_lab = normalize_lab(model, member.get("lab") or cli)
        reported_cost = out_data.get("cost_usd") if out_data else None
        tokens, cost_usd, cost_estimated, tr_path, cache_read, trace = _resolve_usage_and_cost(
            session_id,
            model,
            cli=cli,
            lab=agent_lab,
            reported_cost=reported_cost,
            fallback_cost=member.get("cost_per_task"),
            usage_index=usage_index,
            grok_lookup=grok_lookup,
            agy_lookup=agy_lookup,
            rate_table=rate_table,
        )
        if tr_path is not None:
            matched_tr_paths.append(str(tr_path))

        links: list[dict[str, Any]] = []
        sess_link = make_session_link(cli, session_id)
        if sess_link is not None:
            links.append(sess_link)

        agent_output_ids: list[str] = []
        label = str(member.get("label") or model or member_id)

        if out_path.is_file() and out_size:
            verdict_out_id = f"{member_id}-verdict"
            agent_output_ids.append(verdict_out_id)
            outputs.append(
                make_output(
                    verdict_out_id,
                    "verdict",
                    f"Seat {seat_num} · {label}",
                    path=str(out_path),
                    size=out_size,
                    by=member_id,
                    at=to_iso_utc(out_mtime),
                    body=response if isinstance(response, str) else None,
                    meta={
                        "seat": seat_num,
                        "cli": cli,
                        "model": model,
                        "verdict": verdict,
                        "weight": weight,
                    },
                )
            )

        if md_path.is_file():
            brief_out_id = f"{member_id}-brief"
            agent_output_ids.append(brief_out_id)
            outputs.append(
                make_output(
                    brief_out_id,
                    "markdown",
                    f"{member_id}.md",
                    path=str(md_path),
                    size=md_size,
                    by=member_id,
                    at=to_iso_utc(md_mtime),
                    meta={"seat": seat_num, "role": "brief"},
                )
            )

        errors: list[dict[str, Any]] = []
        if state == "error" and out_data is not None:
            err_msg = out_data.get("error") or f"Exit status: {out_data.get('status')}"
            errors.append({"message": str(err_msg)})

        last_text = (
            _clean_excerpt(response)
            if response
            else ("Evaluating committee brief…" if state == "running" else None)
        )

        agent = make_agent(
            member_id,
            f"Seat {seat_num} · {label}",
            parent=None,
            role=str(role),
            lab=agent_lab,
            model=model,
            provider=cli,
            state=state,
            started_at=to_iso_utc(started_ts),
            ended_at=to_iso_utc(ended_ts),
            dur_s=dur_s,
            tokens=tokens,
            cost_usd=cost_usd,
            errors=errors,
            trace=trace,
            last=last_text,
            result=response if isinstance(response, str) else None,
            weight=weight,
            verdict=verdict,
            links=links,
            output_ids=agent_output_ids,
        )
        agent["costEstimated"] = cost_estimated
        if tokens is not None and cache_read is not None:
            agent["cacheRead"] = cache_read
        seat_parts = [f"Seat {seat_num}"]
        if role:
            seat_parts.append(str(role))
        if model:
            seat_parts.append(str(model))
        seat_prefix = " · ".join(seat_parts)
        committee_prompt = f"{seat_prefix}: {question}" if question else (brief_text or seat_prefix)
        agent["_labelHint"] = {
            "prompt": _bound_hint(committee_prompt, 360),
            "slug": _bound_hint(f"seat-{seat_num}", 400),
            "planTask": _bound_hint(question, 400),
            "rawTitle": _bound_hint(f"Seat {seat_num} · {label}", 400),
        }
        agents.append(agent)

    for ref_idx, (rel_title, ref_path, ref_size, ref_mtime) in enumerate(
        _extract_referenced_files(texts_for_refs, workspace),
        start=1,
    ):
        outputs.append(
            make_output(
                f"ref-{ref_idx}",
                "file",
                rel_title,
                path=ref_path,
                size=ref_size,
                by="root",
                at=to_iso_utc(ref_mtime),
                meta={"referenced": True},
            )
        )

    started_at = to_iso_utc(min(timestamps)) if timestamps else None
    updated_at = to_iso_utc(max(timestamps)) if timestamps else None
    decision = _derive_decision(seat_verdicts, running_count)

    title_suffix = f" ({tier})" if tier else ""
    flow = make_flow(
        f"committee:{run_dir.name}",
        f"{run_dir.name}{title_suffix}",
        project_root=workspace,
        harness="committee",
        kind="committee",
        started_at=started_at,
        updated_at=updated_at,
        goal=question,
        decision=decision,
        agents=agents,
        outputs=outputs,
    )
    if use_cache and sig is not None:
        ref_sigs = [
            (str(o["path"]), _stat_sig(o["path"]))
            for o in outputs
            if isinstance(o.get("meta"), dict) and o["meta"].get("referenced") and o.get("path")
        ]
        for trp in matched_tr_paths:
            ref_sigs.append((trp, _stat_sig(trp)))
        _lru_set(
            _DIR_RUN_CACHE,
            cache_key,
            (sig, ref_sigs, flow, frozenset(run_session_ids), live_md_mtimes, has_queued, dir_latest_mtime),
            _DIR_RUN_CACHE_MAX,
        )
    return flow


def _parse_orchestrate_dir(
    run_dir: Path,
    *,
    usage_index: UsageIndex | None,
    grok_lookup: _GrokUsageLookup | None,
    agy_lookup: _AgyTranscriptLookup | None,
    rate_table: RateTable | None,
    now_ts: float,
    seen_session_ids: set[str],
    use_cache: bool = True,
) -> dict[str, Any] | None:
    cache_key = ("orchestrate", str(run_dir))
    sig = _dir_sig(run_dir, rate_table, usage_index, grok_lookup, agy_lookup) if use_cache else None
    if use_cache and sig is not None:
        cached = _lru_get(_DIR_RUN_CACHE, cache_key)
        if cached is not _MISSING:
            c_sig, c_ref_sigs, c_flow, c_sids, c_live_mtimes, c_has_queued, c_dir_mtime = cached
            if c_sig == sig and all(_stat_sig(p) == s for p, s in c_ref_sigs):
                stale_aged = any(
                    (now_ts - md_m) > HARNESS_STALE_THRESHOLD_S
                    for md_m in c_live_mtimes.values()
                )
                if not stale_aged and c_has_queued and c_dir_mtime is not None:
                    stale_aged = (now_ts - c_dir_mtime) > HARNESS_STALE_THRESHOLD_S
                if not stale_aged:
                    seen_session_ids.update(c_sids)
                    return _refresh_harness_flow(c_flow, c_live_mtimes, now_ts)

    plan_path = run_dir / "plan.resolved.json"
    plan_doc = _safe_read_json(plan_path)
    if not isinstance(plan_doc, dict):
        return None

    tasks = plan_doc.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return None

    goal = plan_doc.get("goal")
    workspace = plan_doc.get("workspace")
    waves = plan_doc.get("waves") if isinstance(plan_doc.get("waves"), list) else []

    task_to_wave: dict[str, str] = {}
    workstreams: list[dict[str, str]] = []
    for w_idx, wave_ids in enumerate(waves, start=1):
        ws_id = f"wave-{w_idx}"
        workstreams.append({"id": ws_id, "title": f"Wave {w_idx}"})
        if isinstance(wave_ids, list):
            for tid in wave_ids:
                task_to_wave[str(tid)] = ws_id

    agents: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    timestamps: list[float] = []
    texts_for_refs: list[str] = [str(goal or "")]
    run_session_ids: list[str] = []
    matched_tr_paths: list[str] = []
    running_count = 0
    live_md_mtimes: dict[str, float] = {}
    has_queued = False
    dir_latest_mtime = _dir_latest_mtime(run_dir)
    has_deps = False

    plan_mtime, plan_size = _safe_stat_mtime_size(plan_path)
    if plan_mtime is not None:
        timestamps.append(plan_mtime)
    outputs.append(
        make_output(
            "plan-resolved",
            "json",
            "plan.resolved.json",
            path=str(plan_path),
            size=plan_size,
            by="root",
            at=to_iso_utc(plan_mtime),
        )
    )

    # Optional orchestrate overview artifacts: _ascii.txt, _diagram.out.json, _diagram.md
    ascii_path = run_dir / "_ascii.txt"
    if ascii_path.is_file():
        a_mtime, a_size = _safe_stat_mtime_size(ascii_path)
        if a_mtime is not None:
            timestamps.append(a_mtime)
        outputs.append(
            make_output(
                "orchestrate-ascii",
                "text",
                "_ascii.txt",
                path=str(ascii_path),
                size=a_size,
                by="root",
                at=to_iso_utc(a_mtime),
            )
        )

    diagram_out_path = run_dir / "_diagram.out.json"
    if diagram_out_path.is_file():
        d_mtime, d_size = _safe_stat_mtime_size(diagram_out_path)
        if d_mtime is not None:
            timestamps.append(d_mtime)
        d_doc = _safe_read_json(diagram_out_path)
        d_body = d_doc.get("response") if isinstance(d_doc, dict) and isinstance(d_doc.get("response"), str) else None
        outputs.append(
            make_output(
                "orchestrate-diagram",
                "json",
                "_diagram.out.json",
                path=str(diagram_out_path),
                size=d_size,
                by="root",
                at=to_iso_utc(d_mtime),
                body=d_body,
            )
        )

    for idx, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            continue
        tid = str(task.get("id") or f"task-{idx}")
        title = str(task.get("title") or tid)
        specialist = str(task.get("specialist") or "worker")
        after = task.get("after") if isinstance(task.get("after"), list) else []
        writes = task.get("writes") if isinstance(task.get("writes"), list) else []
        if after:
            has_deps = True
        for w in writes:
            if isinstance(w, str):
                texts_for_refs.append(f"`{w}`")

        md_path = run_dir / f"{tid}.md"
        out_path = run_dir / f"{tid}.out.json"
        md_mtime, md_size = _safe_stat_mtime_size(md_path)
        brief_head: str | None = None
        if md_path.is_file() and md_size:
            try:
                with md_path.open("r", encoding="utf-8", errors="replace") as bfh:
                    brief_head = _bound_hint(bfh.read(1024), 360)
            except OSError:
                brief_head = None
        out_mtime, out_size = _safe_stat_mtime_size(out_path)
        out_data = _safe_read_json(out_path) if (out_size and out_size > 0) else None
        if not isinstance(out_data, dict):
            out_data = None

        if md_mtime is not None:
            timestamps.append(md_mtime)
        if out_mtime is not None and out_size and out_size > 0:
            timestamps.append(out_mtime)

        if md_path.is_file() and (not out_path.is_file() or not out_size):
            if md_mtime is not None and (now_ts - md_mtime) > HARNESS_STALE_THRESHOLD_S:
                state = "stale"
                started_ts = md_mtime
                ended_ts = None
                dur_s = None
            else:
                state = "running"
                running_count += 1
                started_ts = md_mtime
                ended_ts = None
                dur_s = round(max(0.0, now_ts - md_mtime), 2) if md_mtime is not None else None
                if md_mtime is not None:
                    live_md_mtimes[tid] = md_mtime
        elif out_data is not None:
            raw_status = str(out_data.get("status") or "").lower()
            state = "done" if raw_status == "success" else "error"
            raw_dur = out_data.get("duration_seconds")
            dur_s = round(float(raw_dur), 2) if isinstance(raw_dur, (int, float)) else None
            ended_ts = out_mtime
            started_ts = (
                md_mtime
                if md_mtime is not None
                else (ended_ts - dur_s if ended_ts is not None and dur_s is not None else ended_ts)
            )
        else:
            if dir_latest_mtime is not None and (now_ts - dir_latest_mtime) > HARNESS_STALE_THRESHOLD_S:
                state = "stale"
            else:
                state = "queued"
                has_queued = True
            started_ts = md_mtime
            ended_ts = None
            dur_s = None

        route = task.get("route") if isinstance(task.get("route"), dict) else {}
        cli = (out_data.get("cli") if out_data else None) or route.get("cli")
        model = (
            (out_data.get("actual_model") or out_data.get("model") if out_data else None)
            or task.get("model")
            or task.get("model_id")
            or route.get("model")
        )
        role = (out_data.get("role") if out_data else None) or task.get("role") or specialist
        session_id = out_data.get("session_id") if out_data else None
        if session_id:
            sid_clean = str(session_id).strip()
            seen_session_ids.add(sid_clean)
            run_session_ids.append(sid_clean)

        response = out_data.get("response") if out_data else None
        if isinstance(response, str) and response:
            texts_for_refs.append(response)

        agent_lab = normalize_lab(model, cli)
        reported_cost = out_data.get("cost_usd") if out_data else None
        tokens, cost_usd, cost_estimated, tr_path, cache_read, trace = _resolve_usage_and_cost(
            session_id,
            model,
            cli=cli,
            lab=agent_lab,
            reported_cost=reported_cost,
            fallback_cost=task.get("cost_per_task"),
            usage_index=usage_index,
            grok_lookup=grok_lookup,
            agy_lookup=agy_lookup,
            rate_table=rate_table,
        )
        if tr_path is not None:
            matched_tr_paths.append(str(tr_path))

        links: list[dict[str, Any]] = []
        sess_link = make_session_link(cli, session_id)
        if sess_link is not None:
            links.append(sess_link)

        agent_output_ids: list[str] = []
        if out_path.is_file() and out_size:
            rep_id = f"{tid}-output"
            agent_output_ids.append(rep_id)
            outputs.append(
                make_output(
                    rep_id,
                    "report",
                    f"{tid} · {title}",
                    path=str(out_path),
                    size=out_size,
                    by=tid,
                    at=to_iso_utc(out_mtime),
                    body=response if isinstance(response, str) else None,
                    meta={"specialist": specialist, "cli": cli, "model": model},
                )
            )
        if md_path.is_file():
            brief_id = f"{tid}-brief"
            agent_output_ids.append(brief_id)
            outputs.append(
                make_output(
                    brief_id,
                    "markdown",
                    f"{tid}.md",
                    path=str(md_path),
                    size=md_size,
                    by=tid,
                    at=to_iso_utc(md_mtime),
                    meta={"role": "brief"},
                )
            )

        errors: list[dict[str, Any]] = []
        if state == "error" and out_data is not None:
            errors.append(
                {"message": str(out_data.get("error") or f"Exit status: {out_data.get('status')}")}
            )

        parent_id = str(after[0]) if after else None
        agent = make_agent(
            tid,
            f"{tid} · {title}",
            parent=parent_id,
            role=str(role),
            workstream=task_to_wave.get(tid),
            lab=agent_lab,
            model=model,
            provider=cli,
            state=state,
            started_at=to_iso_utc(started_ts),
            ended_at=to_iso_utc(ended_ts),
            dur_s=dur_s,
            tokens=tokens,
            cost_usd=cost_usd,
            errors=errors,
            trace=trace,
            last=_clean_excerpt(response) or ("Running task…" if state == "running" else None),
            result=response if isinstance(response, str) else None,
            links=links,
            output_ids=agent_output_ids,
        )
        agent["costEstimated"] = cost_estimated
        if tokens is not None and cache_read is not None:
            agent["cacheRead"] = cache_read
        plan_task_str = (
            f"{title} — {goal}"
            if (goal and title and str(goal).strip() not in title)
            else (title or goal)
        )
        agent["_labelHint"] = {
            "prompt": brief_head or _bound_hint(goal, 360),
            "slug": _bound_hint(tid, 400),
            "planTask": _bound_hint(plan_task_str, 400),
            "rawTitle": _bound_hint(title, 400),
        }
        agents.append(agent)

    for ref_idx, (rel_title, ref_path, ref_size, ref_mtime) in enumerate(
        _extract_referenced_files(texts_for_refs, workspace),
        start=1,
    ):
        outputs.append(
            make_output(
                f"ref-{ref_idx}",
                "file",
                rel_title,
                path=ref_path,
                size=ref_size,
                by="root",
                at=to_iso_utc(ref_mtime),
                meta={"referenced": True},
            )
        )

    started_at = to_iso_utc(min(timestamps)) if timestamps else None
    updated_at = to_iso_utc(max(timestamps)) if timestamps else None

    flow = make_flow(
        f"orchestrate:{run_dir.name}",
        run_dir.name,
        project_root=workspace,
        harness="orchestrate",
        kind="mixed" if has_deps else "flat",
        started_at=started_at,
        updated_at=updated_at,
        goal=goal,
        workstreams=workstreams,
        agents=agents,
        outputs=outputs,
    )
    if use_cache and sig is not None:
        ref_sigs = [
            (str(o["path"]), _stat_sig(o["path"]))
            for o in outputs
            if isinstance(o.get("meta"), dict) and o["meta"].get("referenced") and o.get("path")
        ]
        for trp in matched_tr_paths:
            ref_sigs.append((trp, _stat_sig(trp)))
        _lru_set(
            _DIR_RUN_CACHE,
            cache_key,
            (sig, ref_sigs, flow, frozenset(run_session_ids), live_md_mtimes, has_queued, dir_latest_mtime),
            _DIR_RUN_CACHE_MAX,
        )
    return flow


def _parse_sessions_ledger(
    ledger_path: Path,
    *,
    seen_session_ids: set[str],
    usage_index: UsageIndex | None,
    grok_lookup: _GrokUsageLookup | None,
    agy_lookup: _AgyTranscriptLookup | None,
    rate_table: RateTable | None,
    window_s: float = 300.0,
    max_recent_lines: int = 200,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """Group orphaned recent agent-delegate ledger runs within 5-min windows (>= 2 runs)."""
    ledger_sig = _stat_sig(ledger_path)
    if ledger_sig is None:
        return []

    ledger_key = (str(ledger_path), float(window_s), int(max_recent_lines))
    flow_sig = (
        ledger_sig,
        frozenset(seen_session_ids),
        rate_table.cache_signature() if rate_table else None,
        str(usage_index.path) if usage_index else "",
        _stat_sig(usage_index.path) if usage_index else None,
        str(grok_lookup.root) if grok_lookup else "",
        tuple(str(p) for p in agy_lookup.roots) if agy_lookup else (),
    )
    if use_cache:
        cached_flows = _lru_get(_LEDGER_FLOWS_CACHE, ledger_key)
        if cached_flows is not _MISSING:
            c_sig, c_tr_sigs, c_flows = cached_flows
            if c_sig == flow_sig and all(_stat_sig(p) == s for p, s in c_tr_sigs):
                return c_flows

    parsed_key = str(ledger_path)
    cached_parsed = _lru_get(_LEDGER_PARSED_CACHE, parsed_key) if use_cache else _MISSING
    raw_entries: list[dict[str, Any]] | None = None
    if cached_parsed is not _MISSING and cached_parsed[:3] == (ledger_sig[0], ledger_sig[1], max_recent_lines):
        raw_entries = cached_parsed[3]

    if raw_entries is None:
        raw_entries = []
        try:
            with ledger_path.open("r", encoding="utf-8", errors="replace") as fh:
                tail_lines = deque(fh, maxlen=max_recent_lines)
        except OSError:
            return []
        for raw in tail_lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            sid = str(obj.get("session_id") or "").strip()
            if not sid:
                continue
            ts = parse_iso_ts(obj.get("ts"))
            if ts is None:
                continue
            obj_copy = dict(obj)
            obj_copy["_sid"] = sid
            obj_copy["_parsed_ts"] = ts
            raw_entries.append(obj_copy)
        if use_cache:
            _lru_set(
                _LEDGER_PARSED_CACHE,
                parsed_key,
                (ledger_sig[0], ledger_sig[1], max_recent_lines, raw_entries),
                _LEDGER_PARSED_CACHE_MAX,
            )

    entries = [e for e in raw_entries if e["_sid"] not in seen_session_ids]
    if len(entries) < 2:
        if use_cache:
            _lru_set(_LEDGER_FLOWS_CACHE, ledger_key, (flow_sig, [], []), _LEDGER_FLOWS_CACHE_MAX)
        return []

    entries.sort(key=lambda e: e["_parsed_ts"])

    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = [entries[0]]
    for entry in entries[1:]:
        if entry["_parsed_ts"] - current[-1]["_parsed_ts"] <= window_s:
            current.append(entry)
        else:
            if len(current) >= 2:
                clusters.append(current)
            current = [entry]
    if len(current) >= 2:
        clusters.append(current)

    flows: list[dict[str, Any]] = []
    matched_tr_paths: list[str] = []
    for cluster in clusters:
        first_ts = cluster[0]["_parsed_ts"]
        last_ts = cluster[-1]["_parsed_ts"]
        first_sid = str(cluster[0]["_sid"])
        flow_id = f"delegate:{int(first_ts)}-{first_sid[:8]}"

        agents: list[dict[str, Any]] = []
        for idx, rec in enumerate(cluster, start=1):
            sid = rec["_sid"]
            cli = rec.get("cli")
            model = rec.get("model")
            role = rec.get("role") or "worker"
            ts = rec["_parsed_ts"]

            agent_lab = normalize_lab(model, cli)
            tokens, cost_usd, cost_estimated, tr_path, cache_read, trace = _resolve_usage_and_cost(
                sid,
                model,
                cli=cli,
                lab=agent_lab,
                fallback_cost=None,
                usage_index=usage_index,
                grok_lookup=grok_lookup,
                agy_lookup=agy_lookup,
                rate_table=rate_table,
            )
            if tr_path is not None:
                matched_tr_paths.append(str(tr_path))
            links: list[dict[str, Any]] = []
            sess_link = make_session_link(cli, sid)
            if sess_link is not None:
                links.append(sess_link)

            agent = make_agent(
                f"run-{idx}-{sid[:8]}",
                f"{model or cli or 'agent'} ({role})",
                role=str(role),
                lab=agent_lab,
                model=model,
                provider=cli,
                state="done",
                started_at=to_iso_utc(ts),
                ended_at=to_iso_utc(ts),
                tokens=tokens,
                cost_usd=cost_usd,
                trace=trace,
                links=links,
            )
            agent["costEstimated"] = cost_estimated
            if tokens is not None and cache_read is not None:
                agent["cacheRead"] = cache_read
            hint_parts = [
                p
                for p in (
                    str(role) if role else None,
                    str(model) if model else None,
                    str(cli) if cli else None,
                )
                if p
            ]
            hint_summary = " · ".join(hint_parts) if hint_parts else None
            agent["_labelHint"] = {
                "prompt": _bound_hint(rec.get("prompt") or hint_summary, 360),
                "slug": _bound_hint(
                    f"{cli}-{role}" if cli and role else (role or cli),
                    400,
                ),
                "planTask": _bound_hint(rec.get("task"), 400),
                "rawTitle": _bound_hint(f"{model or cli or 'agent'} ({role})", 400),
            }
            agents.append(agent)

        flows.append(
            make_flow(
                flow_id,
                f"Delegated batch ({len(cluster)} runs)",
                harness="delegate",
                kind="flat",
                started_at=to_iso_utc(first_ts),
                updated_at=to_iso_utc(last_ts),
                agents=agents,
            )
        )

    if use_cache:
        tr_sigs = [(trp, _stat_sig(trp)) for trp in set(matched_tr_paths)]
        _lru_set(_LEDGER_FLOWS_CACHE, ledger_key, (flow_sig, tr_sigs, flows), _LEDGER_FLOWS_CACHE_MAX)

    return flows


def discover_harness_runs(
    *,
    committee_dir: Path | str | None = None,
    orchestrate_dir: Path | str | None = None,
    sessions_ledger: Path | str | None = None,
    grok_sessions_root: Path | str | None = None,
    claude_root: Path | str | None = None,
    brain_roots: list[Path] | None = None,
    rate_table: RateTable | None = None,
    usage_index: UsageIndex | None = None,
    now_ts: float | None = None,
) -> list[dict[str, Any]]:
    """Discover committee, orchestrate, and orphaned agent-delegate multi-agent runs."""
    del claude_root  # Reserved for future fallback compatibility.

    c_dirs = _resolve_harness_dirs(committee_dir, DEFAULT_COMMITTEE_DIR, "committee")
    o_dirs = _resolve_harness_dirs(orchestrate_dir, DEFAULT_ORCHESTRATE_DIR, "orchestrate")
    ledger = Path(sessions_ledger) if sessions_ledger is not None else DEFAULT_SESSIONS_LEDGER
    g_root = Path(grok_sessions_root) if grok_sessions_root is not None else DEFAULT_GROK_SESSIONS_ROOT

    rates = rate_table if rate_table is not None else _DEFAULT_RATE_TABLE
    usage = usage_index if usage_index is not None else _DEFAULT_USAGE_INDEX
    grok_lookup = _GrokUsageLookup(g_root)
    agy_lookup = _AgyTranscriptLookup(brain_roots)
    use_cache = now_ts is None
    now = float(now_ts) if now_ts is not None else time.time()

    flows: list[dict[str, Any]] = []
    seen_session_ids: set[str] = set()
    seen_run_dirs: set[str] = set()
    seen_flow_ids: set[str] = set()

    for c_dir in c_dirs:
        if not c_dir.is_dir():
            continue
        try:
            subdirs = sorted(p for p in c_dir.iterdir() if p.is_dir())
        except OSError:
            subdirs = []
        for run_dir in subdirs:
            try:
                rd_key = os.path.realpath(run_dir)
            except OSError:
                rd_key = str(run_dir)
            if rd_key in seen_run_dirs:
                continue
            seen_run_dirs.add(rd_key)
            flow = _parse_committee_dir(
                run_dir,
                usage_index=usage,
                grok_lookup=grok_lookup,
                agy_lookup=agy_lookup,
                rate_table=rates,
                now_ts=now,
                seen_session_ids=seen_session_ids,
                use_cache=use_cache,
            )
            if flow is not None and flow["id"] not in seen_flow_ids:
                seen_flow_ids.add(flow["id"])
                flows.append(flow)

    for o_dir in o_dirs:
        if not o_dir.is_dir():
            continue
        try:
            subdirs = sorted(p for p in o_dir.iterdir() if p.is_dir())
        except OSError:
            subdirs = []
        for run_dir in subdirs:
            try:
                rd_key = os.path.realpath(run_dir)
            except OSError:
                rd_key = str(run_dir)
            if rd_key in seen_run_dirs:
                continue
            seen_run_dirs.add(rd_key)
            flow = _parse_orchestrate_dir(
                run_dir,
                usage_index=usage,
                grok_lookup=grok_lookup,
                agy_lookup=agy_lookup,
                rate_table=rates,
                now_ts=now,
                seen_session_ids=seen_session_ids,
                use_cache=use_cache,
            )
            if flow is not None and flow["id"] not in seen_flow_ids:
                seen_flow_ids.add(flow["id"])
                flows.append(flow)

    flows.extend(
        _parse_sessions_ledger(
            ledger,
            seen_session_ids=seen_session_ids,
            usage_index=usage,
            grok_lookup=grok_lookup,
            agy_lookup=agy_lookup,
            rate_table=rates,
            use_cache=use_cache,
        )
    )

    return flows


__all__ = ["discover_harness_runs"]
