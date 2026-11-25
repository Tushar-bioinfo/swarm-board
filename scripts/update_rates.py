#!/usr/bin/env python3
"""Check active Swarm Board model pricing coverage and optionally sync LiteLLM fallback rates."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any
import urllib.error
import urllib.request

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from swarm_board.adapters import BoardRegistry  # noqa: E402
from swarm_board.schema import REPO_RATES_OVERRIDE_PATH, RateTable, default_t3_userdata_dir  # noqa: E402

DEFAULT_LITELLM_RATES_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)


def default_litellm_dest() -> Path:
    return Path.home() / ".config" / "swarm-board" / "litellm-rates.json"


def _file_age_info(path: Path, now: float) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    try:
        st = path.stat()
        age_hours = round(max(0.0, (now - st.st_mtime) / 3600.0), 2)
        mtime_iso = (
            datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        return {
            "path": str(path),
            "exists": True,
            "sizeBytes": st.st_size,
            "mtimeUtc": mtime_iso,
            "ageHours": age_hours,
        }
    except OSError as exc:
        return {"path": str(path), "exists": False, "error": str(exc)}


def sync_litellm_rates(
    *,
    url: str | None = None,
    dest: Path | None = None,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Atomically download LiteLLM's model price catalog into litellm-rates.json."""
    target_url = (
        url
        or os.environ.get("LITELLM_RATES_URL", "").strip()
        or DEFAULT_LITELLM_RATES_URL
    )
    dest_path = Path(dest) if dest is not None else default_litellm_dest()
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(
        target_url,
        headers={"User-Agent": "swarm-board-update-rates/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw_bytes = resp.read()

    doc = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("Downloaded LiteLLM rates JSON is not a JSON object")

    payload = {
        "fetchedAtMs": int(time.time() * 1000),
        "source": target_url,
        "document": doc,
    }
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    fd, tmp_name = tempfile.mkstemp(
        prefix=".litellm-rates-",
        suffix=".tmp",
        dir=str(dest_path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, dest_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    return {
        "dest": str(dest_path),
        "modelsCount": len(doc),
        "bytes": len(encoded),
        "source": target_url,
    }


def check_rates(
    *,
    registry: BoardRegistry | None = None,
    rate_table: RateTable | None = None,
) -> dict[str, Any]:
    """Scan active BoardRegistry flows and report rate coverage and file freshness."""
    now = time.time()
    reg = registry if registry is not None else BoardRegistry(cache_ttl_s=0.0)
    rt = rate_table if rate_table is not None else reg.rate_table
    flows = reg.list_flows(force=True)

    counts: dict[str, int] = {}
    for flow in flows:
        for agent in flow.get("agents") or ():
            if not isinstance(agent, dict):
                continue
            model = str(agent.get("model") or "").strip()
            if not model or model.lower() == "unknown":
                continue
            counts[model] = counts.get(model, 0) + 1

    rated: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for model, agent_count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        entry = rt.lookup(model)
        rec = {"model": model, "agents": agent_count}
        if entry is not None:
            rated.append(rec)
        else:
            missing.append(rec)

    env_ov = os.environ.get("SWARM_BOARD_RATES_OVERRIDE", "").strip()
    sources = {
        "t3UsageRates": _file_age_info(default_t3_userdata_dir() / "usage-model-rates.json", now),
        "litellmFallback": _file_age_info(default_litellm_dest(), now),
        "userOverride": _file_age_info(
            Path.home() / ".config" / "swarm-board" / "rates_override.json", now
        ),
        "repoOverride": _file_age_info(REPO_RATES_OVERRIDE_PATH, now),
    }
    if env_ov:
        sources["envOverride"] = _file_age_info(Path(env_ov).expanduser(), now)

    return {
        "flowsCount": len(flows),
        "modelsTotal": len(counts),
        "ratedModels": rated,
        "missingModels": missing,
        "sources": sources,
    }


def _format_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Rate sources:")
    for label, info in report["sources"].items():
        if info.get("exists"):
            lines.append(
                f"  {label:16s}: {info['path']} (age={info['ageHours']}h, size={info['sizeBytes']}B)"
            )
        else:
            lines.append(f"  {label:16s}: {info['path']} (missing)")

    rated = report["ratedModels"]
    missing = report["missingModels"]
    lines.append(
        f"\nActive models ({report['flowsCount']} flows): "
        f"{len(rated)}/{report['modelsTotal']} rated, {len(missing)} missing"
    )
    if rated:
        lines.append("Rated:")
        for item in rated:
            lines.append(f"  [ok]      {item['model']} ({item['agents']} agents)")
    if missing:
        lines.append("Missing:")
        for item in missing:
            lines.append(f"  [missing] {item['model']} ({item['agents']} agents)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check active BoardRegistry model coverage and rate file freshness.",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Download LiteLLM pricing catalog to ~/.config/swarm-board/litellm-rates.json then check.",
    )
    parser.add_argument("--url", default=None, help="Override LiteLLM JSON source URL.")
    parser.add_argument("--dest", default=None, help="Override fallback destination path.")
    parser.add_argument("--json", action="store_true", help="Output JSON summary.")
    args = parser.parse_args(argv)

    sync_info: dict[str, Any] | None = None
    if args.sync:
        try:
            sync_info = sync_litellm_rates(
                url=args.url,
                dest=Path(args.dest) if args.dest else None,
            )
        except (OSError, ValueError, urllib.error.URLError) as exc:
            print(f"Sync failed: {exc}", file=sys.stderr)
            return 1

    # Default action is --check if neither flag or either flag is passed.
    report = check_rates()
    if sync_info is not None:
        report["sync"] = sync_info

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        if sync_info is not None:
            print(
                f"Synced {sync_info['modelsCount']} model entries "
                f"({sync_info['bytes']} bytes) -> {sync_info['dest']}\n"
            )
        print(_format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
