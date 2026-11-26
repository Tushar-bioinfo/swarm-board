"""Unified read-only adapter registry for the Multi-Agent Board."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any

from ..safety import is_path_allowed, redact_if_secret_name, safe_read_citation_lines, safe_read_text
from ..schema import RateTable, UsageIndex, default_t3_userdata_dir, parse_iso_ts
from .harness_runs import (
    DEFAULT_COMMITTEE_DIR,
    DEFAULT_ORCHESTRATE_DIR,
    discover_harness_runs,
)

try:
    from ..labeler import (
        BackgroundLabeler,
        LabelCache,
        enrich_flow_labels,
        load_labeler_config,
    )
except ModuleNotFoundError as _exc:
    if _exc.name not in ("swarm_board.labeler", "labeler"):
        raise
    BackgroundLabeler = None  # type: ignore[assignment]
    LabelCache = None  # type: ignore[assignment]
    enrich_flow_labels = None  # type: ignore[assignment]
    load_labeler_config = None  # type: ignore[assignment]

_MAX_INLINE_OUTPUT_BYTES_IN_LIST = 16_384
_STATS_LABS = ("google", "anthropic", "openai", "xai", "t3", "other")
_STATE_TO_BUCKET = {
    "killed": "stopped",
    "error": "stopped",
    "waiting": "running",
    "stale": "done",
    "queued": "done",
}
_BUCKET_COLS = ("cost", "in", "cache", "out", "sav")
_BUCKET_STATES = ("running", "done", "stopped", "interrupted")
_HARNESS_PRIORITY = {
    "t3": 0,
    "agy": 1,
    "grok": 2,
    "codex": 3,
    "claude": 4,
    "committee": 5,
    "orchestrate": 6,
    "dropin": 7,
    "delegate": 8,
}
_NICE_STEPS = (60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600)
_TEST_ISOLATION_KEYS = (
    "statev2_db",
    "rates_path",
    "usage_cache_path",
    "dropin_dir",
    "committee_dir",
    "orchestrate_dir",
)


def _as_path_list(val: Any) -> list[Path] | None:
    if val is None:
        return None
    if isinstance(val, (str, Path)):
        return [Path(val)]
    if isinstance(val, (list, tuple, set)):
        return [Path(v) for v in val if v]
    return None


def _as_str_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, (str, Path)):
        return [str(val)]
    if isinstance(val, (list, tuple, set)):
        return [str(v) for v in val if v]
    return []


def _flow_has_running(flow: dict[str, Any]) -> bool:
    for agent in flow.get("agents") or ():
        if isinstance(agent, dict) and agent.get("state") == "running":
            return True
    return False


def _flow_sort_key(flow: dict[str, Any]) -> tuple[int, float, str]:
    running_rank = 0 if _flow_has_running(flow) else 1
    updated_ts = parse_iso_ts(flow.get("updatedAt") or flow.get("startedAt")) or 0.0
    return (running_rank, -updated_ts, str(flow.get("id") or ""))


def _lab_of(agent: dict[str, Any]) -> str:
    lab = agent.get("lab")
    return lab if lab in _STATS_LABS else "other"


def _mapped_state(state: str | None) -> str:
    s = _STATE_TO_BUCKET.get(str(state or ""), str(state or ""))
    return s if s in _BUCKET_STATES else "done"


def _agent_span(
    agent: dict[str, Any],
    now: float,
    flow: dict[str, Any] | None = None,
) -> tuple[float, float] | None:
    t0 = parse_iso_ts(agent.get("startedAt"))
    d_raw = agent.get("durS")
    d = float(d_raw) if isinstance(d_raw, (int, float)) and d_raw > 0 else None
    end_ts = parse_iso_ts(agent.get("endedAt"))

    if t0 is None and end_ts is None and flow is not None:
        t0 = parse_iso_ts(flow.get("startedAt"))
        if d is None:
            end_ts = parse_iso_ts(flow.get("updatedAt"))

    if t0 is None:
        if end_ts is None:
            return None
        dur = d if d is not None else 1.0
        t0 = end_ts - max(1.0, dur)
        return (t0, max(end_ts, t0 + 1.0))
    if d is not None:
        t1 = t0 + d
    else:
        if end_ts is not None:
            t1 = end_ts
        elif agent.get("state") == "running":
            t1 = now
        else:
            t1 = t0 + 1.0
    return (t0, max(t1, t0 + 1.0))


def _nice_step(sec: float) -> int:
    for s in _NICE_STEPS:
        if sec / s <= 40:
            return s
    return 43200


def _spread(
    buckets: dict[int, dict[str, dict[str, float]]],
    step: int,
    t0: float,
    t1: float,
    lab: str,
    vals: dict[str, float],
    state: str,
    *,
    clip_lo: float | None = None,
    clip_hi: float | None = None,
) -> None:
    """Distribute agent metrics pro rata across [step]-aligned buckets covering [t0, t1)."""
    span_len = t1 - t0
    if span_len <= 0.0:
        return
    eff_t0 = max(t0, clip_lo) if clip_lo is not None else t0
    eff_t1 = min(t1, clip_hi) if clip_hi is not None else t1
    if eff_t1 <= eff_t0:
        return
    k = int(eff_t0 // step) * step
    while k < eff_t1:
        f = (min(t1, k + step) - max(t0, k)) / span_len
        if f > 0.0:
            b = buckets.setdefault(k, {})
            lab_map = b.setdefault(lab, {})
            for col in _BUCKET_COLS:
                lab_map[col] = lab_map.get(col, 0.0) + vals[col] * f
            st_key = "_" + state
            lab_map[st_key] = lab_map.get(st_key, 0.0) + vals["dur"] * f
        k += step


def _pack_buckets(
    buckets: dict[int, dict[str, dict[str, float]]],
    step: int,
    *,
    fixed_t0: int | None = None,
) -> dict[str, Any]:
    t0 = fixed_t0 if fixed_t0 is not None else (min(buckets) if buckets else 0)
    packed_list: list[dict[str, Any]] = []
    for k in sorted(buckets):
        labs_out: dict[str, list[Any]] = {}
        for lab, v in buckets[k].items():
            row: list[Any] = [
                round(v.get("cost", 0.0), 3),
                round(v.get("in", 0.0)),
                round(v.get("cache", 0.0)),
                round(v.get("out", 0.0)),
                round(v.get("sav", 0.0), 2),
            ]
            for st in _BUCKET_STATES:
                row.append(round(v.get("_" + st, 0.0)))
            labs_out[lab] = row
        packed_list.append({"t": (k - t0) // step, "l": labs_out})
    return {
        "t0": t0,
        "step": step,
        "cols": list(_BUCKET_COLS),
        "states": list(_BUCKET_STATES),
        "b": packed_list,
    }


def _canonical_session_ids(agent: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for lk in agent.get("links") or ():
        if not isinstance(lk, dict):
            continue
        if lk.get("kind") in ("thread", "session", "resume"):
            sid = lk.get("id")
            if isinstance(sid, str) and sid.strip():
                out.append(sid.strip())
    return out


def enrich_flow_telemetry(
    flow: dict[str, Any],
    rate_table: RateTable | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    """Attach per-agent costSplit/cacheSavingsUsd/costEstimated and packed flow['timeline'] buckets."""
    rt = rate_table if rate_table is not None else RateTable()
    now = float(now_ts) if now_ts is not None else time.time()
    harness = str(flow.get("harness") or "")
    spans: list[tuple[dict[str, Any], float, float]] = []

    for agent in flow.get("agents") or ():
        if not isinstance(agent, dict):
            continue
        tok = agent.get("tokens")
        cost = agent.get("costUsd")
        cost_split, sav = rt.cost_breakdown(agent.get("model"), tok, cost_usd=cost)
        agent["costSplit"] = cost_split
        agent["cacheSavingsUsd"] = sav
        if not isinstance(agent.get("costEstimated"), bool):
            agent["costEstimated"] = False if (harness == "grok" and cost is not None) else True
        sp = _agent_span(agent, now, flow=flow)
        if sp is not None:
            spans.append((agent, sp[0], sp[1]))

    if not spans:
        flow["timeline"] = None
        return flow

    lo = min(s[1] for s in spans)
    hi = max(s[2] for s in spans)
    step = _nice_step(hi - lo)
    buckets: dict[int, dict[str, dict[str, float]]] = {}
    for agent, t0, t1 in spans:
        tok = agent.get("tokens") if isinstance(agent.get("tokens"), dict) else None
        vals = {
            "cost": float(agent.get("costUsd") or 0.0),
            "in": float(tok.get("in", 0) or 0) if tok else 0.0,
            "cache": float(tok.get("cache", 0) or 0) if tok else 0.0,
            "out": float(tok.get("out", 0) or 0) if tok else 0.0,
            "sav": float(agent.get("cacheSavingsUsd") or 0.0),
            "dur": float(agent.get("durS") or 0.0),
        }
        _spread(buckets, step, t0, t1, _lab_of(agent), vals, _mapped_state(agent.get("state")))

    flow["timeline"] = _pack_buckets(buckets, step)
    return flow


class BoardRegistry:
    """Thread-safe, TTL-cached aggregator across all read-only harness adapters."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        cache_ttl_s: float = 2.0,
    ) -> None:
        self.config: dict[str, Any] = dict(config) if config else {}
        self.cache_ttl_s: float = max(0.0, float(cache_ttl_s))

        self.rate_table = RateTable(self.config.get("rates_path"))
        self.usage_index = UsageIndex(self.config.get("usage_cache_path"))

        label_override: dict[str, Any] = {}
        for k in ("labeler", "labeler_config"):
            if isinstance(self.config.get(k), dict):
                label_override.update(self.config[k])
        if "labeler_enabled" in self.config:
            label_override["enabled"] = bool(self.config["labeler_enabled"])
        elif "enabled" not in label_override and any(
            k in self.config for k in _TEST_ISOLATION_KEYS
        ):
            label_override["enabled"] = False

        label_cache_raw = self.config.get("label_cache_path")
        if label_cache_raw:
            label_cache_path: Path | None = Path(label_cache_raw)
        elif self.config.get("state_dir"):
            label_cache_path = Path(self.config["state_dir"]) / "labels-cache.json"
        else:
            label_cache_path = None

        self._label_config: dict[str, Any] | None
        self._label_cache: Any | None
        self._bg_labeler: Any | None
        if (
            load_labeler_config is not None
            and LabelCache is not None
            and BackgroundLabeler is not None
        ):
            self._label_config = load_labeler_config(label_override or None)
            if label_override.get("enabled") is False and isinstance(self._label_config, dict):
                self._label_config["enabled"] = False
            self._label_cache = LabelCache(label_cache_path)
            self._bg_labeler = BackgroundLabeler(
                config=self._label_config,
                cache=self._label_cache,
            )
        else:
            self._label_config = None
            self._label_cache = None
            self._bg_labeler = None

        self._lock = threading.RLock()
        self._cached_at: float = 0.0
        self._has_snapshot: bool = False
        self._cached_flows: list[dict[str, Any]] = []
        self._flow_by_id: dict[str, dict[str, Any]] = {}
        self._cached_label_sig: tuple[int, float] = self._label_state_sig()
        self.last_errors: dict[str, str] = {}

    def _label_state_sig(self) -> tuple[int, float]:
        ver = int(self._bg_labeler.version()) if self._bg_labeler is not None else 0
        mtime = float(self._label_cache.mtime()) if self._label_cache is not None else 0.0
        return (ver, mtime)

    def close(self) -> None:
        """Stop background workers owned by this registry."""
        bg = getattr(self, "_bg_labeler", None)
        if bg is not None:
            try:
                bg.close()
            except Exception:  # noqa: BLE001
                pass

    def _import_optional_adapter(self, module_name: str) -> Any | None:
        return importlib.import_module(f".{module_name}", package=__package__)

    def _collect_all_flows_unlocked(
        self,
        *,
        enrich_labels: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        errors: dict[str, str] = {}
        collected: list[dict[str, Any]] = []
        cfg = self.config
        now_ts = cfg.get("now_ts")

        # 1. Antigravity native swarms
        try:
            mod = self._import_optional_adapter("agy_swarms")
            if mod is not None and hasattr(mod, "discover_swarms"):
                brain_roots = _as_path_list(cfg.get("brain_roots"))
                flows = mod.discover_swarms(
                    brain_roots=brain_roots,
                    rate_table=self.rate_table,
                    usage_index=self.usage_index,
                    now_ts=now_ts,
                )
                if isinstance(flows, list):
                    collected.extend(flows)
        except Exception as exc:  # noqa: BLE001 - isolate failing adapter
            errors["agy_swarms"] = f"{type(exc).__name__}: {exc}"

        # 2. CLI subagents (Grok / Codex / Claude / drop-ins)
        try:
            mod = self._import_optional_adapter("cli_subagents")
            if mod is not None and hasattr(mod, "discover_cli_flows"):
                flows = mod.discover_cli_flows(
                    grok_sessions_root=cfg.get("grok_sessions_root"),
                    codex_db_path=cfg.get("codex_db"),
                    claude_root=cfg.get("claude_root"),
                    dropin_dir=cfg.get("dropin_dir"),
                    rate_table=self.rate_table,
                    usage_index=self.usage_index,
                    now_ts=now_ts,
                )
                if isinstance(flows, list):
                    collected.extend(flows)
        except Exception as exc:  # noqa: BLE001 - isolate failing adapter
            errors["cli_subagents"] = f"{type(exc).__name__}: {exc}"

        # 3. T3 Code delegation trees
        try:
            mod = self._import_optional_adapter("t3_delegations")
            if mod is not None and hasattr(mod, "discover_t3_flows"):
                brain_roots = _as_path_list(cfg.get("brain_roots"))
                flows = mod.discover_t3_flows(
                    db_path=cfg.get("statev2_db"),
                    env_id=cfg.get("env_id"),
                    limit_trees=int(cfg.get("limit_trees", 25)),
                    rate_table=self.rate_table,
                    usage_index=self.usage_index,
                    brain_roots=brain_roots,
                    grok_sessions_root=cfg.get("grok_sessions_root"),
                    now_ts=now_ts,
                )
                if isinstance(flows, list):
                    collected.extend(flows)
        except Exception as exc:  # noqa: BLE001 - isolate failing adapter
            errors["t3_delegations"] = f"{type(exc).__name__}: {exc}"

        # 4. Harness runs (committee / orchestrate / agent-delegate ledger)
        try:
            flows = discover_harness_runs(
                committee_dir=cfg.get("committee_dir"),
                orchestrate_dir=cfg.get("orchestrate_dir"),
                sessions_ledger=cfg.get("sessions_ledger"),
                grok_sessions_root=cfg.get("grok_sessions_root"),
                claude_root=cfg.get("claude_root"),
                brain_roots=_as_path_list(cfg.get("brain_roots")),
                rate_table=self.rate_table,
                usage_index=self.usage_index,
                now_ts=now_ts,
            )
            if isinstance(flows, list):
                collected.extend(flows)
        except Exception as exc:  # noqa: BLE001 - isolate failing adapter
            errors["harness_runs"] = f"{type(exc).__name__}: {exc}"

        # Deduplicate by flow ID while keeping the first seen or newer entry.
        by_id: dict[str, dict[str, Any]] = {}
        for flow in collected:
            if not isinstance(flow, dict):
                continue
            fid = str(flow.get("id") or "").strip()
            if not fid:
                continue
            prev = by_id.get(fid)
            if prev is None:
                by_id[fid] = flow
            else:
                prev_ts = parse_iso_ts(prev.get("updatedAt") or prev.get("startedAt")) or 0.0
                cur_ts = parse_iso_ts(flow.get("updatedAt") or flow.get("startedAt")) or 0.0
                if cur_ts > prev_ts:
                    by_id[fid] = flow

        sorted_flows = sorted(by_id.values(), key=_flow_sort_key)
        enriched_flows: list[dict[str, Any]] = []
        for flow in sorted_flows:
            f_copy = dict(flow)
            raw_agents = flow.get("agents")
            if isinstance(raw_agents, list):
                f_copy["agents"] = [
                    dict(a) if isinstance(a, dict) else a for a in raw_agents
                ]
            raw_outputs = flow.get("outputs")
            if isinstance(raw_outputs, list):
                f_copy["outputs"] = [
                    dict(o) if isinstance(o, dict) else o for o in raw_outputs
                ]

            if enrich_labels and enrich_flow_labels is not None:
                try:
                    f_copy = enrich_flow_labels(
                        f_copy,
                        cache=self._label_cache,
                        background=self._bg_labeler,
                        config=self._label_config,
                    )
                except Exception as exc:  # noqa: BLE001 - isolate labeler failures
                    errors.setdefault("labeler", f"{type(exc).__name__}: {exc}")

            if enrich_labels:
                for agent in f_copy.get("agents") or ():
                    if isinstance(agent, dict):
                        agent.pop("_labelHint", None)
            enriched_flows.append(f_copy)

        return enriched_flows, errors

    def list_raw_flows(self) -> list[dict[str, Any]]:
        """Return shallow-copied discovered flows with `_labelHint` intact (for CLI batch labeling)."""
        with self._lock:
            flows, _ = self._collect_all_flows_unlocked(enrich_labels=False)
            return flows

    def _ensure_fresh_unlocked(self, force: bool = False) -> None:
        now_mono = time.monotonic()
        cur_label_sig = self._label_state_sig()
        if (
            not force
            and self._has_snapshot
            and (now_mono - self._cached_at) < self.cache_ttl_s
            and cur_label_sig == self._cached_label_sig
        ):
            return
        flows, errors = self._collect_all_flows_unlocked()
        self._cached_flows = flows
        self._flow_by_id = {str(f["id"]): f for f in flows if isinstance(f, dict) and f.get("id")}
        self.last_errors = errors
        self._cached_at = time.monotonic()
        self._cached_label_sig = self._label_state_sig()
        self._has_snapshot = True

    def _compact_flow(self, flow: dict[str, Any]) -> dict[str, Any]:
        """Return a shallow compacted copy with oversized inline output bodies cleared for lazy loading."""
        cloned = dict(flow)
        outputs = flow.get("outputs")
        if isinstance(outputs, list):
            new_outputs: list[Any] = []
            for out in outputs:
                if isinstance(out, dict):
                    body = out.get("body")
                    if isinstance(body, str) and len(body) > _MAX_INLINE_OUTPUT_BYTES_IN_LIST:
                        out_copy = dict(out)
                        out_copy["body"] = None
                        new_outputs.append(out_copy)
                        continue
                new_outputs.append(out)
            cloned["outputs"] = new_outputs
        return cloned

    def _extra_allowed_roots(self) -> list[str]:
        return _as_str_list(self.config.get("extra_allowed_roots"))

    def _output_allowed_roots(self, flow: dict[str, Any]) -> list[str]:
        """Allowed roots for reading registered adapter Output artifacts."""
        roots: list[str] = []
        proj_root = flow.get("projectRoot")
        if isinstance(proj_root, str) and proj_root:
            roots.append(proj_root)
        roots.extend(self._extra_allowed_roots())

        fid = str(flow.get("id") or "")
        harness = str(flow.get("harness") or "")
        if harness == "committee" or fid.startswith("committee:"):
            run_name = fid.split(":", 1)[1] if ":" in fid else ""
            if run_name:
                if self.config.get("committee_dir"):
                    roots.append(str(Path(self.config["committee_dir"]) / run_name))
                else:
                    roots.append(str(DEFAULT_COMMITTEE_DIR / run_name))
                    roots.append(str(Path(tempfile.gettempdir()) / "committee" / run_name))
        elif harness == "orchestrate" or fid.startswith("orchestrate:"):
            run_name = fid.split(":", 1)[1] if ":" in fid else ""
            if run_name:
                if self.config.get("orchestrate_dir"):
                    roots.append(str(Path(self.config["orchestrate_dir"]) / run_name))
                else:
                    roots.append(str(DEFAULT_ORCHESTRATE_DIR / run_name))
                    roots.append(str(Path(tempfile.gettempdir()) / "orchestrate" / run_name))

        dropin = self.config.get("dropin_dir")
        if dropin:
            roots.append(str(dropin))

        dedup: list[str] = []
        seen: set[str] = set()
        for r in roots:
            if not r:
                continue
            try:
                key = os.path.realpath(r)
            except OSError:
                key = r
            if key not in seen:
                seen.add(key)
                dedup.append(r)
        return dedup

    def list_flows(self, force: bool = False) -> list[dict[str, Any]]:
        """Return all discovered flows sorted with running flows first, then updatedAt desc."""
        with self._lock:
            self._ensure_fresh_unlocked(force=force)
            return [self._compact_flow(f) for f in self._cached_flows]

    def get_flow(self, flow_id: str) -> dict[str, Any] | None:
        """Return a single full flow enriched with telemetry breakdown and timeline buckets."""
        with self._lock:
            self._ensure_fresh_unlocked(force=False)
            flow = self._flow_by_id.get(flow_id)
            if flow is None:
                return None
            cloned = copy.deepcopy(flow)
            now_ts = self.config.get("now_ts")
        return enrich_flow_telemetry(cloned, rate_table=self.rate_table, now_ts=now_ts)

    def get_env_id(self) -> str | None:
        """Return configured or locally stored T3 environment ID."""
        cfg_env = self.config.get("env_id")
        if isinstance(cfg_env, str) and cfg_env.strip():
            return cfg_env.strip()
        try:
            mod = self._import_optional_adapter("t3_delegations")
            if mod is not None and hasattr(mod, "_read_env_id"):
                return mod._read_env_id(None)
        except Exception:  # noqa: BLE001
            pass
        try:
            text = (default_t3_userdata_dir() / "environment-id").read_text(encoding="utf-8").strip()
            return text or None
        except OSError:
            return None

    def get_raw_file_path(self, flow_id: str, path: str) -> dict[str, Any]:
        """Resolve and validate a raw file path strictly within a flow's allowed roots."""
        if not path or not isinstance(path, str):
            return {"ok": False, "error": "Missing file path"}
        with self._lock:
            self._ensure_fresh_unlocked(force=False)
            flow = self._flow_by_id.get(flow_id)
            if flow is None:
                return {"ok": False, "error": f"Unknown flow: {flow_id}"}
            allowed_roots = self._output_allowed_roots(flow)

        if not allowed_roots:
            return {"ok": False, "error": "Flow has no allowed roots"}

        secret_reason = redact_if_secret_name(path)
        if secret_reason:
            return {"ok": False, "error": secret_reason}

        p = Path(path)
        if p.is_absolute():
            ok, res = is_path_allowed(path, allowed_roots)
            if not ok or not res:
                return {"ok": False, "error": res or "Access denied"}
            if not os.path.isfile(res):
                return {"ok": False, "error": "Not found under allowed roots"}
            return {"ok": True, "path": res}

        last_err = "Not found under allowed roots"
        for root in allowed_roots:
            cand = str(Path(root) / p)
            ok, res = is_path_allowed(cand, [root])
            if not ok:
                last_err = res or "Access denied"
                continue
            if res and os.path.isfile(res):
                return {"ok": True, "path": res}
        return {"ok": False, "error": last_err}

    def get_stats(
        self,
        scope: str = "all",
        flow_id: str | None = None,
        rng: str = "all",
        lab: str | None = None,
    ) -> dict[str, Any]:
        """Compute deduplicated, range-sliced telemetry statistics across flows or one flow."""
        scope_norm = "flow" if scope == "flow" else "all"
        rng_norm = rng if rng in ("24h", "7d", "all") else "all"
        lab_norm = (
            lab.strip().lower()
            if isinstance(lab, str) and lab.strip() and lab.strip().lower() != "all"
            else None
        )

        with self._lock:
            self._ensure_fresh_unlocked(force=False)
            errors = dict(self.last_errors)
            now = (
                float(self.config["now_ts"])
                if self.config.get("now_ts") is not None
                else time.time()
            )
            if scope_norm == "flow":
                single = self._flow_by_id.get(flow_id) if flow_id else None
                target_flows = [single] if single is not None else []
            else:
                target_flows = sorted(
                    self._cached_flows,
                    key=lambda fl: (
                        _HARNESS_PRIORITY.get(str(fl.get("harness") or ""), 99),
                        str(fl.get("id") or ""),
                    ),
                )

        if scope_norm == "all":
            step = 3600
            hi_ts = int(now // 3600) * 3600
            if rng_norm == "24h":
                window_start: float | None = float(hi_ts - 23 * 3600)
                window_end: float | None = float(hi_ts + 3600)
                fixed_t0: int | None = int(window_start)
            elif rng_norm == "7d":
                window_start = float(hi_ts - 167 * 3600)
                window_end = float(hi_ts + 3600)
                fixed_t0 = int(window_start)
            else:
                window_start = None
                window_end = None
                fixed_t0 = None
        else:
            all_spans: list[tuple[float, float]] = []
            for f in target_flows:
                for a in f.get("agents") or ():
                    if isinstance(a, dict):
                        sp = _agent_span(a, now, flow=f)
                        if sp is not None:
                            all_spans.append(sp)
            if all_spans:
                lo = min(s[0] for s in all_spans)
                hi = max(s[1] for s in all_spans)
                step = _nice_step(hi - lo)
            else:
                step = 3600
            if rng_norm in ("24h", "7d"):
                dur_w = 86400.0 if rng_norm == "24h" else 7.0 * 86400.0
                window_start = now - dur_w
                window_end = now
                fixed_t0 = int(window_start // step) * step
            else:
                window_start = None
                window_end = None
                fixed_t0 = None

        seen_cross_flow_sids: dict[str, str] = {}
        dup_rows = 0
        agents_count = 0
        matched_flow_ids: set[str] = set()
        buckets_acc: dict[int, dict[str, dict[str, float]]] = {}
        by_model: dict[str, dict[str, Any]] = {}
        top_agents: list[list[Any]] = []

        for f in target_flows:
            if not isinstance(f, dict):
                continue
            f_id = str(f.get("id") or "")
            f_title = str(f.get("title") or f_id)[:40]
            f_harness = str(f.get("harness") or "")
            seen_in_flow_usage: set[tuple[tuple[tuple[str, int], ...], float | None]] = set()

            for a in f.get("agents") or ():
                if not isinstance(a, dict):
                    continue
                a_id = str(a.get("id") or "")
                a_lab = _lab_of(a)
                tok = a.get("tokens") if isinstance(a.get("tokens"), dict) else None
                cost = a.get("costUsd")
                sids = _canonical_session_ids(a)
                tok_key = (tuple(sorted((str(k), int(v or 0)) for k, v in tok.items())), cost) if tok is not None else None

                is_dup = False
                if a.get("usageDeduped"):
                    is_dup = True
                elif scope_norm == "all" and (tok is not None or cost is not None):
                    if any(sid in seen_cross_flow_sids and seen_cross_flow_sids[sid] != f_id for sid in sids):
                        is_dup = True
                if not is_dup and tok_key is not None and a_id.startswith("node:") and tok_key in seen_in_flow_usage:
                    is_dup = True

                if not is_dup and (tok is not None or cost is not None):
                    if tok_key is not None:
                        seen_in_flow_usage.add(tok_key)
                    if scope_norm == "all":
                        for sid in sids:
                            seen_cross_flow_sids.setdefault(sid, f_id)

                if lab_norm is not None and a_lab != lab_norm:
                    continue

                sp = _agent_span(a, now, flow=f)
                if window_start is not None and window_end is not None:
                    if sp is None:
                        continue
                    ov = min(sp[1], window_end) - max(sp[0], window_start)
                    if ov <= 0.0:
                        continue
                    frac = ov / (sp[1] - sp[0])
                else:
                    frac = 1.0

                agents_count += 1
                matched_flow_ids.add(f_id)

                if is_dup:
                    dup_rows += 1
                    tok_eff_src: dict[str, int] | None = None
                    cost_eff_src: float | None = None
                else:
                    tok_eff_src = tok
                    cost_eff_src = float(cost) if cost is not None else None

                cost_split, sav = self.rate_table.cost_breakdown(
                    a.get("model"),
                    tok_eff_src,
                    cost_usd=cost_eff_src,
                )
                in_raw = int(tok_eff_src.get("in", 0) or 0) if tok_eff_src else 0
                cache_raw = int(tok_eff_src.get("cache", 0) or 0) if tok_eff_src else 0
                out_raw = int(tok_eff_src.get("out", 0) or 0) if tok_eff_src else 0
                dur_raw = float(a.get("durS") or 0.0)

                if sp is not None:
                    vals_full = {
                        "cost": cost_eff_src or 0.0,
                        "in": float(in_raw),
                        "cache": float(cache_raw),
                        "out": float(out_raw),
                        "sav": float(sav or 0.0),
                        "dur": dur_raw,
                    }
                    _spread(
                        buckets_acc,
                        step,
                        sp[0],
                        sp[1],
                        a_lab,
                        vals_full,
                        _mapped_state(a.get("state")),
                        clip_lo=window_start,
                        clip_hi=window_end,
                    )

                eff_dur = dur_raw * frac
                eff_in = in_raw * frac
                eff_cache = cache_raw * frac
                eff_out = out_raw * frac
                eff_cost = (cost_eff_src * frac) if cost_eff_src is not None else None
                eff_sav = (float(sav) * frac) if sav is not None else 0.0

                mk = str(a.get("model") or "unknown")
                m_rec = by_model.get(mk)
                if m_rec is None:
                    m_rec = {
                        "model": mk,
                        "lab": a_lab,
                        "agents": 0,
                        "durS": 0.0,
                        "in": 0.0,
                        "cache": 0.0,
                        "out": 0.0,
                        "costUsd": 0.0,
                        "costEstimated": True,
                        "cacheSav": 0.0,
                        "costSplit": {"cached": 0.0, "uncached": 0.0, "output": 0.0},
                        "rated": 0,
                        "_all_exact": True,
                    }
                    by_model[mk] = m_rec
                m_rec["agents"] += 1
                m_rec["durS"] += eff_dur
                m_rec["in"] += eff_in
                m_rec["cache"] += eff_cache
                m_rec["out"] += eff_out
                if eff_cost is not None:
                    m_rec["costUsd"] += eff_cost
                    m_rec["rated"] += 1
                    a_est = a.get("costEstimated")
                    if not isinstance(a_est, bool):
                        a_est = False if f_harness == "grok" else True
                    if a_est:
                        m_rec["_all_exact"] = False
                m_rec["cacheSav"] += eff_sav
                if cost_split:
                    for k_split in ("cached", "uncached", "output"):
                        m_rec["costSplit"][k_split] += cost_split[k_split] * frac

                if (eff_cost is not None and eff_cost > 0.0) or (eff_in + eff_cache + eff_out) > 0.0:
                    disp_name = (
                        "root"
                        if a.get("role") == "root"
                        else str(a.get("label") or a.get("name") or a_id.split("%3A")[-1][-24:])
                    )[:48]
                    top_agents.append(
                        [
                            disp_name,
                            f_title,
                            a.get("model"),
                            a_lab,
                            a.get("state"),
                            round(eff_dur),
                            round(eff_in),
                            round(eff_cache),
                            round(eff_out),
                            round(eff_cost, 4) if eff_cost is not None else None,
                            round(sp[0]) if sp is not None else None,
                            round(sp[1]) if sp is not None else None,
                            f_id,
                            a_id,
                        ]
                    )

        model_rows = sorted(
            by_model.values(),
            key=lambda r: (-r["costUsd"], -(r["in"] + r["cache"] + r["out"])),
        )
        for m in model_rows:
            all_exact = m.pop("_all_exact", False)
            m["costEstimated"] = True if (m["rated"] == 0 or not all_exact) else False
            m["durS"] = round(m["durS"])
            m["in"] = round(m["in"])
            m["cache"] = round(m["cache"])
            m["out"] = round(m["out"])
            m["costUsd"] = round(m["costUsd"], 4)
            m["cacheSav"] = round(m["cacheSav"], 4)
            m["costSplit"] = {k: round(v, 4) for k, v in m["costSplit"].items()}

        cost_by_type = {
            k: round(sum(m["costSplit"][k] for m in model_rows), 4)
            for k in ("cached", "uncached", "output")
        }
        top_agents.sort(
            key=lambda row: (
                -(row[9] if row[9] is not None else -1.0),
                -(row[6] + row[7] + row[8]),
            )
        )
        rated_agents = sum(m["rated"] for m in model_rows)
        flows_count = (
            len(target_flows)
            if (rng_norm == "all" and lab_norm is None)
            else len(matched_flow_ids)
        )
        if fixed_t0 is not None:
            packed_t0 = fixed_t0
        elif buckets_acc:
            packed_t0 = min(buckets_acc)
        else:
            packed_t0 = int(now // step) * step

        return {
            "scope": scope_norm,
            "rng": rng_norm,
            "lab": lab_norm,
            "flowsCount": flows_count,
            "agentsCount": agents_count,
            "ratedAgents": rated_agents,
            "now": round(now),
            "costUsd": round(sum(m["costUsd"] for m in model_rows), 4),
            "cacheSavingsUsd": round(sum(m["cacheSav"] for m in model_rows), 4),
            "costByType": cost_by_type,
            "buckets": _pack_buckets(buckets_acc, step, fixed_t0=packed_t0),
            "byModel": model_rows,
            "topAgents": top_agents[:100],
            "dupRows": dup_rows,
            "errors": errors,
        }

    def get_output(self, flow_id: str, output_id: str) -> dict[str, Any] | None:
        """Resolve an output's content (inline body, transcript/post/subagent loader, or safe file read)."""
        with self._lock:
            self._ensure_fresh_unlocked(force=False)
            flow = self._flow_by_id.get(flow_id)
            if flow is None:
                return None
            target: dict[str, Any] | None = None
            for out in flow.get("outputs") or ():
                if isinstance(out, dict) and out.get("id") == output_id:
                    target = copy.deepcopy(out)
                    break
            if target is None:
                return None
            allowed_roots = self._output_allowed_roots(flow)
            harness = str(flow.get("harness") or "")
            proj_root = flow.get("projectRoot")

        # 1. Inline body already present.
        if isinstance(target.get("body"), str):
            return {**target, "ok": True}

        kind = str(target.get("kind") or "")
        meta = target.get("meta") if isinstance(target.get("meta"), dict) else {}

        # 2. Transcript outputs (checked BEFORE subagentId since T3 transcripts carry both).
        if kind == "transcript":
            thread_id = meta.get("threadId")
            if thread_id:
                try:
                    mod = self._import_optional_adapter("t3_delegations")
                    if mod is not None and hasattr(mod, "load_thread_messages"):
                        msgs = mod.load_thread_messages(self.config.get("statev2_db"), str(thread_id))
                        if msgs:
                            return {
                                **target,
                                "ok": True,
                                "messages": msgs,
                                "body": json.dumps(msgs, indent=2, ensure_ascii=False),
                            }
                except Exception as exc:  # noqa: BLE001
                    return {**target, "ok": False, "error": f"Failed to load transcript: {exc}"}

                # Fallback when a T3 thread's messages aren't in SQLite but an AGY transcript path exists.
                t_path = target.get("path")
                if isinstance(t_path, str) and t_path:
                    p = Path(t_path)
                    if p.name == "transcript.jsonl" and len(p.parents) >= 4 and p.is_file():
                        try:
                            agy_mod = self._import_optional_adapter("agy_swarms")
                            if agy_mod is not None and hasattr(agy_mod, "load_transcript_messages"):
                                msgs = agy_mod.load_transcript_messages(p.parents[3], p.parents[2].name)
                                return {
                                    **target,
                                    "ok": True,
                                    "messages": msgs,
                                    "body": json.dumps(msgs, indent=2, ensure_ascii=False),
                                }
                        except Exception as exc:  # noqa: BLE001
                            return {**target, "ok": False, "error": f"Failed to load transcript: {exc}"}

            brain_dir = meta.get("brainDir") or meta.get("brain_dir")
            conv_id = meta.get("conversationId") or meta.get("convId") or meta.get("conv_id")
            if brain_dir and conv_id:
                try:
                    mod = self._import_optional_adapter("agy_swarms")
                    if mod is not None and hasattr(mod, "load_transcript_messages"):
                        msgs = mod.load_transcript_messages(Path(brain_dir), str(conv_id))
                        return {
                            **target,
                            "ok": True,
                            "messages": msgs,
                            "body": json.dumps(msgs, indent=2, ensure_ascii=False),
                        }
                except Exception as exc:  # noqa: BLE001
                    return {**target, "ok": False, "error": f"Failed to load transcript: {exc}"}

            if harness == "grok" and isinstance(target.get("path"), str) and target["path"]:
                try:
                    cli_mod = self._import_optional_adapter("cli_subagents")
                    if cli_mod is not None and hasattr(cli_mod, "load_grok_chat_history"):
                        msgs = cli_mod.load_grok_chat_history(target["path"])
                        return {
                            **target,
                            "ok": True,
                            "messages": msgs,
                            "body": json.dumps(msgs, indent=2, ensure_ascii=False),
                        }
                except Exception as exc:  # noqa: BLE001
                    return {**target, "ok": False, "error": f"Failed to load Grok chat: {exc}"}

        # 3. Oversized AGY board posts (> 64 KB) stored lazily in board.jsonl.
        if kind in ("post", "brief") and meta.get("boardPath"):
            try:
                agy_mod = self._import_optional_adapter("agy_swarms")
                if agy_mod is not None and hasattr(agy_mod, "load_swarm_output"):
                    res = agy_mod.load_swarm_output(
                        {"outputs": [target], "projectRoot": proj_root},
                        output_id,
                    )
                    if isinstance(res, dict) and res.get("ok"):
                        return {
                            **target,
                            "ok": True,
                            "body": res.get("body"),
                        }
                    return {
                        **target,
                        "ok": False,
                        "error": (res or {}).get("error") or "Board post not found",
                    }
            except Exception as exc:  # noqa: BLE001
                return {**target, "ok": False, "error": f"Failed to load board post: {exc}"}

        # 4. Lazy T3 subagent detail loader (prompt + full result).
        subagent_id = meta.get("subagentId") or meta.get("subagent_id")
        if subagent_id:
            try:
                mod = self._import_optional_adapter("t3_delegations")
                if mod is not None and hasattr(mod, "load_subagent_detail"):
                    detail = mod.load_subagent_detail(self.config.get("statev2_db"), str(subagent_id))
                    if detail:
                        body = (
                            detail
                            if isinstance(detail, str)
                            else json.dumps(detail, indent=2, ensure_ascii=False)
                        )
                        return {**target, "ok": True, "detail": detail, "body": body}
                    return {**target, "ok": False, "error": "Subagent detail not found"}
            except Exception as exc:  # noqa: BLE001
                return {**target, "ok": False, "error": f"Failed to load subagent detail: {exc}"}

        # 5. Safe read from disk up to 1 MB.
        path = target.get("path")
        if isinstance(path, str) and path:
            res = safe_read_text(path, allowed_roots, max_bytes=1_048_576)
            if res.get("ok"):
                return {
                    **target,
                    "ok": True,
                    "path": res["path"],
                    "size": res["size"],
                    "truncated": res["truncated"],
                    "mime": res["mime"],
                    "body": res["body"],
                }
            return {**target, "ok": False, "error": res.get("error") or "Access denied"}

        return {**target, "ok": False, "error": "Output has no readable body or path"}

    def get_citation(
        self,
        flow_id: str,
        path: str,
        line: int,
        ctx: int = 4,
    ) -> dict[str, Any]:
        """Read citation lines strictly confined to [flow['projectRoot'], *extra_allowed_roots]."""
        with self._lock:
            self._ensure_fresh_unlocked(force=False)
            flow = self._flow_by_id.get(flow_id)
            if flow is None:
                return {"ok": False, "error": f"Unknown flow: {flow_id}"}
            allowed_roots: list[str] = []
            proj_root = flow.get("projectRoot")
            if isinstance(proj_root, str) and proj_root:
                allowed_roots.append(proj_root)
            allowed_roots.extend(self._extra_allowed_roots())

        if not allowed_roots:
            return {"ok": False, "error": "Flow has no allowed project root"}
        return safe_read_citation_lines(path, line, allowed_roots, ctx=ctx)

    def get_thread_messages(
        self,
        thread_id: str,
        limit: int = 50,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        """Load messages for a T3 thread or Antigravity conversation lazily."""
        messages: list[dict[str, Any]] = []
        used_sqlite_before = False
        before_ts = parse_iso_ts(before) if before else None
        try:
            mod = self._import_optional_adapter("t3_delegations")
            if mod is not None and hasattr(mod, "load_thread_messages"):
                before_arg = before if before_ts is not None else None
                res = mod.load_thread_messages(
                    self.config.get("statev2_db"),
                    thread_id,
                    limit=limit,
                    before_created_at=before_arg,
                )
                if isinstance(res, list) and res:
                    messages = res
                    if before_arg is not None:
                        used_sqlite_before = True
        except Exception:  # noqa: BLE001
            pass

        if not messages:
            try:
                mod = self._import_optional_adapter("agy_swarms")
                if mod is not None and hasattr(mod, "load_transcript_messages"):
                    brain_roots = _as_path_list(self.config.get("brain_roots"))
                    if brain_roots is None and hasattr(mod, "default_brain_roots"):
                        brain_roots = mod.default_brain_roots()
                    for br in brain_roots or ():
                        candidate = br / thread_id
                        if candidate.is_dir():
                            res = mod.load_transcript_messages(br, thread_id, limit=limit)
                            if isinstance(res, list) and res:
                                messages = res
                                break
            except Exception:  # noqa: BLE001
                pass

        if before and messages and not used_sqlite_before:
            if before_ts is not None:
                filtered = [
                    m
                    for m in messages
                    if isinstance(m, dict)
                    and (parse_iso_ts(m.get("at") or m.get("createdAt")) or 0.0) < before_ts
                ]
                messages = filtered[-limit:] if limit > 0 else filtered
            else:
                idx = next(
                    (i for i, m in enumerate(messages) if isinstance(m, dict) and str(m.get("id")) == before),
                    None,
                )
                if idx is not None:
                    messages = messages[max(0, idx - limit) : idx]

        return messages

    def snapshot_digest(self) -> str:
        """Fast deterministic hash of flow/agent states and post/output counts for SSE diffing."""
        with self._lock:
            self._ensure_fresh_unlocked(force=False)
            lv, lm = self._label_state_sig()
            summary = {
                "lv": lv,
                "lm": lm,
                "flows": [
                    {
                        "id": f.get("id"),
                        "u": f.get("updatedAt"),
                        "p": len(f.get("posts") or ()),
                        "o": len(f.get("outputs") or ()),
                        "d": f.get("decision"),
                        "a": [
                            (
                                a.get("id"),
                                a.get("state"),
                                a.get("verdict"),
                                a.get("last"),
                                a.get("label"),
                                a.get("task"),
                            )
                            for a in (f.get("agents") or ())
                            if isinstance(a, dict)
                        ],
                    }
                    for f in self._cached_flows
                    if isinstance(f, dict)
                ],
            }
        payload = json.dumps(summary, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]


__all__ = ["BoardRegistry", "discover_harness_runs", "enrich_flow_telemetry"]
