"""Tests for skill packaging, board.sh symlink resolution, and cross-platform installer."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_PY = REPO_ROOT / "scripts" / "install.py"
SKILL_DIR = REPO_ROOT / "skills" / "swarm-board"
SKILL_MD = SKILL_DIR / "SKILL.md"
BOARD_SH = SKILL_DIR / "scripts" / "board.sh"


def _run_installer(args: list[str], *, home: Path, platform: str | None = None) -> tuple[int, dict]:
    cmd = [sys.executable, str(INSTALL_PY), "--home", str(home), "--json"]
    if platform is not None:
        cmd.extend(["--platform", platform])
    cmd.extend(args)
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, check=False)
    payload = json.loads(proc.stdout.strip())
    return proc.returncode, payload


class SkillPackageTests(unittest.TestCase):
    def test_skill_frontmatter_and_references(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        m = re.match(r"^---\n([\s\S]*?)\n---\n([\s\S]*)$", text)
        self.assertIsNotNone(m, "SKILL.md must begin with YAML frontmatter")
        fm, body = m.group(1), m.group(2)
        self.assertIn("name: swarm-board", fm)
        desc_m = re.search(r"^description:\s*(.+)$", fm, re.M)
        self.assertIsNotNone(desc_m)
        desc = desc_m.group(1).strip().strip("'\"")
        self.assertGreater(len(desc), 20)
        self.assertLessEqual(len(desc), 240)
        self.assertIn("board.sh", body)
        self.assertIn("weekly-rates.md", body)
        self.assertTrue((SKILL_DIR / "references" / "weekly-rates.md").is_file())

    def test_board_sh_syntax_and_symlink_resolution(self) -> None:
        p_syn = subprocess.run(["bash", "-n", str(BOARD_SH)], capture_output=True, text=True, check=False)
        self.assertEqual(p_syn.returncode, 0, p_syn.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sym_parent = tmp_path / "ext" / "skills"
            sym_parent.mkdir(parents=True)
            sym_skill = sym_parent / "swarm-board"
            sym_skill.symlink_to(SKILL_DIR)

            state_dir = tmp_path / "state"
            env = {**os.environ, "SWARM_BOARD_STATE_DIR": str(state_dir)}
            proc = subprocess.run(
                ["bash", str(sym_skill / "scripts" / "board.sh"), "status"],
                cwd=str(tmp_path),
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(proc.stdout.strip())
            self.assertTrue(data["ok"])
            self.assertFalse(data["running"])
            self.assertEqual(Path(data["state_dir"]), state_dir)


class InstallerTests(unittest.TestCase):
    def test_linux_install_idempotency_verify_and_uninstall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "linux-home"
            (home / ".gemini" / "config" / "skills").mkdir(parents=True)
            (home / ".claude").mkdir(parents=True)
            (home / ".codex").mkdir(parents=True)
            t3_cfg = home / ".t3" / "userdata" / "providers" / "antigravity" / "prov1" / "config"
            t3_cfg.mkdir(parents=True)
            (t3_cfg / "skills").symlink_to(home / ".gemini" / "config" / "skills")

            settings_path = home / ".claude" / "settings.json"
            orig_settings = {
                "model": "haiku",
                "permissions": {"defaultMode": "bypassPermissions"},
                "hooks": {
                    "PostToolUse": [
                        {"matcher": "Write", "hooks": [{"type": "command", "command": "echo keep"}]}
                    ]
                },
            }
            settings_text = json.dumps(orig_settings, indent=2) + "\n"
            settings_path.write_text(settings_text, encoding="utf-8")

            codex_toml = home / ".codex" / "config.toml"
            codex_toml.write_text('approval_policy = "never"\n', encoding="utf-8")

            # 1. Dry-run makes zero filesystem mutations
            code_dry, dry_res = _run_installer(["--dry-run"], home=home, platform="linux")
            self.assertEqual(code_dry, 0, dry_res)
            self.assertFalse((home / ".gemini" / "config" / "skills" / "swarm-board").exists())
            self.assertFalse((home / ".gemini" / "config" / "hooks.json").exists())
            self.assertEqual(settings_path.read_text(encoding="utf-8"), settings_text)

            # 2. Real install links all 3 targets, skips redundant T3 symlink, merges hooks
            code_in, in_res = _run_installer([], home=home, platform="linux")
            self.assertEqual(code_in, 0, in_res)
            self.assertEqual(
                (home / ".gemini" / "config" / "skills" / "swarm-board").resolve(),
                SKILL_DIR.resolve(),
            )
            self.assertEqual((home / ".claude" / "skills" / "swarm-board").resolve(), SKILL_DIR.resolve())
            self.assertEqual((home / ".codex" / "skills" / "swarm-board").resolve(), SKILL_DIR.resolve())
            self.assertEqual(len(in_res["targets"]["antigravity"]["skills"]), 1)

            hooks_doc = json.loads((home / ".gemini" / "config" / "hooks.json").read_text(encoding="utf-8"))
            self.assertIn("swarm-board", hooks_doc)
            self.assertIn("ensure-server.sh", hooks_doc["swarm-board"]["PostToolUse"][0]["hooks"][0]["command"])

            merged_settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(merged_settings["model"], "haiku")
            self.assertEqual(merged_settings["permissions"], {"defaultMode": "bypassPermissions"})
            post_hooks = merged_settings["hooks"]["PostToolUse"]
            self.assertEqual(len(post_hooks), 2)
            self.assertEqual(post_hooks[0]["matcher"], "Write")
            self.assertIn("ensure-server.sh", post_hooks[1]["hooks"][0]["command"])
            self.assertEqual(codex_toml.read_text(encoding="utf-8"), 'approval_policy = "never"\n')

            # 3. Second install is idempotent
            code_in2, in_res2 = _run_installer([], home=home, platform="linux")
            self.assertEqual(code_in2, 0, in_res2)
            self.assertEqual(in_res2["targets"]["claude"]["hooks"]["action"], "unchanged")
            self.assertEqual(in_res2["targets"]["antigravity"]["hooks"]["action"], "unchanged")
            self.assertEqual(len(json.loads(settings_path.read_text(encoding="utf-8"))["hooks"]["PostToolUse"]), 2)

            # 4. --verify succeeds
            code_ver, ver_res = _run_installer(["--verify"], home=home, platform="linux")
            self.assertEqual(code_ver, 0, ver_res)
            self.assertTrue(ver_res["ok"])

            # 5. Add an unrelated key in hooks.json to ensure uninstall preserves it
            hooks_doc["other-hook"] = {"Stop": [{"command": "echo bye"}]}
            (home / ".gemini" / "config" / "hooks.json").write_text(json.dumps(hooks_doc), encoding="utf-8")

            # 6. Uninstall removes only swarm-board symlinks & entries
            code_un, un_res = _run_installer(["--uninstall"], home=home, platform="linux")
            self.assertEqual(code_un, 0, un_res)
            self.assertFalse((home / ".gemini" / "config" / "skills" / "swarm-board").exists())
            self.assertFalse((home / ".claude" / "skills" / "swarm-board").exists())
            self.assertFalse((home / ".codex" / "skills" / "swarm-board").exists())

            rem_hooks = json.loads((home / ".gemini" / "config" / "hooks.json").read_text(encoding="utf-8"))
            self.assertNotIn("swarm-board", rem_hooks)
            self.assertIn("other-hook", rem_hooks)

            rem_settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(rem_settings, orig_settings)
            self.assertEqual(codex_toml.read_text(encoding="utf-8"), 'approval_policy = "never"\n')

    def test_macos_t3_provider_dir_and_uninstall_removes_empty_hooks_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "mac user home"
            prov_cfg = home / "Library" / "Application Support" / "T3 Code" / "userdata" / "providers" / "antigravity" / "prov1" / "config"
            prov_cfg.mkdir(parents=True)

            code_in, in_res = _run_installer([], home=home, platform="darwin")
            self.assertEqual(code_in, 0, in_res)
            self.assertEqual(
                (home / ".gemini" / "config" / "skills" / "swarm-board").resolve(),
                SKILL_DIR.resolve(),
            )
            self.assertEqual((prov_cfg / "skills" / "swarm-board").resolve(), SKILL_DIR.resolve())
            self.assertTrue((home / ".gemini" / "config" / "hooks.json").is_file())

            code_ver, ver_res = _run_installer(["--verify"], home=home, platform="darwin")
            self.assertEqual(code_ver, 0, ver_res)

            code_un, _ = _run_installer(["--uninstall"], home=home, platform="darwin")
            self.assertEqual(code_un, 0)
            self.assertFalse((home / ".gemini" / "config" / "hooks.json").exists())
            self.assertFalse((prov_cfg / "skills" / "swarm-board").exists())

    def test_no_hooks_and_non_owned_skill_protection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            custom_dir = home / ".claude" / "skills" / "swarm-board"
            custom_dir.mkdir(parents=True)
            marker = custom_dir / "custom.txt"
            marker.write_text("keep me", encoding="utf-8")
            (home / ".gemini" / "config").mkdir(parents=True)

            code_in, in_res = _run_installer(["--no-hooks"], home=home, platform="linux")
            self.assertEqual(code_in, 0, in_res)
            self.assertFalse((home / ".gemini" / "config" / "hooks.json").exists())
            self.assertFalse((home / ".claude" / "settings.json").exists())
            self.assertEqual(in_res["targets"]["claude"]["skills"][0]["action"], "skipped_existing")
            self.assertTrue(marker.is_file())

            # Uninstall must never delete user's non-symlink folder
            code_un, _ = _run_installer(["--uninstall"], home=home, platform="linux")
            self.assertEqual(code_un, 0)
            self.assertTrue(marker.is_file())


if __name__ == "__main__":
    unittest.main()
