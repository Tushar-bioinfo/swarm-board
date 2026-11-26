"""Unit tests for agent role label + task-sentence generation, caching, CLI, and registry wiring."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from swarm_board.adapters import BoardRegistry
from swarm_board.labeler import (
    BackgroundLabeler,
    LabelCache,
    enrich_flow_labels,
    load_labeler_config,
    main as labeler_main,
    make_cache_key,
    sanitize_label,
    sanitize_task,
)
from swarm_board.schema import make_agent, make_flow, make_output


class LabelerHeuristicTests(unittest.TestCase):
    def test_sibling_identical_prefix_prompts_get_distinct_short_labels_and_tasks(self) -> None:
        flow = make_flow(
            "t3:ink-demo",
            "Efficient Educational Video Plugin",
            harness="t3",
            kind="mixed",
            agents=[
                make_agent("root-1", "Efficient Educational Video Plugin", role="root"),
                make_agent(
                    "fork-1",
                    "Ink Video Style Decisions for Phase 1B",
                    parent="root-1",
                    role="fork",
                ),
                {
                    **make_agent(
                        "b1",
                        "Create an interactive, self-contained HTML comparison board for **Bra...",
                        parent="fork-1",
                        role="worker",
                    ),
                    "_labelHint": {
                        "prompt": "Create an interactive, self-contained HTML comparison board for **Branch 1 Phase 1A Decisions 1 & 2** using real vendored Inkpress fonts and server-rendered KaTeX.",
                        "slug": "ink-taste-board-1-v1",
                    },
                },
                {
                    **make_agent(
                        "b2",
                        "Create an interactive, self-contained HTML comparison board for **Bra...",
                        parent="fork-1",
                        role="worker",
                    ),
                    "_labelHint": {
                        "prompt": "Create an interactive, self-contained HTML comparison board for **Branch 1 Phase 1A Decisions 3 & 4** using real vendored Inkpress fonts and server-rendered KaTeX.",
                        "slug": "ink-taste-board-2-v1",
                    },
                },
                {
                    **make_agent(
                        "b3",
                        "Create an interactive, self-contained HTML comparison board for **Bra...",
                        parent="fork-1",
                        role="worker",
                    ),
                    "_labelHint": {
                        "prompt": "Create an interactive, self-contained HTML comparison board for **Branch 1 Phase 1A Decisions 5 & 6** using real vendored Inkpress fonts and server-rendered KaTeX.",
                        "slug": "ink-taste-board-3-v1",
                    },
                },
                {
                    **make_agent(
                        "b4",
                        "Create an interactive, self-contained HTML comparison board showing *...",
                        parent="fork-1",
                        role="worker",
                    ),
                    "_labelHint": {
                        "prompt": "Create an interactive, self-contained HTML comparison board showing **4 distinct Crayon Art Style variations of Option 1B (`Hand numbers, italic variables`)** using real vendored Inkpress fonts.",
                        "slug": "ink-taste-crayon-math-1b-v1",
                    },
                },
                {
                    **make_agent(
                        "del-1",
                        "You are the **Delegator (Neon High)** for the Inkpress (`/ink` / `/in...",
                        parent="root-1",
                        role="worker",
                    ),
                    "_labelHint": {
                        "prompt": "You are the **Delegator (Neon High)** for the Inkpress (`/ink` / `/ink-video`) Plugin Upgrade Program.",
                        "slug": "inkpress-delegator-neon-high-v1",
                    },
                },
                {
                    **make_agent(
                        "orch-1",
                        "Branch 1 Main Orchestrator — Phase 1A Design, Research & Committee Review",
                        parent="del-1",
                        role="worker",
                    ),
                    "_labelHint": {
                        "prompt": "You are the Branch 1 Main Orchestrator for the Inkpress Plugin Upgrade Program.",
                        "slug": "inkpress-branch1-orchestrator-phase1a-v1",
                    },
                },
                {
                    **make_agent(
                        "orch-2",
                        "Branch 1 Main Orchestrator — Phase 1A Design, Research & Committee Re...",
                        parent="del-1",
                        role="worker",
                    ),
                    "_labelHint": {
                        "prompt": "All three `mid $committee` outputs in `/tmp/committee/inkpress-b1-phase1a/` are now complete on disk. Your previous turn ended before you could synthesize them.",
                        "slug": "inkpress-branch1-orchestrator-phase1a-v1",
                    },
                },
                {
                    **make_agent(
                        "cr-1",
                        "You are the Creative Direction & Visual Craft Specialist (Claude Opus...",
                        parent="orch-1",
                        role="worker",
                    ),
                    "_labelHint": {
                        "prompt": "You are the Creative Direction & Visual Craft Specialist (Claude Opus 5.5) for Branch 1 Phase 1A.",
                        "slug": "inkpress-b1-phase1a-opus-creative-v1",
                    },
                },
            ],
            outputs=[
                make_output(
                    "res-del-1",
                    "result",
                    "You are the **Delegator (Neon High)** for the Inkpress (`/ink` / `/in... result",
                    by="del-1",
                ),
                make_output("file-1", "code", "swarm_board/labeler.py", by="del-1"),
            ],
        )

        enriched = enrich_flow_labels(flow)
        by_id = {a["id"]: a for a in enriched["agents"]}

        # _labelHint must be stripped from every agent
        for a in enriched["agents"]:
            self.assertNotIn("_labelHint", a)
            self.assertTrue(a["label"])
            self.assertTrue(a["task"])
            self.assertNotIn("**", a["label"])
            self.assertNotIn("**", a["task"])
            self.assertNotIn("`", a["label"])
            self.assertFalse(a["label"].endswith("..."))
            self.assertFalse(a["task"].endswith("..."))
            self.assertLessEqual(len(a["label"].split()), 3, a["label"])
            self.assertLessEqual(len(a["task"]), 140, a["task"])

        # Sibling boards under fork-1 must all be distinct and meaningful
        board_labels = [by_id[k]["label"] for k in ("b1", "b2", "b3", "b4")]
        self.assertEqual(len(set(board_labels)), 4, f"Duplicate sibling labels: {board_labels}")
        self.assertIn("1", board_labels[0])
        self.assertIn("3", board_labels[1])
        self.assertIn("5", board_labels[2])
        self.assertIn("Crayon", board_labels[3])

        # Root, fork, delegator, creative direction, and continuation orch-2
        self.assertEqual(by_id["root-1"]["label"], "root")
        self.assertEqual(by_id["del-1"]["label"], "Inkpress Delegator")
        self.assertEqual(by_id["cr-1"]["label"], "Creative Direction")
        self.assertNotEqual(by_id["orch-1"]["label"], by_id["orch-2"]["label"])

        # Synthesized result title rewritten cleanly, real file output untouched
        outs = {o["id"]: o["title"] for o in enriched["outputs"]}
        self.assertEqual(outs["res-del-1"], "Inkpress Delegator result")
        self.assertEqual(outs["file-1"], "swarm_board/labeler.py")

    def test_sanitize_helpers_and_committee_and_swarm_plan_tasks(self) -> None:
        self.assertEqual(sanitize_label("  **Decisions 1 & 2** (Neon High)... "), "Decisions 1–2")
        self.assertFalse(sanitize_task("**Create** a clean board...").endswith("..."))

        flow = make_flow(
            "agy:demo",
            "Demo Swarm",
            harness="agy",
            kind="swarm",
            goal="Audit split panes across themes.",
            agents=[
                {
                    **make_agent("coordinator", "coordinator", role="coordinator"),
                    "_labelHint": {"planTask": "Audit split panes across themes."},
                },
                {
                    **make_agent("ui-auditor", "ui-auditor", parent="coordinator", role="worker"),
                    "_labelHint": {"planTask": "Audit split panes across light and dark themes"},
                },
            ],
        )
        enrich_flow_labels(flow)
        by_id = {a["id"]: a for a in flow["agents"]}
        self.assertEqual(by_id["coordinator"]["label"], "Coordinator")
        self.assertEqual(by_id["ui-auditor"]["label"], "UI Auditor")
        self.assertEqual(by_id["ui-auditor"]["task"], "Audit split panes across light and dark themes.")


class LabelerCacheAndBackgroundTests(unittest.TestCase):
    def test_cache_background_cli_and_registry_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg_path = root / "config.json"
            cache_path = root / "labels-cache.json"
            dropin_dir = root / "dropin"
            dropin_dir.mkdir()

            (dropin_dir / "demo.json").write_text(
                json.dumps(
                    {
                        "id": "dropin:lbl-test",
                        "title": "Label Integration Flow",
                        "agents": [
                            {"id": "r1", "name": "Root", "role": "root", "lab": "t3", "state": "done"},
                            {
                                "id": "w1",
                                "parent": "r1",
                                "name": "Create comparison board for alpha section...",
                                "role": "worker",
                                "lab": "google",
                                "state": "done",
                            },
                            {
                                "id": "w2",
                                "parent": "r1",
                                "name": "Create comparison board for beta section...",
                                "role": "worker",
                                "lab": "google",
                                "state": "done",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            # 1. Config CLI roundtrip (--set / --status / --disable)
            with patch.dict(os.environ, {"SWARM_BOARD_CONFIG": str(cfg_path)}, clear=False):
                self.assertEqual(labeler_main(["--set", "agy:gemini-3.8-flash-low"]), 0)
                cfg = load_labeler_config()
                self.assertTrue(cfg["enabled"])
                self.assertEqual(cfg["cli"], "agy")
                self.assertEqual(cfg["model"], "gemini-3.8-flash-low")

                self.assertEqual(labeler_main(["--disable"]), 0)
                cfg2 = load_labeler_config()
                self.assertFalse(cfg2["enabled"])

            # 2. Disabled BackgroundLabeler spawns zero threads
            cache = LabelCache(cache_path)
            bg_off = BackgroundLabeler(cache=cache, config={"enabled": False})
            bg_off.enqueue("f1", "Flow", [{"agentId": "w1", "cacheKey": "k1"}])
            self.assertIsNone(bg_off._thread)
            bg_off.close()

            # 3. Enabled BackgroundLabeler runs mocked CLI, persists to cache, bumps version
            called = threading.Event()

            def fake_runner(cli: str, model: str, prompt: str, timeout_s: float) -> str:
                called.set()
                return (
                    "```json\n"
                    '{"w1": {"label": "Alpha Board", "task": "Build the Alpha comparison board."}, '
                    '"w2": {"label": "Beta Board", "task": "Build the Beta comparison board."}}\n'
                    "```"
                )

            bg_on = BackgroundLabeler(
                cache=cache,
                config={"enabled": True, "cli": "agy", "model": "gemini-3.8-flash-low"},
                cli_runner=fake_runner,
            )
            try:
                reg = BoardRegistry(
                    {
                        "committee_dir": root / "no-com",
                        "orchestrate_dir": root / "no-orch",
                        "sessions_ledger": root / "no-ledger",
                        "grok_sessions_root": root / "no-grok",
                        "codex_db": root / "no-codex",
                        "claude_root": root / "no-claude",
                        "brain_roots": [],
                        "statev2_db": root / "no-t3.sqlite",
                        "dropin_dir": dropin_dir,
                        "label_cache_path": cache_path,
                        "labeler": {"enabled": True, "cli": "agy", "model": "gemini-3.8-flash-low"},
                    },
                    cache_ttl_s=10.0,
                )
                # Replace background worker with mocked runner for isolated test
                reg._bg_labeler.close()
                reg._bg_labeler = bg_on

                d0 = reg.snapshot_digest()
                flows1 = reg.list_flows(force=True)
                self.assertEqual(len(flows1), 1)
                self.assertTrue(called.wait(timeout=3.0), "BackgroundLabeler did not execute")

                # Wait briefly for atomic cache write + version bump
                deadline = time.monotonic() + 3.0
                while bg_on.version() == 0 and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertGreaterEqual(bg_on.version(), 1)
                self.assertNotEqual(reg.snapshot_digest(), d0)

                # Next list_flows should invalidate TTL automatically and serve cached LLM labels!
                flows2 = reg.list_flows(force=False)
                by_id = {a["id"]: a for a in flows2[0]["agents"]}
                self.assertEqual(by_id["w1"]["label"], "Alpha Board")
                self.assertEqual(by_id["w1"]["task"], "Build the Alpha comparison board.")
                self.assertEqual(by_id["w1"]["labelSource"], "llm")
                self.assertEqual(by_id["w2"]["label"], "Beta Board")
                self.assertEqual(by_id["w2"]["labelSource"], "llm")

                # Cache reload from disk in a fresh LabelCache instance
                cache2 = LabelCache(cache_path)
                self.assertEqual(cache2.stats()["entries"], 2)
                self.assertEqual(cache2.clear(), 2)
                self.assertEqual(cache2.stats()["entries"], 0)
                reg.close()
            finally:
                bg_on.close()

    def test_cache_key_consistency_between_raw_and_registry(self) -> None:
        k1 = make_cache_key("agy:gemini-3.8-flash-low", "w1", "raw", "prompt", "sig")
        k2 = make_cache_key("agy:gemini-3.8-flash-low", "w1", "raw", "prompt", "sig")
        k3 = make_cache_key("claude:claude-haiku-4-5", "w1", "raw", "prompt", "sig")
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)


if __name__ == "__main__":
    unittest.main()
