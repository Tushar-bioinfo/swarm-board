"""Isolated unit tests for normalized schema and all cross-harness read-only adapters."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from swarm_board.adapters import BoardRegistry
from swarm_board.adapters.agy_swarms import (
    discover_swarms,
    load_swarm_output,
    load_transcript_messages,
)
from swarm_board.adapters.cli_subagents import (
    discover_cli_flows,
    load_grok_chat_history,
)
from swarm_board.adapters.harness_runs import discover_harness_runs
from swarm_board.adapters.t3_delegations import (
    discover_t3_flows,
    load_subagent_detail,
    load_thread_messages,
)
from swarm_board.schema import (
    VALID_LINK_KINDS,
    VALID_STATES,
    RateTable,
    UsageIndex,
    make_session_link,
    normalize_lab,
    normalize_state,
)


def _assert_flow_invariants(test: unittest.TestCase, flow: dict) -> None:
    for key in (
        "id",
        "title",
        "project",
        "projectRoot",
        "harness",
        "kind",
        "startedAt",
        "updatedAt",
        "goal",
        "decision",
        "workstreams",
        "agents",
        "posts",
        "outputs",
        "links",
    ):
        test.assertIn(key, flow, f"Missing flow key: {key}")

    agents = flow["agents"]
    agent_ids = {a["id"] for a in agents}
    output_ids = {o["id"] for o in flow["outputs"]}

    for lk in flow["links"]:
        test.assertIn(lk["kind"], VALID_LINK_KINDS)

    for agent in agents:
        for akey in (
            "id",
            "parent",
            "name",
            "role",
            "workstream",
            "lab",
            "model",
            "state",
            "startedAt",
            "endedAt",
            "durS",
            "tokens",
            "costUsd",
            "errors",
            "trace",
            "last",
            "result",
            "weight",
            "verdict",
            "links",
            "outputIds",
        ):
            test.assertIn(akey, agent, f"Missing agent key: {akey}")
        test.assertIn(agent["state"], VALID_STATES)
        test.assertIn(agent["lab"], {"google", "anthropic", "openai", "xai", "t3", "other"})
        if agent["parent"] is not None:
            test.assertIn(agent["parent"], agent_ids, f"Orphan parent {agent['parent']}")
        if agent["tokens"] is not None:
            test.assertEqual(set(agent["tokens"].keys()), {"in", "cache", "out"})
        if agent["costUsd"] is not None:
            test.assertGreaterEqual(agent["costUsd"], 0.0)
        for oid in agent["outputIds"]:
            test.assertIn(oid, output_ids, f"Missing referenced output {oid}")
        for lk in agent["links"]:
            test.assertIn(lk["kind"], VALID_LINK_KINDS)
            cmd = (lk.get("cmd") or "").lower()
            for forbidden in ("agy ", "grok ", "cursor ", "muse "):
                test.assertFalse(cmd.startswith(forbidden), f"Forbidden resume cmd: {cmd}")


def _create_mock_t3_db(db_path: Path) -> None:
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


class SchemaAndRatesTests(unittest.TestCase):
    def test_normalize_lab_and_state_and_session_links(self) -> None:
        self.assertEqual(normalize_lab("gemini-4-neon", "antigravity"), "google")
        self.assertEqual(normalize_lab("claude-opus-5-5-medium", "anthropic"), "anthropic")
        self.assertEqual(normalize_lab("gpt-6-2-codex", "openai"), "openai")
        self.assertEqual(normalize_lab("o3-mini"), "openai")
        self.assertEqual(normalize_lab("grok-4.7-build", "grok"), "xai")
        self.assertEqual(normalize_lab(None, "t3"), "t3")

        self.assertEqual(normalize_state("working"), "running")
        self.assertEqual(normalize_state("completed"), "done")
        self.assertEqual(normalize_state("failed"), "error")
        self.assertEqual(normalize_state("cancelled"), "interrupted")
        self.assertEqual(normalize_state("approval_required"), "waiting")
        self.assertEqual(normalize_state("pending"), "queued")

        cl_link = make_session_link("claude", "sess-claude-1")
        self.assertEqual(cl_link["kind"], "resume")
        self.assertEqual(cl_link["cmd"], "claude --resume sess-claude-1")

        cx_link = make_session_link("codex", "sess-codex-1")
        self.assertEqual(cx_link["kind"], "resume")
        self.assertEqual(cx_link["cmd"], "codex resume sess-codex-1")

        for cli in ("agy", "grok", "cursor", "muse"):
            lk = make_session_link(cli, f"sid-{cli}")
            self.assertEqual(lk["kind"], "session")
            self.assertIsNone(lk["cmd"])
            self.assertEqual(lk["id"], f"sid-{cli}")

    def test_rate_table_and_usage_index_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rates_path = root / "rates.json"
            rates_path.write_text(
                json.dumps(
                    {
                        "document": {
                            "gemini/gemini-4-neon": {
                                "input_cost_per_token": 1e-6,
                                "output_cost_per_token": 4e-6,
                                "cache_read_input_token_cost": 1e-7,
                                "cache_creation_input_token_cost": 1.25e-6,
                            },
                            "anthropic.claude-opus-5": {
                                "input_cost_per_token": 5e-6,
                                "output_cost_per_token": 2e-5,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            rt = RateTable(rates_path)
            # Alias gemini-pro-orchestrator-neon -> gemini-4-neon & effort suffix strip
            c1 = rt.estimate_cost_usd(
                "gemini-pro-orchestrator-neon",
                {"in": 1000, "cache": 2000, "out": 500},
            )
            self.assertAlmostEqual(c1 or 0.0, 0.0032, places=4)

            c2 = rt.estimate_cost_usd(
                "claude-opus-5-medium",
                {"in": 1000, "cache": 1000, "out": 100},
                cache_read=600,
                cache_write=400,
            )
            expected_c2 = round(1000 * 5e-6 + 100 * 2e-5 + 600 * (5e-6 * 0.1) + 400 * (5e-6 * 1.25), 4)
            self.assertAlmostEqual(c2 or 0.0, expected_c2, places=4)

            # Mutate rates file and bump mtime
            rates_path.write_text(
                json.dumps(
                    {
                        "document": {
                            "gemini/gemini-4-neon": {
                                "input_cost_per_token": 2e-6,
                                "output_cost_per_token": 8e-6,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            st = rates_path.stat()
            os.utime(rates_path, (st.st_mtime + 10, st.st_mtime + 10))
            c1_new = rt.estimate_cost_usd("gemini-4-neon-high", {"in": 1000, "cache": 0, "out": 500})
            self.assertAlmostEqual(c1_new or 0.0, 0.006, places=4)

            usage_path = root / "usage.json"
            usage_path.write_text(
                json.dumps(
                    {
                        "files": {
                            "/home/u/.codex/sessions/11111111-2222-3333-4444-555555555555.jsonl": {
                                "cs": {"sessionId": "sid-1", "model": "gpt-6"},
                                "r": [[0, 0, 0, 100, 50, 10, 25, 0, 0, 0.12]],
                            },
                            "/home/u/.codex/sessions/part2.jsonl": {
                                "cs": {"sessionId": "sid-1", "model": "gpt-6"},
                                "r": [[0, 0, 0, 200, 10, 0, 75, 0, 0, 0.08]],
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            ui = UsageIndex(usage_path)
            rec = ui.get("sid-1")
            self.assertIsNotNone(rec)
            self.assertEqual(rec["in"], 300)
            self.assertEqual(rec["cache"], 70)
            self.assertEqual(rec["out"], 100)
            self.assertAlmostEqual(rec["costUsd"], 0.2, places=4)

            usage_path.write_text(
                json.dumps(
                    {
                        "files": {
                            "/tmp/sid-2.jsonl": {
                                "cs": {"sessionId": "sid-2", "model": "claude"},
                                "r": [[0, 0, 0, 999, 0, 0, 1, 0, 0, 0.5]],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            st_u = usage_path.stat()
            os.utime(usage_path, (st_u.st_mtime + 10, st_u.st_mtime + 10))
            self.assertIsNone(ui.get("sid-1"))
            self.assertEqual(ui.get("sid-2")["in"], 999)


class AgySwarmsAdapterTests(unittest.TestCase):
    def test_agy_swarms_full_plan_errors_oversized_post_and_remote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proj = root / "demo-proj"
            (proj / ".git").mkdir(parents=True)
            (proj / "docs").mkdir()
            (proj / "docs" / "REPORT.md").write_text("# Report\nline 2\n", encoding="utf-8")
            # Secret file should never appear in outputs even if mentioned
            (proj / ".env").write_text("API_KEY=secret\n", encoding="utf-8")

            rates_path = root / "rates.json"
            rates_path.write_text(
                json.dumps(
                    {
                        "document": {
                            "gemini/gemini-4-neon": {
                                "input_cost_per_token": 1e-6,
                                "output_cost_per_token": 2e-6,
                                "cache_read_input_token_cost": 1e-7,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            rt = RateTable(rates_path)

            brain = root / "brain"
            coord_id = "11111111-1111-1111-1111-111111111111"
            worker_id = "22222222-2222-2222-2222-222222222222"
            mon_id = "33333333-3333-3333-3333-333333333333"
            noplan_id = "44444444-4444-4444-4444-444444444444"
            remote_id = "55555555-5555-5555-5555-555555555555"

            # Swarm 1: with plan.json, errors, board posts (>64 KB post)
            s1_dir = brain / coord_id / ".system_generated" / "swarm"
            s1_dir.mkdir(parents=True)
            (s1_dir / "swarm.json").write_text(
                json.dumps(
                    {
                        "swarm_id": coord_id,
                        "backend": "local",
                        "created_at": "2026-11-25T10:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            (s1_dir / "members.json").write_text(
                json.dumps(
                    {
                        "title": "Demo Swarm",
                        "members": {
                            "coordinator": {
                                "conversation_id": coord_id,
                                "role": "coordinator",
                                "wake_mode": "working",
                                "applied_model": "gemini-4-neon-medium",
                            },
                            "worker-a": {
                                "conversation_id": worker_id,
                                "role": "worker",
                                "wake_mode": "mentions",
                                "applied_model": "gemini-4-neon-medium",
                            },
                            "monitor": {
                                "conversation_id": mon_id,
                                "role": "monitor",
                                "wake_mode": "mentions",
                                "killed": True,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            (s1_dir / "plan.json").write_text(
                json.dumps(
                    {
                        "goal": {"title": "Ship Feature", "line": "Test everything"},
                        "threads": [{"id": "ws-build", "title": "Build", "goal": "Write files", "tasks": ["worker-a"]}],
                    }
                ),
                encoding="utf-8",
            )
            big_text = "Large summary line\n" + ("X" * 70_000)
            (s1_dir / "board.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"seq": 1, "agent": "coordinator", "kind": "brief", "time": "2026-11-25 10:00:05", "text": "@worker-a please write docs/REPORT.md"}),
                        json.dumps({"seq": 2, "agent": "worker-a", "time": "2026-11-25 10:02:00", "text": f"Wrote `docs/REPORT.md` see #1 @coordinator\n{big_text}"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            # Member transcripts
            c_logs = brain / coord_id / ".system_generated" / "logs"
            c_logs.mkdir(parents=True)
            (c_logs / "transcript.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"type": "SYSTEM_MESSAGE", "created_at": "2026-11-25T10:00:01Z", "content": f"Active Workspaces:\n- {proj}\n"}),
                        json.dumps({"type": "PLANNER_RESPONSE", "status": "DONE", "model": "gemini-4-neon", "created_at": "2026-11-25T10:00:10Z", "input_tokens": 1000, "cache_read_tokens": 500, "output_tokens": 200, "content": "Coordinating"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            w_logs = brain / worker_id / ".system_generated" / "logs"
            w_logs.mkdir(parents=True)
            (w_logs / "transcript.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"type": "PLANNER_RESPONSE", "status": "DONE", "model": "gemini-4-neon", "created_at": "2026-11-25T10:01:00Z", "input_tokens": 500, "output_tokens": 100, "tool_calls": [{"name": "write_file", "args": {"Path": str(proj / "docs" / "REPORT.md")}}]}),
                        json.dumps({"type": "GENERIC", "status": "ERROR", "error": "permission check failed: timed out after 60s", "created_at": "2026-11-25T10:01:30Z", "content": "Permission check failed"}),
                        json.dumps({"type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:01:40Z", "tool_calls": [{"name": "write_file", "args": {"Path": str(proj / ".env")}}, {"name": "swarm_wait", "args": "{}"}]}),
                        json.dumps({"type": "GENERIC", "status": "DONE", "created_at": "2026-11-25T10:01:45Z", "content": "swarm_wait completed"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            # Swarm 2: no plan.json, stale running member
            s2_dir = brain / noplan_id / ".system_generated" / "swarm"
            s2_dir.mkdir(parents=True)
            (s2_dir / "swarm.json").write_text(json.dumps({"swarm_id": noplan_id, "backend": "local", "created_at": "2026-11-25T08:00:00Z"}), encoding="utf-8")
            (s2_dir / "members.json").write_text(json.dumps({"members": {"solo": {"conversation_id": noplan_id, "role": "worker", "wake_mode": "working"}}}), encoding="utf-8")

            # Swarm 3: remote fallback
            s3_dir = brain / remote_id / ".system_generated" / "swarm"
            s3_dir.mkdir(parents=True)
            (s3_dir / "swarm.json").write_text(json.dumps({"swarm_id": remote_id, "backend": "remote", "title": "Remote Swarm", "created_at": "2026-11-25T09:00:00Z"}), encoding="utf-8")

            now_ts = 1795610000.0
            os.utime(s2_dir / "members.json", (now_ts - 1200.0, now_ts - 1200.0))
            flows = discover_swarms(brain_roots=[brain], rate_table=rt, now_ts=now_ts)
            self.assertEqual(len(flows), 3)
            for f in flows:
                _assert_flow_invariants(self, f)

            demo = next(f for f in flows if f["id"] == f"agy:{coord_id}")
            self.assertEqual(demo["title"], "Demo Swarm")
            self.assertEqual(demo["projectRoot"], str(proj))
            self.assertEqual(demo["workstreams"][0]["id"], "ws-build")

            by_name = {a["id"]: a for a in demo["agents"]}
            self.assertEqual(by_name["coordinator"]["state"], "done")
            self.assertEqual(by_name["worker-a"]["state"], "waiting")
            self.assertEqual(by_name["worker-a"]["workstream"], "ws-build")
            self.assertEqual(by_name["worker-a"]["errors"][0]["kind"], "perm_timeout")
            self.assertEqual(by_name["monitor"]["state"], "killed")
            self.assertTrue(by_name["monitor"].get("isHelper"))

            # Secret .env must NOT be registered as output
            out_paths = [o.get("path") for o in demo["outputs"] if o.get("path")]
            self.assertFalse(any(p.endswith(".env") for p in out_paths))
            self.assertTrue(any(o["title"] == "docs/REPORT.md" for o in demo["outputs"]))

            # Oversized post should have body=None initially, lazy-loaded via load_swarm_output
            post2 = next(o for o in demo["outputs"] if o["id"] == "post-2")
            self.assertIsNone(post2["body"])
            loaded_p2 = load_swarm_output(demo, "post-2")
            self.assertTrue(loaded_p2["ok"])
            self.assertIn("Large summary line", loaded_p2["body"])

            # Remote fallback
            rem = next(f for f in flows if f["id"] == f"agy:{remote_id}")
            self.assertIn("Remote", rem["goal"])

            # No-plan stale swarm
            stale_flow = next(f for f in flows if f["id"] == f"agy:{noplan_id}")
            self.assertEqual(stale_flow["agents"][0]["state"], "stale")

            # Transcript loader
            tr_msgs = load_transcript_messages(brain, worker_id, limit=10)
            self.assertEqual(len(tr_msgs), 4)


class T3DelegationsAdapterTests(unittest.TestCase):
    def test_t3_depth2_mixed_labs_ro_file_and_lazy_loaders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proj = root / "proj"
            proj.mkdir()
            (proj / "README.md").write_text("Hello T3\nLine 2\n", encoding="utf-8")

            db_path = root / "statev2.sqlite"
            _create_mock_t3_db(db_path)

            grok_root = root / "grok_sessions" / "proj" / "grok-sid-1"
            grok_root.mkdir(parents=True)
            (grok_root / "usage.json").write_text(
                json.dumps(
                    {
                        "session": {
                            "inputTokens": 2000,
                            "cachedReadTokens": 500,
                            "cacheCreationTokens": 0,
                            "outputTokens": 400,
                            "costUsdTicks": 25_000_000_000,  # $2.50
                            "primaryModelId": "grok-4.7-build",
                        }
                    }
                ),
                encoding="utf-8",
            )

            con = sqlite3.connect(str(db_path))
            con.execute("INSERT INTO projection_projects VALUES ('p1', 'Proj', ?)", (str(proj),))
            # Root thread + intermediate fork thread + 14 leaf threads (depth 2 -> kind='tree')
            con.execute(
                "INSERT INTO orchestration_v2_projection_threads VALUES (?, 'p1', ?, 'antigravity', 'antigravity', 'pt-root', '2026-11-25T10:00:00Z', '2026-11-25T10:05:00Z', NULL, NULL, ?)",
                ("root-1", "Tree Root", json.dumps({"lineage": {"rootThreadId": "root-1"}, "modelSelection": {"model": "gemini-4-neon"}})),
            )
            con.execute(
                "INSERT INTO orchestration_v2_projection_threads VALUES (?, 'p1', ?, 'claude', 'claude', 'pt-fork', '2026-11-25T10:01:00Z', '2026-11-25T10:04:00Z', NULL, NULL, ?)",
                ("fork-1", "Fork Lead", json.dumps({"lineage": {"rootThreadId": "root-1", "parentThreadId": "root-1", "relationshipToParent": "fork"}, "modelSelection": {"model": "claude-opus-5-5"}})),
            )
            con.execute(
                "INSERT INTO orchestration_v2_projection_subagents VALUES (?, 'root-1', 'run-0', 'n0', 'claude', 'claude', 'claude', 'pt-fork', 'fork-1', 'delegate', 'completed', '2026-11-25T10:01:00Z', '2026-11-25T10:04:00Z', '2026-11-25T10:04:00Z', ?)",
                ("sub-lead", json.dumps({"title": "Lead", "role": "reviewer", "model": "claude-opus-5-5", "prompt": "Lead prompt", "result": "See [README.md](README.md)"})),
            )
            for idx in range(1, 14):
                ctid = f"leaf-{idx}"
                sid = f"sub-leaf-{idx}"
                status = "running" if idx == 1 else "completed"
                completed_at = None if idx == 1 else "2026-11-25T10:03:00Z"
                drv = "grok" if idx == 2 else "claude"
                mdl = "grok-4.7-build" if idx == 2 else "claude-haiku-4-5"
                pt = "pt-grok" if idx == 2 else None
                con.execute(
                    "INSERT INTO orchestration_v2_projection_threads VALUES (?, 'p1', ?, ?, ?, ?, '2026-11-25T10:02:00Z', '2026-11-25T10:03:00Z', NULL, NULL, ?)",
                    (ctid, f"Leaf {idx}", drv, drv, pt, json.dumps({"lineage": {"rootThreadId": "root-1", "parentThreadId": "fork-1"}, "modelSelection": {"model": mdl}})),
                )
                con.execute(
                    "INSERT INTO orchestration_v2_projection_subagents VALUES (?, 'fork-1', ?, ?, ?, ?, ?, ?, ?, 'delegate', ?, '2026-11-25T10:02:00Z', ?, '2026-11-25T10:03:00Z', ?)",
                    (sid, f"run-{idx}", f"n-{idx}", drv, drv, drv, pt, ctid, status, completed_at, json.dumps({"title": f"Worker {idx}", "role": "worker", "model": mdl, "prompt": f"Prompt {idx}", "result": f"Full result {idx}"})),
                )
                if idx == 1:
                    con.execute("INSERT INTO orchestration_v2_projection_runs VALUES ('run-live', ?, 'running')", (ctid,))

            con.execute(
                "INSERT INTO orchestration_v2_projection_provider_threads VALUES ('pt-grok', 'leaf-2', 'grok', 'grok', '2026-11-25T10:03:00Z', ?)",
                (json.dumps({"nativeThreadRef": {"nativeId": "grok-sid-1"}}),),
            )
            for m_idx in range(1, 5):
                con.execute(
                    "INSERT INTO orchestration_v2_projection_messages VALUES (?, 'fork-1', 'r', 'n', 'assistant', 0, ?, ?, ?)",
                    (f"m-{m_idx}", f"2026-11-25T10:01:0{m_idx}Z", f"2026-11-25T10:01:0{m_idx}Z", json.dumps({"text": f"msg {m_idx}"})),
                )
            con.commit()
            con.close()

            # Enforce strict read-only file permissions to prove _open_ro never writes
            os.chmod(db_path, 0o444)
            try:
                flows = discover_t3_flows(
                    db_path=db_path,
                    env_id="env-test",
                    grok_sessions_root=root / "grok_sessions",
                    brain_roots=[],
                )
                self.assertEqual(len(flows), 1)
                flow = flows[0]
                _assert_flow_invariants(self, flow)
                self.assertEqual(flow["kind"], "tree")
                self.assertEqual(len(flow["agents"]), 15)  # root + lead + 13 leaves

                by_id = {a["id"]: a for a in flow["agents"]}
                self.assertEqual(by_id["leaf-1"]["state"], "running")
                self.assertEqual(by_id["root-1"]["state"], "running")
                self.assertEqual(by_id["leaf-2"]["lab"], "xai")
                self.assertAlmostEqual(by_id["leaf-2"]["costUsd"] or 0.0, 2.5, places=2)
                self.assertEqual(by_id["leaf-2"]["tokens"], {"in": 1500, "cache": 500, "out": 400})

                detail = load_subagent_detail(db_path, "sub-lead")
                self.assertEqual(detail["prompt"], "Lead prompt")
                self.assertIn("README.md", detail["result"])

                msgs = load_thread_messages(db_path, "fork-1", limit=2)
                self.assertEqual([m["text"] for m in msgs], ["msg 3", "msg 4"])
                older = load_thread_messages(db_path, "fork-1", limit=2, before_created_at=msgs[0]["createdAt"])
                self.assertEqual([m["text"] for m in older], ["msg 1", "msg 2"])
            finally:
                os.chmod(db_path, 0o644)

    def test_t3_trace_counts_from_agy_transcript_and_turn_items_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proj = root / "proj"
            proj.mkdir()
            db_path = root / "statev2.sqlite"
            _create_mock_t3_db(db_path)

            brain = root / "brain"
            agy_nid = "aaaaaaaa-1111-2222-3333-444444444444"
            agy_logs = brain / agy_nid / ".system_generated" / "logs"
            agy_logs.mkdir(parents=True)
            # 35 distinct tools + repeated calls -> must keep all counts as ints (never cap at 30)
            tcs_1 = [{"name": f"tool_{i}", "args": {"Path": "/tmp/a"}} for i in range(35)]
            tcs_2 = [{"name": "read_file", "args": {"Path": "/tmp/b"}} for _ in range(4)]
            (agy_logs / "transcript.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "PLANNER_RESPONSE",
                                "model": "gemini-4-neon",
                                "input_tokens": 100,
                                "cache_read_tokens": 400,
                                "output_tokens": 20,
                                "tool_calls": tcs_1,
                            }
                        ),
                        json.dumps(
                            {
                                "type": "PLANNER_RESPONSE",
                                "model": "gemini-4-neon",
                                "input_tokens": 50,
                                "cache_read_tokens": 100,
                                "output_tokens": 10,
                                "tool_calls": tcs_2,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            con = sqlite3.connect(str(db_path))
            con.execute(
                """
                CREATE TABLE orchestration_v2_projection_turn_items (
                    turn_item_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    run_id TEXT,
                    node_id TEXT,
                    provider_thread_id TEXT,
                    provider_turn_id TEXT,
                    parent_item_id TEXT,
                    ordinal INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            con.execute("INSERT INTO projection_projects VALUES ('p1', 'Proj', ?)", (str(proj),))
            con.execute(
                "INSERT INTO orchestration_v2_projection_threads VALUES ('root-1', 'p1', 'Root', 'antigravity', 'antigravity', 'pt-root', '2026-11-25T10:00:00Z', '2026-11-25T10:05:00Z', NULL, NULL, ?)",
                (json.dumps({"lineage": {"rootThreadId": "root-1"}, "modelSelection": {"model": "gemini-4-neon"}}),),
            )
            con.execute(
                "INSERT INTO orchestration_v2_projection_threads VALUES ('child-claude', 'p1', 'Claude Child', 'claudeAgent', 'claudeAgent', 'pt-cl', '2026-11-25T10:01:00Z', '2026-11-25T10:03:00Z', NULL, NULL, ?)",
                (json.dumps({"lineage": {"rootThreadId": "root-1", "parentThreadId": "root-1"}, "modelSelection": {"model": "claude-opus-5-5"}}),),
            )
            con.execute(
                "INSERT INTO orchestration_v2_projection_provider_threads VALUES ('pt-root', 'root-1', 'antigravity', 'antigravity', '2026-11-25T10:05:00Z', ?)",
                (json.dumps({"nativeThreadRef": {"nativeId": agy_nid}}),),
            )
            con.execute(
                "INSERT INTO orchestration_v2_projection_provider_threads VALUES ('pt-cl', 'child-claude', 'claudeAgent', 'claudeAgent', '2026-11-25T10:03:00Z', ?)",
                (json.dumps({"nativeThreadRef": {"nativeId": "cl-nid-1"}}),),
            )
            con.execute(
                "INSERT INTO orchestration_v2_projection_subagents VALUES ('sub-cl', 'root-1', 'r1', 'n1', 'claudeAgent', 'claudeAgent', 'claudeAgent', 'pt-cl', 'child-claude', 'delegate', 'completed', '2026-11-25T10:01:00Z', '2026-11-25T10:03:00Z', '2026-11-25T10:03:00Z', ?)",
                (json.dumps({"title": "Claude Task", "role": "worker", "model": "claude-opus-5-5"}),),
            )
            # Duplicate continuation node:* pointing at same child-claude thread must NOT get duplicate turn_items trace
            con.execute(
                "INSERT INTO orchestration_v2_projection_subagents VALUES ('node:cont-cl', 'root-1', 'r2', 'n2', 'claudeAgent', 'claudeAgent', 'claudeAgent', 'pt-cl', 'child-claude', 'thread-send', 'completed', '2026-11-25T10:04:00Z', '2026-11-25T10:05:00Z', '2026-11-25T10:05:00Z', ?)",
                (json.dumps({"title": "Claude Cont", "role": "worker", "model": "claude-opus-5-5"}),),
            )

            # Turn items for child-claude: 3 command_execution, 2 dynamic_tool, plus non-tool items (reasoning, subagent, assistant_message)
            ti_rows = [
                ("ti-1", "child-claude", 1, "command_execution"),
                ("ti-2", "child-claude", 2, "command_execution"),
                ("ti-3", "child-claude", 3, "command_execution"),
                ("ti-4", "child-claude", 4, "dynamic_tool"),
                ("ti-5", "child-claude", 5, "dynamic_tool"),
                ("ti-6", "child-claude", 6, "subagent"),
                ("ti-7", "child-claude", 7, "reasoning"),
                ("ti-8", "child-claude", 8, "assistant_message"),
            ]
            for tid_item, th_id, ord_n, typ in ti_rows:
                con.execute(
                    "INSERT INTO orchestration_v2_projection_turn_items VALUES (?, ?, 'r1', 'n1', 'pt-cl', 'pturn', NULL, ?, ?, 'completed', '2026-11-25T10:02:00Z', '{}')",
                    (tid_item, th_id, ord_n, typ),
                )
            con.commit()
            con.close()

            usage_path = root / "usage.json"
            usage_path.write_text(
                json.dumps(
                    {
                        "files": {
                            "/tmp/cl.jsonl": {
                                "cs": {"sessionId": "cl-nid-1"},
                                "r": [[0, 0, 0, 20, 800, 200, 50, 0, 0, None]],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            flows = discover_t3_flows(
                db_path=db_path,
                env_id="env-1",
                usage_index=UsageIndex(usage_path),
                brain_roots=[brain],
                grok_sessions_root=root / "no-grok",
            )
            self.assertEqual(len(flows), 1)
            by_id = {a["id"]: a for a in flows[0]["agents"]}
            root_trace = by_id["root-1"]["trace"]
            self.assertEqual(len(root_trace), 36)
            self.assertTrue(all(isinstance(c, int) for _, c in root_trace))
            self.assertEqual(sum(c for _, c in root_trace), 39)
            self.assertEqual(by_id["root-1"]["cacheRead"], 500)

            self.assertEqual(by_id["child-claude"]["trace"], [["command", 3], ["tool", 2]])
            self.assertEqual(by_id["child-claude"]["cacheRead"], 800)
            self.assertEqual(by_id["node:cont-cl"]["trace"], [])
            self.assertNotIn("cacheRead", by_id["node:cont-cl"])


class HarnessAndCliAdapterTests(unittest.TestCase):
    def test_committee_orchestrate_and_ledger_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proj = root / "ws"
            (proj / "docs").mkdir(parents=True)
            (proj / "docs" / "arch.md").write_text("line1\nline2\nline3\n", encoding="utf-8")
            (proj / "auth.json").write_text("{}\n", encoding="utf-8")

            c_dir = root / "committee"
            o_dir = root / "orchestrate"
            c_run = c_dir / "sample-review"
            c_run.mkdir(parents=True)
            (c_run / "committee.resolved.json").write_text(
                json.dumps(
                    {
                        "tier": "premium",
                        "workspace": str(proj),
                        "question": "Approve design in [docs/arch.md](docs/arch.md)? Skip `auth.json`.",
                        "members": [
                            {"id": "committee-1", "seat": 1, "model": "claude-opus-5", "normalized_weight": 0.4, "route": {"cli": "claude"}},
                            {"id": "committee-2", "seat": 2, "model": "grok-4.7-build", "normalized_weight": 0.4, "route": {"cli": "grok"}, "cost_per_task": 0.5},
                            {"id": "committee-3", "seat": 3, "model": "gemini-4-neon", "normalized_weight": 0.2, "route": {"cli": "agy"}},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (c_run / "committee-1.md").write_text("Seat 1 brief", encoding="utf-8")
            (c_run / "committee-2.md").write_text("Seat 2 brief", encoding="utf-8")
            (c_run / "committee-3.md").write_text("Seat 3 brief", encoding="utf-8")
            (c_run / "committee-1.out.json").write_text(
                json.dumps({"status": "success", "duration_seconds": 42.5, "cli": "claude", "session_id": "sid-com-1", "response": "**COND PASS**: Good work."}),
                encoding="utf-8",
            )
            (c_run / "committee-2.out.json").write_text(
                json.dumps({"status": "success", "duration_seconds": 30.0, "cli": "grok", "session_id": "sid-com-2", "response": "Verdict: **COND GO**"}),
                encoding="utf-8",
            )
            # committee-3.out.json missing -> live running seat!

            # Committee without committee.resolved.json fallback
            c_raw = c_dir / "no-resolved"
            c_raw.mkdir(parents=True)
            (c_raw / "committee-1.out.json").write_text(
                json.dumps({"status": "success", "cli": "codex", "model": "gpt-6", "session_id": "sid-codex-1", "response": "**PASS** all checks"}),
                encoding="utf-8",
            )

            # Orchestrate run with wave deps + ascii/diagram
            o_run = o_dir / "orch-demo"
            o_run.mkdir(parents=True)
            (o_run / "plan.resolved.json").write_text(
                json.dumps(
                    {
                        "goal": "Build pipeline",
                        "workspace": str(proj),
                        "waves": [["t1"], ["t2"]],
                        "tasks": [
                            {"id": "t1", "title": "First", "model": "gemini-4-neon", "route": {"cli": "agy"}},
                            {"id": "t2", "title": "Second", "after": ["t1"], "model": "claude-haiku-4-5", "route": {"cli": "claude"}},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (o_run / "_ascii.txt").write_text("t1 -> t2\n", encoding="utf-8")
            (o_run / "_diagram.out.json").write_text(json.dumps({"response": "graph TD; t1-->t2;"}), encoding="utf-8")
            (o_run / "t1.out.json").write_text(json.dumps({"status": "success", "session_id": "sid-orch-1", "response": "Done t1"}), encoding="utf-8")
            (o_run / "t2.md").write_text("Run t2", encoding="utf-8")

            # Ledger with 1 seen session id (deduped) + 2 orphaned runs clustered together
            ledger = root / "sessions.jsonl"
            ledger.write_text(
                "\n".join(
                    [
                        json.dumps({"ts": "2026-11-25T11:00:00Z", "session_id": "sid-com-1", "cli": "claude", "model": "claude-opus-5", "role": "seat"}),
                        json.dumps({"ts": "2026-11-25T12:00:00Z", "session_id": "orphan-1", "cli": "agy", "model": "gemini-4-neon", "role": "worker"}),
                        json.dumps({"ts": "2026-11-25T12:01:00Z", "session_id": "orphan-2", "cli": "claude", "model": "claude-haiku-4-5", "role": "reviewer"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            flows = discover_harness_runs(
                committee_dir=c_dir,
                orchestrate_dir=o_dir,
                sessions_ledger=ledger,
                grok_sessions_root=root / "none",
                now_ts=1795612000.0,
            )
            self.assertEqual(len(flows), 4)
            for f in flows:
                _assert_flow_invariants(self, f)

            sr = next(f for f in flows if f["id"] == "committee:sample-review")
            self.assertEqual(sr["decision"]["word"], "CONDITIONAL")
            self.assertIn("running", sr["decision"]["qualifier"])
            self.assertEqual(sr["agents"][0]["verdict"], "COND PASS")
            self.assertEqual(sr["agents"][2]["state"], "running")
            ref_titles = [o["title"] for o in sr["outputs"] if o.get("meta", {}).get("referenced")]
            self.assertEqual(ref_titles, ["docs/arch.md"])  # auth.json blocked

            orch = next(f for f in flows if f["id"] == "orchestrate:orch-demo")
            self.assertEqual(orch["kind"], "mixed")
            self.assertEqual(orch["agents"][1]["parent"], "t1")

            del_flows = [f for f in flows if f["harness"] == "delegate"]
            self.assertEqual(len(del_flows), 1)
            self.assertEqual(len(del_flows[0]["agents"]), 2)  # sid-com-1 deduped out

    def test_cli_subagents_grok_codex_claude_and_dropin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 1. Grok headless + non-headless ignored
            g_sess = root / "grok" / "demo-proj" / "grok-h1"
            g_sess.mkdir(parents=True)
            (g_sess / "summary.json").write_text(
                json.dumps({"session_kind": "headless", "info": {"id": "grok-h1", "cwd": "/tmp/ws"}, "generated_title": "Bench Task", "created_at": "2026-11-25T10:00:00Z", "updated_at": "2026-11-25T10:02:00Z"}),
                encoding="utf-8",
            )
            (g_sess / "usage.json").write_text(
                json.dumps({"session": {"inputTokens": 1000, "cachedReadTokens": 200, "outputTokens": 300, "costUsdTicks": 12_340_000_000, "primaryModelId": "grok-4.7-build"}}),
                encoding="utf-8",
            )
            (g_sess / "events.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"type": "tool_started", "tool_name": "read_file"}),
                        json.dumps({"type": "mcp_tool_call_started", "tool_name": "list_pages"}),
                        json.dumps({"type": "turn_ended", "outcome": "completed"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (g_sess / "chat_history.jsonl").write_text(json.dumps({"type": "assistant", "content": "Finished run", "ts": "2026-11-25T10:02:00Z"}) + "\n", encoding="utf-8")

            g_inter = root / "grok" / "demo-proj" / "grok-interactive"
            g_inter.mkdir(parents=True)
            (g_inter / "summary.json").write_text(json.dumps({"session_kind": "interactive"}), encoding="utf-8")

            # 2. Codex mock state_5.sqlite
            c_db = root / "state_5.sqlite"
            con = sqlite3.connect(str(c_db))
            con.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT, title TEXT, model TEXT, agent_nickname TEXT, agent_role TEXT, attached_thread_parent_id TEXT, source TEXT, created_at_ms INTEGER, updated_at_ms INTEGER, preview TEXT)"
            )
            con.execute("INSERT INTO threads VALUES ('cx-parent', '/tmp/ws', 'Codex Root', 'gpt-6-codex', NULL, NULL, NULL, 'cli', 1795600000000, 1795600060000, 'Parent')")
            con.execute("INSERT INTO threads VALUES ('cx-child', '/tmp/ws', 'Codex Sub', 'gpt-6-codex', 'sub-1', 'reviewer', 'cx-parent', 'subagent', 1795600010000, 1795600050000, 'Checked')")
            con.commit()
            con.close()

            # 3. Claude teams + project subagents
            cl_root = root / "claude"
            (cl_root / "teams" / "alpha").mkdir(parents=True)
            (cl_root / "tasks" / "alpha").mkdir(parents=True)
            (cl_root / "teams" / "alpha" / "config.json").write_text(
                json.dumps({"name": "alpha", "description": "Team Alpha", "leadAgentId": "lead-1", "members": [{"agentId": "lead-1", "name": "lead", "agentType": "team-lead", "model": "claude-opus-5", "sessionId": "cl-s1"}]}),
                encoding="utf-8",
            )
            (cl_root / "tasks" / "alpha" / "1.json").write_text(json.dumps({"owner": "lead", "subject": "Audit module"}), encoding="utf-8")
            sub_dir = cl_root / "projects" / "proj-x" / "subagents"
            sub_dir.mkdir(parents=True)
            (sub_dir / "agent-a1.jsonl").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-11-25T10:00:00Z",
                        "cwd": "/tmp/ws",
                        "sessionId": "cl-sub-1",
                        "message": {
                            "model": "claude-haiku-4-5",
                            "content": [{"type": "tool_use", "name": "Read"}, {"type": "tool_use", "name": "Bash"}],
                            "usage": {"input_tokens": 50, "cache_read_input_tokens": 120, "cache_creation_input_tokens": 30, "output_tokens": 10},
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            # 4. Drop-in JSON
            d_dir = root / "dropin"
            d_dir.mkdir()
            (d_dir / "custom.json").write_text(
                json.dumps({"id": "dropin:1", "title": "Custom Dropin", "agents": [{"id": "a1", "name": "Worker", "lab": "other", "state": "done"}]}),
                encoding="utf-8",
            )

            flows = discover_cli_flows(
                grok_sessions_root=root / "grok",
                codex_db_path=c_db,
                claude_root=cl_root,
                dropin_dir=d_dir,
            )
            harnesses = {f["harness"] for f in flows}
            self.assertEqual(harnesses, {"grok", "codex", "claude", "dropin"})
            for f in flows:
                _assert_flow_invariants(self, f)

            gf = next(f for f in flows if f["harness"] == "grok")
            self.assertEqual(len(gf["agents"]), 1)
            self.assertAlmostEqual(gf["agents"][0]["costUsd"] or 0.0, 1.234, places=3)
            self.assertEqual(gf["agents"][0]["cacheRead"], 200)
            self.assertEqual(sum(c for _, c in gf["agents"][0]["trace"]), 2)
            self.assertEqual(len(load_grok_chat_history(g_sess / "chat_history.jsonl")), 1)

            cf = next(f for f in flows if f["id"] == "claude-subagents:proj-x")
            c_sub = next(a for a in cf["agents"] if a["id"] == "agent-a1")
            self.assertEqual(c_sub["cacheRead"], 120)
            self.assertEqual(sum(c for _, c in c_sub["trace"]), 2)


class BoardRegistryIntegrationTests(unittest.TestCase):
    def test_registry_sorting_fault_isolation_outputs_citations_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            ws.mkdir()
            (ws / "notes.md").write_text("\n".join(f"note line {i}" for i in range(1, 15)) + "\n", encoding="utf-8")
            (ws / ".env").write_text("SECRET=bad\n", encoding="utf-8")

            c_dir = root / "committee"
            run_dir = c_dir / "done-run"
            run_dir.mkdir(parents=True)
            (run_dir / "committee.resolved.json").write_text(
                json.dumps(
                    {
                        "workspace": str(ws),
                        "question": "Check `notes.md`",
                        "members": [{"id": "committee-1", "seat": 1, "model": "claude-opus-5", "route": {"cli": "claude"}}],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "committee-1.md").write_text("Brief", encoding="utf-8")
            big_resp = "**PASS**\n\n" + ("Detail " * 3000)  # > 16 KB inline compaction
            (run_dir / "committee-1.out.json").write_text(
                json.dumps({"status": "success", "cli": "claude", "session_id": "s1", "response": big_resp}),
                encoding="utf-8",
            )

            dropin_dir = root / "dropin"
            dropin_dir.mkdir()
            (dropin_dir / "live.json").write_text(
                json.dumps({"id": "dropin:live", "title": "Live Dropin", "updatedAt": "2020-01-01T00:00:00Z", "agents": [{"id": "w1", "name": "Live", "state": "running"}]}),
                encoding="utf-8",
            )

            reg = BoardRegistry(
                {
                    "committee_dir": c_dir,
                    "orchestrate_dir": root / "no-orch",
                    "sessions_ledger": root / "no-ledger.jsonl",
                    "grok_sessions_root": root / "no-grok",
                    "codex_db": root / "no-codex.sqlite",
                    "claude_root": root / "no-claude",
                    "brain_roots": [],
                    "statev2_db": root / "no-t3.sqlite",
                    "dropin_dir": dropin_dir,
                    "rates_path": root / "no-rates.json",
                    "usage_cache_path": root / "no-usage.json",
                },
                cache_ttl_s=0.0,
            )

            # Force one adapter to raise to verify isolation
            with patch.object(reg, "_import_optional_adapter", side_effect=RuntimeError("boom")):
                flows = reg.list_flows(force=True)
                self.assertIn("agy_swarms", reg.last_errors)
                self.assertEqual(len(flows), 1)  # harness_runs still succeeded

            flows = reg.list_flows(force=True)
            self.assertEqual(reg.last_errors, {})
            # Running flow sorts BEFORE newer done flow
            self.assertEqual(flows[0]["id"], "dropin:live")
            self.assertEqual(flows[1]["id"], "committee:done-run")

            # Compacted > 16 KB output body in list_flows, intact in get_output
            verdict_in_list = next(o for o in flows[1]["outputs"] if o["id"] == "committee-1-verdict")
            self.assertIsNone(verdict_in_list["body"])
            loaded = reg.get_output("committee:done-run", "committee-1-verdict")
            self.assertTrue(loaded["ok"])
            self.assertGreater(len(loaded["body"]), 16_384)

            # Citation confinement
            cit = reg.get_citation("committee:done-run", "notes.md", 5, ctx=2)
            self.assertTrue(cit["ok"])
            self.assertEqual([x["num"] for x in cit["lines"]], [3, 4, 5, 6, 7])
            bad_cit = reg.get_citation("committee:done-run", ".env", 1)
            self.assertFalse(bad_cit["ok"])

            # Digest stability & mutation sensitivity
            d1 = reg.snapshot_digest()
            d2 = reg.snapshot_digest()
            self.assertEqual(d1, d2)

            (dropin_dir / "live.json").write_text(
                json.dumps({"id": "dropin:live", "title": "Live Dropin", "updatedAt": "2026-11-25T12:00:00Z", "agents": [{"id": "w1", "name": "Live", "state": "done"}]}),
                encoding="utf-8",
            )
            reg.list_flows(force=True)
            d3 = reg.snapshot_digest()
            self.assertNotEqual(d1, d3)

    def test_registry_lazy_loaders_across_harnesses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # 1. Mock T3 DB with subagent + messages + subagent-detail output without inline body
            db_path = root / "statev2.sqlite"
            _create_mock_t3_db(db_path)
            con = sqlite3.connect(str(db_path))
            con.execute("INSERT INTO projection_projects VALUES ('p1', 'Proj', ?)", (str(root),))
            con.execute(
                "INSERT INTO orchestration_v2_projection_threads VALUES ('t-root', 'p1', 'Root', 'antigravity', 'antigravity', NULL, '2026-11-25T10:00:00Z', '2026-11-25T10:02:00Z', NULL, NULL, ?)",
                (json.dumps({"lineage": {"rootThreadId": "t-root"}}),),
            )
            con.execute(
                "INSERT INTO orchestration_v2_projection_threads VALUES ('t-child', 'p1', 'Child', 'claude', 'claude', NULL, '2026-11-25T10:00:10Z', '2026-11-25T10:01:00Z', NULL, NULL, ?)",
                (json.dumps({"lineage": {"rootThreadId": "t-root", "parentThreadId": "t-root"}}),),
            )
            con.execute(
                "INSERT INTO orchestration_v2_projection_subagents VALUES ('sub-1', 't-root', 'r1', 'n1', 'claude', 'claude', 'claude', NULL, 't-child', 'delegate', 'completed', '2026-11-25T10:00:10Z', '2026-11-25T10:01:00Z', '2026-11-25T10:01:00Z', ?)",
                (json.dumps({"title": "Worker 1", "role": "worker", "model": "claude-opus-5", "prompt": "Check prompt", "result": "Check result"}),),
            )
            con.execute(
                "INSERT INTO orchestration_v2_projection_messages VALUES ('msg-1', 't-child', 'r1', 'n1', 'assistant', 0, '2026-11-25T10:00:20Z', '2026-11-25T10:00:20Z', ?)",
                (json.dumps({"text": "Child says hi"}),),
            )
            con.commit()
            con.close()

            # 2. Mock AGY swarm with conversationId transcript + oversized board post
            brain = root / "brain"
            agy_cid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            s_dir = brain / agy_cid / ".system_generated" / "swarm"
            s_dir.mkdir(parents=True)
            (s_dir / "swarm.json").write_text(json.dumps({"swarm_id": agy_cid, "backend": "local", "created_at": "2026-11-25T09:00:00Z"}), encoding="utf-8")
            (s_dir / "members.json").write_text(json.dumps({"title": "AGY Lazy", "members": {"coordinator": {"conversation_id": agy_cid, "role": "coordinator", "wake_mode": "working"}}}), encoding="utf-8")
            (s_dir / "board.jsonl").write_text(json.dumps({"seq": 1, "agent": "coordinator", "time": "2026-11-25 09:01:00", "text": "Huge " + ("Z" * 70_000)}) + "\n", encoding="utf-8")
            logs = brain / agy_cid / ".system_generated" / "logs"
            logs.mkdir(parents=True)
            (logs / "transcript.jsonl").write_text(json.dumps({"type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T09:01:10Z", "content": "AGY step"}) + "\n", encoding="utf-8")

            # 3. Mock Grok headless chat
            g_dir = root / "grok" / "proj" / "grok-1"
            g_dir.mkdir(parents=True)
            (g_dir / "summary.json").write_text(json.dumps({"session_kind": "headless", "info": {"id": "grok-1", "cwd": str(root)}, "generated_title": "Grok Run", "created_at": "2026-11-25T08:00:00Z", "updated_at": "2026-11-25T08:01:00Z"}), encoding="utf-8")
            (g_dir / "chat_history.jsonl").write_text(json.dumps({"type": "assistant", "content": "Grok reply", "ts": "2026-11-25T08:01:00Z"}) + "\n", encoding="utf-8")

            reg = BoardRegistry(
                {
                    "committee_dir": root / "no-com",
                    "orchestrate_dir": root / "no-orch",
                    "sessions_ledger": root / "no-ledger",
                    "grok_sessions_root": root / "grok",
                    "codex_db": root / "no-codex",
                    "claude_root": root / "no-claude",
                    "brain_roots": [brain],
                    "statev2_db": db_path,
                    "env_id": "env-1",
                    "dropin_dir": root / "no-drop",
                    "rates_path": root / "no-rates.json",
                    "usage_cache_path": root / "no-usage.json",
                },
                cache_ttl_s=60.0,
            )
            flows = reg.list_flows(force=True)
            self.assertEqual(reg.last_errors, {})

            # T3 transcript output must return thread messages (not subagent prompt/result)
            t3_tr = reg.get_output("t3:t-root", "tr-t-child")
            self.assertTrue(t3_tr["ok"])
            self.assertEqual(t3_tr["messages"][0]["text"], "Child says hi")
            self.assertEqual(reg.get_thread_messages("t-child")[0]["text"], "Child says hi")

            # Direct subagent detail output branch (no inline body, kind='detail')
            with reg._lock:
                reg._flow_by_id["t3:t-root"]["outputs"].append(
                    {"id": "detail-sub-1", "kind": "detail", "title": "Detail", "path": None, "size": None, "by": "t-child", "at": None, "body": None, "src": None, "meta": {"subagentId": "sub-1"}}
                )
            sub_detail = reg.get_output("t3:t-root", "detail-sub-1")
            self.assertTrue(sub_detail["ok"])
            self.assertEqual(sub_detail["detail"]["prompt"], "Check prompt")

            # AGY transcript output (conversationId) + oversized post + thread messages
            agy_tr = reg.get_output(f"agy:{agy_cid}", "transcript-coordinator")
            self.assertTrue(agy_tr["ok"])
            self.assertEqual(agy_tr["messages"][0]["content"], "AGY step")
            self.assertEqual(reg.get_thread_messages(agy_cid)[0]["content"], "AGY step")
            agy_post = reg.get_output(f"agy:{agy_cid}", "post-1")
            self.assertTrue(agy_post["ok"])
            self.assertTrue(agy_post["body"].startswith("Huge "))

            # Grok transcript output
            grok_flow = next(f for f in flows if f["harness"] == "grok")
            grok_tr = reg.get_output(grok_flow["id"], grok_flow["outputs"][0]["id"])
            self.assertTrue(grok_tr["ok"])
            self.assertEqual(grok_tr["messages"][0]["content"], "Grok reply")


if __name__ == "__main__":
    unittest.main()
