"""Strict read-only path containment and secret-file exclusion helpers."""

from __future__ import annotations

import fnmatch
import mimetypes
import os
from pathlib import Path
import re
from typing import Any

DENIED_BASENAMES = frozenset(
    {
        "auth.json",
        ".credentials.json",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
        "credentials.json",
        "secrets.json",
    }
)

DENIED_GLOBS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.sqlite",
    "*.sqlite3",
    "*.sqlite-wal",
    "*.sqlite-shm",
    "*.sqlite-journal",
    "*.db",
    "id_*",
)

DENIED_SEGMENTS = frozenset({".git", "secrets"})

_DENIED_GLOB_RE = re.compile(
    "(?:" + "|".join(fnmatch.translate(pat) for pat in DENIED_GLOBS) + ")"
)


def redact_if_secret_name(path_str: str) -> str | None:
    """Return a denial reason if any segment or filename matches a secret/credential rule."""
    norm = path_str.replace("\\", "/") if "\\" in path_str else path_str
    parts = [seg for seg in norm.split("/") if seg]
    name = parts[-1] if parts else ""
    name_lower = name.lower()
    if name_lower in DENIED_BASENAMES:
        return f"Denied secret file: {name}"
    if _DENIED_GLOB_RE.match(name_lower):
        for pattern in DENIED_GLOBS:
            if fnmatch.fnmatchcase(name_lower, pattern):
                return f"Denied sensitive file pattern ({pattern}): {name}"
        return f"Denied sensitive file pattern: {name}"
    for part in parts:
        if part.lower() in DENIED_SEGMENTS:
            return f"Denied directory segment: {part}"
    return None


def is_path_allowed(target_path: str, allowed_roots: list[str]) -> tuple[bool, str | None]:
    """Verify target_path resolves strictly inside allowed_roots and passes secret checks."""
    if not target_path or not allowed_roots:
        return False, "Missing path or allowed roots"
    raw_reason = redact_if_secret_name(target_path)
    if raw_reason:
        return False, raw_reason
    try:
        resolved_target = os.path.realpath(target_path)
    except OSError as exc:
        return False, f"Cannot resolve path: {exc}"

    resolved_reason = redact_if_secret_name(resolved_target)
    if resolved_reason:
        return False, resolved_reason

    for root in allowed_roots:
        if not root:
            continue
        try:
            resolved_root = os.path.realpath(root)
        except OSError:
            continue
        try:
            common = os.path.commonpath([resolved_root, resolved_target])
        except ValueError:
            continue
        if common == resolved_root:
            return True, resolved_target
    return False, "Path is outside allowed project roots"


def _resolve_candidate(target_path: str, allowed_roots: list[str]) -> tuple[bool, str | None]:
    """Resolve relative or absolute target_path against allowed_roots safely."""
    p = Path(target_path)
    if p.is_absolute():
        return is_path_allowed(target_path, allowed_roots)
    for root in allowed_roots:
        if not root:
            continue
        candidate = str(Path(root) / p)
        ok, res = is_path_allowed(candidate, [root])
        if ok and res and os.path.exists(res):
            return True, res
    return False, "Not found under this project"


def safe_read_text(
    path: str,
    allowed_roots: list[str],
    max_bytes: int = 1_048_576,
) -> dict[str, Any]:
    """Read up to max_bytes of UTF-8 text from an allowed path cleanly."""
    ok, resolved_or_err = _resolve_candidate(path, allowed_roots)
    if not ok or not resolved_or_err:
        return {"ok": False, "error": resolved_or_err or "Access denied"}
    resolved = resolved_or_err
    if not os.path.isfile(resolved):
        return {"ok": False, "error": "Not found under this project"}
    try:
        total_size = os.path.getsize(resolved)
        with open(resolved, "rb") as fh:
            raw = fh.read(max_bytes)
    except OSError as exc:
        return {"ok": False, "error": f"Read failed: {exc}"}

    mime, _ = mimetypes.guess_type(resolved)
    body = raw.decode("utf-8", errors="replace")
    return {
        "ok": True,
        "path": resolved,
        "size": total_size,
        "truncated": total_size > max_bytes,
        "mime": mime or "text/plain",
        "body": body,
    }


def safe_read_citation_lines(
    path: str,
    line: int,
    allowed_roots: list[str],
    ctx: int = 4,
) -> dict[str, Any]:
    """Read 1-indexed citation line +- ctx lines from an allowed text file."""
    ok, resolved_or_err = _resolve_candidate(path, allowed_roots)
    if not ok or not resolved_or_err:
        return {"ok": False, "error": resolved_or_err or "Not found under this project"}
    resolved = resolved_or_err
    if not os.path.isfile(resolved):
        return {"ok": False, "error": "Not found under this project"}

    target_line = max(1, int(line))
    context = max(0, int(ctx))
    start = max(1, target_line - context)
    end = target_line + context

    lines_out: list[dict[str, Any]] = []
    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as fh:
            for idx, text in enumerate(fh, start=1):
                if idx < start:
                    continue
                if idx > end:
                    break
                lines_out.append(
                    {
                        "num": idx,
                        "text": text.rstrip("\r\n"),
                        "cited": idx == target_line,
                    }
                )
    except OSError as exc:
        return {"ok": False, "error": f"Read failed: {exc}"}

    if not lines_out:
        return {
            "ok": False,
            "error": f"Line {target_line} beyond end of file",
            "path": resolved,
        }
    return {
        "ok": True,
        "path": resolved,
        "start": start,
        "target": target_line,
        "lines": lines_out,
    }
