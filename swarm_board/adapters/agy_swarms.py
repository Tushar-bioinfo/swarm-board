"""Read-only adapter for Antigravity (agy) native swarms."""

from __future__ import annotations

from collections import Counter
import glob
import json
import os
from pathlib import Path
import re
import time
from typing import Any

from swarm_board import safety
from swarm_board.schema import (
    RateTable,
    UsageIndex,
    compute_dur_s,
    default_t3_userdata_dir,
    make_agent,
    make_flow,
    make_output,
    make_post,
    make_session_link,
    normalize_lab,
    normalize_state,
    parse_iso_ts,
    to_iso_utc,
)

INLINE_TEXT_MAX_BYTES = 65_536
STALE_THRESHOLD_S = 600.0
HELPER_ROLES = frozenset({"narrator", "monitor"})

_MENTION_RE = re.compile(r"(?<![A-Za-z0-9_])@([a-zA-Z0-9][a-zA-Z0-9_-]*)")
_SEQ_REF_RE = re.compile(r"(?<![A-Za-z0-9_])#(\d+)\b")
_ACTIVE_WS_RE = re.compile(r"Active Workspaces:\s*\n((?:\s*-\s*[^\n]+\n?)+)")
_BACKTICK_PATH_RE = re.compile(
    r"`((?:docs|out|cli|tools|src|lib|scripts|tests|skills|plugins)/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+)`"
)
_CMD_CD_RE = re.compile(r"\bcd\s+['\"]?(/[^'\"&|;\s]+)['\"]?")

_BINARY_EXTS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".mp3",
        ".wav",
        ".ogg",
        ".m4a",
        ".flac",
        ".mp4",
        ".webm",
        ".mov",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".tgz",
        ".whl",
        ".so",
        ".pyc",
        ".bin",
    }
)

_PROJECT_MARKERS = (
    ".git",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "SKILL.md",
    "Makefile",
)

# In-memory mtime:size caches so unchanged multi-MB transcripts/boards are never re-parsed.
_TRANSCRIPT_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}
_BOARD_CACHE: dict[str, tuple[int, int, list[dict[str, Any]]]] = {}
_PROJECT_ROOT_CACHE: dict[str, str | None] = {}
_INLINE_FILE_CACHE: dict[str, tuple[int, int, str | None]] = {}
_SWARM_FLOW_CACHE: dict[
    str,
    tuple[
        tuple[Any, ...],
        list[tuple[str, tuple[int, int] | None]],
        list[tuple[str, tuple[int, int] | None]],
        dict[str, Any],
    ],
] = {}
_DEFAULT_RATE_TABLE = RateTable()
_DEFAULT_USAGE_INDEX = UsageIndex()


def default_brain_roots() -> list[Path]:
    """Discover default Antigravity brain roots on this machine."""
    home = Path.home()
    t3_userdata = default_t3_userdata_dir()
    candidates: list[Path] = []

    for pattern in (
        str(t3_userdata / "providers" / "antigravity" / "*" / "antigravity-acp" / "brain"),
        str(home / ".t3" / "userdata" / "providers" / "antigravity" / "*" / "antigravity-acp" / "brain"),
        str(home / "Library" / "Application Support" / "T3 Code" / "userdata" / "providers" / "antigravity" / "*" / "antigravity-acp" / "brain"),
        str(home / ".gemini" / "*" / "brain"),
    ):
        for match in sorted(glob.glob(pattern)):
            p = Path(match)
            if p.is_dir():
                candidates.append(p)

    env_dir = os.environ.get("ANTIGRAVITY_EXECUTABLE_DATA_DIR", "").strip()
    if env_dir:
        base = Path(env_dir)
        for cand in (base.parent / "brain", base / "brain"):
            if cand.is_dir():
                candidates.append(cand)

    seen: set[str] = set()
    roots: list[Path] = []
    for p in candidates:
        try:
            key = os.path.realpath(p)
        except OSError:
            key = str(p)
        if key not in seen:
            seen.add(key)
            roots.append(p)
    return roots


