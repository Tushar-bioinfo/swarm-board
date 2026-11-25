"""Tests for Step 3A accounting, deduplication, range slicing, AGY CLI join, layered rates, and path safety."""

from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import update_rates
from swarm_board.adapters import BoardRegistry, enrich_flow_telemetry
from swarm_board.adapters.agy_swarms import default_brain_roots
from swarm_board.adapters.harness_runs import _resolve_harness_dirs, discover_harness_runs
from swarm_board.adapters.t3_delegations import discover_t3_flows
from swarm_board.schema import RateTable, UsageIndex, default_t3_userdata_dir, to_iso_utc


def _create_t3_db(db_path: Path) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(
            """
            CREATE TABLE projection_projects (
                project_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                workspace_root TEXT NOT NULL
            );
            CREATE TABLE orchestration_v2_projection_threads (
                thread_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                title TEXT NOT NULL,
                default_provider TEXT,
                provider_instance_id TEXT,
                active_provider_thread_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT,
                deleted_at TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE orchestration_v2_projection_subagents (
                subagent_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                parent_node_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                driver TEXT NOT NULL,
                provider_instance_id TEXT NOT NULL,
                provider_thread_id TEXT,
                child_thread_id TEXT,
                origin TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE orchestration_v2_projection_runs (
                run_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE orchestration_v2_projection_provider_threads (
                provider_thread_id TEXT PRIMARY KEY,
                thread_id TEXT,
                provider TEXT,
                driver TEXT,
                updated_at TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE orchestration_v2_projection_messages (
                message_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                run_id TEXT,
                node_id TEXT,
                role TEXT NOT NULL,
                streaming INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        con.commit()
    finally:
        con.close()


def _write_rates_file(path: Path, doc: dict[str, dict[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetchedAtMs": 1795600000000, "document": doc}), encoding="utf-8")


def _write_agy_transcript(brain_root: Path, conv_id: str, steps: list[dict]) -> Path:
    tr = brain_root / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
    tr.parent.mkdir(parents=True, exist_ok=True)
    tr.write_text("\n".join(json.dumps(s) for s in steps) + "\n", encoding="utf-8")
    return tr


class CrossPlatformPathTests(unittest.TestCase):
    def test_default_t3_userdata_dir_env_linux_and_macos(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env_dir = home / "custom-t3"
            with patch.dict(os.environ, {"HOME": str(home), "T3_USERDATA_DIR": str(env_dir)}):
                self.assertEqual(default_t3_userdata_dir(), env_dir)

            # Linux ~/.t3/userdata preferred when present
            linux_dir = home / ".t3" / "userdata"
            linux_dir.mkdir(parents=True)
            with patch.dict(os.environ, {"HOME": str(home)}, clear=False):
                os.environ.pop("T3_USERDATA_DIR", None)
                self.assertEqual(default_t3_userdata_dir(), linux_dir)

            # macOS Application Support fallback when ~/.t3/userdata absent
            linux_dir.rmdir()
            mac_dir = home / "Library" / "Application Support" / "T3 Code" / "userdata"
            mac_dir.mkdir(parents=True)
            with patch.dict(os.environ, {"HOME": str(home)}, clear=False):
                os.environ.pop("T3_USERDATA_DIR", None)
                self.assertEqual(default_t3_userdata_dir(), mac_dir)

    def test_default_brain_roots_includes_gemini_cli_and_macos_t3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cli_brain = home / ".gemini" / "antigravity-cli" / "brain"
            mac_brain = (
                home
                / "Library"
                / "Application Support"
                / "T3 Code"
                / "userdata"
                / "providers"
                / "antigravity"
                / "hash1"
                / "antigravity-acp"
                / "brain"
            )
            cli_brain.mkdir(parents=True)
            mac_brain.mkdir(parents=True)
            with patch.dict(os.environ, {"HOME": str(home)}, clear=False):
                os.environ.pop("T3_USERDATA_DIR", None)
                roots = [os.path.realpath(p) for p in default_brain_roots()]
            self.assertIn(os.path.realpath(cli_brain), roots)
            self.assertIn(os.path.realpath(mac_brain), roots)

    def test_harness_dir_resolution_dedup_on_tmpdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("swarm_board.adapters.harness_runs.tempfile.gettempdir", return_value=tmp):
                dirs = _resolve_harness_dirs(None, Path("/tmp/committee"), "committee")
                self.assertEqual(len(dirs), 2)
                self.assertEqual(dirs[0], Path("/tmp/committee"))
                self.assertEqual(dirs[1], Path(tmp) / "committee")
            with patch("swarm_board.adapters.harness_runs.tempfile.gettempdir", return_value="/tmp"):
                deduped = _resolve_harness_dirs(None, Path("/tmp/committee"), "committee")
                self.assertEqual(len(deduped), 1)


class NodeDeduplicationTests(unittest.TestCase):
    def test_t3_node_continuation_dedup_preserves_fresh_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proj = root / "proj"
            proj.mkdir()
            db_path = root / "statev2.sqlite"
            _create_t3_db(db_path)

            rates_path = root / "rates.json"
            _write_rates_file(
                rates_path,
                {
                    "gemini/gemini-4-neon": {
                        "input_cost_per_token": 2e-6,
                        "output_cost_per_token": 8e-6,
                        "cache_read_input_token_cost": 2e-7,
                    },
                    "gemini/gemini-4-hydrogen": {
                        "input_cost_per_token": 1e-6,
                        "output_cost_per_token": 4e-6,
                        "cache_read_input_token_cost": 1e-7,
                    },
                },
            )
            usage_path = root / "usage.json"
            usage_path.write_text(
                json.dumps(
                    {
                        "files": {
                            "/tmp/nid-1.jsonl": {
                                "cs": {"sessionId": "nid-1", "model": "gemini-4-neon"},
                                "r": [[0, 0, 0, 1_000_000, 500_000, 0, 100_000, 0, 0, None]],
                            },
                            "/tmp/nid-fresh.jsonl": {
                                "cs": {"sessionId": "nid-fresh", "model": "gemini-4-hydrogen"},
                                "r": [[0, 0, 0, 200_000, 100_000, 0, 50_000, 0, 0, None]],
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            con = sqlite3.connect(str(db_path))
            con.execute("INSERT INTO projection_projects VALUES ('p1', 'Demo', ?)", (str(proj),))
            con.execute(
                "INSERT INTO orchestration_v2_projection_threads VALUES ('root-1', 'p1', 'Root Flow', 'antigravity', 'antigravity', NULL, '2026-11-25T10:00:00Z', '2026-11-25T10:20:00Z', NULL, NULL, ?)",
                (json.dumps({"lineage": {"rootThreadId": "root-1"}}),),
            )
            con.execute(
                "INSERT INTO orchestration_v2_projection_threads VALUES ('child-1', 'p1', 'Worker Thread', 'antigravity', 'antigravity', 'pt-1', '2026-11-25T10:01:00Z', '2026-11-25T10:15:00Z', NULL, NULL, ?)",
                (json.dumps({"lineage": {"rootThreadId": "root-1", "parentThreadId": "root-1"}, "modelSelection": {"model": "gemini-4-neon"}}),),
            )
            con.execute(
                "INSERT INTO orchestration_v2_projection_provider_threads VALUES ('pt-1', 'child-1', 'antigravity', 'antigravity', '2026-11-25T10:05:00Z', ?)",
                (json.dumps({"nativeThreadRef": {"nativeId": "nid-1"}}),),
            )
            con.execute(
                "INSERT INTO orchestration_v2_projection_provider_threads VALUES ('pt-fresh', 'child-1', 'antigravity', 'antigravity', '2026-11-25T10:15:00Z', ?)",
                (json.dumps({"nativeThreadRef": {"nativeId": "nid-fresh"}}),),
            )
            # 1st row: owns child_thread_id 'child-1' -> nid-1
            con.execute(
                "INSERT INTO orchestration_v2_projection_subagents VALUES ('sub-initial', 'root-1', 'r1', 'n1', 'antigravity', 'antigravity', 'antigravity', 'pt-1', 'child-1', 'delegate', 'completed', '2026-11-25T10:01:00Z', '2026-11-25T10:05:00Z', '2026-11-25T10:05:00Z', ?)",
                (json.dumps({"title": "Worker 1", "role": "worker", "model": "gemini-4-neon"}),),
            )
            # 2nd row: node:* continuation sharing nativeId nid-1 even with switched model -> MUST deduplicate
            con.execute(
                "INSERT INTO orchestration_v2_projection_subagents VALUES ('node:cont-same-native', 'root-1', 'r2', 'n2', 'antigravity', 'antigravity', 'antigravity', 'pt-1', 'child-1', 'thread-send', 'completed', '2026-11-25T10:06:00Z', '2026-11-25T10:08:00Z', '2026-11-25T10:08:00Z', ?)",
                (json.dumps({"title": "Continue 1", "role": "worker", "model": "gemini-4-hydrogen"}),),
            )
            # 3rd row: node:* continuation without pt_id falling back to child-1 (nid-1) -> MUST deduplicate
            con.execute(
                "INSERT INTO orchestration_v2_projection_subagents VALUES ('node:cont-fallback', 'root-1', 'r3', 'n3', 'antigravity', 'antigravity', 'antigravity', NULL, 'child-1', 'thread-send', 'completed', '2026-11-25T10:09:00Z', '2026-11-25T10:10:00Z', '2026-11-25T10:10:00Z', ?)",
                (json.dumps({"title": "Continue 2", "role": "worker", "model": "gemini-4-neon"}),),
            )
            # 4th row: node:* continuation with freshContext (distinct nativeId nid-fresh) -> MUST keep tokens/cost
            con.execute(
                "INSERT INTO orchestration_v2_projection_subagents VALUES ('node:cont-fresh', 'root-1', 'r4', 'n4', 'antigravity', 'antigravity', 'antigravity', 'pt-fresh', 'child-1', 'thread-send', 'completed', '2026-11-25T10:11:00Z', '2026-11-25T10:15:00Z', '2026-11-25T10:15:00Z', ?)",
                (json.dumps({"title": "Fresh Continue", "role": "worker", "model": "gemini-4-hydrogen"}),),
            )
            con.commit()
            con.close()

            rt = RateTable(rates_path, override_paths=[])
            ui = UsageIndex(usage_path)
            flows = discover_t3_flows(
                db_path=db_path,
                env_id="env-1",
                rate_table=rt,
                usage_index=ui,
                brain_roots=[],
                grok_sessions_root=root / "none",
            )
            self.assertEqual(len(flows), 1)
            by_id = {a["id"]: a for a in flows[0]["agents"]}
            self.assertEqual(len(by_id), 5)  # All node:* rows remain visible on the tree

            # First row keeps nid-1 usage ($2.90)
            self.assertEqual(by_id["child-1"]["tokens"], {"in": 1_000_000, "cache": 500_000, "out": 100_000})
            self.assertAlmostEqual(by_id["child-1"]["costUsd"], 2.9, places=4)
            self.assertFalse(by_id["child-1"].get("usageDeduped", False))

            # Duplicate node:* continuations have usage cleared
            for dup_id in ("node:cont-same-native", "node:cont-fallback"):
                self.assertIsNone(by_id[dup_id]["tokens"])
                self.assertIsNone(by_id[dup_id]["costUsd"])
                self.assertTrue(by_id[dup_id]["usageDeduped"])
                self.assertEqual(by_id[dup_id]["meta"], {"usageDeduped": True})

            # Fresh context continuation keeps its own distinct usage ($0.41)
            self.assertEqual(by_id["node:cont-fresh"]["tokens"], {"in": 200_000, "cache": 100_000, "out": 50_000})
            self.assertAlmostEqual(by_id["node:cont-fresh"]["costUsd"], 0.41, places=4)
            self.assertFalse(by_id["node:cont-fresh"].get("usageDeduped", False))

            reg = BoardRegistry(
                {
                    "committee_dir": root / "no-c",
                    "orchestrate_dir": root / "no-o",
                    "sessions_ledger": root / "no-l",
                    "grok_sessions_root": root / "no-g",
                    "codex_db": root / "no-cx",
                    "claude_root": root / "no-cl",
                    "brain_roots": [],
                    "statev2_db": db_path,
                    "env_id": "env-1",
                    "rates_path": rates_path,
                    "usage_cache_path": usage_path,
                },
                cache_ttl_s=0.0,
            )
            reg.rate_table = rt
            stats = reg.get_stats(scope="flow", flow_id="t3:root-1")
            self.assertEqual(stats["dupRows"], 2)
            self.assertEqual(stats["agentsCount"], 5)
            self.assertEqual(stats["ratedAgents"], 2)
            self.assertAlmostEqual(stats["costUsd"], 3.31, places=2)


class StatsCrossAdapterAndRangeTests(unittest.TestCase):
    def _build_registry(self, tmp: Path, now_ts: float) -> BoardRegistry:
        rates_path = tmp / "rates.json"
        _write_rates_file(
            rates_path,
            {
                "gemini/gemini-4-neon": {
                    "input_cost_per_token": 2e-6,
                    "output_cost_per_token": 1e-5,
                    "cache_read_input_token_cost": 2e-7,
                },
                "anthropic.claude-opus-5": {
                    "input_cost_per_token": 5e-6,
                    "output_cost_per_token": 2e-5,
                    "cache_read_input_token_cost": 5e-7,
                },
                "xai/grok-4.7-build": {
                    "input_cost_per_token": 1e-6,
                    "output_cost_per_token": 5e-6,
                    "cache_read_input_token_cost": 1e-7,
                },
            },
        )
        reg = BoardRegistry(
            {
                "committee_dir": tmp / "no-c",
                "orchestrate_dir": tmp / "no-o",
                "sessions_ledger": tmp / "no-l",
                "grok_sessions_root": tmp / "no-g",
                "codex_db": tmp / "no-cx",
                "claude_root": tmp / "no-cl",
                "brain_roots": [],
                "statev2_db": tmp / "no-t3",
                "rates_path": rates_path,
                "usage_cache_path": tmp / "no-usage.json",
                "now_ts": now_ts,
            },
            cache_ttl_s=60.0,
        )
        reg.rate_table = RateTable(rates_path, override_paths=[])
        return reg

    def test_cross_adapter_dedup_in_get_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now_ts = float(int(datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc).timestamp()))
            reg = self._build_registry(root, now_ts)

            shared_grok_sid = "01a2102a-ecfc-7903-b085-7a8065ec329a"
            shared_agy_sid = "1d5d6844-dcd1-4483-8975-a002506aa3ac"

            flows = [
                {
                    "id": "delegate:1",
                    "title": "Delegate Batch",
                    "project": "p",
                    "projectRoot": None,
                    "harness": "delegate",
                    "kind": "flat",
                    "startedAt": to_iso_utc(now_ts - 3600),
                    "updatedAt": to_iso_utc(now_ts - 3600),
                    "goal": None,
                    "decision": None,
                    "workstreams": [],
                    "posts": [],
                    "outputs": [],
                    "links": [],
                    "agents": [
                        {
                            "id": "run-1",
                            "parent": None,
                            "name": "grok run",
                            "role": "worker",
                            "lab": "xai",
                            "model": "grok-4.7-build",
                            "state": "done",
                            "startedAt": to_iso_utc(now_ts - 3600),
                            "endedAt": to_iso_utc(now_ts - 3600),
                            "durS": 60,
                            "tokens": {"in": 1000, "cache": 0, "out": 200},
                            "costUsd": 2.50,
                            "costEstimated": False,
                            "links": [{"kind": "session", "id": shared_grok_sid}],
                            "outputIds": [],
                        }
                    ],
                },
                {
                    "id": "grok:01a2102a",
                    "title": "Grok Session",
                    "project": "p",
                    "projectRoot": None,
                    "harness": "grok",
                    "kind": "flat",
                    "startedAt": to_iso_utc(now_ts - 3600),
                    "updatedAt": to_iso_utc(now_ts - 3540),
                    "goal": None,
                    "decision": None,
                    "workstreams": [],
                    "posts": [],
                    "outputs": [],
                    "links": [],
                    "agents": [
                        {
                            "id": shared_grok_sid,
                            "parent": None,
                            "name": "Grok Real",
                            "role": "worker",
                            "lab": "xai",
                            "model": "grok-4.7-build",
                            "state": "done",
                            "startedAt": to_iso_utc(now_ts - 3600),
                            "endedAt": to_iso_utc(now_ts - 3540),
                            "durS": 60,
                            "tokens": {"in": 1000, "cache": 0, "out": 200},
                            "costUsd": 2.50,
                            "costEstimated": False,
                            "links": [{"kind": "session", "id": shared_grok_sid}],
                            "outputIds": [],
                        }
                    ],
                },
                {
                    "id": "t3:root-1",
                    "title": "T3 Flow",
                    "project": "p",
                    "projectRoot": None,
                    "harness": "t3",
                    "kind": "flat",
                    "startedAt": to_iso_utc(now_ts - 7200),
                    "updatedAt": to_iso_utc(now_ts - 3600),
                    "goal": None,
                    "decision": None,
                    "workstreams": [],
                    "posts": [],
                    "outputs": [],
                    "links": [],
                    "agents": [
                        {
                            "id": "root-1",
                            "parent": None,
                            "name": "Root",
                            "role": "root",
                            "lab": "google",
                            "model": "gemini-4-neon",
                            "state": "done",
                            "startedAt": to_iso_utc(now_ts - 7200),
                            "endedAt": to_iso_utc(now_ts - 3600),
                            "durS": 100,
                            "tokens": {"in": 500_000, "cache": 1_000_000, "out": 50_000},
                            "costUsd": 1.70,
                            "costEstimated": True,
                            "links": [{"kind": "session", "id": shared_agy_sid}],
                            "outputIds": [],
                        }
                    ],
                },
                {
                    "id": f"agy:{shared_agy_sid}",
                    "title": "AGY Swarm",
                    "project": "p",
                    "projectRoot": None,
                    "harness": "agy",
                    "kind": "swarm",
                    "startedAt": to_iso_utc(now_ts - 7200),
                    "updatedAt": to_iso_utc(now_ts - 3600),
                    "goal": None,
                    "decision": None,
                    "workstreams": [],
                    "posts": [],
                    "outputs": [],
                    "links": [],
                    "agents": [
                        {
                            "id": "coordinator",
                            "parent": None,
                            "name": "coordinator",
                            "role": "coordinator",
                            "lab": "google",
                            "model": "gemini-4-neon",
                            "state": "done",
                            "startedAt": to_iso_utc(now_ts - 7200),
                            "endedAt": to_iso_utc(now_ts - 3600),
                            "durS": 100,
                            "tokens": {"in": 500_000, "cache": 1_000_000, "out": 50_000},
                            "costUsd": 1.70,
                            "costEstimated": True,
                            "links": [{"kind": "session", "id": shared_agy_sid}],
                            "outputIds": [],
                        }
                    ],
                },
            ]
            with reg._lock:
                reg._cached_flows = flows
                reg._flow_by_id = {f["id"]: f for f in flows}
                reg._has_snapshot = True
                reg._cached_at = 1e12

            # Board list_flows still keeps all 4 flows visible
            self.assertEqual(len(reg.list_flows(force=False)), 4)

            stats = reg.get_stats(scope="all", rng="all")
            self.assertEqual(stats["dupRows"], 2)
            self.assertEqual(stats["flowsCount"], 4)
            self.assertEqual(stats["agentsCount"], 4)
            self.assertEqual(stats["ratedAgents"], 2)
            self.assertAlmostEqual(stats["costUsd"], 4.20, places=2)
            # Grok had costEstimated=False and was preferred over delegate's duplicate
            grok_row = next(m for m in stats["byModel"] if m["model"] == "grok-4.7-build")
            self.assertFalse(grok_row["costEstimated"])
            self.assertAlmostEqual(grok_row["costUsd"], 2.50, places=2)

    def test_range_slicing_24h_7d_all_and_flow_enrichment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Align now_ts to exact hour + 1800s (so hi_ts = now_ts - 1800, window_end = now_ts + 1800)
            hi_ts = int(datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc).timestamp())
            now_ts = float(hi_ts + 1800)
            w24_start = float(hi_ts - 23 * 3600)

            reg = self._build_registry(root, now_ts)
            flows = [
                {
                    "id": "t3:range-test",
                    "title": "Range Test Flow",
                    "project": "p",
                    "projectRoot": None,
                    "harness": "t3",
                    "kind": "flat",
                    "startedAt": to_iso_utc(hi_ts - 10 * 86400),
                    "updatedAt": to_iso_utc(now_ts),
                    "goal": None,
                    "decision": None,
                    "workstreams": [],
                    "posts": [],
                    "outputs": [],
                    "links": [],
                    "agents": [
                        {
                            "id": "a-old",
                            "parent": None,
                            "name": "Old Agent",
                            "role": "worker",
                            "lab": "openai",
                            "model": "grok-4.7-build",  # intentionally different lab test
                            "state": "done",
                            "startedAt": to_iso_utc(hi_ts - 10 * 86400),
                            "endedAt": to_iso_utc(hi_ts - 10 * 86400 + 3600),
                            "durS": 3600,
                            "tokens": {"in": 1_000_000, "cache": 0, "out": 0},
                            "costUsd": 10.0,
                            "costEstimated": True,
                            "links": [],
                            "outputIds": [],
                        },
                        {
                            "id": "a-week",
                            "parent": None,
                            "name": "Week Agent",
                            "role": "worker",
                            "lab": "anthropic",
                            "model": "claude-opus-5",
                            "state": "done",
                            "startedAt": to_iso_utc(hi_ts - 3 * 86400),
                            "endedAt": to_iso_utc(hi_ts - 3 * 86400 + 3600),
                            "durS": 3600,
                            "tokens": {"in": 1_000_000, "cache": 0, "out": 0},
                            "costUsd": 5.0,
                            "costEstimated": True,
                            "links": [],
                            "outputIds": [],
                        },
                        {
                            "id": "a-straddle",
                            "parent": None,
                            "name": "Straddle Agent",
                            "role": "worker",
                            "lab": "google",
                            "model": "gemini-4-neon",
                            # 1h before w24_start to 1h after w24_start -> exactly 50% in 24h!
                            "startedAt": to_iso_utc(w24_start - 3600),
                            "endedAt": to_iso_utc(w24_start + 3600),
                            "durS": 7200,
                            "tokens": {"in": 1_000_000, "cache": 2_000_000, "out": 100_000},
                            "costUsd": 4.0,
                            "costEstimated": True,
                            "links": [],
                            "outputIds": [],
                        },
                        {
                            "id": "a-recent",
                            "parent": None,
                            "name": "Recent Agent",
                            "role": "worker",
                            "lab": "google",
                            "model": "gemini-4-neon",
                            "state": "done",
                            "startedAt": to_iso_utc(hi_ts - 3600),
                            "endedAt": to_iso_utc(hi_ts),
                            "durS": 3600,
                            "tokens": {"in": 500_000, "cache": 0, "out": 0},
                            "costUsd": 1.0,
                            "costEstimated": True,
                            "links": [],
                            "outputIds": [],
                        },
                    ],
                }
            ]
            with reg._lock:
                reg._cached_flows = flows
                reg._flow_by_id = {f["id"]: f for f in flows}
                reg._has_snapshot = True
                reg._cached_at = 1e12

            # rng = "all" -> $10 + $5 + $4 + $1 = $20.00
            st_all = reg.get_stats(scope="all", rng="all")
            self.assertAlmostEqual(st_all["costUsd"], 20.0, places=2)
            self.assertEqual(st_all["agentsCount"], 4)
            self.assertEqual(len(st_all["topAgents"][0]), 14)
            self.assertEqual(st_all["topAgents"][0][12:], ["t3:range-test", "a-old"])

            # rng = "7d" -> excludes a-old ($10), keeps $5 + $4 + $1 = $10.00
            st_7d = reg.get_stats(scope="all", rng="7d")
            self.assertAlmostEqual(st_7d["costUsd"], 10.0, places=2)
            self.assertEqual(st_7d["agentsCount"], 3)
            self.assertEqual(len(st_7d["byModel"]), 2)

            # rng = "24h" -> excludes a-old and a-week, pro-rates a-straddle 50% ($2.0) + a-recent ($1.0) = $3.00
            st_24h = reg.get_stats(scope="all", rng="24h")
            self.assertAlmostEqual(st_24h["costUsd"], 3.0, places=2)
            self.assertEqual(st_24h["agentsCount"], 2)
            self.assertEqual(len(st_24h["byModel"]), 1)
            self.assertEqual(st_24h["byModel"][0]["model"], "gemini-4-neon")
            self.assertEqual(st_24h["byModel"][0]["in"], 1_000_000)  # 500k + 500k
            self.assertEqual(st_24h["byModel"][0]["cache"], 1_000_000)  # 50% of 2M
            self.assertEqual(st_24h["byModel"][0]["durS"], 7200)  # 3600 + 3600
            # Verify costByType matches costUsd
            cbt_sum = sum(st_24h["costByType"].values())
            self.assertAlmostEqual(cbt_sum, st_24h["costUsd"], places=2)
            # Verify buckets sum matches costUsd
            bucket_cost = sum(
                vals[0]
                for b in st_24h["buckets"]["b"]
                for vals in b["l"].values()
            )
            self.assertAlmostEqual(bucket_cost, st_24h["costUsd"], places=2)

            # Lab filter
            st_anth = reg.get_stats(scope="all", rng="all", lab="anthropic")
            self.assertAlmostEqual(st_anth["costUsd"], 5.0, places=2)
            self.assertEqual(st_anth["agentsCount"], 1)

            # get_flow enriches per-agent costSplit, cacheSavingsUsd, costEstimated, and timeline
            enriched = reg.get_flow("t3:range-test")
            self.assertIsNotNone(enriched)
            self.assertIsNotNone(enriched["timeline"])
            straddle_agent = next(a for a in enriched["agents"] if a["id"] == "a-straddle")
            self.assertIn("costSplit", straddle_agent)
            self.assertIn("cacheSavingsUsd", straddle_agent)
            self.assertTrue(straddle_agent["costEstimated"])
            self.assertAlmostEqual(
                sum(straddle_agent["costSplit"].values()),
                4.0,
                places=2,
            )


class AgyCliTranscriptJoinTests(unittest.TestCase):
    def test_joins_agy_transcripts_across_committee_orchestrate_and_delegate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "ws"
            ws.mkdir()
            brain = root / "gemini" / "antigravity-cli" / "brain"

            sid_com1 = "11111111-1111-1111-1111-111111111111"
            sid_com2 = "22222222-2222-2222-2222-222222222222"
            sid_com3 = "33333333-3333-3333-3333-333333333333"
            sid_orch = "44444444-4444-4444-4444-444444444444"
            sid_del = "55555555-5555-5555-5555-555555555555"

            for sid, model_name in (
                (sid_com1, "gemini-4-neon-high"),
                (sid_com2, "gemini-4-neon-high"),
                (sid_com3, "gemini-4-neon-high"),
                (sid_orch, "gemini-4-neon"),
                (sid_del, "gemini-4-neon-high"),
            ):
                _write_agy_transcript(
                    brain,
                    sid,
                    [
                        {
                            "type": "PLANNER_RESPONSE",
                            "model": model_name,
                            "input_tokens": 100_000,
                            "cache_read_tokens": 200_000,
                            "output_tokens": 10_000,
                        }
                    ],
                )

            rates_path = root / "rates.json"
            _write_rates_file(
                rates_path,
                {
                    "gemini/gemini-4-neon": {
                        "input_cost_per_token": 3e-6,
                        "output_cost_per_token": 1.5e-5,
                        "cache_read_input_token_cost": 1.5e-7,
                    }
                },
            )
            rt = RateTable(rates_path, override_paths=[])

            # Committee run with 3 Google seats:
            # seat 1 has cost_per_task (keeps 3.74, gets tokens)
            # seat 2 has explicit cost_usd in .out.json (keeps 1.25 exact, gets tokens)
            # seat 3 has neither (estimates costUsd from RateTable, gets tokens)
            c_dir = root / "committee"
            c_run = c_dir / "agy-com"
            c_run.mkdir(parents=True)
            (c_run / "committee.resolved.json").write_text(
                json.dumps(
                    {
                        "workspace": str(ws),
                        "question": "Check AGY join",
                        "members": [
                            {"id": "committee-1", "seat": 1, "model": "gemini-4-neon-high", "cost_per_task": 3.74, "route": {"cli": "agy"}},
                            {"id": "committee-2", "seat": 2, "model": "gemini-4-neon-high", "route": {"cli": "agy"}},
                            {"id": "committee-3", "seat": 3, "model": "gemini-4-neon-high", "route": {"cli": "agy"}},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (c_run / "committee-1.out.json").write_text(
                json.dumps({"status": "success", "cli": "agy", "session_id": sid_com1, "response": "**PASS**"}),
                encoding="utf-8",
            )
            (c_run / "committee-2.out.json").write_text(
                json.dumps({"status": "success", "cli": "agy", "session_id": sid_com2, "cost_usd": 1.25, "response": "**PASS**"}),
                encoding="utf-8",
            )
            (c_run / "committee-3.out.json").write_text(
                json.dumps({"status": "success", "cli": "agy", "session_id": sid_com3, "response": "**PASS**"}),
                encoding="utf-8",
            )

            # Orchestrate run
            o_dir = root / "orchestrate"
            o_run = o_dir / "agy-orch"
            o_run.mkdir(parents=True)
            (o_run / "plan.resolved.json").write_text(
                json.dumps(
                    {
                        "goal": "Run orch",
                        "workspace": str(ws),
                        "tasks": [{"id": "t1", "title": "AGY task", "model": "gemini-4-neon", "route": {"cli": "agy"}}],
                    }
                ),
                encoding="utf-8",
            )
            (o_run / "t1.out.json").write_text(
                json.dumps({"status": "success", "cli": "agy", "session_id": sid_orch, "response": "Done"}),
                encoding="utf-8",
            )

            # Agent-delegate ledger (>= 2 runs in cluster)
            ledger = root / "sessions.jsonl"
            ledger.write_text(
                "\n".join(
                    [
                        json.dumps({"ts": 1795600000, "session_id": sid_del, "cli": "agy", "model": "gemini-4-neon-high", "role": "worker"}),
                        json.dumps({"ts": 1795600010, "session_id": "other-sid", "cli": "claude", "model": "claude-opus-5", "role": "reviewer"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            flows = discover_harness_runs(
                committee_dir=c_dir,
                orchestrate_dir=o_dir,
                sessions_ledger=ledger,
                grok_sessions_root=root / "no-grok",
                brain_roots=[brain],
                rate_table=rt,
                usage_index=UsageIndex(root / "no-usage.json"),
                now_ts=1795600500.0,
            )

            expected_tok = {"in": 100_000, "cache": 200_000, "out": 10_000}
            expected_est_cost = round(100_000 * 3e-6 + 200_000 * 1.5e-7 + 10_000 * 1.5e-5, 4)  # 0.48

            com = next(f for f in flows if f["id"] == "committee:agy-com")
            c_agents = {a["id"]: a for a in com["agents"]}
            self.assertEqual(c_agents["committee-1"]["tokens"], expected_tok)
            self.assertAlmostEqual(c_agents["committee-1"]["costUsd"], 3.74, places=2)
            self.assertTrue(c_agents["committee-1"]["costEstimated"])

            self.assertEqual(c_agents["committee-2"]["tokens"], expected_tok)
            self.assertAlmostEqual(c_agents["committee-2"]["costUsd"], 1.25, places=2)
            self.assertFalse(c_agents["committee-2"]["costEstimated"])

            self.assertEqual(c_agents["committee-3"]["tokens"], expected_tok)
            self.assertAlmostEqual(c_agents["committee-3"]["costUsd"], expected_est_cost, places=4)
            self.assertTrue(c_agents["committee-3"]["costEstimated"])

            orch = next(f for f in flows if f["id"] == "orchestrate:agy-orch")
            self.assertEqual(orch["agents"][0]["tokens"], expected_tok)
            self.assertAlmostEqual(orch["agents"][0]["costUsd"], expected_est_cost, places=4)

            del_flow = next(f for f in flows if f["harness"] == "delegate")
            agy_del = next(a for a in del_flow["agents"] if a["lab"] == "google")
            self.assertEqual(agy_del["tokens"], expected_tok)
            self.assertAlmostEqual(agy_del["costUsd"], expected_est_cost, places=4)


class LayeredRatesAndUpdateScriptTests(unittest.TestCase):
    def test_layered_override_precedence_fallback_and_cost_breakdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fallback_path = root / "litellm-rates.json"
            _write_rates_file(
                fallback_path,
                {
                    "gemini/gemini-4-neon": {
                        "input_cost_per_token": 2e-6,
                        "output_cost_per_token": 8e-6,
                        "cache_read_input_token_cost": 2e-7,
                    }
                },
            )
            repo_ov = root / "repo_override.json"
            user_ov = root / "user_override.json"
            env_ov = root / "env_override.json"

            repo_ov.write_text(
                json.dumps(
                    {
                        "models": {
                            "gemini-4-neon": {
                                "input_cost_per_token": 3e-6,
                                "output_cost_per_token": 1.2e-5,
                            },
                            "custom-model-a": {
                                "input_cost_per_token": 1e-6,
                                "output_cost_per_token": 4e-6,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            user_ov.write_text(
                json.dumps(
                    {
                        "models": {
                            "gemini-4-neon": {
                                "input_cost_per_token": 4e-6,
                                "output_cost_per_token": 1.6e-5,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env_ov.write_text(
                json.dumps(
                    {
                        "models": {
                            "gemini-pro-orchestrator-neon": {
                                "input_cost_per_token": 5e-6,
                                "output_cost_per_token": 2.0e-5,
                                "cache_read_input_token_cost": 5e-7,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            # Primary path missing -> uses fallback_path + layered overrides [env, user, repo]
            rt = RateTable(
                root / "missing-primary.json",
                fallback_path=fallback_path,
                override_paths=[env_ov, user_ov, repo_ov],
            )
            self.assertAlmostEqual(rt.lookup("custom-model-a")["input_cost_per_token"], 1e-6)
            # user_ov beats repo_ov beats fallback_path
            self.assertAlmostEqual(rt.lookup("gemini-4-neon")["input_cost_per_token"], 4e-6)
            # env_ov direct override on aliased slug beats alias fallback
            self.assertAlmostEqual(
                rt.lookup("gemini-pro-orchestrator-neon")["input_cost_per_token"], 5e-6
            )

            # cost_breakdown unscaled vs scaled
            tok = {"in": 1000, "cache": 2000, "out": 500}
            split_unscaled, sav = rt.cost_breakdown("gemini-pro-orchestrator-neon", tok)
            # uncached=1000*5e-6=0.005, cached=2000*5e-7=0.001, out=500*2e-5=0.01 -> total=0.016
            self.assertEqual(split_unscaled, {"cached": 0.001, "uncached": 0.005, "output": 0.01})
            # savings = 2000 * (5e-6 - 5e-7) = 0.009
            self.assertAlmostEqual(sav, 0.009, places=4)

            split_scaled, _ = rt.cost_breakdown("gemini-pro-orchestrator-neon", tok, cost_usd=0.032)
            self.assertEqual(split_scaled, {"cached": 0.002, "uncached": 0.01, "output": 0.02})

    def test_update_rates_sync_and_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / ".config" / "swarm-board" / "litellm-rates.json"
            sample_remote = {
                "gpt-6.2-sol": {
                    "input_cost_per_token": 1.5e-6,
                    "output_cost_per_token": 6e-6,
                }
            }

            class _FakeResp:
                def __init__(self, data: bytes) -> None:
                    self._bio = io.BytesIO(data)

                def read(self) -> bytes:
                    return self._bio.read()

                def __enter__(self) -> _FakeResp:
                    return self

                def __exit__(self, *args: object) -> None:
                    return None

            with patch(
                "scripts.update_rates.urllib.request.urlopen",
                return_value=_FakeResp(json.dumps(sample_remote).encode("utf-8")),
            ):
                sync_res = update_rates.sync_litellm_rates(
                    url="https://example.invalid/rates.json",
                    dest=dest,
                )

            self.assertEqual(sync_res["modelsCount"], 1)
            self.assertTrue(dest.is_file())
            written = json.loads(dest.read_text(encoding="utf-8"))
            self.assertIn("fetchedAtMs", written)
            self.assertEqual(written["document"], sample_remote)

            dropin_dir = root / "dropin"
            dropin_dir.mkdir()
            (dropin_dir / "sample.json").write_text(
                json.dumps(
                    {
                        "id": "dropin:rates-check",
                        "title": "Rates Check Dropin",
                        "agents": [
                            {"id": "a1", "name": "a1", "model": "gpt-6.2-sol", "state": "done"},
                            {"id": "a2", "name": "a2", "model": "unverified-alias-xyz", "state": "done"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            rt = RateTable(root / "no-primary.json", fallback_path=dest, override_paths=[])
            reg = BoardRegistry(
                {
                    "committee_dir": root / "no-c",
                    "orchestrate_dir": root / "no-o",
                    "sessions_ledger": root / "no-l",
                    "grok_sessions_root": root / "no-g",
                    "codex_db": root / "no-cx",
                    "claude_root": root / "no-cl",
                    "brain_roots": [],
                    "statev2_db": root / "no-t3",
                    "dropin_dir": dropin_dir,
                },
                cache_ttl_s=0.0,
            )

            report = update_rates.check_rates(registry=reg, rate_table=rt)
            self.assertEqual([m["model"] for m in report["ratedModels"]], ["gpt-6.2-sol"])
            self.assertEqual([m["model"] for m in report["missingModels"]], ["unverified-alias-xyz"])


class RawFileAndEnvIdTests(unittest.TestCase):
    def test_get_raw_file_path_containment_and_secrets_and_env_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "project"
            (ws / "docs").mkdir(parents=True)
            (ws / ".git").mkdir()
            (ws / "secrets").mkdir()
            ok_doc = ws / "docs" / "guide.md"
            ok_doc.write_text("# Guide\n", encoding="utf-8")
            (ws / ".env").write_text("SECRET=1\n", encoding="utf-8")
            (ws / ".env.local").write_text("SECRET=2\n", encoding="utf-8")
            (ws / "auth.json").write_text("{}\n", encoding="utf-8")
            (ws / "server.key").write_text("KEY\n", encoding="utf-8")
            (ws / ".git" / "config").write_text("[core]\n", encoding="utf-8")
            (ws / "secrets" / "token.txt").write_text("tok\n", encoding="utf-8")

            outside = root / "outside"
            outside.mkdir()
            outside_file = outside / "secret.txt"
            outside_file.write_text("nope\n", encoding="utf-8")
            os.symlink(outside_file, ws / "escape.txt")

            extra_dir = root / "extra"
            extra_dir.mkdir()
            extra_file = extra_dir / "artifact.txt"
            extra_file.write_text("ok\n", encoding="utf-8")

            c_dir = root / "committee"
            c_run = c_dir / "c-demo"
            c_run.mkdir(parents=True)
            (c_run / "committee.resolved.json").write_text(
                json.dumps(
                    {
                        "workspace": str(ws),
                        "members": [{"id": "committee-1", "seat": 1, "model": "claude-opus-5", "route": {"cli": "claude"}}],
                    }
                ),
                encoding="utf-8",
            )
            (c_run / "committee-1.md").write_text("brief\n", encoding="utf-8")
            (c_run / "committee-1.out.json").write_text(
                json.dumps({"status": "success", "response": "**PASS**"}),
                encoding="utf-8",
            )

            t3_ud = root / "t3-userdata"
            t3_ud.mkdir()
            (t3_ud / "environment-id").write_text("env-from-file\n", encoding="utf-8")

            with patch.dict(os.environ, {"T3_USERDATA_DIR": str(t3_ud)}):
                reg = BoardRegistry(
                    {
                        "committee_dir": c_dir,
                        "orchestrate_dir": root / "no-o",
                        "sessions_ledger": root / "no-l",
                        "grok_sessions_root": root / "no-g",
                        "codex_db": root / "no-cx",
                        "claude_root": root / "no-cl",
                        "brain_roots": [],
                        "statev2_db": root / "no-t3",
                        "extra_allowed_roots": [str(extra_dir)],
                    },
                    cache_ttl_s=0.0,
                )
                self.assertEqual(reg.get_env_id(), "env-from-file")

            reg_explicit = BoardRegistry({"env_id": "env-explicit"})
            self.assertEqual(reg_explicit.get_env_id(), "env-explicit")

            reg.list_flows(force=True)
            fid = "committee:c-demo"

            # Allowed inside projectRoot, committee_dir/<run>, and extra_allowed_roots
            res_rel = reg.get_raw_file_path(fid, "docs/guide.md")
            self.assertTrue(res_rel["ok"])
            self.assertEqual(res_rel["path"], os.path.realpath(ok_doc))

            res_abs = reg.get_raw_file_path(fid, str(c_run / "committee-1.md"))
            self.assertTrue(res_abs["ok"])
            self.assertEqual(res_abs["path"], os.path.realpath(c_run / "committee-1.md"))

            res_extra = reg.get_raw_file_path(fid, str(extra_file))
            self.assertTrue(res_extra["ok"])
            self.assertEqual(res_extra["path"], os.path.realpath(extra_file))

            # Denied: outside, traversal, symlink escape, directory, missing, unknown flow, and secrets
            for bad in (
                str(outside_file),
                "../outside/secret.txt",
                str(ws / "escape.txt"),
                str(ws / "docs"),
                "docs/does-not-exist.md",
                ".env",
                ".env.local",
                "auth.json",
                "server.key",
                ".git/config",
                "secrets/token.txt",
            ):
                r = reg.get_raw_file_path(fid, bad)
                self.assertFalse(r["ok"], f"Expected denial for {bad}")

            self.assertFalse(reg.get_raw_file_path("unknown:flow", "docs/guide.md")["ok"])


if __name__ == "__main__":
    unittest.main()
