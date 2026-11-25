"""Tests for building and verifying the live Halftone Plate + Stats UI."""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
UI_DIR = REPO_ROOT / "swarm_board" / "ui"
SRC_DIR = UI_DIR / "src"
BUILD_PY = UI_DIR / "build.py"
INDEX_HTML = UI_DIR / "index.html"
SIZE_LIMIT = 153_600

FORBIDDEN_FILES = (
    "fixtures.py",
    "fixtures_base.py",
    "fixtures2.py",
    "fixtures_stats.py",
    "run.py",
    "run2.py",
    "check.js",
    "check2.js",
)
FORBIDDEN_PATTERNS = (
    "window.__DATA",
    "/home/",
    "fixtures",
    "check2",
    "check.js",
)
REQUIRED_ENDPOINTS = (
    "/api/flows",
    "/api/flow/",
    "/api/stats",
    "/api/output/",
    "/api/file",
    "/api/raw",
    "/api/thread/",
    "/events",
    "EventSource",
)
REQUIRED_MARKERS = (
    "#0b0c0e",
    "htScreen",
    'aria-orientation="vertical"',
    "sw-n",
    "Loading flows",
    "No active or recent multi-agent flows found yet.",
    "data-msgs",
    "<video",
    "<audio",
    "swq",
)


class UiLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        proc = subprocess.run(
            [sys.executable, str(BUILD_PY)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"build.py failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
            )
        cls.build_output = proc.stdout
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_index_exists_and_under_budget(self) -> None:
        self.assertTrue(INDEX_HTML.is_file())
        size = INDEX_HTML.stat().st_size
        self.assertGreater(size, 40_000)
        self.assertLess(size, SIZE_LIMIT, f"index.html size {size} B >= {SIZE_LIMIT} B")

    def test_no_scratch_files_or_forbidden_tokens(self) -> None:
        for fname in FORBIDDEN_FILES:
            self.assertFalse((UI_DIR / fname).exists(), f"Unexpected file: {UI_DIR / fname}")
            self.assertFalse((SRC_DIR / fname).exists(), f"Unexpected src file: {SRC_DIR / fname}")

        files_to_scan = [INDEX_HTML, BUILD_PY, *sorted(SRC_DIR.iterdir())]
        for path in files_to_scan:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for pat in FORBIDDEN_PATTERNS:
                self.assertNotIn(
                    pat,
                    text,
                    f"Forbidden token {pat!r} found in {path.relative_to(REPO_ROOT)}",
                )

    def test_all_live_endpoints_and_ui_markers_present(self) -> None:
        for ep in REQUIRED_ENDPOINTS:
            self.assertIn(ep, self.html, f"Missing live endpoint wiring: {ep}")
        for marker in REQUIRED_MARKERS:
            self.assertIn(marker, self.html, f"Missing expected UI marker: {marker}")

    def test_inline_script_passes_node_syntax_check(self) -> None:
        node_bin = shutil.which("node")
        if not node_bin:
            self.skipTest("node is not installed")
        scripts = re.findall(r"<script>([\s\S]*?)</script>", self.html)
        self.assertEqual(len(scripts), 1, "Expected exactly one inline <script> block")
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8") as tmp:
            tmp.write(scripts[0])
            tmp.flush()
            proc = subprocess.run(
                [node_bin, "--check", tmp.name],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                proc.returncode,
                0,
                f"node --check failed:\n{proc.stderr}\n{proc.stdout}",
            )


if __name__ == "__main__":
    unittest.main()
