"""Agent short label & single-sentence task engine with optional headless CLI enrichment."""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable

SCHEMA_VERSION = "v1"
SUPPORTED_CLIS = ("agy", "claude", "codex", "grok")
DEFAULT_LABELER_CONFIG: dict[str, Any] = {
    "enabled": False,
    "cli": "agy",
    "model": "gemini-3.8-flash-low",
    "timeout_s": 25,
}

_DEFAULT_CONFIG_PATH = Path.home() / ".config" / "swarm-board" / "config.json"
_CONFIG_FILE_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}
_HEURISTIC_GROUP_CACHE: dict[tuple[Any, ...], tuple[dict[str, str], dict[str, str]]] = {}
_HEURISTIC_CACHE_MAX = 1024
_LOGGER = logging.getLogger("swarm_board.labeler")
_MAX_CACHE_ENTRIES = 5000
_COMPLETED_BATCH_TTL_S = 300.0

# Pre-compiled hygiene & extraction patterns
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_MD_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_ITALIC_STAR_RE = re.compile(r"(?<!\w)\*|\*(?!\w)")
_WS_RE = re.compile(r"\s+")
_ELLIPSIS_TRAIL_RE = re.compile(r"(?:\.{2,}|…)+\s*$")
_PAREN_MODEL_TAG_RE = re.compile(
    r"\s*\((?:[^)]*\b(?:"
    r"neon|opus|sol|haiku|sonnet|claude|gemini|gpt|grok|hydrogen|carbon|fable|luna|astra|"
    r"xhigh|high|medium|med|low|max|ultra|ultrathink|fast|slot\s+\d+(?:\s+of\s+\d+)?"
    r")\b[^)]*)\)",
    re.IGNORECASE,
)
_ORPHAN_EFFORT_PAREN_RE = re.compile(r"\s*\b(?:xhigh|high|medium|med|low)\s*\)", re.IGNORECASE)
_EMPTY_PAREN_RE = re.compile(r"\s*\(\s*\)")
_PAREN_ANY_RE = re.compile(r"\([^)]*\)")
_LONG_PAREN_RE = re.compile(r"\s*\([^)]{8,}\)")
_LEADING_ACT_AS_RE = re.compile(
    r"^\s*Act\s+as\s+(?:the\s+|an?\s+)?[A-Za-z0-9_-]+\s+sub-?agent(?:\s+for\s+this\s+task)?\.\s*",
    re.IGNORECASE,
)
_LEADING_MENTION_RE = re.compile(r"^\s*@[A-Za-z0-9_-]+(?:\s*[:,\-–—]\s*|\s+)")
_YOU_ARE_PREFIX_RE = re.compile(r"^\s*You\s+are(?:\s+executing|\s+the|\s+an?)?\s+", re.IGNORECASE)
_YOU_ARE_WRAP_RE = re.compile(r"^\s*You\s+are\s+(?:the\s+|an?\s+)?(.+)$", re.IGNORECASE)
_YOU_EXEC_WRAP_RE = re.compile(r"^\s*You\s+are\s+executing\s+(.+)$", re.IGNORECASE)
_ROLE_ASSIGNMENT_RE = re.compile(
    r"^\s*You\s+are\s+(?:the\s+|an?\s+)?(.+?)"
    r"(?:\s+(?:for|of|in|on|inside)\s+(?:the\s+)?(.+?))?"
    r"(?:[.\n]|$)",
    re.IGNORECASE,
)
_SEAT_RE = re.compile(r"^\s*Seat\s+(\d+)\s*[·:\-–—]\s*(.+)$", re.IGNORECASE)
_COMMITTEE_PAREN_RE = re.compile(r"\(\s*committee-(\d+)\s*:\s*([^)]+)\)", re.IGNORECASE)
_MODEL_ROLE_RE = re.compile(
    r"^\s*([A-Za-z0-9._-]+)\s*\((worker|reviewer|coordinator|lead|critic|auditor|judge|planner)\)\s*$",
    re.IGNORECASE,
)
_SLUG_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,36}$")
_SLUG_SPLIT_RE = re.compile(r"[-_]+")
_UUID_LIKE_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_LEADING_MODEL_DETAIL_RE = re.compile(
    r"^(?:"
    r"(?:claude\s+)?opus(?:\s+\d+(?:\.\d+)?)?(?:\s*(?:#\d+|\d+/\d+))?|"
    r"gemini(?:\s+\d+(?:\.\d+)?)?(?:\s+(?:pro|flash|neon|hydrogen|carbon))?|"
    r"gpt[- ]?\d+(?:\.\d+)?(?:\s+(?:sol|luna|astra))?|"
    r"grok(?:\s+\d+(?:\.\d+)?)?|"
    r"neon|opus|sol|haiku|sonnet|hydrogen|carbon|luna|astra"
    r")(?:\s+(?:high|medium|med|low|xhigh|max))?\b[:\s\-–—]*",
    re.IGNORECASE,
)
_PREFIX_SPLIT_RE = re.compile(r"^([^:—–]+?)\s*(?::|—|–|\s+-\s+)\s*(.+)$")
_GENERIC_SLOT_PREFIX_RE = re.compile(
    r"^(?:"
    r"phase\s+\d+[a-z]?(?:\s+worker\s+\d+)?|"
    r"step\s+\d+[a-z]?(?:-[a-z0-9-]+)*|"
    r"wave\s+\d+[a-z0-9-]*|"
    r"worker\s+#?\d+|"
    r"task\s+#?\d+|"
    r"neon\s+[a-z0-9#]+|"
    r"opus\s+(?:#?\d+|\d+/\d+)|"
    r"sol\s+(?:#?\d+|\d+/\d+)"
    r")$",
    re.IGNORECASE,
)
_ACTION_PREFIX_RE = re.compile(
    r"^(?:perm(?:ission)?\s+check|re-?test(?:\s+.+)?|browser(?:\s*&\s*[a-z0-9-]+)?\s+check|"
    r"review(?:\s+[a-z0-9_-]+)?|re-?check(?:\s+[a-z0-9_-]+)?|final\s+approval(?:\s*#\d+)?|"
    r"design\s+review|cold\s+test|final\s+verification\s+review)$",
    re.IGNORECASE,
)
_DECISIONS_PAIR_RE = re.compile(r"\bDecisions?\s+(\d+)\s*(?:&|and|-|–)\s*(\d+)\b", re.IGNORECASE)
_OPTION_TAG_RE = re.compile(r"\bOption\s+([A-Za-z0-9]+)\b", re.IGNORECASE)
_VERSION_TAG_RE = re.compile(r"\b(v[2-9]|v1[0-9])\b", re.IGNORECASE)
_VERSION_PAREN_RE = re.compile(r"\((v[2-9]|v1[0-9])\)", re.IGNORECASE)
_RANGE_HYPHEN_RE = re.compile(r"\b([a-zA-Z]?\d+)-([a-zA-Z]?\d+)\b")
_NUM_AMP_PAIR_RE = re.compile(r"\b(\d+)\s*&\s*(\d+)\b")
_FOR_PHASE_RE = re.compile(
    r"^(.+?)\s+for\s+(Phase\s+[0-9A-Za-z.]+|Wave\s+[0-9A-Za-z.-]+|Module\s+[0-9A-Za-z.-]+)\b(.*)$",
    re.IGNORECASE,
)
_FOR_TAIL_RE = re.compile(
    r"\s+for\s+(?:PA\d+|Mod(?:ule)?\s+\d|Branch\s+\d|Phase\s+\d).*$",
    re.IGNORECASE,
)
_ABS_PATH_CLAUSE_RE = re.compile(
    r"\s+(?:in|inside|at|from|to)\s+(?:[A-Z]+=)?/(?:[A-Za-z0-9_.@+-]+/)+[A-Za-z0-9_.@+-]*"
)
_BARE_ABS_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])/(?:home|Users|tmp|var|opt|etc)/[^\s,)]+")
_BRANCH_PAREN_RE = re.compile(r"\s*\(branch\s+[^)]+\)", re.IGNORECASE)
_USING_TAIL_RE = re.compile(r"\s+using\s+(?:real\s+vendored|vendored|server-rendered)\b.*$", re.IGNORECASE)
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
_LIST_ITEM_RE = re.compile(r"^(?:[-*]|\d+\.)\s+")
_SENT_SPLIT_RE = re.compile(r"(?<=[a-z0-9)])([.!?])\s+(?=[A-Z])")
_COMPLETE_ON_DISK_RE = re.compile(
    r"\b(?:complete\s+on\s+disk|is\s+complete|are\s+now\s+complete)\b",
    re.IGNORECASE,
)
_BEFORE_COULD_RE = re.compile(r"\bbefore\s+you\s+could\s+(.+?)[.!?]?$", re.IGNORECASE)
_STUDY_LAB_RE = re.compile(r"\bStudy\s+Lab\b", re.IGNORECASE)
_SCENES_RANGE_RE = re.compile(r"\bScenes?\s+(s\d+[–-]s?\d+)\b", re.IGNORECASE)
_SCENES_LIST_RE = re.compile(r"\bScenes?\s+(s\d+(?:\s*,\s*s\d+)+)\b", re.IGNORECASE)
_AMP_TAIL_RE = re.compile(r"&\s*([A-Za-z0-9_-]+)\s*$")
_MAIN_WORD_RE = re.compile(r"\bMain\b", re.IGNORECASE)
_BOILERPLATE_TASK_RE = re.compile(
    r"^(?:Read\s+first|You\s+have\s+already|Your\s+primary\s+source|Your\s+previous\s+run)\b",
    re.IGNORECASE,
)
_SCENE_TOK_RE = re.compile(r"^s\d+(?:[–-]s?\d+)?$")
_NUM_TOK_RE = re.compile(r"^\d+(?:\.\d+)?[a-z]?$")
_RANGE_PART_RE = re.compile(r"^[a-zA-Z]?\d+$")
_HASH_TAG_RE = re.compile(r"^#\d+$")
_WORD_TOKEN_RE = re.compile(r"#\d+|[A-Za-z0-9][A-Za-z0-9.&/+–-]*")
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)

