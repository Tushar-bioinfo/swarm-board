"""Read-only adapter for Grok headless sessions, Codex subagents, Claude teams/subagents, and drop-in JSON runs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

from swarm_board.schema import (
    RateTable,
    UsageIndex,
    compute_dur_s,
    make_agent,
    make_flow,
    make_link,
    make_output,
    make_post,
    make_session_link,
    normalize_lab,
    normalize_state,
    parse_iso_ts,
    to_iso_utc,
)

GROK_CLUSTER_WINDOW_S = 7200.0
GROK_STALE_THRESHOLD_S = 600.0
GROK_COST_TICKS_PER_USD = 1e10
TAIL_READ_BYTES = 24_576

_DEFAULT_RATE_TABLE = RateTable()
_DEFAULT_USAGE_INDEX = UsageIndex()


def _safe_json_load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _truncate(text: str | None, limit: int = 160) -> str | None:
    if not text:
        return None
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return None
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _read_tail_jsonl(path: Path, *, max_bytes: int = TAIL_READ_BYTES, max_lines: int = 40) -> list[dict[str, Any]]:
    """Read the last JSONL objects from a file without loading multi-MB files into memory."""
    try:
        st = path.stat()
        if st.st_size <= 0:
            return []
        with path.open("rb") as fh:
            if st.st_size > max_bytes:
                fh.seek(st.st_size - max_bytes)
                raw = fh.read()
                # Drop partial first line after seek.
                nl = raw.find(b"\n")
                if nl != -1:
                    raw = raw[nl + 1 :]
            else:
                raw = fh.read()
    except OSError:
        return []

    out: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines()[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def load_grok_chat_history(path: str | Path, limit: int = 50) -> list[dict[str, Any]]:
    """Lazily read the latest chat messages from a Grok chat_history.jsonl file."""
    p = Path(path)
    if not p.is_file():
        return []
    messages: list[dict[str, Any]] = []
    try:
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                role = str(obj.get("type") or obj.get("role") or "unknown")
                content = obj.get("content")
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False) if content is not None else ""
                messages.append(
                    {
                        "role": role,
                        "content": content[:8192],
                        "at": to_iso_utc(parse_iso_ts(obj.get("ts") or obj.get("timestamp"))),
                    }
                )
    except OSError:
        return []
    return messages[-max(1, int(limit)) :]


# ---------------------------------------------------------------------------
# 1. Grok Headless Sessions
# ---------------------------------------------------------------------------


def _parse_grok_session(
    session_dir: Path,
    *,
    rate_table: RateTable,
    now_ts: float,
) -> dict[str, Any] | None:
    summary = _safe_json_load(session_dir / "summary.json")
    if not isinstance(summary, dict):
        return None
    if summary.get("session_kind") != "headless":
        return None

    info = summary.get("info") if isinstance(summary.get("info"), dict) else {}
    sid = str(info.get("id") or session_dir.name).strip()
    if not sid:
        return None

    cwd = str(info.get("cwd") or summary.get("git_root_dir") or "").strip()
    title = str(
        summary.get("generated_title")
        or summary.get("session_summary")
        or f"Grok {sid[:8]}"
    ).strip()

    created_ts = parse_iso_ts(summary.get("created_at"))
    updated_ts = parse_iso_ts(summary.get("updated_at") or summary.get("last_active_at"))

    usage_doc = _safe_json_load(session_dir / "usage.json")
    usage_sess = (
        usage_doc.get("session")
        if isinstance(usage_doc, dict) and isinstance(usage_doc.get("session"), dict)
        else {}
    )

    model = str(
        usage_sess.get("primaryModelId")
        or summary.get("current_model_id")
        or "grok"
    ).strip()

    in_raw = int(usage_sess.get("inputTokens") or 0)
    out_tok = int(usage_sess.get("outputTokens") or 0)
    cache_read = int(usage_sess.get("cachedReadTokens") or 0)
    cache_write = int(usage_sess.get("cacheCreationTokens") or 0)
    non_cached_in = max(0, in_raw - cache_read)

    tokens: dict[str, int] | None = None
    if in_raw or out_tok or cache_read or cache_write:
        tokens = {
            "in": non_cached_in,
            "cache": cache_read + cache_write,
            "out": out_tok,
        }

    cost_usd: float | None = None
    cost_ticks = usage_sess.get("costUsdTicks")
    if isinstance(cost_ticks, (int, float)) and cost_ticks > 0:
        cost_usd = round(float(cost_ticks) / GROK_COST_TICKS_PER_USD, 4)
    elif tokens is not None:
        cost_usd = rate_table.estimate_cost_usd(
            model,
            tokens,
            cache_read=cache_read,
            cache_write=cache_write,
        )

    # Tail-inspect events.jsonl and chat_history.jsonl (never read updates.jsonl).
    events_path = session_dir / "events.jsonl"
    chat_path = session_dir / "chat_history.jsonl"

    state = "done"
    tail_events = _read_tail_jsonl(events_path) if events_path.is_file() else []
    turn_outcome: str | None = None
    for ev in reversed(tail_events):
        if ev.get("type") == "turn_ended":
            turn_outcome = str(ev.get("outcome") or "").lower()
            break

    last_assistant: str | None = None
    tail_chat = _read_tail_jsonl(chat_path) if chat_path.is_file() else []
    for msg in reversed(tail_chat):
        if msg.get("type") == "assistant" and isinstance(msg.get("content"), str) and msg["content"].strip():
            last_assistant = msg["content"].strip()
            break

    if turn_outcome:
        state = normalize_state(turn_outcome)
    else:
        ref_mtime = updated_ts
        for probe in (events_path, chat_path, session_dir / "summary.json"):
            try:
                m = probe.stat().st_mtime
                if ref_mtime is None or m > ref_mtime:
                    ref_mtime = m
            except OSError:
                pass
        if last_assistant:
            state = "done"
        elif ref_mtime is not None and (now_ts - ref_mtime) <= GROK_STALE_THRESHOLD_S:
            state = "running"
        else:
            state = "stale"

    chat_size: int | None = None
    try:
        if chat_path.is_file():
            chat_size = chat_path.stat().st_size
    except OSError:
        chat_size = None

    return {
        "sid": sid,
        "cwd": cwd,
        "title": title,
        "model": model,
        "reasoning_effort": summary.get("reasoning_effort"),
        "created_ts": created_ts,
        "updated_ts": updated_ts,
        "state": state,
        "tokens": tokens,
        "cost_usd": cost_usd,
        "model_calls": usage_sess.get("modelCalls"),
        "last": _truncate(summary.get("last_turn_summary") or last_assistant, 160),
        "result": _truncate(last_assistant or summary.get("last_turn_summary"), 400),
        "chat_path": str(chat_path) if chat_path.is_file() else None,
        "chat_size": chat_size,
    }


def _discover_grok_flows(
    grok_sessions_root: Path,
    *,
    rate_table: RateTable,
    now_ts: float,
) -> list[dict[str, Any]]:
    if not grok_sessions_root.is_dir():
        return []

    sessions_by_cwd: dict[str, list[dict[str, Any]]] = {}
    try:
        project_dirs = sorted(p for p in grok_sessions_root.iterdir() if p.is_dir())
    except OSError:
        return []

    for pdir in project_dirs:
        try:
            sdirs = sorted(p for p in pdir.iterdir() if p.is_dir())
        except OSError:
            continue
        for sdir in sdirs:
            parsed = _parse_grok_session(sdir, rate_table=rate_table, now_ts=now_ts)
            if parsed is None:
                continue
            cwd_key = parsed["cwd"] or pdir.name
            sessions_by_cwd.setdefault(cwd_key, []).append(parsed)

    flows: list[dict[str, Any]] = []
    for cwd_key, items in sessions_by_cwd.items():
        items.sort(key=lambda s: s["created_ts"] or 0.0)
        # Cluster sessions sharing the same cwd within a 2-hour window.
        clusters: list[list[dict[str, Any]]] = []
        for item in items:
            ts = item["created_ts"] or 0.0
            if not clusters:
                clusters.append([item])
                continue
            prev_ts = clusters[-1][-1]["created_ts"] or 0.0
            if abs(ts - prev_ts) <= GROK_CLUSTER_WINDOW_S:
                clusters[-1].append(item)
            else:
                clusters.append([item])

        for cluster in clusters:
            first = cluster[0]
            cwd = first["cwd"] or None
            project_name = Path(cwd).name if cwd else cwd_key
            starts = [s["created_ts"] for s in cluster if s["created_ts"] is not None]
            ends = [
                s["updated_ts"] or s["created_ts"]
                for s in cluster
                if (s["updated_ts"] or s["created_ts"]) is not None
            ]
            flow_start = to_iso_utc(min(starts)) if starts else None
            flow_updated = to_iso_utc(max(ends)) if ends else flow_start

            agents: list[dict[str, Any]] = []
            outputs: list[dict[str, Any]] = []
            for s in cluster:
                s_start = to_iso_utc(s["created_ts"])
                s_end = to_iso_utc(s["updated_ts"]) if s["state"] != "running" else None
                dur_s = compute_dur_s(
                    s_start,
                    s_end,
                    is_running=(s["state"] == "running"),
                    now_ts=now_ts,
                )
                out_ids: list[str] = []
                if s["chat_path"]:
                    out_id = f"grok-chat-{s['sid'][:8]}"
                    outputs.append(
                        make_output(
                            out_id,
                            "transcript",
                            f"{s['title']} (chat history)",
                            path=s["chat_path"],
                            size=s["chat_size"],
                            by=s["sid"],
                            at=s_end or s_start,
                            body=None,
                            meta={
                                "sessionId": s["sid"],
                                "reasoningEffort": s["reasoning_effort"],
                                "modelCalls": s["model_calls"],
                            },
                        )
                    )
                    out_ids.append(out_id)

                links = []
                slink = make_session_link("grok", s["sid"])
                if slink:
                    links.append(slink)

                agents.append(
                    make_agent(
                        s["sid"],
                        s["title"],
                        parent=None,
                        role="worker",
                        lab="xai",
                        model=s["model"],
                        provider="xai",
                        state=s["state"],
                        started_at=s_start,
                        ended_at=s_end,
                        dur_s=dur_s,
                        tokens=s["tokens"],
                        cost_usd=s["cost_usd"],
                        last=s["last"],
                        result=s["result"],
                        links=links,
                        output_ids=out_ids,
                    )
                )

            if len(cluster) == 1:
                flow_title = first["title"]
            else:
                flow_title = f"Grok headless ({len(cluster)} runs) — {project_name}"

            flows.append(
                make_flow(
                    f"grok:{first['sid'][:12]}",
                    flow_title,
                    project=project_name,
                    project_root=cwd,
                    harness="grok",
                    kind="flat",
                    started_at=flow_start,
                    updated_at=flow_updated,
                    agents=agents,
                    outputs=outputs,
                )
            )

    return flows


# ---------------------------------------------------------------------------
# 2. Codex Subagents (read-only SQLite)
# ---------------------------------------------------------------------------


def _discover_codex_flows(
    codex_db_path: Path,
    *,
    rate_table: RateTable,
    usage_index: UsageIndex,
    now_ts: float,
) -> list[dict[str, Any]]:
    if not codex_db_path.is_file():
        return []

    rows: list[dict[str, Any]] = []
    parents: dict[str, dict[str, Any]] = {}
    con: sqlite3.Connection | None = None
    try:
        uri = f"file:{codex_db_path}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=1.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON;")
        con.execute("PRAGMA busy_timeout = 1000;")

        cols = {r[1] for r in con.execute("PRAGMA table_info(threads)")}
        if not cols:
            return []

        where_parts: list[str] = []
        if "agent_nickname" in cols:
            where_parts.append("agent_nickname IS NOT NULL")
        if "attached_thread_parent_id" in cols:
            where_parts.append("attached_thread_parent_id IS NOT NULL")
        if "source" in cols:
            where_parts.append("source LIKE '%subagent%'")
        if not where_parts:
            return []

        query = f"SELECT * FROM threads WHERE {' OR '.join(where_parts)}"
        rows = [dict(r) for r in con.execute(query).fetchall()]
        if not rows:
            return []

        parent_ids = {
            str(r["attached_thread_parent_id"])
            for r in rows
            if r.get("attached_thread_parent_id")
        }
        if parent_ids:
            placeholders = ",".join("?" for _ in parent_ids)
            for pr in con.execute(
                f"SELECT * FROM threads WHERE id IN ({placeholders})",
                tuple(parent_ids),
            ).fetchall():
                d = dict(pr)
                parents[str(d["id"])] = d
    except sqlite3.Error:
        return []
    finally:
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass

    # Group subagents by parent/root thread id.
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        pid = str(r.get("attached_thread_parent_id") or r.get("id"))
        groups.setdefault(pid, []).append(r)

    flows: list[dict[str, Any]] = []
    for root_id, members in groups.items():
        parent_row = parents.get(root_id)
        all_rows = ([parent_row] if parent_row else []) + members
        cwd = str(
            (parent_row or members[0]).get("cwd") or ""
        ).strip() or None

        agents: list[dict[str, Any]] = []
        starts: list[float] = []
        ends: list[float] = []

        seen_ids: set[str] = set()
        for r in all_rows:
            tid = str(r.get("id") or "").strip()
            if not tid or tid in seen_ids:
                continue
            seen_ids.add(tid)

            is_parent = parent_row is not None and tid == str(parent_row.get("id"))
            c_ts = parse_iso_ts(r.get("created_at_ms") or r.get("created_at"))
            u_ts = parse_iso_ts(r.get("updated_at_ms") or r.get("updated_at"))
            if c_ts is not None:
                starts.append(c_ts)
            if u_ts is not None:
                ends.append(u_ts)

            model = str(r.get("model") or "codex").strip()
            u_rec = usage_index.get(tid)
            tokens: dict[str, int] | None = None
            cost_usd: float | None = None
            if u_rec:
                tokens = {"in": u_rec["in"], "cache": u_rec["cache"], "out": u_rec["out"]}
                cost_usd = u_rec.get("costUsd")
                if cost_usd is None:
                    cost_usd = rate_table.estimate_cost_usd(
                        model or u_rec.get("model"),
                        tokens,
                        cache_read=u_rec.get("cacheRead"),
                        cache_write=u_rec.get("cacheWrite"),
                    )
            elif r.get("tokens_used"):
                tokens = {"in": int(r["tokens_used"]), "cache": 0, "out": 0}
                cost_usd = rate_table.estimate_cost_usd(model, tokens)

            links = []
            slink = make_session_link("codex", tid)
            if slink:
                links.append(slink)

            name = str(
                r.get("agent_nickname")
                or r.get("name")
                or r.get("title")
                or tid[:8]
            ).strip()
            role = "coordinator" if is_parent else str(r.get("agent_role") or "worker")
            parent_ref = None if is_parent else (str(parent_row["id"]) if parent_row else None)

            s_iso = to_iso_utc(c_ts)
            e_iso = to_iso_utc(u_ts)
            agents.append(
                make_agent(
                    tid,
                    name,
                    parent=parent_ref,
                    role=role,
                    lab="openai",
                    model=model,
                    provider="openai",
                    state="done",
                    started_at=s_iso,
                    ended_at=e_iso,
                    dur_s=compute_dur_s(s_iso, e_iso, is_running=False, now_ts=now_ts),
                    tokens=tokens,
                    cost_usd=cost_usd,
                    last=_truncate(r.get("preview") or r.get("first_user_message"), 160),
                    links=links,
                )
            )

        flow_title = str(
            (parent_row or members[0]).get("title")
            or (parent_row or members[0]).get("name")
            or f"Codex subagents {root_id[:8]}"
        ).strip()
        flow_links = []
        rlink = make_session_link("codex", root_id)
        if rlink:
            flow_links.append(rlink)

        flows.append(
            make_flow(
                f"codex:{root_id}",
                flow_title,
                project_root=cwd,
                harness="codex",
                kind="tree" if parent_row else "flat",
                started_at=to_iso_utc(min(starts)) if starts else None,
                updated_at=to_iso_utc(max(ends)) if ends else None,
                agents=agents,
                links=flow_links,
            )
        )

    return flows


# ---------------------------------------------------------------------------
# 3. Claude Code Teams & Subagents
# ---------------------------------------------------------------------------


def _discover_claude_flows(
    claude_root: Path,
    *,
    rate_table: RateTable,
    usage_index: UsageIndex,
    now_ts: float,
) -> list[dict[str, Any]]:
    if not claude_root.is_dir():
        return []

    flows: list[dict[str, Any]] = []

    # 3a. Claude teams: ~/.claude/teams/*/config.json + ~/.claude/tasks/*/*.json
    teams_dir = claude_root / "teams"
    tasks_root = claude_root / "tasks"
    if teams_dir.is_dir():
        try:
            team_configs = sorted(teams_dir.glob("*/config.json"))
        except OSError:
            team_configs = []
        for cfg_path in team_configs:
            cfg = _safe_json_load(cfg_path)
            if not isinstance(cfg, dict):
                continue
            team_name = str(cfg.get("name") or cfg_path.parent.name).strip()
            members = cfg.get("members") if isinstance(cfg.get("members"), list) else []

            # Load optional tasks for this team.
            team_tasks: list[dict[str, Any]] = []
            tdir = tasks_root / team_name
            if tdir.is_dir():
                try:
                    for tf in sorted(tdir.glob("*.json")):
                        td = _safe_json_load(tf)
                        if isinstance(td, dict):
                            team_tasks.append(td)
                except OSError:
                    pass

            agents: list[dict[str, Any]] = []
            starts: list[float] = []
            cwd = str(cfg.get("cwd") or "").strip() or None
            member_ids = {
                str(m.get("agentId") or m.get("name") or "").strip()
                for m in members
                if isinstance(m, dict)
            }
            member_ids.discard("")
            lead_id = str(cfg.get("leadAgentId") or "").strip() or None
            for m in members:
                if not isinstance(m, dict):
                    continue
                m_id = str(m.get("agentId") or m.get("name") or "").strip()
                if not m_id:
                    continue
                m_name = str(m.get("name") or m_id)
                m_model = str(m.get("model") or "claude").strip()
                sid = str(m.get("sessionId") or m_id).strip()
                joined_ts = parse_iso_ts(m.get("joinedAt") or cfg.get("createdAt"))
                if joined_ts is not None:
                    starts.append(joined_ts)

                u_rec = usage_index.get(sid)
                tokens = (
                    {"in": u_rec["in"], "cache": u_rec["cache"], "out": u_rec["out"]}
                    if u_rec
                    else None
                )
                cost_usd = u_rec.get("costUsd") if u_rec else None
                if cost_usd is None and tokens is not None:
                    cost_usd = rate_table.estimate_cost_usd(m_model, tokens)

                links = []
                slink = make_session_link("claude", sid)
                if slink:
                    links.append(slink)

                m_tasks = [t for t in team_tasks if t.get("owner") == m_name]
                last_task = _truncate(m_tasks[-1].get("subject") if m_tasks else None, 160)
                parent_id = (
                    lead_id
                    if (
                        m.get("agentType") != "team-lead"
                        and lead_id
                        and lead_id in member_ids
                        and lead_id != m_id
                    )
                    else None
                )

                agents.append(
                    make_agent(
                        m_id,
                        m_name,
                        parent=parent_id,
                        role=str(m.get("agentType") or "worker"),
                        lab="anthropic",
                        model=m_model,
                        provider="anthropic",
                        state=normalize_state(m.get("status") or "done"),
                        started_at=to_iso_utc(joined_ts),
                        tokens=tokens,
                        cost_usd=cost_usd,
                        last=last_task,
                        links=links,
                    )
                )

            try:
                cfg_mtime = cfg_path.stat().st_mtime
            except OSError:
                cfg_mtime = None

            flows.append(
                make_flow(
                    f"claude-team:{team_name}",
                    str(cfg.get("description") or team_name),
                    project_root=cwd,
                    harness="claude",
                    kind="swarm",
                    started_at=to_iso_utc(min(starts)) if starts else to_iso_utc(cfg_mtime),
                    updated_at=to_iso_utc(cfg_mtime),
                    goal=str(cfg.get("description") or "") or None,
                    agents=agents,
                )
            )

    # 3b. Claude subagent JSONLs: ~/.claude/projects/**/subagents/agent-*.jsonl
    projects_dir = claude_root / "projects"
    if projects_dir.is_dir():
        subagent_files: list[Path] = []
        try:
            subagent_files.extend(sorted(projects_dir.glob("*/subagents/agent-*.jsonl")))
            subagent_files.extend(sorted(projects_dir.glob("*/*/subagents/agent-*.jsonl")))
        except OSError:
            subagent_files = []

        grouped: dict[str, list[Path]] = {}
        for sf in subagent_files:
            parent_key = sf.parent.parent.name
            grouped.setdefault(parent_key, []).append(sf)

        for parent_key, files in grouped.items():
            agents = []
            outputs = []
            starts = []
            ends = []
            cwd = None

            for sf in files:
                tail = _read_tail_jsonl(sf, max_lines=30)
                sid = sf.stem
                model = "claude"
                in_tok = cache_tok = out_tok = 0
                last_msg: str | None = None
                first_ts: float | None = None
                last_ts: float | None = None

                for entry in tail:
                    ts = parse_iso_ts(entry.get("timestamp") or entry.get("ts"))
                    if ts is not None:
                        if first_ts is None or ts < first_ts:
                            first_ts = ts
                        if last_ts is None or ts > last_ts:
                            last_ts = ts
                    if not cwd and isinstance(entry.get("cwd"), str):
                        cwd = entry["cwd"]
                    if isinstance(entry.get("sessionId"), str):
                        sid = entry["sessionId"]
                    msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
                    if isinstance(msg.get("model"), str):
                        model = msg["model"]
                    usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
                    in_tok += int(usage.get("input_tokens") or 0)
                    cache_tok += int(usage.get("cache_read_input_tokens") or 0) + int(
                        usage.get("cache_creation_input_tokens") or 0
                    )
                    out_tok += int(usage.get("output_tokens") or 0)
                    content = msg.get("content")
                    if isinstance(content, str) and content.strip():
                        last_msg = content.strip()

                try:
                    st = sf.stat()
                    if last_ts is None:
                        last_ts = st.st_mtime
                    fsize = st.st_size
                except OSError:
                    fsize = None

                if first_ts is not None:
                    starts.append(first_ts)
                if last_ts is not None:
                    ends.append(last_ts)

                u_rec = usage_index.get(sid)
                tokens: dict[str, int] | None = None
                cost_usd: float | None = None
                if u_rec:
                    tokens = {"in": u_rec["in"], "cache": u_rec["cache"], "out": u_rec["out"]}
                    cost_usd = u_rec.get("costUsd")
                elif in_tok or cache_tok or out_tok:
                    tokens = {"in": in_tok, "cache": cache_tok, "out": out_tok}
                if cost_usd is None and tokens is not None:
                    cost_usd = rate_table.estimate_cost_usd(model, tokens)

                out_id = f"claude-sub-{sf.stem}"
                outputs.append(
                    make_output(
                        out_id,
                        "transcript",
                        sf.name,
                        path=str(sf),
                        size=fsize,
                        by=sf.stem,
                        at=to_iso_utc(last_ts),
                        body=None,
                    )
                )
                links = []
                slink = make_session_link("claude", sid)
                if slink:
                    links.append(slink)

                s_iso = to_iso_utc(first_ts or last_ts)
                e_iso = to_iso_utc(last_ts)
                agents.append(
                    make_agent(
                        sf.stem,
                        sf.stem,
                        parent=parent_key,
                        role="subagent",
                        lab="anthropic",
                        model=model,
                        provider="anthropic",
                        state="done",
                        started_at=s_iso,
                        ended_at=e_iso,
                        dur_s=compute_dur_s(s_iso, e_iso, is_running=False, now_ts=now_ts),
                        tokens=tokens,
                        cost_usd=cost_usd,
                        last=_truncate(last_msg, 160),
                        links=links,
                        output_ids=[out_id],
                    )
                )

            if agents:
                sub_count = len(agents)
                f_start = to_iso_utc(min(starts)) if starts else None
                f_end = to_iso_utc(max(ends)) if ends else None
                root_agent = make_agent(
                    parent_key,
                    f"Session {parent_key[:8]}",
                    parent=None,
                    role="root",
                    lab="anthropic",
                    provider="anthropic",
                    state="done",
                    started_at=f_start,
                    ended_at=f_end,
                    dur_s=compute_dur_s(f_start, f_end, is_running=False, now_ts=now_ts),
                )
                flows.append(
                    make_flow(
                        f"claude-subagents:{parent_key}",
                        f"Claude subagents ({sub_count})",
                        project_root=cwd,
                        harness="claude",
                        kind="tree",
                        started_at=f_start,
                        updated_at=f_end,
                        agents=[root_agent] + agents,
                        outputs=outputs,
                    )
                )

    return flows


# ---------------------------------------------------------------------------
# 4. Drop-in JSON Runs
# ---------------------------------------------------------------------------


def _normalize_dropin_flow(raw: dict[str, Any], path: Path) -> dict[str, Any] | None:
    flow_id = str(raw.get("id") or f"dropin:{path.stem}").strip()
    title = str(raw.get("title") or path.stem).strip()
    if not flow_id or not title:
        return None

    norm_agents: list[dict[str, Any]] = []
    for idx, a in enumerate(raw.get("agents") if isinstance(raw.get("agents"), list) else []):
        if not isinstance(a, dict):
            continue
        aid = str(a.get("id") or f"agent-{idx + 1}")
        aname = str(a.get("name") or aid)
        norm_links: list[dict[str, Any]] = []
        for lk in a.get("links") if isinstance(a.get("links"), list) else []:
            if isinstance(lk, dict):
                norm_links.append(
                    make_link(
                        str(lk.get("kind") or "session"),
                        str(lk.get("label") or "Link"),
                        href=lk.get("href"),
                        cmd=lk.get("cmd"),
                        link_id=lk.get("id"),
                    )
                )
        norm_agents.append(
            make_agent(
                aid,
                aname,
                parent=a.get("parent"),
                role=str(a.get("role") or "worker"),
                workstream=a.get("workstream"),
                lab=a.get("lab"),
                model=a.get("model"),
                provider=a.get("provider"),
                state=str(a.get("state") or "done"),
                started_at=a.get("startedAt") or a.get("started_at"),
                ended_at=a.get("endedAt") or a.get("ended_at"),
                dur_s=a.get("durS") if a.get("durS") is not None else a.get("dur_s"),
                tokens=a.get("tokens") if isinstance(a.get("tokens"), dict) else None,
                cost_usd=a.get("costUsd") if a.get("costUsd") is not None else a.get("cost_usd"),
                errors=a.get("errors") if isinstance(a.get("errors"), list) else None,
                trace=a.get("trace") if isinstance(a.get("trace"), list) else None,
                last=a.get("last"),
                result=a.get("result"),
                weight=a.get("weight"),
                verdict=a.get("verdict"),
                links=norm_links,
                output_ids=a.get("outputIds") if isinstance(a.get("outputIds"), list) else None,
            )
        )

    norm_outputs: list[dict[str, Any]] = []
    for idx, o in enumerate(raw.get("outputs") if isinstance(raw.get("outputs"), list) else []):
        if not isinstance(o, dict):
            continue
        oid = str(o.get("id") or f"out-{idx + 1}")
        norm_outputs.append(
            make_output(
                oid,
                str(o.get("kind") or "file"),
                str(o.get("title") or oid),
                path=o.get("path"),
                size=o.get("size"),
                by=str(o.get("by") or "root"),
                at=o.get("at"),
                body=o.get("body"),
                src=o.get("src"),
                meta=o.get("meta") if isinstance(o.get("meta"), dict) else None,
            )
        )

    norm_posts: list[dict[str, Any]] = []
    for idx, p in enumerate(raw.get("posts") if isinstance(raw.get("posts"), list) else []):
        if not isinstance(p, dict):
            continue
        norm_posts.append(
            make_post(
                p.get("id", idx + 1),
                str(p.get("author") or "unknown"),
                p.get("at"),
                str(p.get("text") or ""),
                mentions=p.get("mentions") if isinstance(p.get("mentions"), list) else None,
            )
        )

    norm_flow_links: list[dict[str, Any]] = []
    for lk in raw.get("links") if isinstance(raw.get("links"), list) else []:
        if isinstance(lk, dict):
            norm_flow_links.append(
                make_link(
                    str(lk.get("kind") or "session"),
                    str(lk.get("label") or "Link"),
                    href=lk.get("href"),
                    cmd=lk.get("cmd"),
                    link_id=lk.get("id"),
                )
            )

    try:
        mtime_iso = to_iso_utc(path.stat().st_mtime)
    except OSError:
        mtime_iso = None

    return make_flow(
        flow_id,
        title,
        project=str(raw.get("project") or ""),
        project_root=raw.get("projectRoot") or raw.get("project_root"),
        harness=str(raw.get("harness") or "dropin"),
        kind=str(raw.get("kind") or "flat"),
        started_at=raw.get("startedAt") or raw.get("started_at") or mtime_iso,
        updated_at=raw.get("updatedAt") or raw.get("updated_at") or mtime_iso,
        goal=raw.get("goal"),
        decision=raw.get("decision") if isinstance(raw.get("decision"), dict) else None,
        workstreams=raw.get("workstreams") if isinstance(raw.get("workstreams"), list) else None,
        agents=norm_agents,
        posts=norm_posts,
        outputs=norm_outputs,
        links=norm_flow_links,
    )


def _discover_dropin_flows(dropin_dir: Path) -> list[dict[str, Any]]:
    if not dropin_dir.is_dir():
        return []
    flows: list[dict[str, Any]] = []
    try:
        json_files = sorted(dropin_dir.glob("*.json"))
    except OSError:
        return []
    for jf in json_files:
        raw = _safe_json_load(jf)
        if not isinstance(raw, dict):
            continue
        flow = _normalize_dropin_flow(raw, jf)
        if flow is not None:
            flows.append(flow)
    return flows


# ---------------------------------------------------------------------------
# Public Entrypoint
# ---------------------------------------------------------------------------


def discover_cli_flows(
    *,
    grok_sessions_root: Path | None = None,
    codex_db_path: Path | None = None,
    claude_root: Path | None = None,
    dropin_dir: Path | None = None,
    rate_table: RateTable | None = None,
    usage_index: UsageIndex | None = None,
    now_ts: float | None = None,
) -> list[dict[str, Any]]:
    """Discover multi-agent CLI flows across Grok, Codex, Claude Code, and drop-in JSON runs."""
    home = Path.home()
    rt = rate_table if rate_table is not None else _DEFAULT_RATE_TABLE
    ui = usage_index if usage_index is not None else _DEFAULT_USAGE_INDEX
    now = now_ts if now_ts is not None else time.time()

    g_root = grok_sessions_root if grok_sessions_root is not None else home / ".grok" / "sessions"
    c_db = codex_db_path if codex_db_path is not None else home / ".codex" / "state_5.sqlite"
    cl_root = claude_root if claude_root is not None else home / ".claude"
    if dropin_dir is not None:
        d_dir = dropin_dir
    else:
        env_dropin = os.environ.get("AGENT_BOARD_DROPIN_DIR", "").strip()
        d_dir = Path(env_dropin) if env_dropin else home / ".cache" / "agent-board" / "runs"

    flows: list[dict[str, Any]] = []
    flows.extend(_discover_grok_flows(g_root, rate_table=rt, now_ts=now))
    flows.extend(_discover_codex_flows(c_db, rate_table=rt, usage_index=ui, now_ts=now))
    flows.extend(_discover_claude_flows(cl_root, rate_table=rt, usage_index=ui, now_ts=now))
    flows.extend(_discover_dropin_flows(d_dir))

    flows.sort(key=lambda f: parse_iso_ts(f.get("updatedAt")) or 0.0, reverse=True)
    return flows