def _safe_json_load(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _decode_arg_value(val: Any) -> Any:
    """Safely decode agy tool_calls[].args values which are often JSON-encoded strings."""
    if not isinstance(val, str):
        return val
    s = val.strip()
    if not s:
        return val
    try:
        decoded = json.loads(s)
        return decoded
    except (ValueError, TypeError):
        return val


def _extract_mentions(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _MENTION_RE.finditer(text):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _extract_seq_refs(text: str) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for m in _SEQ_REF_RE.finditer(text):
        num = int(m.group(1))
        if num not in seen:
            seen.add(num)
            out.append(num)
    return out


def _truncate(text: str | None, limit: int = 160) -> str | None:
    if not text:
        return None
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return None
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _bound_hint(text: Any, limit: int = 400) -> str | None:
    """Strip outer whitespace and bound a label hint string to `limit` chars."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    return cleaned[:limit]


def _is_internal_path(path_str: str) -> bool:
    norm = path_str.replace("\\", "/")
    return any(
        seg in norm
        for seg in (
            "/.t3/userdata/providers/",
            "/Library/Application Support/T3 Code/",
            "/.gemini/",
            "/antigravity-acp/",
            "/.system_generated/",
            "/tmp/",
            "/var/tmp/",
            "/proc/",
            "/dev/",
        )
    )


def _find_project_root_for_path(path_str: str) -> str | None:
    if not path_str or not path_str.startswith("/") or _is_internal_path(path_str):
        return None
    p = Path(path_str)
    cur = p if p.is_dir() else p.parent
    start_key = str(cur)
    if start_key in _PROJECT_ROOT_CACHE:
        return _PROJECT_ROOT_CACHE[start_key]
    home_str = str(Path.home())
    # Walk up looking for a project marker, stopping before $HOME or filesystem root.
    found: str | None = None
    while str(cur) not in ("/", "", home_str):
        try:
            if any((cur / marker).exists() for marker in _PROJECT_MARKERS):
                found = str(cur)
                break
        except OSError:
            pass
        if cur.parent == cur:
            break
        cur = cur.parent
    _PROJECT_ROOT_CACHE[start_key] = found
    return found


def _classify_error(err_text: str, content_text: str) -> tuple[str, str]:
    combined = f"{err_text}\n{content_text}".lower()
    is_perm = (
        "permission check failed" in combined
        or ("permission" in combined and ("timeout" in combined or "timed out" in combined))
        or "approval timed out" in combined
    )
    kind = "perm_timeout" if is_perm else "tool_error"
    raw = (err_text or "").strip()
    if not raw:
        for line in (content_text or "").splitlines():
            s = line.strip()
            if s and not s.startswith(("Created At:", "Completed At:")):
                raw = s
                break
    short = _truncate(raw, 140) or kind
    return kind, short


def _parse_board(board_path: Path) -> list[dict[str, Any]]:
    """Parse board.jsonl with (mtime_ns, size) caching."""
    try:
        st = board_path.stat()
    except OSError:
        return []

    cache_key = str(board_path)
    cached = _BOARD_CACHE.get(cache_key)
    if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return cached[2]

    posts: list[dict[str, Any]] = []
    try:
        with board_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line_no, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                seq = obj.get("seq", line_no)
                author = str(obj.get("agent") or obj.get("author") or "unknown")
                text = str(obj.get("text") or "")
                raw_time = obj.get("time") or obj.get("created_at") or obj.get("at")
                ts = parse_iso_ts(raw_time)
                at_iso = to_iso_utc(ts)
                mentions = _extract_mentions(text)
                refs = _extract_seq_refs(text)
                posts.append(
                    {
                        "seq": seq,
                        "author": author,
                        "at": at_iso,
                        "ts": ts,
                        "text": text,
                        "kind": obj.get("kind"),
                        "mentions": mentions,
                        "refs": refs,
                        "byteSize": len(text.encode("utf-8")),
                    }
                )
    except OSError:
        return []

    _BOARD_CACHE[cache_key] = (st.st_mtime_ns, st.st_size, posts)
    return posts


def _parse_transcript(transcript_path: Path) -> dict[str, Any] | None:
    """Stream-parse a member transcript.jsonl with (mtime_ns, size) caching."""
    try:
        st = transcript_path.stat()
    except OSError:
        return None

    cache_key = str(transcript_path)
    cached = _TRANSCRIPT_CACHE.get(cache_key)
    if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return cached[2]

    first_ts: float | None = None
    last_ts: float | None = None
    in_tokens = 0
    cache_tokens = 0
    out_tokens = 0
    model: str | None = None
    tool_counts: Counter[str] = Counter()
    error_buckets: dict[tuple[str, str], dict[str, Any]] = {}
    written_paths: list[str] = []
    written_seen: set[str] = set()
    candidate_paths: list[str] = []
    workspace_roots: list[str] = []
    ws_seen: set[str] = set()

    last_action: str | None = None
    last_reply: str | None = None
    last_step_type: str | None = None
    last_step_status: str | None = None
    last_planner_has_tool_calls = False
    last_tool_names: list[str] = []
    saw_swarm_wait_done = False
    step_count = 0

    try:
        with transcript_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    step = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(step, dict):
                    continue
                step_count += 1
                stype = str(step.get("type") or "")
                status = str(step.get("status") or "")
                ts = parse_iso_ts(step.get("created_at"))
                if ts is not None:
                    if first_ts is None or ts < first_ts:
                        first_ts = ts
                    if last_ts is None or ts > last_ts:
                        last_ts = ts

                last_step_type = stype
                last_step_status = status

                content = step.get("content")
                content_str = content if isinstance(content, str) else ""

                # Extract Active Workspaces from system/user context messages.
                if "Active Workspaces:" in content_str:
                    for m in _ACTIVE_WS_RE.finditer(content_str):
                        for ws_line in m.group(1).splitlines():
                            ws_path = ws_line.strip().lstrip("-").strip()
                            if (
                                ws_path.startswith("/")
                                and "/antigravity-acp/" not in ws_path
                                and "/.system_generated/" not in ws_path
                                and ws_path not in ws_seen
                            ):
                                ws_seen.add(ws_path)
                                workspace_roots.append(ws_path)

                if stype == "PLANNER_RESPONSE":
                    m_name = step.get("model")
                    if isinstance(m_name, str) and m_name.strip():
                        model = m_name.strip()
                    in_tokens += int(step.get("input_tokens") or 0)
                    cache_tokens += int(step.get("cache_read_tokens") or 0)
                    out_tokens += int(step.get("output_tokens") or 0)

                    tcs = step.get("tool_calls")
                    if isinstance(tcs, list) and tcs:
                        last_planner_has_tool_calls = True
                        saw_swarm_wait_done = False
                        current_names: list[str] = []
                        for tc in tcs:
                            if not isinstance(tc, dict):
                                continue
                            tname = str(tc.get("name") or "").strip()
                            if not tname:
                                continue
                            tool_counts[tname] += 1
                            current_names.append(tname)

                            args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
                            raw_path = args.get("Path") or args.get("path") or args.get("TargetFile")
                            decoded_path = _decode_arg_value(raw_path)
                            if isinstance(decoded_path, str) and decoded_path.startswith("/"):
                                if len(candidate_paths) < 64:
                                    candidate_paths.append(decoded_path)
                                if tname in ("write_file", "edit_file") and decoded_path not in written_seen:
                                    written_seen.add(decoded_path)
                                    written_paths.append(decoded_path)
                                last_action = _truncate(f"{tname} {Path(decoded_path).name}", 120)
                            elif tname == "run_command":
                                cmd = _decode_arg_value(args.get("CommandLine") or args.get("command") or "")
                                if isinstance(cmd, str):
                                    for cd_m in _CMD_CD_RE.finditer(cmd):
                                        if len(candidate_paths) < 64:
                                            candidate_paths.append(cd_m.group(1))
                                    summary = _decode_arg_value(args.get("toolSummary") or "")
                                    label = summary if isinstance(summary, str) and summary.strip() else cmd
                                    last_action = _truncate(f"run_command: {label}", 120)
                            else:
                                last_action = _truncate(tname, 120)
                        last_tool_names = current_names
                    else:
                        last_planner_has_tool_calls = False
                        last_tool_names = []
                        if content_str.strip():
                            last_reply = content_str.strip()
                            last_action = _truncate(last_reply, 160)

                elif stype == "GENERIC":
                    if status == "DONE" and (
                        "swarm_wait" in content_str
                        or "swarm_wait" in last_tool_names
                    ):
                        saw_swarm_wait_done = True

                # Error detection across step types.
                err_val = step.get("error")
                if status == "ERROR" or (isinstance(err_val, str) and err_val.strip()):
                    kind, short_msg = _classify_error(str(err_val or ""), content_str)
                    bkey = (kind, short_msg)
                    at_iso = to_iso_utc(ts)
                    bucket = error_buckets.get(bkey)
                    if bucket is None:
                        error_buckets[bkey] = {
                            "kind": kind,
                            "count": 1,
                            "msg": short_msg,
                            "at": at_iso,
                        }
                    else:
                        bucket["count"] += 1
                        if at_iso:
                            bucket["at"] = at_iso
    except OSError:
        return None

    trace = [[name, count] for name, count in tool_counts.most_common()]
    errors = sorted(error_buckets.values(), key=lambda e: (-e["count"], e["kind"]))

    summary = {
        "first_ts": first_ts,
        "last_ts": last_ts,
        "mtime_s": st.st_mtime,
        "size": st.st_size,
        "step_count": step_count,
        "model": model,
        "tokens": {
            "in": in_tokens,
            "cache": cache_tokens,
            "out": out_tokens,
        },
        "trace": trace,
        "errors": errors,
        "written_paths": written_paths,
        "candidate_paths": candidate_paths,
        "workspace_roots": workspace_roots,
        "last_action": last_action,
        "last_reply": last_reply,
        "last_step_type": last_step_type,
        "last_step_status": last_step_status,
        "last_planner_has_tool_calls": last_planner_has_tool_calls,
        "last_tool_names": last_tool_names,
        "saw_swarm_wait_done": saw_swarm_wait_done,
    }
    _TRANSCRIPT_CACHE[cache_key] = (st.st_mtime_ns, st.st_size, summary)
    return summary


def _derive_member_state(
    member_meta: dict[str, Any],
    transcript: dict[str, Any] | None,
    *,
    now_ts: float,
    fallback_mtime_s: float | None = None,
) -> str:
    """Derive accurate live member state from members.json + transcript tail."""
    phase = str(member_meta.get("phase") or "").upper()
    desired = str(member_meta.get("desired_state") or "").upper()
    if member_meta.get("killed") or phase == "KILLED" or desired == "KILLED":
        return "killed"

    wake_mode = str(member_meta.get("wake_mode") or "").lower()

    if transcript is not None:
        last_type = transcript["last_step_type"]
        last_status = transcript["last_step_status"]
        has_tc = transcript["last_planner_has_tool_calls"]
        last_tools = transcript["last_tool_names"]
        saw_wait_done = transcript["saw_swarm_wait_done"]

        if last_type == "PLANNER_RESPONSE" and not has_tc and last_status == "DONE":
            state = "waiting" if wake_mode == "mentions" else "done"
        elif saw_wait_done:
            state = "waiting"
        elif (
            last_type == "GENERIC"
            and last_status == "DONE"
            and last_tools
            and all(t in ("swarm_wait", "swarm_post", "swarm_read", "swarm") for t in last_tools)
        ):
            state = "waiting" if wake_mode == "mentions" else "done"
        elif wake_mode == "mentions" and last_status == "DONE":
            state = "waiting"
        else:
            state = "running"

        if state == "running" and (now_ts - transcript["mtime_s"]) > STALE_THRESHOLD_S:
            state = "stale"
        return state

    # Fallback when transcript is absent.
    if wake_mode == "mentions":
        return "waiting"
    if wake_mode == "working":
        if fallback_mtime_s is not None and (now_ts - fallback_mtime_s) > STALE_THRESHOLD_S:
            return "stale"
        return "running"
    if phase:
        return normalize_state(phase)
    return "done"


def _infer_project_root(
    transcripts_by_member: dict[str, dict[str, Any]],
) -> str | None:
    """Infer workspace projectRoot from Active Workspaces or tool_calls paths."""
    # 1. Explicit Active Workspaces from SYSTEM_MESSAGE steps.
    ws_counts: Counter[str] = Counter()
    for tr in transcripts_by_member.values():
        for ws in tr.get("workspace_roots") or []:
            ws_counts[ws] += 1
    if ws_counts:
        return ws_counts.most_common(1)[0][0]

    # 2. Project root markers from written/read/cd paths.
    root_counts: Counter[str] = Counter()
    for tr in transcripts_by_member.values():
        for p in (tr.get("written_paths") or []) + (tr.get("candidate_paths") or []):
            pr = _find_project_root_for_path(p)
            if pr:
                root_counts[pr] += 1
    if root_counts:
        return root_counts.most_common(1)[0][0]

    # 3. Fallback: parent directory of first non-internal written path.
    for tr in transcripts_by_member.values():
        for p in tr.get("written_paths") or []:
            if p.startswith("/") and not _is_internal_path(p):
                return str(Path(p).parent)
    return None


def _infer_file_kind(path_str: str) -> str:
    ext = Path(path_str).suffix.lower()
    if ext in (".md", ".markdown", ".mdx"):
        return "markdown"
    if ext in (".json", ".jsonl"):
        return "json"
    if ext in (".mp3", ".wav", ".ogg", ".m4a", ".flac"):
        return "audio"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
        return "image"
    if ext in (".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".sh", ".css", ".html", ".yaml", ".yml", ".toml"):
        return "code"
    return "file"


def _maybe_inline_file(resolved: str, size: int, mtime_ns: int | None = None) -> str | None:
    """Inline small UTF-8 text files (<= 64 KB); leave large or binary files lazy."""
    if size > INLINE_TEXT_MAX_BYTES:
        return None
    if Path(resolved).suffix.lower() in _BINARY_EXTS:
        return None
    if mtime_ns is not None:
        cached = _INLINE_FILE_CACHE.get(resolved)
        if cached is not None and cached[0] == mtime_ns and cached[1] == size:
            return cached[2]
    try:
        raw = Path(resolved).read_bytes()
    except OSError:
        return None
    body: str | None = None if b"\x00" in raw[:4096] else raw.decode("utf-8", errors="replace")
    if mtime_ns is not None:
        _INLINE_FILE_CACHE[resolved] = (mtime_ns, size, body)
    return body


def load_transcript_messages(
    brain_dir: str | Path,
    conv_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Lazily read the latest meaningful transcript steps for a conversation."""
    tp = Path(brain_dir) / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
    if not tp.is_file():
        return []
    messages: list[dict[str, Any]] = []
    try:
        with tp.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    step = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(step, dict):
                    continue
                stype = str(step.get("type") or "")
                content = step.get("content") if isinstance(step.get("content"), str) else ""
                tcs = step.get("tool_calls") if isinstance(step.get("tool_calls"), list) else []
                err = step.get("error") if isinstance(step.get("error"), str) else None
                if not content and not tcs and not err:
                    continue
                entry: dict[str, Any] = {
                    "stepIndex": step.get("step_index"),
                    "type": stype,
                    "source": step.get("source"),
                    "status": step.get("status"),
                    "at": to_iso_utc(parse_iso_ts(step.get("created_at"))),
                    "model": step.get("model"),
                    "content": content[:8192] if content else "",
                    "toolCalls": [
                        tc.get("name")
                        for tc in tcs
                        if isinstance(tc, dict) and tc.get("name")
                    ],
                    "error": err,
                }
                messages.append(entry)
    except OSError:
        return []

    max_items = max(1, int(limit))
    return messages[-max_items:]


def load_swarm_output(
    flow: dict[str, Any],
    output_id: str,
    *,
    limit: int = 50,
) -> dict[str, Any] | None:
    """Resolve an Output entry from an agy swarm Flow lazily or inline."""
    outputs = flow.get("outputs") if isinstance(flow, dict) else None
    if not isinstance(outputs, list):
        return None
    target = next((o for o in outputs if isinstance(o, dict) and o.get("id") == output_id), None)
    if target is None:
        return None

    if target.get("body") is not None:
        return {"ok": True, "output": target, "body": target["body"]}

    kind = target.get("kind")
    meta = target.get("meta") if isinstance(target.get("meta"), dict) else {}

    if kind == "transcript":
        brain_dir = meta.get("brainDir")
        conv_id = meta.get("conversationId")
        if brain_dir and conv_id:
            msgs = load_transcript_messages(brain_dir, conv_id, limit=limit)
            return {"ok": True, "output": target, "messages": msgs}
        return {"ok": False, "error": "Transcript metadata missing"}

    if kind in ("post", "brief") and meta.get("boardPath"):
        posts = _parse_board(Path(meta["boardPath"]))
        seq = meta.get("seq")
        for p in posts:
            if p.get("seq") == seq:
                return {"ok": True, "output": target, "body": p.get("text", "")}
        return {"ok": False, "error": f"Board post #{seq} not found"}

    path_str = target.get("path")
    project_root = flow.get("projectRoot")
    if path_str and project_root:
        res = safety.safe_read_text(path_str, [project_root])
        res["output"] = target
        return res

    return {"ok": False, "error": "Output has no readable source"}


def _stat_sig(path: Path | str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _build_swarm_flow(
    brain_dir: Path,
    swarm_dir: Path,
    *,
    rate_table: RateTable,
    usage_index: UsageIndex,
    now_ts: float,
    use_cache: bool = True,
) -> dict[str, Any] | None:
    base_sig = (
        _stat_sig(swarm_dir / "swarm.json"),
        _stat_sig(swarm_dir / "members.json"),
        _stat_sig(swarm_dir / "plan.json"),
        _stat_sig(swarm_dir / "board.jsonl"),
        rate_table.cache_signature(),
        str(usage_index.path),
        _stat_sig(usage_index.path),
    )
    cache_key = str(swarm_dir)
    if use_cache:
        cached_entry = _SWARM_FLOW_CACHE.get(cache_key)
        if cached_entry is not None:
            c_base_sig, c_tr_sigs, c_out_sigs, c_flow = cached_entry
            if (
                c_base_sig == base_sig
                and all(_stat_sig(p) == s for p, s in c_tr_sigs)
                and all(_stat_sig(p) == s for p, s in c_out_sigs)
            ):
                return c_flow

    conv_id = swarm_dir.parent.parent.name
    swarm_json = _safe_json_load(swarm_dir / "swarm.json")
    members_json = _safe_json_load(swarm_dir / "members.json")
    plan_json = _safe_json_load(swarm_dir / "plan.json")
    board_path = swarm_dir / "board.jsonl"
    board_posts = _parse_board(board_path) if board_path.is_file() else []

    if swarm_json is None and members_json is None:
        return None

    swarm_id = str((swarm_json or {}).get("swarm_id") or conv_id)
    backend = str((swarm_json or {}).get("backend") or "local")
    created_ts = parse_iso_ts((swarm_json or {}).get("created_at"))
    started_iso = to_iso_utc(created_ts)

    # Handle remote swarms or missing members.json cleanly.
    raw_members = (members_json or {}).get("members")
    if backend != "local" or not isinstance(raw_members, dict) or not raw_members:
        title = (
            (members_json or {}).get("title")
            or (swarm_json or {}).get("title")
            or f"Swarm {swarm_id[:8]}"
        )
        flow_links = []
        slink = make_session_link("agy", swarm_id)
        if slink:
            flow_links.append(slink)
        return make_flow(
            f"agy:{swarm_id}",
            str(title),
            harness="agy",
            kind="swarm",
            started_at=started_iso,
            updated_at=started_iso,
            goal=f"Remote swarm ({backend})" if backend != "local" else None,
            links=flow_links,
        )

    # Plan: goal, workstreams, and task->workstream mapping.
    goal_str: str | None = None
    workstreams: list[dict[str, str]] = []
    ws_by_member: dict[str, str] = {}
    plan_tasks: dict[str, str] = {}
    if isinstance(plan_json, dict):
        goal_obj = plan_json.get("goal")
        if isinstance(goal_obj, dict):
            g_title = str(goal_obj.get("title") or "").strip()
            g_line = str(goal_obj.get("line") or "").strip()
            if g_title and g_line:
                goal_str = f"{g_title} — {g_line}"
            else:
                goal_str = g_title or g_line or None
        elif isinstance(goal_obj, str) and goal_obj.strip():
            goal_str = goal_obj.strip()

        raw_tasks = plan_json.get("tasks")
        if isinstance(raw_tasks, dict):
            for t_name, t_desc in raw_tasks.items():
                if not isinstance(t_name, str) or not t_name.strip():
                    continue
                if isinstance(t_desc, str):
                    bounded = _bound_hint(t_desc, 400)
                elif isinstance(t_desc, dict):
                    bounded = _bound_hint(
                        t_desc.get("title") or t_desc.get("task") or t_desc.get("description"),
                        400,
                    )
                else:
                    bounded = None
                if bounded:
                    plan_tasks[t_name] = bounded

        raw_threads = plan_json.get("threads")
        if isinstance(raw_threads, list):
            for idx, th in enumerate(raw_threads):
                if not isinstance(th, dict):
                    continue
                tid = str(th.get("id") or f"ws-{idx + 1}")
                ttitle = str(th.get("title") or tid)
                tgoal = str(th.get("goal") or "")
                workstreams.append({"id": tid, "title": ttitle, "goal": tgoal})
                tasks = th.get("tasks")
                if isinstance(tasks, list):
                    for tname in tasks:
                        if isinstance(tname, str) and tname:
                            ws_by_member[tname] = tid

    # Index briefs and latest board posts per member.
    brief_by_member: dict[str, str] = {}
    last_post_by_member: dict[str, dict[str, Any]] = {}
    for p in board_posts:
        author = p["author"]
        last_post_by_member[author] = p
        if p.get("kind") == "brief":
            for m_name in p.get("mentions") or []:
                if m_name not in brief_by_member:
                    brief_by_member[m_name] = p["text"]

    # Parse member transcripts.
    transcripts_by_member: dict[str, dict[str, Any]] = {}
    transcript_paths: dict[str, Path] = {}
    for m_name, m_meta in raw_members.items():
        if not isinstance(m_meta, dict):
            continue
        m_cid = str(m_meta.get("conversation_id") or "").strip()
        if not m_cid:
            continue
        tp = brain_dir / m_cid / ".system_generated" / "logs" / "transcript.jsonl"
        if tp.is_file():
            transcript_paths[m_name] = tp
            parsed = _parse_transcript(tp)
            if parsed is not None:
                transcripts_by_member[m_name] = parsed

    project_root = _infer_project_root(transcripts_by_member)

    try:
        members_mtime_s = (swarm_dir / "members.json").stat().st_mtime
    except OSError:
        members_mtime_s = None

    # Build board Posts and board Outputs.
    norm_posts: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    output_ids_by_member: dict[str, list[str]] = {}

    for p in board_posts:
        seq = p["seq"]
        author = p["author"]
        text = p["text"]
        norm_posts.append(
            make_post(
                seq,
                author,
                p["at"],
                text,
                mentions=p["mentions"],
            )
        )
        out_id = f"post-{seq}"
        first_line = _truncate(text.splitlines()[0] if text.splitlines() else text, 80) or f"Post #{seq}"
        inline_body = text if p["byteSize"] <= INLINE_TEXT_MAX_BYTES else None
        outputs.append(
            make_output(
                out_id,
                "brief" if p.get("kind") == "brief" else "post",
                f"#{seq} {author}: {first_line}",
                size=p["byteSize"],
                by=author,
                at=p["at"],
                body=inline_body,
                meta={
                    "seq": seq,
                    "kind": p.get("kind") or "post",
                    "mentions": p["mentions"],
                    "refs": p["refs"],
                    "boardPath": str(board_path),
                    "truncated": inline_body is None,
                },
            )
        )
        output_ids_by_member.setdefault(author, []).append(out_id)

    # Discover generated project files from transcripts and final board posts.
    discovered_files: list[tuple[str, str]] = []  # (path, author)
    seen_resolved_files: set[str] = set()

    for m_name, tr in transcripts_by_member.items():
        for wp in tr.get("written_paths") or []:
            discovered_files.append((wp, m_name))

    if project_root:
        for p in board_posts:
            for m in _BACKTICK_PATH_RE.finditer(p["text"]):
                rel = m.group(1)
                discovered_files.append((str(Path(project_root) / rel), p["author"]))

    if project_root:
        dedup_candidates: dict[str, str] = {}
        for cand_path, author in discovered_files:
            if cand_path and cand_path not in dedup_candidates:
                dedup_candidates[cand_path] = author
        file_idx = 0
        for cand_path, author in dedup_candidates.items():
            ok, resolved = safety.is_path_allowed(cand_path, [project_root])
            if not ok or not resolved or resolved in seen_resolved_files:
                continue
            if not os.path.isfile(resolved):
                continue
            seen_resolved_files.add(resolved)
            try:
                st = os.stat(resolved)
            except OSError:
                continue
            file_idx += 1
            try:
                rel_title = str(Path(resolved).relative_to(os.path.realpath(project_root)))
            except ValueError:
                rel_title = Path(resolved).name
            out_id = f"file-{file_idx}"
            outputs.append(
                make_output(
                    out_id,
                    _infer_file_kind(resolved),
                    rel_title,
                    path=resolved,
                    size=st.st_size,
                    by=author,
                    at=to_iso_utc(st.st_mtime),
                    body=_maybe_inline_file(resolved, st.st_size, st.st_mtime_ns),
                    meta={"relativePath": rel_title},
                )
            )
            output_ids_by_member.setdefault(author, []).append(out_id)

    # Build Agents (coordinator first, then primary workers, then helper roles).
    def _member_sort_key(item: tuple[str, Any]) -> tuple[int, str]:
        name, meta = item
        role = str((meta or {}).get("role") or "").lower()
        if name == "coordinator" or role == "coordinator":
            return (0, name)
        if name in HELPER_ROLES or role in HELPER_ROLES:
            return (2, name)
        return (1, name)

    agents: list[dict[str, Any]] = []
    all_starts: list[float] = []
    all_ends: list[float] = []
    if created_ts is not None:
        all_starts.append(created_ts)

    coord_name = next(
        (
            k
            for k, v in raw_members.items()
            if isinstance(v, dict)
            and (k == "coordinator" or str(v.get("role") or "").lower() == "coordinator")
        ),
        None,
    )

    for m_name, m_meta in sorted(raw_members.items(), key=_member_sort_key):
        if not isinstance(m_meta, dict):
            continue
        m_cid = str(m_meta.get("conversation_id") or "").strip()
        raw_role = str(m_meta.get("role") or "worker").lower()
        if m_name == coord_name or raw_role == "coordinator":
            role = "coordinator"
            parent = None
        elif m_name in HELPER_ROLES or raw_role in HELPER_ROLES:
            role = m_name if m_name in HELPER_ROLES else raw_role
            parent = coord_name
        else:
            role = raw_role or "worker"
            parent = coord_name

        tr = transcripts_by_member.get(m_name)
        tp = transcript_paths.get(m_name)
        state = _derive_member_state(
            m_meta,
            tr,
            now_ts=now_ts,
            fallback_mtime_s=members_mtime_s,
        )

        model = (
            (tr.get("model") if tr else None)
            or m_meta.get("applied_model")
            or m_meta.get("model")
            or m_meta.get("desired_model")
        )

        tokens: dict[str, int] | None = None
        cost_usd: float | None = None
        cost_estimated = True
        if tr is not None:
            t = tr["tokens"]
            if t["in"] or t["cache"] or t["out"]:
                tokens = t
                cost_usd = rate_table.estimate_cost_usd(model, tokens)
        if tokens is None and m_cid:
            u = usage_index.get(m_cid)
            if u:
                tokens = {"in": u["in"], "cache": u["cache"], "out": u["out"]}
                cost_usd = u.get("costUsd")
                if cost_usd is not None:
                    cost_estimated = False
                else:
                    cost_usd = rate_table.estimate_cost_usd(
                        model or u.get("model"),
                        tokens,
                        cache_read=u.get("cacheRead"),
                        cache_write=u.get("cacheWrite"),
                    )

        m_start_ts = tr["first_ts"] if tr and tr["first_ts"] is not None else created_ts
        m_end_ts = (
            tr["last_ts"]
            if tr and tr["last_ts"] is not None and state != "running"
            else None
        )
        if m_start_ts is not None:
            all_starts.append(m_start_ts)
        if tr and tr["last_ts"] is not None:
            all_ends.append(tr["last_ts"])

        m_start_iso = to_iso_utc(m_start_ts)
        m_end_iso = to_iso_utc(m_end_ts)
        dur_s = compute_dur_s(
            m_start_iso,
            m_end_iso,
            is_running=(state == "running"),
            now_ts=now_ts,
        )

        # Transcript output entry (lazy).
        member_out_ids = list(output_ids_by_member.get(m_name, []))
        if tp is not None and tr is not None:
            tr_out_id = f"transcript-{m_name}"
            outputs.append(
                make_output(
                    tr_out_id,
                    "transcript",
                    f"{m_name} transcript",
                    path=str(tp),
                    size=tr["size"],
                    by=m_name,
                    at=to_iso_utc(tr["last_ts"] or tr["mtime_s"]),
                    body=None,
                    meta={
                        "brainDir": str(brain_dir),
                        "conversationId": m_cid,
                        "steps": tr["step_count"],
                    },
                )
            )
            member_out_ids.append(tr_out_id)

        member_links = []
        slink = make_session_link("agy", m_cid)
        if slink:
            member_links.append(slink)

        last_post = last_post_by_member.get(m_name)
        result_text = None
        if last_post and last_post.get("text"):
            result_text = _truncate(last_post["text"], 400)
        elif tr and tr.get("last_reply"):
            result_text = _truncate(tr["last_reply"], 400)

        last_text = (
            (tr.get("last_action") if tr else None)
            or (_truncate(last_post["text"], 160) if last_post else None)
        )

        agent = make_agent(
            m_name,
            m_name,
            parent=parent,
            role=role,
            workstream=ws_by_member.get(m_name),
            lab=normalize_lab(model, "antigravity"),
            model=model,
            provider="antigravity",
            state=state,
            started_at=m_start_iso,
            ended_at=m_end_iso,
            dur_s=dur_s,
            tokens=tokens,
            cost_usd=cost_usd,
            errors=tr["errors"] if tr else [],
            trace=tr["trace"] if tr else [],
            last=last_text,
            result=result_text,
            links=member_links,
            output_ids=member_out_ids,
        )
        # Attach brief cleanly for UI/inspector consumers.
        brief_text = brief_by_member.get(m_name)
        if brief_text:
            agent["brief"] = brief_text
        if role in HELPER_ROLES:
            agent["isHelper"] = True
        agent["costEstimated"] = cost_estimated
        plan_task = plan_tasks.get(m_name) or (
            _bound_hint(goal_str, 400) if role == "coordinator" else None
        )
        agent["_labelHint"] = {
            "prompt": _bound_hint(brief_text, 360),
            "slug": None,
            "planTask": plan_task,
            "rawTitle": _bound_hint(m_name, 400),
        }
        agents.append(agent)

    for p in board_posts:
        if p.get("ts") is not None:
            all_ends.append(p["ts"])

    flow_start_iso = to_iso_utc(min(all_starts)) if all_starts else started_iso
    flow_updated_iso = to_iso_utc(max(all_ends)) if all_ends else flow_start_iso

    title = (
        str(members_json.get("title") or "").strip()
        or (goal_str.split(" — ")[0] if goal_str else "")
        or f"Swarm {swarm_id[:8]}"
    )

    flow_links = []
    coord_link = make_session_link("agy", swarm_id)
    if coord_link:
        flow_links.append(coord_link)

    flow = make_flow(
        f"agy:{swarm_id}",
        title,
        project_root=project_root,
        harness="agy",
        kind="swarm",
        started_at=flow_start_iso,
        updated_at=flow_updated_iso,
        goal=goal_str,
        workstreams=workstreams,
        agents=agents,
        posts=norm_posts,
        outputs=outputs,
        links=flow_links,
    )
    if use_cache and not any(a.get("state") == "running" for a in agents):
        tr_sigs = [(str(tp), _stat_sig(tp)) for tp in transcript_paths.values()]
        out_sigs = [
            (str(o["path"]), _stat_sig(o["path"]))
            for o in outputs
            if isinstance(o.get("path"), str) and o.get("kind") != "transcript"
        ]
        _SWARM_FLOW_CACHE[cache_key] = (base_sig, tr_sigs, out_sigs, flow)
    return flow


def discover_swarms(
    brain_roots: list[Path] | None = None,
    *,
    rate_table: RateTable | None = None,
    usage_index: UsageIndex | None = None,
    now_ts: float | None = None,
) -> list[dict[str, Any]]:
    """Discover all Antigravity native swarms across brain directories (read-only)."""
    roots = brain_roots if brain_roots is not None else default_brain_roots()
    rt = rate_table if rate_table is not None else _DEFAULT_RATE_TABLE
    ui = usage_index if usage_index is not None else _DEFAULT_USAGE_INDEX
    use_cache = now_ts is None
    now = now_ts if now_ts is not None else time.time()

    flows: list[dict[str, Any]] = []
    seen_flow_ids: set[str] = set()

    for brain_dir in roots:
        if not brain_dir.is_dir():
            continue
        try:
            swarm_dirs = sorted(brain_dir.glob("*/.system_generated/swarm"))
        except OSError:
            continue
        for swarm_dir in swarm_dirs:
            if not swarm_dir.is_dir():
                continue
            flow = _build_swarm_flow(
                brain_dir,
                swarm_dir,
                rate_table=rt,
                usage_index=ui,
                now_ts=now,
                use_cache=use_cache,
            )
            if flow is None or flow["id"] in seen_flow_ids:
                continue
            seen_flow_ids.add(flow["id"])
            flows.append(flow)

    flows.sort(key=lambda f: parse_iso_ts(f.get("updatedAt")) or 0.0, reverse=True)
    return flows
