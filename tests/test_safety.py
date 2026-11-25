"""Unit tests for strict read-only path containment and secret exclusion."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from swarm_board.safety import (
    is_path_allowed,
    redact_if_secret_name,
    safe_read_citation_lines,
    safe_read_text,
)


class SafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "proj"
        self.root.mkdir()
        self.outside = Path(self.tmp.name) / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("do-not-leak\n", encoding="utf-8")

        self.src_dir = self.root / "src"
        self.src_dir.mkdir()
        self.sample_file = self.src_dir / "main.py"
        self.sample_file.write_text(
            "\n".join(f"line {i}: val = {i}" for i in range(1, 21)) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_blocks_traversal_and_outside_paths(self) -> None:
        ok, err = is_path_allowed(str(self.root / ".." / "outside" / "secret.txt"), [str(self.root)])
        self.assertFalse(ok)
        self.assertIn("outside", err or "")

        ok2, _ = is_path_allowed("/etc/passwd", [str(self.root)])
        self.assertFalse(ok2)

    def test_blocks_symlink_escape(self) -> None:
        link_path = self.root / "escape.txt"
        os.symlink(self.outside / "secret.txt", link_path)
        ok, err = is_path_allowed(str(link_path), [str(self.root)])
        self.assertFalse(ok)
        self.assertIn("outside", err or "")

    def test_blocks_secret_names_and_patterns(self) -> None:
        bad_rel_paths = [
            ".env",
            ".env.local",
            ".env.production",
            "auth.json",
            ".credentials.json",
            "tls/server.key",
            "certs/ca.pem",
            "ssh/id_ed25519",
            "ssh/id_rsa",
            ".git/config",
            "data/statev2.sqlite",
            "data/statev2.sqlite-wal",
            "data/statev2.sqlite-shm",
            "secrets/token.txt",
            "sub/app.db",
        ]
        for rel in bad_rel_paths:
            fpath = self.root / rel
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text("SECRET=1\n", encoding="utf-8")
            self.assertIsNotNone(redact_if_secret_name(str(fpath)), rel)
            res = safe_read_text(str(fpath), [str(self.root)])
            self.assertFalse(res["ok"], f"Expected denial for {rel}")

    def test_safe_read_text_truncates_at_max_bytes(self) -> None:
        big = self.root / "big.log"
        big.write_bytes(b"A" * 2048)
        res = safe_read_text("big.log", [str(self.root)], max_bytes=512)
        self.assertTrue(res["ok"])
        self.assertTrue(res["truncated"])
        self.assertEqual(res["size"], 2048)
        self.assertEqual(len(res["body"]), 512)

    def test_citation_lines_boundaries(self) -> None:
        # Line 1 boundary
        c1 = safe_read_citation_lines("src/main.py", 1, [str(self.root)], ctx=4)
        self.assertTrue(c1["ok"])
        self.assertEqual(c1["start"], 1)
        self.assertEqual([x["num"] for x in c1["lines"]], [1, 2, 3, 4, 5])
        self.assertTrue(c1["lines"][0]["cited"])

        # Middle line
        c10 = safe_read_citation_lines(str(self.sample_file), 10, [str(self.root)], ctx=4)
        self.assertTrue(c10["ok"])
        self.assertEqual(len(c10["lines"]), 9)
        self.assertEqual([x["num"] for x in c10["lines"] if x["cited"]], [10])

        # EOF boundary
        c20 = safe_read_citation_lines("src/main.py", 20, [str(self.root)], ctx=4)
        self.assertTrue(c20["ok"])
        self.assertEqual([x["num"] for x in c20["lines"]], [16, 17, 18, 19, 20])

        # Beyond EOF
        c99 = safe_read_citation_lines("src/main.py", 99, [str(self.root)], ctx=4)
        self.assertFalse(c99["ok"])

        # Missing file
        cmiss = safe_read_citation_lines("src/missing.py", 1, [str(self.root)], ctx=4)
        self.assertFalse(cmiss["ok"])
        self.assertIn("Not found", cmiss["error"])


if __name__ == "__main__":
    unittest.main()
