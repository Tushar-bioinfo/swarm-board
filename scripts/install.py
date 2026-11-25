#!/usr/bin/env python3
"""Cross-platform (macOS + Linux) installer and verifier for Swarm Board skills and hooks."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_SOURCE = REPO_ROOT / "skills" / "swarm-board"
ENSURE_SCRIPT = REPO_ROOT / "scripts" / "ensure-server.sh"
BUILD_SCRIPT = REPO_ROOT / "swarm_board" / "ui" / "build.py"
UI_INDEX = REPO_ROOT / "swarm_board" / "ui" / "index.html"
UI_SIZE_LIMIT = 153_600

VALID_TARGETS = ("antigravity", "claude", "codex")
AGY_HOOK_MATCHER = "mcp_t3-code_delegate_task|delegate_task|swarm|invoke_subagent|run_workflow"
CLAUDE_HOOK_MATCHER = "Agent|Task|TeamCreate|SendMessage|mcp__t3-code__delegate_task"

EXECUTABLE_PATHS = (
    REPO_ROOT / "scripts" / "ensure-server.sh",
    REPO_ROOT / "scripts" / "update_rates.py",
    REPO_ROOT / "scripts" / "install.py",
    REPO_ROOT / "skills" / "swarm-board" / "scripts" / "board.sh",
)


def _hook_command() -> str:
    return f"{shlex.quote(str(ENSURE_SCRIPT))} --hook"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
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


def _load_json_dict(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"Could not parse {path}: {exc}"
    if not isinstance(data, dict):
        return None, f"Expected JSON object in {path}"
    return data, None


def _t3_antigravity_provider_roots(home: Path, platform_name: str) -> list[Path]:
    roots = [
        home / ".t3" / "userdata" / "providers" / "antigravity",
        home / "Library" / "Application Support" / "T3 Code" / "userdata" / "providers" / "antigravity",
    ]
    if platform_name == "darwin":
        roots.reverse()
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root not in seen:
            seen.add(root)
            out.append(root)
    return out


def _find_extra_agy_skill_targets(home: Path, platform_name: str) -> list[Path]:
    global_skills = home / ".gemini" / "config" / "skills"
    try:
        global_resolved = global_skills.resolve()
    except OSError:
        global_resolved = global_skills

    targets: list[Path] = []
    for root in _t3_antigravity_provider_roots(home, platform_name):
        if not root.is_dir():
            continue
        try:
            providers = sorted(root.iterdir())
        except OSError:
            continue
        for prov_dir in providers:
            cfg = prov_dir / "config"
            if not cfg.is_dir():
                continue
            skills_dir = cfg / "skills"
            if skills_dir.is_symlink():
                try:
                    if skills_dir.resolve() == global_resolved:
                        continue
                except OSError:
                    pass
            cand = skills_dir / "swarm-board"
            if cand not in targets:
                targets.append(cand)
    return targets


def detect_targets(home: Path, platform_name: str) -> list[str]:
    use_which = False
    try:
        use_which = home.resolve() == Path.home().resolve()
    except OSError:
        use_which = False

    detected: list[str] = []
    has_t3_agy = any(r.exists() for r in _t3_antigravity_provider_roots(home, platform_name))
    if (home / ".gemini").exists() or has_t3_agy or (use_which and shutil.which("agy")):
        detected.append("antigravity")
    if (home / ".claude").exists() or (use_which and shutil.which("claude")):
        detected.append("claude")
    if (home / ".codex").exists() or (use_which and shutil.which("codex")):
        detected.append("codex")
    return detected


def parse_targets(raw: str, home: Path, platform_name: str) -> list[str]:
    cleaned = raw.strip().lower()
    if cleaned == "all":
        return list(VALID_TARGETS)
    if cleaned == "auto":
        return detect_targets(home, platform_name)
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    invalid = [p for p in parts if p not in VALID_TARGETS]
    if invalid:
        raise ValueError(
            f"Unsupported target(s): {', '.join(invalid)}. Choose from: {', '.join(VALID_TARGETS)}, auto, all."
        )
    deduped: list[str] = []
    for p in parts:
        if p not in deduped:
            deduped.append(p)
    return deduped


def _is_swarm_board_skill_link(path: Path, source: Path = SKILL_SOURCE) -> bool:
    if not path.is_symlink():
        return False
    try:
        raw_target = Path(os.readlink(path))
        resolved = raw_target if raw_target.is_absolute() else (path.parent / raw_target).resolve()
        if resolved == source.resolve():
            return True
        return (
            resolved.name == "swarm-board"
            and resolved.parent.name == "skills"
            and (resolved / "SKILL.md").is_file()
        )
    except OSError:
        return False


def _skill_targets_for(target: str, home: Path, platform_name: str) -> list[Path]:
    if target == "antigravity":
        paths = [home / ".gemini" / "config" / "skills" / "swarm-board"]
        for extra in _find_extra_agy_skill_targets(home, platform_name):
            if extra not in paths:
                paths.append(extra)
        return paths
    if target == "claude":
        return [home / ".claude" / "skills" / "swarm-board"]
    if target == "codex":
        return [home / ".codex" / "skills" / "swarm-board"]
    return []


def _sync_symlink(dst: Path, source: Path, *, dry_run: bool) -> dict[str, str]:
    if _is_swarm_board_skill_link(dst, source):
        try:
            current_target = Path(os.readlink(dst))
            resolved = current_target if current_target.is_absolute() else (dst.parent / current_target).resolve()
            if resolved == source.resolve():
                return {"path": str(dst), "action": "unchanged"}
        except OSError:
            pass
        if not dry_run:
            dst.unlink()
            dst.symlink_to(source)
        return {"path": str(dst), "action": "updated" if not dry_run else "would_update"}

    if dst.exists() or dst.is_symlink():
        return {
            "path": str(dst),
            "action": "skipped_existing",
            "reason": "destination exists and is not a swarm-board symlink",
        }

    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.symlink_to(source)
    return {"path": str(dst), "action": "linked" if not dry_run else "would_link"}


def _remove_symlink(dst: Path, source: Path, *, dry_run: bool) -> dict[str, str]:
    if _is_swarm_board_skill_link(dst, source):
        if not dry_run:
            dst.unlink()
        return {"path": str(dst), "action": "removed" if not dry_run else "would_remove"}
    if dst.exists() or dst.is_symlink():
        return {"path": str(dst), "action": "kept_non_owned"}
    return {"path": str(dst), "action": "absent"}


def _is_swarm_board_claude_hook(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return False
    for h in hooks:
        if not isinstance(h, dict):
            continue
        cmd = str(h.get("command") or "")
        if "ensure-server.sh" in cmd and "--hook" in cmd:
            return True
    return False


def _desired_agy_hook_entry(cmd: str) -> dict[str, Any]:
    return {
        "PostToolUse": [
            {
                "matcher": AGY_HOOK_MATCHER,
                "hooks": [{"type": "command", "command": cmd, "timeout": 10}],
            }
        ]
    }


def _desired_claude_hook_entry(cmd: str) -> dict[str, Any]:
    return {
        "matcher": CLAUDE_HOOK_MATCHER,
        "hooks": [{"type": "command", "command": cmd, "timeout": 10}],
    }


def _install_agy_hook(home: Path, cmd: str, *, dry_run: bool) -> dict[str, Any]:
    hooks_path = home / ".gemini" / "config" / "hooks.json"
    data, err = _load_json_dict(hooks_path)
    if err is not None or data is None:
        return {"path": str(hooks_path), "action": "error", "error": err}
    desired = _desired_agy_hook_entry(cmd)
    if data.get("swarm-board") == desired:
        return {"path": str(hooks_path), "action": "unchanged"}
    updated = dict(data)
    updated["swarm-board"] = desired
    if not dry_run:
        _atomic_write_json(hooks_path, updated)
    return {"path": str(hooks_path), "action": "updated" if not dry_run else "would_update"}


def _uninstall_agy_hook(home: Path, *, dry_run: bool) -> dict[str, Any]:
    hooks_path = home / ".gemini" / "config" / "hooks.json"
    if not hooks_path.exists():
        return {"path": str(hooks_path), "action": "absent"}
    data, err = _load_json_dict(hooks_path)
    if err is not None or data is None:
        return {"path": str(hooks_path), "action": "error", "error": err}
    if "swarm-board" not in data:
        return {"path": str(hooks_path), "action": "unchanged"}
    updated = {k: v for k, v in data.items() if k != "swarm-board"}
    if not updated:
        if not dry_run:
            hooks_path.unlink()
        return {"path": str(hooks_path), "action": "removed_file" if not dry_run else "would_remove_file"}
    if not dry_run:
        _atomic_write_json(hooks_path, updated)
    return {"path": str(hooks_path), "action": "removed_entry" if not dry_run else "would_remove_entry"}


def _install_claude_hook(home: Path, cmd: str, *, dry_run: bool) -> dict[str, Any]:
    settings_path = home / ".claude" / "settings.json"
    data, err = _load_json_dict(settings_path)
    if err is not None or data is None:
        return {"path": str(settings_path), "action": "error", "error": err}

    desired = _desired_claude_hook_entry(cmd)
    hooks_obj = data.get("hooks")
    if hooks_obj is None:
        new_hooks: dict[str, Any] = {}
    elif isinstance(hooks_obj, dict):
        new_hooks = dict(hooks_obj)
    else:
        return {
            "path": str(settings_path),
            "action": "error",
            "error": f"Expected object at 'hooks' in {settings_path}",
        }

    post_raw = new_hooks.get("PostToolUse")
    if post_raw is None:
        post_list: list[Any] = []
    elif isinstance(post_raw, list):
        post_list = list(post_raw)
    else:
        return {
            "path": str(settings_path),
            "action": "error",
            "error": f"Expected list at 'hooks.PostToolUse' in {settings_path}",
        }

    cleaned_list: list[Any] = []
    inserted = False
    for item in post_list:
        if _is_swarm_board_claude_hook(item):
            if not inserted:
                cleaned_list.append(desired)
                inserted = True
        else:
            cleaned_list.append(item)
    if not inserted:
        cleaned_list.append(desired)

    if post_list == cleaned_list and "PostToolUse" in new_hooks and isinstance(data.get("hooks"), dict):
        return {"path": str(settings_path), "action": "unchanged"}

    new_hooks["PostToolUse"] = cleaned_list
    updated = dict(data)
    updated["hooks"] = new_hooks
    if not dry_run:
        _atomic_write_json(settings_path, updated)
    return {"path": str(settings_path), "action": "updated" if not dry_run else "would_update"}


def _uninstall_claude_hook(home: Path, *, dry_run: bool) -> dict[str, Any]:
    settings_path = home / ".claude" / "settings.json"
    if not settings_path.exists():
        return {"path": str(settings_path), "action": "absent"}
    data, err = _load_json_dict(settings_path)
    if err is not None or data is None:
        return {"path": str(settings_path), "action": "error", "error": err}

    hooks_obj = data.get("hooks")
    if not isinstance(hooks_obj, dict):
        return {"path": str(settings_path), "action": "unchanged"}
    post_raw = hooks_obj.get("PostToolUse")
    if not isinstance(post_raw, list):
        return {"path": str(settings_path), "action": "unchanged"}

    filtered = [item for item in post_raw if not _is_swarm_board_claude_hook(item)]
    if len(filtered) == len(post_raw):
        return {"path": str(settings_path), "action": "unchanged"}

    new_hooks = dict(hooks_obj)
    if filtered:
        new_hooks["PostToolUse"] = filtered
    else:
        new_hooks.pop("PostToolUse", None)

    updated = dict(data)
    if new_hooks:
        updated["hooks"] = new_hooks
    else:
        updated.pop("hooks", None)

    if not dry_run:
        _atomic_write_json(settings_path, updated)
    return {"path": str(settings_path), "action": "removed_entry" if not dry_run else "would_remove_entry"}


def _ensure_ui_and_permissions(*, rebuild: bool, dry_run: bool) -> dict[str, Any]:
    actions: dict[str, Any] = {}
    need_build = rebuild or not UI_INDEX.is_file()
    if need_build:
        if dry_run:
            actions["ui_build"] = "would_build"
        else:
            proc = subprocess.run(
                [sys.executable, str(BUILD_SCRIPT)],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"UI build failed:\n{proc.stdout}\n{proc.stderr}")
            actions["ui_build"] = f"built ({UI_INDEX.stat().st_size} B)"
    else:
        actions["ui_build"] = f"present ({UI_INDEX.stat().st_size} B)"

    chmod_updated: list[str] = []
    for p in EXECUTABLE_PATHS:
        if not p.is_file():
            continue
        mode = p.stat().st_mode
        if not (mode & 0o111):
            if not dry_run:
                os.chmod(p, mode | 0o755)
            chmod_updated.append(str(p.relative_to(REPO_ROOT)))
    actions["chmod"] = chmod_updated
    return actions


def verify_installation(
    *,
    home: Path,
    platform_name: str,
    targets: list[str],
    require_hooks: bool,
) -> dict[str, Any]:
    issues: list[str] = []
    checks: dict[str, Any] = {}

    py_ok = sys.version_info >= (3, 10)
    checks["python"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if not py_ok:
        issues.append(f"Python >= 3.10 required (found {checks['python']})")

    if not UI_INDEX.is_file():
        issues.append(f"Missing built UI file: {UI_INDEX}")
        checks["ui_index"] = "missing"
    else:
        sz = UI_INDEX.stat().st_size
        checks["ui_index"] = f"{sz} B"
        if sz >= UI_SIZE_LIMIT:
            issues.append(f"{UI_INDEX} is {sz} B >= limit {UI_SIZE_LIMIT} B")

    missing_exec = [str(p.relative_to(REPO_ROOT)) for p in EXECUTABLE_PATHS if not (p.is_file() and os.access(p, os.X_OK))]
    checks["executables_ok"] = not missing_exec
    if missing_exec:
        issues.append(f"Missing or non-executable scripts: {', '.join(missing_exec)}")

    target_status: dict[str, Any] = {}
    for target in targets:
        t_info: dict[str, Any] = {"skills": []}
        for skill_dst in _skill_targets_for(target, home, platform_name):
            ok_link = _is_swarm_board_skill_link(skill_dst, SKILL_SOURCE)
            t_info["skills"].append({"path": str(skill_dst), "ok": ok_link})
            if not ok_link:
                issues.append(f"[{target}] missing/invalid skill symlink: {skill_dst}")

        if target == "antigravity":
            hooks_path = home / ".gemini" / "config" / "hooks.json"
            if require_hooks:
                data, err = _load_json_dict(hooks_path)
                hook_ok = (
                    err is None
                    and isinstance(data, dict)
                    and isinstance(data.get("swarm-board"), dict)
                    and "ensure-server.sh" in json.dumps(data["swarm-board"])
                )
                t_info["hooks"] = {"path": str(hooks_path), "ok": hook_ok}
                if not hook_ok:
                    issues.append(f"[antigravity] missing swarm-board hook in {hooks_path}")
            else:
                t_info["hooks"] = "skipped (--no-hooks)"
        elif target == "claude":
            settings_path = home / ".claude" / "settings.json"
            if require_hooks:
                data, err = _load_json_dict(settings_path)
                post = (data or {}).get("hooks", {}).get("PostToolUse", []) if isinstance((data or {}).get("hooks"), dict) else []
                hook_ok = err is None and isinstance(post, list) and any(_is_swarm_board_claude_hook(e) for e in post)
                t_info["hooks"] = {"path": str(settings_path), "ok": hook_ok}
                if not hook_ok:
                    issues.append(f"[claude] missing swarm-board PostToolUse hook in {settings_path}")
            else:
                t_info["hooks"] = "skipped (--no-hooks)"
        elif target == "codex":
            t_info["hooks"] = "n/a (Codex invokes /swarm-board on demand)"

        target_status[target] = t_info

    checks["targets"] = target_status
    return {"ok": not issues, "issues": issues, "checks": checks}


def run_install(argv: list[str] | None = None) -> tuple[int, dict[str, Any]]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--targets",
        default="auto",
        help="Comma-separated targets from {antigravity,claude,codex}, or 'auto' (default) / 'all'.",
    )
    parser.add_argument(
        "--no-hooks",
        action="store_true",
        help="Install skill symlinks only without modifying hooks.json / settings.json.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying disk.")
    parser.add_argument("--uninstall", action="store_true", help="Remove swarm-board symlinks and hooks.")
    parser.add_argument("--verify", action="store_true", help="Verify installation and exit 0/1.")
    parser.add_argument("--rebuild", action="store_true", help="Force rebuilding swarm_board/ui/index.html.")
    parser.add_argument("--home", default=None, help="Override target HOME directory (testing/custom).")
    parser.add_argument(
        "--platform",
        choices=("auto", "linux", "darwin"),
        default="auto",
        help="Override OS platform detection (default: sys.platform).",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    args = parser.parse_args(argv)

    home = Path(args.home).expanduser() if args.home else Path.home()
    platform_name = sys.platform if args.platform == "auto" else args.platform
    if args.verify and args.uninstall:
        return 2, {"ok": False, "error": "--verify and --uninstall are mutually exclusive"}

    try:
        targets = parse_targets(args.targets, home, platform_name)
    except ValueError as exc:
        return 2, {"ok": False, "error": str(exc)}

    if args.verify:
        report = verify_installation(
            home=home,
            platform_name=platform_name,
            targets=targets,
            require_hooks=not args.no_hooks,
        )
        return (0 if report["ok"] else 1), report

    if args.uninstall:
        res: dict[str, Any] = {
            "ok": True,
            "mode": "uninstall",
            "dry_run": args.dry_run,
            "home": str(home),
            "platform": platform_name,
            "targets": {},
        }
        has_err = False
        for target in targets:
            skills_res = [_remove_symlink(dst, SKILL_SOURCE, dry_run=args.dry_run) for dst in _skill_targets_for(target, home, platform_name)]
            if target == "antigravity":
                hook_res: Any = _uninstall_agy_hook(home, dry_run=args.dry_run)
            elif target == "claude":
                hook_res = _uninstall_claude_hook(home, dry_run=args.dry_run)
            else:
                hook_res = "n/a (Codex invokes /swarm-board on demand)"
            if isinstance(hook_res, dict) and hook_res.get("action") == "error":
                has_err = True
            res["targets"][target] = {"skills": skills_res, "hooks": hook_res}
        res["ok"] = not has_err
        return (0 if res["ok"] else 1), res

    prep = _ensure_ui_and_permissions(rebuild=args.rebuild, dry_run=args.dry_run)
    cmd = _hook_command()
    result: dict[str, Any] = {
        "ok": True,
        "mode": "install",
        "dry_run": args.dry_run,
        "home": str(home),
        "platform": platform_name,
        "prep": prep,
        "targets": {},
    }
    has_err = False
    for target in targets:
        skills_res = [_sync_symlink(dst, SKILL_SOURCE, dry_run=args.dry_run) for dst in _skill_targets_for(target, home, platform_name)]
        if target == "codex":
            hook_res: Any = "n/a (Codex invokes /swarm-board on demand)"
        elif args.no_hooks:
            hook_res = "skipped (--no-hooks)"
        elif target == "antigravity":
            hook_res = _install_agy_hook(home, cmd, dry_run=args.dry_run)
        elif target == "claude":
            hook_res = _install_claude_hook(home, cmd, dry_run=args.dry_run)
        else:
            hook_res = "n/a"
        if isinstance(hook_res, dict) and hook_res.get("action") == "error":
            has_err = True
        result["targets"][target] = {"skills": skills_res, "hooks": hook_res}
    result["ok"] = not has_err
    return (0 if result["ok"] else 1), result


def _format_human(payload: dict[str, Any]) -> str:
    if "issues" in payload:
        status = "OK" if payload["ok"] else "FAILED"
        lines = [f"Swarm Board verify: {status} (python {payload['checks']['python']}, UI {payload['checks']['ui_index']})"]
        for name, info in payload["checks"]["targets"].items():
            sk = ", ".join(f"{x['path']}={'ok' if x['ok'] else 'MISSING'}" for x in info["skills"])
            lines.append(f"  {name}: skills=[{sk}] hooks={info['hooks']}")
        for issue in payload["issues"]:
            lines.append(f"  issue: {issue}")
        return "\n".join(lines)

    if not payload.get("ok") and payload.get("error"):
        return f"Error: {payload['error']}"

    prefix = "[dry-run] " if payload.get("dry_run") else ""
    lines = [f"{prefix}Swarm Board {payload['mode']} ({payload['platform']}, home={payload['home']}):"]
    if "prep" in payload:
        lines.append(f"  UI: {payload['prep']['ui_build']}")
        if payload["prep"]["chmod"]:
            lines.append(f"  chmod +x: {', '.join(payload['prep']['chmod'])}")
    if not payload["targets"]:
        lines.append("  No targets matched (~/.gemini, ~/.claude, ~/.codex not found). Use --targets antigravity,claude,codex if needed.")
    for name, info in payload["targets"].items():
        sk = ", ".join(f"{x['path']} ({x['action']})" for x in info["skills"])
        hk = info["hooks"]
        hk_str = f"{hk['path']} ({hk['action']})" if isinstance(hk, dict) else str(hk)
        lines.append(f"  {name}:")
        lines.append(f"    skills: {sk}")
        lines.append(f"    hooks : {hk_str}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    code, payload = run_install(argv)
    if argv is None:
        raw_argv = sys.argv[1:]
    else:
        raw_argv = argv
    if "--json" in raw_argv:
        print(json.dumps(payload, indent=2))
    else:
        print(_format_human(payload))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
