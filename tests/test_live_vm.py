"""Read-only live VM verification tests across real multi-agent harnesses."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time
import unittest

from swarm_board.adapters import BoardRegistry
from swarm_board.adapters.t3_delegations import discover_t3_flows
from swarm_board.safety import redact_if_secret_name


T3_DB = Path.home() / ".t3" / "userdata" / "statev2.sqlite"


class LiveVmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not T3_DB.is_file():
            raise unittest.SkipTest("Live VM ~/.t3/userdata/statev2.sqlite not present")
        cls.reg = BoardRegistry(cache_ttl_s=0.0)
        cls.cold_t0 = time.perf_counter()
        cls.flows = cls.reg.list_flows(force=True)
        cls.cold_ms = (time.perf_counter() - cls.cold_t0) * 1000.0

    def test_discovers_expected_live_flows_without_errors(self) -> None:
        self.assertEqual(self.reg.last_errors, {})
        titles = " | ".join(f.get("title", "") for f in self.flows)
        harnesses = {f["harness"] for f in self.flows}

        self.assertIn("t3", harnesses)
        self.assertIn("agy", harnesses)
        self.assertIn("committee", harnesses)
        self.assertIn("grok", harnesses)

        self.assertIn("Inkpress Audio Skill", titles)
        self.assertIn("benchs-d004-v2-kickoff", titles)
        self.assertIn("Pathology Model Benchmark", titles)
        self.assertIn("Swarm Board", titles)

    def test_warm_latency_and_peak_rss_budgets(self) -> None:
        # Warm T3 query must stay < 60 ms
        discover_t3_flows()
        t0 = time.perf_counter()
        discover_t3_flows()
        t3_warm_ms = (time.perf_counter() - t0) * 1000.0
        self.assertLess(t3_warm_ms, 60.0, f"T3 warm={t3_warm_ms:.1f}ms")

        # Warm full registry scan (bypassing TTL via force=True) must stay < 150 ms
        t1 = time.perf_counter()
        self.reg.list_flows(force=True)
        reg_warm_ms = (time.perf_counter() - t1) * 1000.0
        self.assertLess(reg_warm_ms, 150.0, f"Registry warm={reg_warm_ms:.1f}ms")

        # Measure real standalone BoardRegistry peak RSS via VmHWM in a clean subprocess
        # (ru_maxrss across fork+exec inherits parent runner RSS on Linux; VmHWM resets on exec).
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import os, re, resource; "
                    "from swarm_board.adapters import BoardRegistry; "
                    "r = BoardRegistry(cache_ttl_s=0.0); "
                    "r.list_flows(force=True); "
                    "r.list_flows(force=True); "
                    "st = open('/proc/self/status').read() if os.path.exists('/proc/self/status') else ''; "
                    "m = re.search(r'^VmHWM:\\s+(\\d+)\\s+kB', st, re.M); "
                    "kb = int(m.group(1)) if m else resource.getrusage(resource.RUSAGE_SELF).ru_maxrss; "
                    "print(kb / 1024.0)"
                ),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        rss_mb = float(proc.stdout.strip().splitlines()[-1])
        self.assertLess(rss_mb, 40.0, f"Standalone Peak RSS={rss_mb:.1f}MB")

    def test_real_telemetry_citations_and_lazy_loaders(self) -> None:
        # Verify real duration, tokens, and cost across T3, AGY, and Grok
        for harness in ("t3", "agy", "grok"):
            subset = [f for f in self.flows if f["harness"] == harness]
            self.assertTrue(subset, f"No flows for harness {harness}")
            agents = [a for f in subset for a in f["agents"]]
            self.assertTrue(any(a["durS"] is not None for a in agents), f"No durS for {harness}")
            self.assertTrue(any(a["tokens"] is not None for a in agents), f"No tokens for {harness}")
            self.assertTrue(any(a["costUsd"] is not None for a in agents), f"No costUsd for {harness}")

        # Real citation lookup on benchs-d004-v2-kickoff
        cit = self.reg.get_citation(
            "committee:benchs-d004-v2-kickoff",
            "docs/v2/V2_ARCHITECTURE_DRAFT.md",
            42,
        )
        self.assertTrue(cit.get("ok"), cit.get("error"))
        self.assertEqual(len(cit["lines"]), 9)
        self.assertEqual([ln["num"] for ln in cit["lines"] if ln["cited"]], [42])

        # Real AGY transcript output + thread messages
        agy_flow = next(f for f in self.flows if f["harness"] == "agy" and f["outputs"])
        agy_tr = next(o for o in agy_flow["outputs"] if o["kind"] == "transcript")
        agy_loaded = self.reg.get_output(agy_flow["id"], agy_tr["id"])
        self.assertTrue(agy_loaded and agy_loaded.get("ok"))
        self.assertTrue(agy_loaded.get("messages"))
        agy_cid = agy_tr["meta"].get("conversationId")
        if agy_cid:
            self.assertTrue(self.reg.get_thread_messages(agy_cid, limit=5))

        # Real T3 transcript output + thread messages
        t3_flow = next(f for f in self.flows if f["harness"] == "t3" and f["outputs"])
        t3_tr = next(
            o
            for o in t3_flow["outputs"]
            if o["kind"] == "transcript" and o.get("meta", {}).get("threadId")
        )
        t3_loaded = self.reg.get_output(t3_flow["id"], t3_tr["id"])
        self.assertTrue(t3_loaded and t3_loaded.get("ok"))
        self.assertTrue(t3_loaded.get("messages"))

    def test_security_and_compliance_invariants(self) -> None:
        for flow in self.flows:
            for out in flow["outputs"]:
                p = out.get("path")
                if p:
                    self.assertIsNone(redact_if_secret_name(p), f"Secret leaked in output: {p}")
            for agent in flow["agents"]:
                for lk in agent["links"]:
                    cmd = (lk.get("cmd") or "").lower()
                    for forbidden in ("agy ", "grok ", "cursor ", "muse "):
                        self.assertFalse(cmd.startswith(forbidden), f"Invented CLI resume cmd: {cmd}")


if __name__ == "__main__":
    unittest.main()