_SIGNAL_RULES: tuple[tuple[re.Pattern[str], str, bool], ...] = (
    (re.compile(r"\bsynthesi[sz]e\b", re.IGNORECASE), "Synthesis", True),
    (re.compile(r"\bresume\b", re.IGNORECASE), "Resume", True),
    (re.compile(r"\bre-?check\b", re.IGNORECASE), "Re-check", True),
    (re.compile(r"\bre-?review\b", re.IGNORECASE), "Re-review", True),
    (re.compile(r"\breview\b", re.IGNORECASE), "Review", False),
    (re.compile(r"\bverify\b", re.IGNORECASE), "Verify", False),
    (re.compile(r"\baudit\b", re.IGNORECASE), "Audit", False),
)

_ACRONYM_MAP: dict[str, str] = {
    "ui": "UI",
    "api": "API",
    "llm": "LLM",
    "llms": "LLMs",
    "html": "HTML",
    "css": "CSS",
    "sql": "SQL",
    "sqlite": "SQLite",
    "qa": "QA",
    "vtt": "VTT",
    "yt": "YT",
    "h&e": "H&E",
    "fm": "FM",
    "fms": "FMs",
    "ml": "ML",
    "vm": "VM",
    "pr": "PR",
    "prs": "PRs",
    "ci": "CI",
    "t3": "T3",
    "agy": "AGY",
    "pa": "PA",
    "pa1": "PA1",
    "ai": "AI",
    "io": "I/O",
    "r/w": "R/W",
    "v1": "V1",
    "v2": "V2",
    "v3": "V3",
    "v4": "V4",
    "v5": "V5",
    "1a": "1A",
    "1b": "1B",
    "1c": "1C",
    "2a": "2A",
    "2b": "2B",
    "3a": "3A",
    "3b": "3B",
    "l1": "L1",
    "l2": "L2",
    "l3": "L3",
    "l4": "L4",
    "katex": "KaTeX",
    "kokoro": "Kokoro",
    "inkpress": "Inkpress",
    "inkclassshell": "InkClassShell",
    "arxiv": "arXiv",
    "biorxiv": "bioRxiv",
}

_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "for",
        "of",
        "in",
        "on",
        "at",
        "to",
        "from",
        "by",
        "with",
        "and",
        "or",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "this",
        "that",
        "these",
        "those",
        "your",
        "you",
        "our",
        "into",
        "via",
        "using",
        "use",
        "only",
        "all",
        "create",
        "build",
        "write",
        "make",
        "run",
        "conduct",
        "complete",
        "execute",
        "executing",
        "implement",
        "interactive",
        "self-contained",
        "distinct",
        "variations",
        "variation",
        "showing",
        "show",
        "main",
        "program",
        "upgrade",
        "plugin",
        "task",
        "branch",
        "incl",
        "full",
        "fresh-eyes",
        "pilot",
        "real",
        "vendored",
    }
)

_TRAILING_DANGLING_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "for",
        "of",
        "in",
        "on",
        "at",
        "to",
        "from",
        "by",
        "with",
        "into",
        "via",
        "using",
        "is",
        "are",
        "that",
        "which",
    }
)

_GENERIC_ROLE_HEADS = frozenset(
    {
        "delegator",
        "lead",
        "head",
        "orchestrator",
        "engineer",
        "specialist",
        "architect",
        "director",
        "reviewer",
        "auditor",
        "researcher",
        "worker",
        "coordinator",
    }
)

_LOW_SIGNAL_DOMAIN_WORDS = frozenset(
    {
        "branch",
        "phase",
        "step",
        "wave",
        "module",
        "plugin",
        "upgrade",
        "program",
        "project",
        "repo",
        "repository",
        "work",
        "team",
        "pilot",
        "set",
    }
)

_GENERIC_NOISE_ADJECTIVES = frozenset(
    {"warm", "custom", "expanded", "upgraded", "offline", "unified", "clean", "complete"}
)


# ---------------------------------------------------------------------------
# Cross-platform state & config paths (no circular server import)
# ---------------------------------------------------------------------------


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


def _read_user_config_file(path: Path | None) -> dict[str, Any]:
    """Read and mtime-cache JSON config dict from disk (returns {} on absence/error)."""
    if path is None:
        return {}
    try:
        st = path.stat()
    except OSError:
        return {}
    key = str(path)
    cached = _CONFIG_FILE_CACHE.get(key)
    if cached is not None and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return cached[2]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        data = raw if isinstance(raw, dict) else {}
    except (OSError, ValueError, TypeError):
        data = {}
    _CONFIG_FILE_CACHE[key] = (st.st_mtime_ns, st.st_size, data)
    return data


def _parse_bool_env(val: str) -> bool | None:
    low = val.strip().lower()
    if low in ("1", "true", "yes", "on"):
        return True
    if low in ("0", "false", "no", "off"):
        return False
    return None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write compact JSON to `path` via mkstemp + fsync + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}-",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def load_labeler_config(user_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load effective labeler configuration from defaults, config.json, user_config, and env."""
    cfg: dict[str, Any] = dict(DEFAULT_LABELER_CONFIG)

    file_cfg = _read_user_config_file(get_config_path())
    file_lbl = file_cfg.get("labeler")
    if isinstance(file_lbl, dict):
        for k in ("enabled", "cli", "model", "timeout_s"):
            if k in file_lbl and file_lbl[k] is not None:
                cfg[k] = file_lbl[k]

    if isinstance(user_config, dict):
        u_lbl = user_config.get("labeler")
        if isinstance(u_lbl, dict):
            for k in ("enabled", "cli", "model", "timeout_s"):
                if k in u_lbl and u_lbl[k] is not None:
                    cfg[k] = u_lbl[k]
        for k in ("enabled", "cli", "model", "timeout_s"):
            if k in user_config and user_config[k] is not None:
                cfg[k] = user_config[k]

    env_enabled = os.environ.get("SWARM_BOARD_LABELER", "")
    if env_enabled.strip():
        parsed = _parse_bool_env(env_enabled)
        if parsed is not None:
            cfg["enabled"] = parsed

    env_cli = os.environ.get("SWARM_BOARD_LABEL_CLI", "").strip().lower()
    if env_cli:
        cfg["cli"] = env_cli

    env_model = os.environ.get("SWARM_BOARD_LABEL_MODEL", "").strip()
    if env_model:
        cfg["model"] = env_model

    cli = str(cfg.get("cli") or DEFAULT_LABELER_CONFIG["cli"]).strip().lower()
    if cli not in SUPPORTED_CLIS:
        cli = str(DEFAULT_LABELER_CONFIG["cli"])
    model = str(cfg.get("model") or DEFAULT_LABELER_CONFIG["model"]).strip()
    if not model:
        model = str(DEFAULT_LABELER_CONFIG["model"])
    try:
        timeout_s = int(float(cfg.get("timeout_s", DEFAULT_LABELER_CONFIG["timeout_s"])))
    except (TypeError, ValueError):
        timeout_s = int(DEFAULT_LABELER_CONFIG["timeout_s"])
    timeout_s = max(3, min(180, timeout_s))

    return {
        "enabled": bool(cfg.get("enabled", False)),
        "cli": cli,
        "model": model,
        "timeout_s": timeout_s,
    }


def _update_config_file(patch: dict[str, Any], config_path: Path | None = None) -> Path:
    """Persist labeler keys into ~/.config/swarm-board/config.json preserving other sections."""
    target = config_path or get_config_path() or _DEFAULT_CONFIG_PATH
    existing: dict[str, Any] = {}
    if target.is_file():
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw
        except (OSError, ValueError, TypeError):
            existing = {}
    lbl = dict(existing.get("labeler")) if isinstance(existing.get("labeler"), dict) else {}
    lbl.update(patch)
    existing["labeler"] = lbl
    _atomic_write_json(target, existing)
    _CONFIG_FILE_CACHE.pop(str(target), None)
    return target


# ---------------------------------------------------------------------------
# Persistent mtime-cached LabelCache
# ---------------------------------------------------------------------------


def make_cache_key(
    model_spec: str,
    agent_id: str,
    raw_name: str,
    prompt_head: str,
    sibling_sig: str,
    schema_version: str = SCHEMA_VERSION,
) -> str:
    """Compute a deterministic content-addressed key for one agent's label entry."""
    payload = json.dumps(
        [
            schema_version,
            model_spec,
            agent_id,
            raw_name,
            prompt_head[:400],
            sibling_sig,
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def _compute_sibling_sig(
    siblings: list[dict[str, Any]],
    hints_by_id: dict[str, dict[str, Any]],
) -> str:
    """Compute a compact signature representing all sibling agents under one parent."""
    items: list[tuple[str, str, str]] = []
    for ag in siblings:
        aid = str(ag.get("id") or "")
        hint = hints_by_id.get(aid) or {}
        raw_name = str(hint.get("rawTitle") or ag.get("name") or "")
        prompt_head = str(hint.get("prompt") or ag.get("brief") or "")[:120]
        items.append((aid, raw_name, prompt_head))
    items.sort()
    raw = json.dumps(items, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


class LabelCache:
    """Mtime-cached, atomic-write JSON cache for LLM-generated agent labels and tasks."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path is not None else get_state_dir() / "labels-cache.json"
        self._lock = threading.RLock()
        self._mtime: float = 0.0
        self._mtime_ns: int = -1
        self._size: int = -1
        self._entries: dict[str, dict[str, Any]] = {}

    def _ensure_loaded(self) -> None:
        try:
            st = self.path.stat()
        except OSError:
            self._entries = {}
            self._mtime = 0.0
            self._mtime_ns = -1
            self._size = -1
            return
        if st.st_mtime_ns == self._mtime_ns and st.st_size == self._size:
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            self._entries = {}
            self._mtime = st.st_mtime
            self._mtime_ns = st.st_mtime_ns
            self._size = st.st_size
            return

        entries_raw = raw.get("entries") if isinstance(raw, dict) else None
        if not isinstance(entries_raw, dict):
            entries_raw = raw if isinstance(raw, dict) else {}
        cleaned: dict[str, dict[str, Any]] = {}
        for k, v in entries_raw.items():
            if not isinstance(v, dict):
                continue
            lbl = v.get("label")
            tsk = v.get("task")
            if isinstance(lbl, str) and lbl.strip() and isinstance(tsk, str) and tsk.strip():
                cleaned[str(k)] = dict(v)
        self._entries = cleaned
        self._mtime = st.st_mtime
        self._mtime_ns = st.st_mtime_ns
        self._size = st.st_size

    def mtime(self) -> float:
        """Return cache file mtime (0.0 when absent)."""
        with self._lock:
            try:
                return self.path.stat().st_mtime
            except OSError:
                return 0.0

    def get(self, key: str) -> dict[str, Any] | None:
        """Lookup a cached label entry by content hash key."""
        if not key:
            return None
        with self._lock:
            self._ensure_loaded()
            item = self._entries.get(key)
            return dict(item) if item is not None else None

    def put_many(self, mapping: dict[str, dict[str, Any]]) -> int:
        """Merge entries and persist atomically to disk. Returns number of entries stored."""
        if not mapping:
            return 0
        with self._lock:
            self._ensure_loaded()
            stored = 0
            now_ts = time.time()
            for key, val in mapping.items():
                if not isinstance(val, dict):
                    continue
                lbl = str(val.get("label") or "").strip()
                tsk = str(val.get("task") or "").strip()
                if not lbl or not tsk:
                    continue
                rec = dict(val)
                rec["label"] = lbl
                rec["task"] = tsk
                rec.setdefault("updatedAt", now_ts)
                self._entries[str(key)] = rec
                stored += 1
            if not stored:
                return 0
            if len(self._entries) > _MAX_CACHE_ENTRIES:
                ordered = sorted(
                    self._entries.items(),
                    key=lambda kv: float(kv[1].get("updatedAt") or 0.0),
                    reverse=True,
                )[:_MAX_CACHE_ENTRIES]
                self._entries = dict(ordered)

            doc = {
                "schemaVersion": SCHEMA_VERSION,
                "updatedAt": datetime.now(tz=timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
                "entries": self._entries,
            }
            _atomic_write_json(self.path, doc)
            try:
                st = self.path.stat()
                self._mtime = st.st_mtime
                self._mtime_ns = st.st_mtime_ns
                self._size = st.st_size
            except OSError:
                pass
            return stored

    def clear(self) -> int:
        """Remove all cached label entries from memory and disk."""
        with self._lock:
            self._ensure_loaded()
            removed = len(self._entries)
            self._entries = {}
            self._mtime = 0.0
            self._mtime_ns = -1
            self._size = -1
            try:
                if self.path.exists():
                    self.path.unlink()
            except OSError:
                pass
            return removed

    def stats(self) -> dict[str, Any]:
        """Return cache statistics without forcing writes."""
        with self._lock:
            self._ensure_loaded()
            exists = self.path.is_file()
            return {
                "path": str(self.path),
                "exists": exists,
                "entries": len(self._entries),
                "mtime": self._mtime,
                "sizeBytes": max(0, self._size),
            }


# ---------------------------------------------------------------------------
# Hygiene & deterministic sibling-aware label/task synthesis
# ---------------------------------------------------------------------------


def _strip_markdown(text: str) -> str:
    if not text:
        return ""
    s = text
    if "](" in s:
        s = _MD_LINK_RE.sub(r"\1", s)
    if s.startswith("#") or "#" in s[:6]:
        s = _MD_HEADING_RE.sub("", s)
    if "**" in s:
        s = s.replace("**", "")
    if "__" in s:
        s = s.replace("__", "")
    if "`" in s:
        s = s.replace("`", "")
    if "*" in s:
        s = _ITALIC_STAR_RE.sub("", s)
    return _WS_RE.sub(" ", s).strip()


def _strip_subagent_preamble(text: str) -> str:
    s = _strip_markdown(text)
    if s.startswith(("Act as", "act as")):
        s = _LEADING_ACT_AS_RE.sub("", s)
    if s.startswith("@"):
        s = _LEADING_MENTION_RE.sub("", s)
    return s.strip()


def _strip_model_tags(text: str) -> str:
    if "(" not in text and ")" not in text:
        return text
    s = _PAREN_MODEL_TAG_RE.sub("", text)
    if ")" in s:
        s = _ORPHAN_EFFORT_PAREN_RE.sub("", s)
    if "(" in s:
        s = _EMPTY_PAREN_RE.sub("", s)
    return _WS_RE.sub(" ", s).strip()


def _format_token(tok: str) -> str:
    if not tok:
        return ""
    if _HASH_TAG_RE.match(tok):
        return tok
    low = tok.lower()
    if low in _ACRONYM_MAP:
        return _ACRONYM_MAP[low]
    if _SCENE_TOK_RE.match(low):
        return _RANGE_HYPHEN_RE.sub(r"\1–\2", low)
    if _NUM_TOK_RE.match(low):
        return tok.upper() if any(c.isalpha() for c in tok) and len(tok) <= 3 else tok
    if "–" in tok or "-" in tok:
        sep = "–" if "–" in tok else "-"
        parts = tok.split(sep)
        if all(_RANGE_PART_RE.match(p) for p in parts):
            return "–".join(p.lower() if p[:1].lower() == "s" else p for p in parts)
        return sep.join(_format_token(p) for p in parts if p)
    if "&" in tok and len(tok) <= 5:
        return tok.upper()
    if "/" in tok and len(tok) <= 6:
        return tok.upper()
    if tok.isupper() and 2 <= len(tok) <= 4:
        return tok
    return tok[0].upper() + tok[1:]


def _short_model_name(raw: str) -> str:
    s = _strip_markdown(raw).strip()
    low = s.lower()
    for key, label in (
        ("opus", "Opus"),
        ("sonnet", "Sonnet"),
        ("haiku", "Haiku"),
        ("fable", "Fable"),
        ("neon", "Neon"),
        ("hydrogen", "Hydrogen"),
        ("carbon", "Carbon"),
        ("flash", "Flash"),
        ("sol", "Sol"),
        ("luna", "Luna"),
        ("astra", "Astra"),
        ("grok", "Grok"),
        ("codex", "Codex"),
        ("claude", "Claude"),
        ("gemini", "Gemini"),
    ):
        if key in low:
            return label
    toks = _WORD_TOKEN_RE.findall(s)
    return _format_token(toks[0]) if toks else "Model"


def sanitize_label(raw: str, *, preserve_connectors: bool = True) -> str:
    """Enforce a clean 1-3 word (<= 30 chars) display label without markdown or trailing ellipses."""
    s = _strip_markdown(raw or "").strip().strip("\"'“”‘’")
    s = _ELLIPSIS_TRAIL_RE.sub("", s).strip(" :-–—.,;|")
    s = _strip_model_tags(s)
    s = _NUM_AMP_PAIR_RE.sub(r"\1–\2", s)
    s = _RANGE_HYPHEN_RE.sub(r"\1–\2", s)
    if not s:
        return "Worker"

    if preserve_connectors and " · " in s:
        left, right = s.split(" · ", 1)
        left_toks = _WORD_TOKEN_RE.findall(left)
        right_toks = _WORD_TOKEN_RE.findall(right)
        if left_toks and right_toks and (len(left_toks) + len(right_toks)) <= 4:
            l_fmt = " ".join(_format_token(t) for t in left_toks[:2])
            r_fmt = " ".join(_format_token(t) for t in right_toks[:1])
            joined = f"{l_fmt} · {r_fmt}"
            if len(joined) <= 30:
                return joined

    tokens = _WORD_TOKEN_RE.findall(s)
    if not tokens:
        return "Worker"
    kept: list[str] = []
    for tok in tokens:
        fmt = _format_token(tok)
        if not fmt:
            continue
        kept.append(fmt)
        if len(kept) == 3:
            break
    while len(kept) > 1 and kept[-1].lower() in _TRAILING_DANGLING_WORDS:
        kept.pop()
    out = " ".join(kept)
    while len(out) > 30 and len(kept) > 1:
        kept.pop()
        out = " ".join(kept)
    if len(out) > 30:
        out = out[:30].rstrip(" -–—")
    return out or "Worker"


def _safe_suffix_label(base: str, tag: str, max_len: int = 30) -> str:
    """Append a numeric disambiguation tag (#2, #3, ...) guaranteed to fit within max_len."""
    words = [w for w in base.split() if not _HASH_TAG_RE.match(w)]
    base_words = words[:2] if len(words) >= 3 else (words or ["Worker"])
    avail = max(1, max_len - len(tag) - 1)
    head = " ".join(base_words)
    if len(head) > avail:
        if len(base_words) > 1 and len(base_words[0]) <= avail:
            head = base_words[0]
        else:
            head = head[:avail].rstrip(" -–—.,;:")
    return f"{head} {tag}".strip()


def _clean_sentence_body(text: str) -> str:
    s = _strip_subagent_preamble(text)
    s = _strip_model_tags(s)
    if "(" in s:
        s = _BRANCH_PAREN_RE.sub("", s)
    if "/" in s:
        s = _ABS_PATH_CLAUSE_RE.sub("", s)
        s = _BARE_ABS_PATH_RE.sub("", s)
    return _WS_RE.sub(" ", s).strip()


def _extract_first_sentence(text: str) -> str:
    if not text:
        return ""
    pre_stripped = _LEADING_ACT_AS_RE.sub("", text)
    if pre_stripped.lstrip().startswith("@"):
        pre_stripped = _LEADING_MENTION_RE.sub("", pre_stripped.lstrip())

    # Take ONLY the first non-heading, non-bullet paragraph so guardrails never bleed in.
    chosen_para = ""
    for block in _PARAGRAPH_SPLIT_RE.split(pre_stripped.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        if lines[0].startswith("#") and len(lines) == 1:
            continue
        non_head = [ln for ln in lines if not ln.startswith("#") and not _LIST_ITEM_RE.match(ln)]
        if non_head:
            chosen_para = " ".join(non_head)
            break
    if not chosen_para:
        chosen_para = pre_stripped

    joined = _clean_sentence_body(chosen_para)
    if not joined:
        return ""

    m = _SENT_SPLIT_RE.search(joined)
    if not m:
        return joined
    first = joined[: m.start() + 1].strip()
    rest = joined[m.end() :].strip()
    if _COMPLETE_ON_DISK_RE.search(first):
        m_next = _SENT_SPLIT_RE.search(rest)
        second = rest[: m_next.start() + 1].strip() if m_next else rest
        m_act = _BEFORE_COULD_RE.search(second)
        if m_act:
            base = _PAREN_ANY_RE.sub("", first).replace("$", "").strip().rstrip(".!? ")
            base = _WS_RE.sub(" ", base)
            return f"{base}; {m_act.group(1).strip()}."
    return first


def sanitize_task(raw: str, max_len: int = 140) -> str:
    """Produce a clean, <= 140 char single sentence without markdown or trailing ellipses."""
    s = _clean_sentence_body(raw or "").strip("\"'“”‘’")
    s = _ELLIPSIS_TRAIL_RE.sub("", s).strip()
    if not s:
        return ""

    m_exec = _YOU_EXEC_WRAP_RE.match(s)
    if m_exec:
        s = "Execute " + m_exec.group(1).strip()
    else:
        m_role = _YOU_ARE_WRAP_RE.match(s)
        if m_role:
            s = m_role.group(1).strip()

    s = _WS_RE.sub(" ", s).strip()
    if len(s) > max_len:
        s_pruned = _USING_TAIL_RE.sub("", s).strip()
        if 24 <= len(s_pruned) <= max_len:
            s = s_pruned
    if len(s) > max_len and "(" in s:
        s_noparen = _LONG_PAREN_RE.sub("", s).strip()
        if 24 <= len(s_noparen) <= max_len:
            s = s_noparen
    if len(s) > max_len:
        cutoff = max_len - 1
        chunk = s[:cutoff]
        clause_pos = max(chunk.rfind("; "), chunk.rfind(" — "), chunk.rfind(", "))
        if clause_pos >= int(max_len * 0.55):
            chunk = chunk[:clause_pos]
        else:
            sp = chunk.rfind(" ")
            if sp >= int(max_len * 0.45):
                chunk = chunk[:sp]
        s = chunk

    if s.count("(") > s.count(")"):
        last_open = s.rfind("(")
        if last_open >= 16:
            s = s[:last_open]

    s = _ELLIPSIS_TRAIL_RE.sub("", s).rstrip(" ,;:-–—")
    words = s.split()
    while len(words) > 2 and words[-1].lower().rstrip(".,;:-–—") in _TRAILING_DANGLING_WORDS:
        words.pop()
    s = " ".join(words).rstrip(" ,;:-–—")
    if not s:
        return ""
    if s[0].islower():
        s = s[0].upper() + s[1:]
    if s[-1] not in ".!?":
        s += "."
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip(" ,;:-–—.") + "."
    return s


def _derive_task(
    agent: dict[str, Any],
    hint: dict[str, Any],
    flow: dict[str, Any],
) -> str:
    role = str(agent.get("role") or "worker").lower()
    raw_name = str(hint.get("rawTitle") or agent.get("name") or "")
    flow_goal = str(flow.get("goal") or "").strip()
    flow_title = str(flow.get("title") or "").strip()
    prompt = str(hint.get("prompt") or agent.get("brief") or "").strip()
    plan_task = str(hint.get("planTask") or "").strip()

    if role == "root":
        src = flow_goal or raw_name or flow_title or "Coordinate flow."
        return sanitize_task(_extract_first_sentence(src) if "\n" in src else src)

    if role == "coordinator":
        src = plan_task or flow_goal or flow_title or "Coordinate swarm workers."
        return sanitize_task(_extract_first_sentence(src) if "\n" in src else src)

    if plan_task:
        cand = sanitize_task(_extract_first_sentence(plan_task))
        if cand:
            return cand

    prompt_cand = sanitize_task(_extract_first_sentence(prompt)) if prompt else ""
    has_clean_title = (
        bool(raw_name)
        and not _SEAT_RE.match(raw_name)
        and not _MODEL_ROLE_RE.match(raw_name)
        and not _COMMITTEE_PAREN_RE.search(raw_name)
        and not _SLUG_NAME_RE.match(raw_name.strip())
    )
    if prompt_cand and not (has_clean_title and (len(prompt_cand) < 22 or _BOILERPLATE_TASK_RE.match(prompt_cand))):
        return prompt_cand

    if has_clean_title:
        cleaned_raw = _ELLIPSIS_TRAIL_RE.sub("", raw_name).strip()
        if cleaned_raw:
            cand = sanitize_task(_extract_first_sentence(cleaned_raw))
            if cand:
                return cand

    if prompt_cand:
        return prompt_cand

    m_seat = _SEAT_RE.match(raw_name)
    m_cpar = _COMMITTEE_PAREN_RE.search(raw_name) if not m_seat else None
    if m_seat or m_cpar:
        seat_n = m_seat.group(1) if m_seat else m_cpar.group(1)  # type: ignore[union-attr]
        if flow_goal:
            return sanitize_task(f"Committee review: {_extract_first_sentence(flow_goal)}")
        return sanitize_task(f"Evaluate {flow_title or 'committee brief'} as Seat {seat_n}.")

    m_mr = _MODEL_ROLE_RE.match(raw_name)
    if m_mr:
        r_word = m_mr.group(2).lower()
        target = flow_goal or flow_title or "delegated task"
        verb = "Review" if r_word == "reviewer" else "Execute"
        return sanitize_task(f"{verb} {target}.")

    last_act = str(agent.get("last") or "").strip()
    if last_act and not _SLUG_NAME_RE.match(last_act):
        cand = sanitize_task(_extract_first_sentence(last_act))
        if cand:
            return cand

    if _SLUG_NAME_RE.match(raw_name.strip()):
        human = " ".join(_format_token(p) for p in _SLUG_SPLIT_RE.split(raw_name.strip()) if p)
        if flow_goal:
            return sanitize_task(f"{human} for {flow_goal}.")
        return sanitize_task(f"Work on {human}.")

    return sanitize_task(f"Execute {role} task.")


def _extract_content_words(text: str, *, limit: int = 3) -> list[str]:
    s = _strip_model_tags(_strip_subagent_preamble(text))
    s = _RANGE_HYPHEN_RE.sub(r"\1–\2", s)
    tokens = _WORD_TOKEN_RE.findall(s)
    out: list[str] = []
    seen_low: set[str] = set()
    for tok in tokens:
        low = tok.lower()
        if low in _STOP_WORDS or low in seen_low:
            continue
        if len(low) == 1 and not low.isdigit() and low not in ("a", "b", "c", "d"):
            continue
        seen_low.add(low)
        out.append(_format_token(tok))
        if len(out) >= limit:
            break
    return out


def _condense_detail(detail: str) -> str:
    """Condense a structured detail clause into a crisp 2-3 word label."""
    raw_stripped = _strip_markdown(detail)
    ver_m = _VERSION_PAREN_RE.search(raw_stripped)
    ver_tag = ver_m.group(1).upper() if ver_m else ""

    s = _strip_model_tags(raw_stripped)
    s = _LEADING_MODEL_DETAIL_RE.sub("", s).strip()
    s = _ELLIPSIS_TRAIL_RE.sub("", s).strip()
    s = _FOR_TAIL_RE.sub("", s).strip()
    s = _RANGE_HYPHEN_RE.sub(r"\1–\2", s)

    if _STUDY_LAB_RE.search(s):
        return "Study Lab"
    m_scenes = _SCENES_RANGE_RE.search(s)
    if m_scenes:
        return f"Scenes {m_scenes.group(1).lower().replace('-', '–')}"
    m_slist = _SCENES_LIST_RE.search(s)
    if m_slist:
        items = [x.strip().lower() for x in m_slist.group(1).split(",") if x.strip()]
        if len(items) >= 2:
            return f"Scenes {items[0]}–{items[-1]}"
    m_dec = _DECISIONS_PAIR_RE.search(s)
    if m_dec:
        return f"Decisions {m_dec.group(1)}–{m_dec.group(2)}"
    if ver_tag and "review" in s.lower():
        return f"{ver_tag} Review"

    words = _extract_content_words(s, limit=4)
    if len(words) <= 3:
        return sanitize_label(" ".join(words)) if words else "Worker"

    filtered = [w for w in words if w.lower() not in _GENERIC_NOISE_ADJECTIVES]
    chosen = (filtered or words)[:3]
    return sanitize_label(" ".join(chosen))


def _label_from_single_agent(agent: dict[str, Any], hint: dict[str, Any], flow: dict[str, Any]) -> str:
    role = str(agent.get("role") or "worker").lower()
    if role == "root":
        return "root"
    if role == "coordinator":
        return "Coordinator"

    raw_name = _strip_markdown(str(hint.get("rawTitle") or agent.get("name") or "")).strip()
    prompt = _strip_subagent_preamble(str(hint.get("prompt") or agent.get("brief") or ""))
    slug = str(hint.get("slug") or "").strip()

    # 1. Committee seat / inline committee gate
    m_seat = _SEAT_RE.match(raw_name)
    if m_seat:
        return sanitize_label(f"Seat {m_seat.group(1)} · {_short_model_name(m_seat.group(2))}")
    m_cpar = _COMMITTEE_PAREN_RE.search(raw_name)
    if m_cpar:
        return sanitize_label(f"Seat {m_cpar.group(1)} · {_short_model_name(m_cpar.group(2))}")

    # 2. Model-only delegate: "claude-opus-5-5 (reviewer)"
    m_mr = _MODEL_ROLE_RE.match(raw_name)
    if m_mr:
        return sanitize_label(f"{_short_model_name(m_mr.group(1))} {m_mr.group(2).title()}")

    # 3. Short slug / AGY roster name (no spaces, not UUID)
    if raw_name and _SLUG_NAME_RE.match(raw_name) and not _UUID_LIKE_RE.match(raw_name):
        parts = [p for p in _SLUG_SPLIT_RE.split(raw_name) if p]
        return sanitize_label(" ".join(_format_token(p) for p in parts[:3]))

    # 4. Explicit role prompt ("You are the Delegator (Neon High) for the Inkpress ...")
    role_src = ""
    if _YOU_ARE_PREFIX_RE.match(raw_name):
        role_src = prompt or raw_name
    elif _ELLIPSIS_TRAIL_RE.search(raw_name) and _YOU_ARE_PREFIX_RE.match(prompt):
        role_src = prompt

    if role_src:
        first_line = _clean_sentence_body(role_src.splitlines()[0])
        m_assign = _ROLE_ASSIGNMENT_RE.match(first_line)
        if m_assign:
            role_phrase = _strip_model_tags(m_assign.group(1)).strip()
            subj_phrase = _strip_model_tags(m_assign.group(2) or "").strip()
            subj_phrase = _PAREN_ANY_RE.sub("", subj_phrase).strip()
            role_phrase = _MAIN_WORD_RE.sub("", role_phrase).strip()
            if "&" in role_phrase:
                role_phrase = role_phrase.split("&", 1)[0].strip()

            role_tokens = [t for t in _WORD_TOKEN_RE.findall(role_phrase) if t.lower() not in ("the", "a", "an")]
            if len(role_tokens) == 1 and role_tokens[0].lower() in _GENERIC_ROLE_HEADS and subj_phrase:
                subj_tokens = [
                    t
                    for t in _WORD_TOKEN_RE.findall(subj_phrase)
                    if t.lower() not in _STOP_WORDS and t.lower() not in _LOW_SIGNAL_DOMAIN_WORDS
                ]
                if subj_tokens:
                    return sanitize_label(f"{_format_token(subj_tokens[0])} {_format_token(role_tokens[0])}")
            if role_tokens:
                return sanitize_label(" ".join(role_tokens[:3]))

    # 5. Structured prefix titles ("Phase 3 Worker 1: ...", "Pillar 1A: ...", "Branch 1 Main Orchestrator — ...")
    clean_title = _strip_model_tags(_ELLIPSIS_TRAIL_RE.sub("", raw_name)).strip()
    m_pref = _PREFIX_SPLIT_RE.match(clean_title)
    if m_pref:
        prefix = m_pref.group(1).strip()
        detail = m_pref.group(2).strip()
        prefix_clean = _WS_RE.sub(" ", _MAIN_WORD_RE.sub("", prefix)).strip()

        if _GENERIC_SLOT_PREFIX_RE.match(prefix_clean):
            return _condense_detail(detail)

        if _ACTION_PREFIX_RE.match(prefix_clean):
            p_words = _WORD_TOKEN_RE.findall(prefix_clean)
            d_words = _extract_content_words(detail, limit=2)
            if len(p_words) == 2 and p_words[1].lower() in ("check", "fixes") and d_words:
                return sanitize_label(f"{p_words[0]} {d_words[0]}")
            if len(p_words) <= 2 and d_words:
                return sanitize_label(f"{' '.join(p_words)} {d_words[0]}")
            return sanitize_label(" ".join(p_words[:3]))

        p_tokens = _WORD_TOKEN_RE.findall(prefix_clean)
        if p_tokens and p_tokens[-1].lower() in _GENERIC_ROLE_HEADS and len(p_tokens) <= 4:
            if len(p_tokens) == 4:
                return sanitize_label(f"{p_tokens[0]} {p_tokens[1]} {p_tokens[-1]}")
            return sanitize_label(" ".join(p_tokens[:3]))
        if len(p_tokens) >= 4:
            detail_words = _extract_content_words(detail, limit=3)
            if detail_words:
                return _condense_detail(detail)
        if len(p_tokens) == 3:
            return sanitize_label(" ".join(p_tokens))

        if len(p_tokens) <= 2:
            m_amp = _AMP_TAIL_RE.search(detail)
            if m_amp:
                return sanitize_label(f"{' '.join(p_tokens)} {m_amp.group(1)}")
            d_words = _extract_content_words(detail, limit=2)
            if d_words:
                return sanitize_label(f"{' '.join(p_tokens)} {d_words[0]}")
            return sanitize_label(" ".join(p_tokens))

    # 6. Fork or "<Topic> for <Phase/Wave/Module X>" pattern
    m_for = _FOR_PHASE_RE.match(clean_title)
    if m_for:
        topic_words = _extract_content_words(m_for.group(1), limit=4)
        phase_toks = _WORD_TOKEN_RE.findall(m_for.group(2))
        style_word = next((w for w in topic_words if w.lower() in ("style", "styles", "decisions", "pacing")), None)
        if style_word == "Style":
            style_word = "Styles"
        pick = style_word or (topic_words[-1] if topic_words else "")
        if pick and len(phase_toks) <= 2:
            return sanitize_label(f"{' '.join(phase_toks)} {pick}")

    # 7. General title / prompt fallback
    words = _extract_content_words(clean_title, limit=3)
    if words:
        return sanitize_label(" ".join(words))
    if slug and not _UUID_LIKE_RE.match(slug):
        parts = [p for p in _SLUG_SPLIT_RE.split(slug) if p and not _VERSION_TAG_RE.match(p)]
        if parts:
            return sanitize_label(" ".join(parts[:3]))
    if prompt:
        pwords = _extract_content_words(_extract_first_sentence(prompt), limit=3)
        if pwords:
            return sanitize_label(" ".join(pwords))
    return "Worker"


def _cluster_diff_labels(
    siblings: list[dict[str, Any]],
    hints_by_id: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Differentiate siblings with missing, truncated, or near-identical titles via prompt prefix/suffix diffing."""
    raw_names: dict[str, str] = {}
    name_prefixes: dict[tuple[str, str, str], int] = {}
    name_counts: dict[str, int] = {}

    for ag in siblings:
        role = str(ag.get("role") or "worker").lower()
        if role in ("root", "coordinator"):
            continue
        aid = str(ag.get("id") or "")
        hint = hints_by_id.get(aid) or {}
        rn = _strip_markdown(str(hint.get("rawTitle") or ag.get("name") or "")).strip()
        raw_names[aid] = rn
        low = rn.lower()
        name_counts[low] = name_counts.get(low, 0) + 1
        words = _ELLIPSIS_TRAIL_RE.sub("", rn).split()
        if len(words) >= 3:
            k3 = (words[0].lower(), words[1].lower(), words[2].lower())
            name_prefixes[k3] = name_prefixes.get(k3, 0) + 1

    sources: dict[str, list[str]] = {}
    for ag in siblings:
        aid = str(ag.get("id") or "")
        rn = raw_names.get(aid)
        if rn is None:
            continue
        if _SLUG_NAME_RE.match(rn) or _SEAT_RE.match(rn) or _MODEL_ROLE_RE.match(rn) or _COMMITTEE_PAREN_RE.search(rn):
            continue
        clean_rn = _ELLIPSIS_TRAIL_RE.sub("", rn).strip()
        is_truncated = bool(_ELLIPSIS_TRAIL_RE.search(rn))
        is_duplicate_name = name_counts.get(rn.lower(), 0) > 1
        rn_words = clean_rn.split()
        shares_unstructured_prefix = (
            len(rn_words) >= 4
            and not _PREFIX_SPLIT_RE.match(clean_rn)
            and name_prefixes.get((rn_words[0].lower(), rn_words[1].lower(), rn_words[2].lower()), 0) > 1
        )
        if not (not rn or is_truncated or is_duplicate_name or shares_unstructured_prefix):
            continue

        hint = hints_by_id.get(aid) or {}
        prompt = _strip_subagent_preamble(str(hint.get("prompt") or ag.get("brief") or ""))
        base_text = _extract_first_sentence(prompt) if prompt else clean_rn
        base_text = _strip_model_tags(base_text)
        ws_toks = [t for t in base_text.split() if t]
        if len(ws_toks) >= 4:
            sources[aid] = ws_toks

    if len(sources) < 2:
        return {}

    groups: dict[tuple[str, str, str], list[str]] = {}
    for aid, toks in sources.items():
        key = (toks[0].lower(), toks[1].lower(), toks[2].lower())
        groups.setdefault(key, []).append(aid)

    out: dict[str, str] = {}
    for aids in groups.values():
        if len(aids) < 2:
            continue
        tok_lists = [sources[aid] for aid in aids]
        min_len = min(len(t) for t in tok_lists)
        pref_len = 0
        while pref_len < min_len and len({t[pref_len].lower() for t in tok_lists}) == 1:
            pref_len += 1
        suff_len = 0
        while (
            suff_len < (min_len - pref_len)
            and len({t[len(t) - 1 - suff_len].lower() for t in tok_lists}) == 1
        ):
            suff_len += 1
        if pref_len < 3:
            continue
        middles = {
            aid: " ".join(sources[aid][pref_len : len(sources[aid]) - suff_len if suff_len else None])
            for aid in aids
        }
        if len({m.lower() for m in middles.values()}) <= 1:
            continue

        for aid in aids:
            diff_str = middles[aid]
            m_dec = _DECISIONS_PAIR_RE.search(diff_str)
            if m_dec:
                out[aid] = sanitize_label(f"Decisions {m_dec.group(1)}–{m_dec.group(2)}")
                continue
            m_opt = _OPTION_TAG_RE.search(diff_str)
            if m_opt:
                opt_code = m_opt.group(1)
                cwords = [
                    w
                    for w in _extract_content_words(diff_str, limit=6)
                    if w.lower() not in ("option", opt_code.lower(), "art") and not w.isdigit()
                ]
                chosen = (cwords[:2] + [_format_token(opt_code)])[:3]
                out[aid] = sanitize_label(" ".join(chosen))
                continue
            cwords = _extract_content_words(diff_str, limit=3)
            if cwords:
                out[aid] = sanitize_label(" ".join(cwords))

    return out


def _find_unique_diff_token(
    aid: str,
    colliding_ids: list[str],
    hints_by_id: dict[str, dict[str, Any]],
    agents_by_id: dict[str, dict[str, Any]],
    current_label: str,
) -> tuple[str | None, bool]:
    """Pick a concise differentiating token and report whether it marks a follow-up/continuation."""
    existing_low = {w.lower() for w in _WORD_TOKEN_RE.findall(current_label)}
    per_agent_text: dict[str, str] = {}
    for cid in colliding_ids:
        ag = agents_by_id[cid]
        hint = hints_by_id.get(cid) or {}
        prompt_clean = _strip_subagent_preamble(str(hint.get("prompt") or ag.get("brief") or "")[:400])
        title_clean = _strip_markdown(str(hint.get("rawTitle") or ag.get("name") or ""))
        slug = str(hint.get("slug") or "")
        per_agent_text[cid] = f"{title_clean} {prompt_clean} {slug}"

    own_text = per_agent_text[aid]
    other_texts = " ".join(per_agent_text[cid] for cid in colliding_ids if cid != aid).lower()

    for pat, lbl_tok, is_followup in _SIGNAL_RULES:
        if pat.search(own_text) and not pat.search(other_texts):
            return lbl_tok, is_followup

    for m in _VERSION_TAG_RE.finditer(own_text):
        v = m.group(1).upper()
        if not re.search(rf"\b{re.escape(v)}\b", other_texts, re.IGNORECASE) and v.lower() not in existing_low:
            return v, False

    other_words = {w.lower() for w in _WORD_TOKEN_RE.findall(other_texts)}
    for cand in _extract_content_words(own_text, limit=8):
        clow = cand.lower()
        if clow not in other_words and clow not in existing_low and len(cand) >= 2:
            return cand, False
    return None, False


def _dedupe_sibling_labels(
    siblings: list[dict[str, Any]],
    labels: dict[str, str],
    hints_by_id: dict[str, dict[str, Any]],
    *,
    locked_ids: set[str] | None = None,
) -> None:
    """Guarantee every sibling under one parent has a unique label (case-insensitive)."""
    locked = locked_ids or set()
    agents_by_id = {str(ag.get("id") or ""): ag for ag in siblings}

    by_norm: dict[str, list[str]] = {}
    for ag in siblings:
        aid = str(ag.get("id") or "")
        lbl = labels.get(aid) or "Worker"
        by_norm.setdefault(lbl.lower(), []).append(aid)

    # Pass 1: semantic differentiation (prefer relabeling follow-ups only so originals keep their clean label).
    for norm_lbl, ids in list(by_norm.items()):
        if len(ids) <= 1:
            continue
        diffs: dict[str, tuple[str | None, bool]] = {
            aid: _find_unique_diff_token(aid, ids, hints_by_id, agents_by_id, labels[aid])
            for aid in ids
            if aid not in locked
        }
        has_followup = any(is_fu and tok for tok, is_fu in diffs.values())
        for aid in ids:
            if aid in locked:
                continue
            tok, is_fu = diffs.get(aid, (None, False))
            if not tok:
                continue
            if has_followup and not is_fu:
                continue
            cur = labels[aid]
            cur_words = cur.split()
            if len(cur_words) >= 3:
                cand = sanitize_label(" ".join(cur_words[:2] + [tok]))
            else:
                cand = sanitize_label(f"{cur} {tok}")
            if cand.lower() != norm_lbl:
                labels[aid] = cand

    # Pass 2: guaranteed O(1)-terminating #2, #3 suffix for any remaining identical retries.
    seen: set[str] = set()
    for ag in siblings:
        aid = str(ag.get("id") or "")
        if aid in locked:
            seen.add(labels[aid].lower())

    counts: dict[str, int] = {}
    for ag in siblings:
        aid = str(ag.get("id") or "")
        if aid in locked:
            continue
        cur = labels.get(aid) or "Worker"
        low = cur.lower()
        if low not in seen:
            seen.add(low)
            counts[low] = 1
            continue
        n = counts.get(low, 1) + 1
        while True:
            cand = _safe_suffix_label(cur, f"#{n}", max_len=30)
            if cand.lower() not in seen:
                labels[aid] = cand
                seen.add(cand.lower())
                counts[low] = n
                break
            n += 1


def _derive_sibling_group(
    siblings: list[dict[str, Any]],
    hints_by_id: dict[str, dict[str, Any]],
    flow: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    """Compute and memoize deterministic (labels_by_id, tasks_by_id) for one sibling group."""
    flow_title = str(flow.get("title") or "")
    flow_goal = str(flow.get("goal") or "")
    sig_items = []
    for ag in siblings:
        aid = str(ag.get("id") or "")
        hint = hints_by_id.get(aid) or {}
        sig_items.append(
            (
                aid,
                str(ag.get("role") or ""),
                str(hint.get("rawTitle") or ag.get("name") or ""),
                str(hint.get("prompt") or ag.get("brief") or "")[:400],
                str(hint.get("planTask") or ""),
                str(hint.get("slug") or ""),
                str(ag.get("last") or "")[:120],
            )
        )
    cache_key = (flow_title, flow_goal, tuple(sig_items))
    cached = _HEURISTIC_GROUP_CACHE.get(cache_key)
    if cached is not None:
        return cached

    diff_labels = _cluster_diff_labels(siblings, hints_by_id)
    labels: dict[str, str] = {}
    tasks: dict[str, str] = {}
    for ag in siblings:
        aid = str(ag.get("id") or "")
        labels[aid] = diff_labels[aid] if aid in diff_labels else _label_from_single_agent(ag, hints_by_id.get(aid) or {}, flow)
        tasks[aid] = _derive_task(ag, hints_by_id.get(aid) or {}, flow)
    _dedupe_sibling_labels(siblings, labels, hints_by_id)

    if len(_HEURISTIC_GROUP_CACHE) >= _HEURISTIC_CACHE_MAX:
        _HEURISTIC_GROUP_CACHE.clear()
    _HEURISTIC_GROUP_CACHE[cache_key] = (labels, tasks)
    return labels, tasks


# ---------------------------------------------------------------------------
# Background headless CLI labeler
# ---------------------------------------------------------------------------


def _build_cli_command(cli: str, model: str) -> list[str]:
    if cli == "agy":
        return [
            "agy",
            "--print",
            "--model",
            model,
            "--output-format",
            "text",
            "--disable-slash-commands",
        ]
    if cli == "claude":
        return [
            "claude",
            "-p",
            "--model",
            model,
            "--tools",
            "",
            "--no-session-persistence",
            "--output-format",
            "text",
        ]
    if cli == "codex":
        return [
            "codex",
            "exec",
            "-m",
            model,
            "--sandbox",
            "read-only",
            "-",
        ]
    if cli == "grok":
        return [
            "grok",
            "-p",
            "--model",
            model,
        ]
    raise ValueError(f"Unsupported labeler CLI: {cli!r}")


def _default_cli_runner(cli: str, model: str, prompt: str, timeout_s: float) -> str:
    cmd = _build_cli_command(cli, model)
    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=max(3.0, float(timeout_s)),
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:240]
        raise RuntimeError(f"{cli} exited with code {proc.returncode}: {err}")
    return proc.stdout


def _build_batch_prompt(flow_id: str, flow_title: str, batch_items: list[dict[str, Any]]) -> str:
    compact_agents = [
        {
            "id": str(item.get("agentId") or ""),
            "parent": item.get("parent"),
            "role": item.get("role") or "worker",
            "name": str(item.get("rawName") or "")[:120],
            "slug": str(item.get("slug") or "")[:60],
            "hint": str(item.get("promptHead") or "")[:320],
        }
        for item in batch_items
    ]
    return (
        "Generate concise, distinct dashboard labels and 1-sentence task descriptions for these multi-agent workers.\n"
        "Rules:\n"
        "- Return ONLY valid JSON object mapping each agent `id` to {\"label\": \"2-3 words\", \"task\": \"One clean sentence.\"}.\n"
        "- `label`: 1-3 words (target 2-3 words), <= 30 chars, NO markdown, NO quotes, NO ellipses, NO model names unless seat/model-only.\n"
        "- Siblings under the same `parent` MUST have distinct, contrasting labels.\n"
        "- `task`: ONE clean sentence <= 140 chars summarizing what the agent does, NO markdown, NO ellipses.\n\n"
        f"Flow: {flow_title} ({flow_id})\n"
        f"Agents JSON:\n{json.dumps(compact_agents, separators=(',', ':'), ensure_ascii=False)}\n"
    )


def _parse_batch_json(raw_output: str) -> dict[str, dict[str, Any]]:
    text = (raw_output or "").strip()
    if not text:
        return {}
    m_fence = _MD_CODE_FENCE_RE.search(text)
    if m_fence:
        text = m_fence.group(1).strip()
    m_obj = _JSON_OBJ_RE.search(text)
    if m_obj:
        text = m_obj.group(0)
    data = json.loads(text)
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


class BackgroundLabeler:
    """Single-worker daemon thread that enriches cache-miss agents via headless local CLIs."""

    def __init__(
        self,
        cache: LabelCache | None = None,
        config: dict[str, Any] | None = None,
        *,
        max_queue: int = 32,
        cli_runner: Callable[[str, str, str, float], str] | None = None,
    ) -> None:
        self.config = load_labeler_config(config)
        self.enabled: bool = bool(self.config["enabled"])
        self.cache = cache if cache is not None else LabelCache()
        self._max_queue = max(1, int(max_queue))
        self._cli_runner = cli_runner or _default_cli_runner

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._queue: deque[tuple[str, str, str, list[dict[str, Any]]]] = deque()
        self._queued_batches: set[str] = set()
        self._completed_batches: dict[str, float] = {}
        self._thread: threading.Thread | None = None
        self._closed = False
        self._version = 0
        self._consecutive_failures = 0
        self._backoff_until = 0.0

    def version(self) -> int:
        """Monotonic counter incremented whenever new LLM labels land in cache."""
        with self._lock:
            return self._version

    def _batch_hash(self, flow_id: str, batch_items: list[dict[str, Any]]) -> str:
        model_spec = f"{self.config['cli']}:{self.config['model']}"
        keys = sorted(str(it.get("cacheKey") or it.get("agentId") or "") for it in batch_items)
        raw = json.dumps([SCHEMA_VERSION, model_spec, flow_id, keys], separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:20]

    def enqueue(
        self,
        flow_id: str,
        flow_title: str,
        batch_items: list[dict[str, Any]],
    ) -> None:
        """Deduplicated, non-blocking enqueue for async headless labeling."""
        if not self.enabled or not batch_items:
            return
        bhash = self._batch_hash(flow_id, batch_items)
        now = time.monotonic()
        with self._lock:
            if self._closed:
                return
            if self._completed_batches:
                expired = [k for k, ts in self._completed_batches.items() if (now - ts) >= _COMPLETED_BATCH_TTL_S]
                for k in expired:
                    self._completed_batches.pop(k, None)
            if bhash in self._queued_batches or bhash in self._completed_batches:
                return
            if len(self._queue) >= self._max_queue:
                dropped_hash, _, _, _ = self._queue.popleft()
                self._queued_batches.discard(dropped_hash)
            self._queue.append((bhash, flow_id, flow_title, list(batch_items)))
            self._queued_batches.add(bhash)

            if self._thread is None or not self._thread.is_alive():
                t = threading.Thread(
                    target=self._worker_loop,
                    name="swarm-board-labeler",
                    daemon=True,
                )
                self._thread = t
                t.start()
            self._cond.notify_all()

    def run_batch_sync(
        self,
        flow_id: str,
        flow_title: str,
        batch_items: list[dict[str, Any]],
    ) -> dict[str, dict[str, str]]:
        """Invoke headless CLI synchronously for one flow batch, validate, and persist to cache."""
        if not batch_items:
            return {}
        cli = str(self.config["cli"])
        model = str(self.config["model"])
        timeout_s = float(self.config["timeout_s"])
        model_spec = f"{cli}:{model}"

        prompt = _build_batch_prompt(flow_id, flow_title, batch_items)
        raw_out = self._cli_runner(cli, model, prompt, timeout_s)
        parsed = _parse_batch_json(raw_out)
        if not parsed:
            return {}

        by_parent: dict[str, list[dict[str, Any]]] = {}
        item_by_id: dict[str, dict[str, Any]] = {}
        for it in batch_items:
            aid = str(it.get("agentId") or "")
            if not aid:
                continue
            item_by_id[aid] = it
            pkey = str(it.get("parent") or "__root__")
            by_parent.setdefault(pkey, []).append(it)

        sanitized_by_id: dict[str, dict[str, str]] = {}
        for aid, it in item_by_id.items():
            raw_entry = parsed.get(aid)
            if not raw_entry:
                continue
            raw_lbl = str(raw_entry.get("label") or "").strip()
            raw_tsk = str(raw_entry.get("task") or "").strip()
            if not raw_lbl or not raw_tsk:
                continue
            lbl = sanitize_label(raw_lbl)
            tsk = sanitize_task(raw_tsk)
            if lbl and tsk:
                sanitized_by_id[aid] = {"label": lbl, "task": tsk}

        for siblings in by_parent.values():
            group_ids = [
                str(it.get("agentId") or "")
                for it in siblings
                if str(it.get("agentId") or "") in sanitized_by_id
            ]
            if len(group_ids) <= 1:
                continue
            fake_agents = [
                {
                    "id": aid,
                    "name": item_by_id[aid].get("rawName") or "",
                    "role": item_by_id[aid].get("role") or "worker",
                }
                for aid in group_ids
            ]
            fake_hints = {
                aid: {
                    "rawTitle": item_by_id[aid].get("rawName") or "",
                    "prompt": item_by_id[aid].get("promptHead") or "",
                    "slug": item_by_id[aid].get("slug") or "",
                }
                for aid in group_ids
            }
            lbl_map = {aid: sanitized_by_id[aid]["label"] for aid in group_ids}
            _dedupe_sibling_labels(fake_agents, lbl_map, fake_hints)
            for aid in group_ids:
                sanitized_by_id[aid]["label"] = lbl_map[aid]

        to_store: dict[str, dict[str, Any]] = {}
        now_ts = time.time()
        for aid, pair in sanitized_by_id.items():
            ckey = str(item_by_id[aid].get("cacheKey") or "")
            if not ckey:
                continue
            to_store[ckey] = {
                "label": pair["label"],
                "task": pair["task"],
                "model": model_spec,
                "agentId": aid,
                "flowId": flow_id,
                "updatedAt": now_ts,
            }

        if to_store:
            stored = self.cache.put_many(to_store)
            if stored > 0:
                with self._lock:
                    self._version += 1
        return sanitized_by_id

    def _worker_loop(self) -> None:
        while True:
            with self._lock:
                while not self._closed and not self._queue:
                    self._cond.wait()
                if self._closed:
                    return
                now = time.monotonic()
                wait_s = self._backoff_until - now
                if wait_s > 0.0:
                    self._cond.wait(timeout=wait_s)
                    continue
                if not self._queue:
                    continue
                bhash, flow_id, flow_title, items = self._queue.popleft()
                self._queued_batches.discard(bhash)

            try:
                self.run_batch_sync(flow_id, flow_title, items)
                with self._lock:
                    self._consecutive_failures = 0
                    self._backoff_until = 0.0
                    self._completed_batches[bhash] = time.monotonic()
            except Exception as exc:  # noqa: BLE001 - worker must never crash or spin
                _LOGGER.debug("BackgroundLabeler batch failed for %s: %s", flow_id, exc)
                with self._lock:
                    self._consecutive_failures += 1
                    delay = min(300.0, 5.0 * (3 ** max(0, self._consecutive_failures - 1)))
                    self._backoff_until = time.monotonic() + delay
                    self._completed_batches[bhash] = time.monotonic()

    def close(self, timeout: float = 2.0) -> None:
        """Stop the worker thread cleanly."""
        with self._lock:
            self._closed = True
            self._queue.clear()
            self._queued_batches.clear()
            t = self._thread
            self._thread = None
            self._cond.notify_all()
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=max(0.1, float(timeout)))


# ---------------------------------------------------------------------------
# Main flow enrichment entry point
# ---------------------------------------------------------------------------


def enrich_flow_labels(
    flow: dict[str, Any],
    *,
    cache: LabelCache | None = None,
    background: BackgroundLabeler | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Enrich every agent in `flow` with sibling-distinct `label`, clean 1-sentence `task`, and `labelSource`.

    Consumes and pops optional `_labelHint` from every agent dict so it never leaks into JSON.
    Callers passing shared cached flows should pass fresh shallow copies of `flow`, `flow['agents']`,
    and each agent dict so `_labelHint` popping remains safe across requests.
    """
    if not isinstance(flow, dict):
        return flow

    agents = flow.get("agents")
    if not isinstance(agents, list) or not agents:
        return flow

    if background is not None and config is None:
        cfg = background.config
    elif config is not None:
        cfg = load_labeler_config(config)
    else:
        cfg = load_labeler_config()
    model_spec = f"{cfg['cli']}:{cfg['model']}"
    flow_id = str(flow.get("id") or "")
    flow_title = str(flow.get("title") or "")
    need_cache_keys = cache is not None or (background is not None and background.enabled)

    # 1. Pop _labelHint from every agent immediately so it can never leak into serialized JSON.
    hints_by_id: dict[str, dict[str, Any]] = {}
    old_names: dict[str, str] = {}
    explicit_ids: set[str] = set()
    by_parent: dict[str, list[dict[str, Any]]] = {}

    for ag in agents:
        if not isinstance(ag, dict):
            continue
        raw_hint = ag.pop("_labelHint", None)
        aid = str(ag.get("id") or "")
        hints_by_id[aid] = raw_hint if isinstance(raw_hint, dict) else {}
        old_names[aid] = str(ag.get("name") or "")
        if isinstance(ag.get("label"), str) and ag["label"].strip():
            explicit_ids.add(aid)
        pkey = str(ag.get("parent") or "__root__")
        by_parent.setdefault(pkey, []).append(ag)

    # 2. Compute sibling-aware heuristics and tasks per parent group (memoized across warm polls).
    heuristic_labels: dict[str, str] = {}
    heuristic_tasks: dict[str, str] = {}
    sibling_sigs: dict[str, str] = {}
    for siblings in by_parent.values():
        lbls, tsks = _derive_sibling_group(siblings, hints_by_id, flow)
        ssig = _compute_sibling_sig(siblings, hints_by_id) if need_cache_keys else ""
        for ag in siblings:
            aid = str(ag.get("id") or "")
            heuristic_labels[aid] = lbls[aid]
            heuristic_tasks[aid] = tsks[aid]
            if need_cache_keys:
                sibling_sigs[aid] = ssig

    # 3. Resolve cache hits & apply labels/tasks without mutating nested dicts.
    batch_items: list[dict[str, Any]] = []
    llm_applied_ids: set[str] = set()

    for ag in agents:
        if not isinstance(ag, dict):
            continue
        aid = str(ag.get("id") or "")
        role = str(ag.get("role") or "worker").lower()
        hint = hints_by_id.get(aid) or {}
        raw_name = str(hint.get("rawTitle") or ag.get("name") or "")
        prompt_head = str(hint.get("prompt") or ag.get("brief") or hint.get("planTask") or "")[:400]

        if aid in explicit_ids:
            ag["label"] = sanitize_label(str(ag["label"]))
            if not (isinstance(ag.get("task"), str) and ag["task"].strip()):
                ag["task"] = heuristic_tasks[aid]
            ag["labelSource"] = "explicit"
            continue

        ckey = (
            make_cache_key(model_spec, aid, raw_name, prompt_head, sibling_sigs.get(aid, ""))
            if need_cache_keys and role not in ("root", "coordinator")
            else ""
        )
        cached = cache.get(ckey) if (cache is not None and ckey) else None
        if cached is not None:
            ag["label"] = sanitize_label(str(cached["label"]))
            ag["task"] = sanitize_task(str(cached["task"]))
            ag["labelSource"] = "llm"
            llm_applied_ids.add(aid)
        else:
            ag["label"] = heuristic_labels[aid]
            ag["task"] = heuristic_tasks[aid]
            ag["labelSource"] = "heuristic"
            if background is not None and background.enabled and ckey:
                batch_items.append(
                    {
                        "agentId": aid,
                        "parent": ag.get("parent"),
                        "role": role,
                        "rawName": raw_name,
                        "slug": str(hint.get("slug") or ""),
                        "promptHead": prompt_head,
                        "cacheKey": ckey,
                    }
                )

    if explicit_ids or llm_applied_ids:
        for siblings in by_parent.values():
            if len(siblings) <= 1:
                continue
            lbl_map = {str(ag.get("id") or ""): str(ag.get("label") or "Worker") for ag in siblings}
            locked = {
                str(ag.get("id") or "")
                for ag in siblings
                if str(ag.get("id") or "") in explicit_ids or str(ag.get("id") or "") in llm_applied_ids
            }
            _dedupe_sibling_labels(siblings, lbl_map, hints_by_id, locked_ids=locked)
            for ag in siblings:
                aid = str(ag.get("id") or "")
                ag["label"] = lbl_map[aid]

    # 4. Rewrite synthesized "<old_name> result" / "<old_name> transcript" output titles safely.
    outputs = flow.get("outputs")
    if isinstance(outputs, list) and outputs:
        label_by_id = {
            str(ag.get("id") or ""): str(ag.get("label") or "")
            for ag in agents
            if isinstance(ag, dict)
        }
        new_outputs: list[Any] = []
        changed_outputs = False
        for out in outputs:
            if not isinstance(out, dict):
                new_outputs.append(out)
                continue
            kind = str(out.get("kind") or "")
            title = out.get("title")
            by_id = str(out.get("by") or "")
            old_name = old_names.get(by_id, "")
            new_lbl = label_by_id.get(by_id, "")
            if (
                kind in ("result", "transcript")
                and isinstance(title, str)
                and old_name
                and new_lbl
                and old_name != new_lbl
                and (title == f"{old_name} result" or title == f"{old_name} transcript")
            ):
                suffix = " result" if title.endswith(" result") else " transcript"
                out_copy = dict(out)
                out_copy["title"] = f"{new_lbl}{suffix}"
                new_outputs.append(out_copy)
                changed_outputs = True
            else:
                new_outputs.append(out)
        if changed_outputs:
            flow["outputs"] = new_outputs

    # 5. Queue cache-miss non-root agents asynchronously without blocking.
    if background is not None and background.enabled and batch_items:
        background.enqueue(flow_id, flow_title, batch_items)

    return flow


# ---------------------------------------------------------------------------
# CLI (`python3 -m swarm_board.labeler`)
# ---------------------------------------------------------------------------


def _collect_cli_flows(*, raw: bool = False) -> list[dict[str, Any]]:
    from .adapters import BoardRegistry

    reg = BoardRegistry({"labeler_enabled": False}, cache_ttl_s=0.0)
    try:
        if raw and hasattr(reg, "list_raw_flows"):
            return reg.list_raw_flows()
        return reg.list_flows(force=True)
    finally:
        reg.close()


def _filter_flows(flows: list[dict[str, Any]], query: str | None) -> list[dict[str, Any]]:
    if not query:
        return flows
    q = query.strip()
    exact = [f for f in flows if str(f.get("id") or "") == q]
    if exact:
        return exact
    q_low = q.lower()
    return [
        f
        for f in flows
        if q_low in str(f.get("id") or "").lower() or q_low in str(f.get("title") or "").lower()
    ]


def _cli_status(as_json: bool = False) -> int:
    cfg = load_labeler_config()
    cfg_path = get_config_path()
    cache = LabelCache()
    payload = {
        "config": cfg,
        "configPath": str(cfg_path) if cfg_path is not None else None,
        "cliAvailable": {cli: bool(shutil.which(cli)) for cli in SUPPORTED_CLIS},
        "cache": cache.stats(),
    }
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(f"Labeler enabled : {cfg['enabled']}")
    print(f"CLI / model     : {cfg['cli']}:{cfg['model']} (timeout {cfg['timeout_s']}s)")
    print(f"Config path     : {payload['configPath'] or '(disabled)'}")
    avail = ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in payload["cliAvailable"].items())
    print(f"CLIs on PATH    : {avail}")
    cs = payload["cache"]
    print(f"Cache           : {cs['entries']} entries @ {cs['path']}")
    return 0


def _cli_set(spec: str) -> int:
    if ":" not in spec:
        print("Error: --set requires <cli:model>, e.g. agy:gemini-3.8-flash-low", file=sys.stderr)
        return 2
    cli_raw, model_raw = spec.split(":", 1)
    cli = cli_raw.strip().lower()
    model = model_raw.strip()
    if cli not in SUPPORTED_CLIS:
        print(f"Error: unsupported CLI {cli!r}; expected one of {', '.join(SUPPORTED_CLIS)}", file=sys.stderr)
        return 2
    if not model:
        print("Error: model name cannot be empty", file=sys.stderr)
        return 2
    path = _update_config_file({"enabled": True, "cli": cli, "model": model})
    print(f"Enabled background labeler: {cli}:{model} (saved to {path})")
    return 0


def _cli_disable() -> int:
    path = _update_config_file({"enabled": False})
    print(f"Disabled background labeler (saved to {path})")
    return 0


def _cli_preview(query: str | None, limit: int) -> int:
    flows = _filter_flows(_collect_cli_flows(), query)
    if limit > 0:
        flows = flows[:limit]
    if not flows:
        print("No matching flows found.")
        return 0
    cache = LabelCache()
    cfg = load_labeler_config()
    for flow in flows:
        t0 = time.perf_counter()
        needs_enrich = any("label" not in a or "task" not in a for a in (flow.get("agents") or ()))
        if needs_enrich:
            enrich_flow_labels(flow, cache=cache, config=cfg)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        print(f"\n[{flow.get('harness')}] {flow.get('title')} ({flow.get('id')}) [{dt_ms:.2f} ms]")
        for ag in flow.get("agents") or ():
            src = ag.get("labelSource") or "heuristic"
            print(f"  {ag.get('name')!r} -> {ag.get('label')!r} [{src}] | {ag.get('task')}")
    return 0


def _cli_run(query: str | None, limit: int) -> int:
    cfg = load_labeler_config()
    cfg["enabled"] = True
    cache = LabelCache()
    labeler = BackgroundLabeler(cache=cache, config=cfg)
    flows = _filter_flows(_collect_cli_flows(raw=True), query)
    if limit > 0:
        flows = flows[:limit]

    total_updated = 0
    try:
        for flow in flows:
            agents = flow.get("agents") or []
            if not agents:
                continue
            by_parent: dict[str, list[dict[str, Any]]] = {}
            hints_by_id: dict[str, dict[str, Any]] = {}
            for ag in agents:
                aid = str(ag.get("id") or "")
                hint = ag.get("_labelHint") if isinstance(ag.get("_labelHint"), dict) else {}
                hints_by_id[aid] = hint
                by_parent.setdefault(str(ag.get("parent") or "__root__"), []).append(ag)

            model_spec = f"{cfg['cli']}:{cfg['model']}"
            batch_items: list[dict[str, Any]] = []
            for siblings in by_parent.values():
                ssig = _compute_sibling_sig(siblings, hints_by_id)
                for ag in siblings:
                    role = str(ag.get("role") or "worker").lower()
                    if role in ("root", "coordinator"):
                        continue
                    aid = str(ag.get("id") or "")
                    hint = hints_by_id.get(aid) or {}
                    raw_name = str(hint.get("rawTitle") or ag.get("name") or "")
                    prompt_head = str(hint.get("prompt") or ag.get("brief") or hint.get("planTask") or "")[:400]
                    ckey = make_cache_key(model_spec, aid, raw_name, prompt_head, ssig)
                    if cache.get(ckey) is None:
                        batch_items.append(
                            {
                                "agentId": aid,
                                "parent": ag.get("parent"),
                                "role": role,
                                "rawName": raw_name,
                                "slug": str(hint.get("slug") or ""),
                                "promptHead": prompt_head,
                                "cacheKey": ckey,
                            }
                        )
            if not batch_items:
                continue
            res = labeler.run_batch_sync(str(flow.get("id") or ""), str(flow.get("title") or ""), batch_items)
            total_updated += len(res)
            print(f"[{flow.get('id')}] labeled {len(res)}/{len(batch_items)} agents via {model_spec}")
    finally:
        labeler.close()
    print(f"Done. Cached {total_updated} new labels.")
    return 0


def _cli_clear() -> int:
    cache = LabelCache()
    removed = cache.clear()
    print(f"Cleared {removed} cached label entries ({cache.path})")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for `python3 -m swarm_board.labeler`."""
    parser = argparse.ArgumentParser(
        prog="python3 -m swarm_board.labeler",
        description="Configure, preview, or run Swarm Board agent labels & task summaries.",
    )
    parser.add_argument("--status", action="store_true", help="Show labeler configuration and cache stats")
    parser.add_argument("--json", action="store_true", help="Output JSON (with --status)")
    parser.add_argument("--set", metavar="CLI:MODEL", help="Enable labeler with <cli>:<model>")
    parser.add_argument("--disable", action="store_true", help="Disable background LLM labeling")
    parser.add_argument(
        "--preview",
        nargs="?",
        const="",
        default=None,
        metavar="FLOW_ID",
        help="Preview deterministic/cached labels for matching flow(s)",
    )
    parser.add_argument("--run", action="store_true", help="Run LLM labeling synchronously on cache-miss flows")
    parser.add_argument("--flow", default=None, help="Optional flow ID/title filter for --run")
    parser.add_argument("--limit", type=int, default=5, help="Max flows to process for --preview / --run (default: 5)")
    parser.add_argument("--clear", action="store_true", help="Clear cached LLM labels")

    args = parser.parse_args(argv)

    if args.set is not None:
        return _cli_set(args.set)
    if args.disable:
        return _cli_disable()
    if args.clear:
        return _cli_clear()
    if args.run:
        return _cli_run(args.flow, args.limit)
    if args.preview is not None:
        return _cli_preview(args.preview or None, args.limit)
    return _cli_status(as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
