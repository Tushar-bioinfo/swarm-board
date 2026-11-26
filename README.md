# Swarm Board

Local, zero-dependency multi-agent viewer and cost/token telemetry dashboard for T3 Code, Antigravity, Claude Code, Codex, Grok, `$committee`, `$orchestrate`, and `$agent-delegate`.

## Highlights

- **Halftone Plate Obsidian UI**: Unified board graph (`swarm` lanes, `tree` canopy pods, `committee` council seal, `fan` DAG), resizable split reader with markdown/table/code/media/citation previews, keyboard shortcuts, and live `Last 5 messages` inspection.
- **T3-Style Stats Dashboard**: Monotone spline charts (`Cost`, `Tokens`, `Time`) across `Past 24h`, `7 days`, and `All`, cache savings breakdown, model & agent drilldowns, and automatic deduplication of duplicate T3 `node:*` continuations and cross-adapter child sessions.
- **0 MB Idle Footprint**: Runs strictly on `127.0.0.1` by default (or optionally binds strictly to your Tailscale IPv4 interface for remote VM → Mac viewing) with zero third-party packages (`python3` 3.10+ stdlib only) and auto-exits 2 hours after the last open tab closes.
- **Read-Only & Sandboxed**: Only reads workspace/harness artifacts, enforces allowlisted `Host` headers and `GET`/`HEAD`, redacts secret filenames, and keeps path containment checks on all file/media endpoints.

## Quickstart

Install `/swarm-board` skill symlinks and lightweight `PostToolUse` hooks for macOS or Linux:

```bash
python3 scripts/install.py
python3 scripts/install.py --verify
```

**Linux only:** also install the systemd user unit once. Without it, `scripts/ensure-server.sh --ensure` fails. Run from the repo root:

```bash
mkdir -p ~/.config/systemd/user ~/.cache/swarm-board
cat > ~/.config/systemd/user/swarm-board.service <<EOF
[Unit]
Description=Swarm Board HTTP/SSE server

[Service]
Type=simple
WorkingDirectory=$(pwd)
Environment="PYTHONPATH=$(pwd)"
Environment="PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart="$(command -v python3)" -m swarm_board.server
StandardOutput=append:$HOME/.cache/swarm-board/server.log
StandardError=append:$HOME/.cache/swarm-board/server.log
EOF
systemctl --user daemon-reload
```

**Existing Linux installs:** after upgrading, run the same block again, then `scripts/ensure-server.sh --stop`. The next `--ensure` starts the updated unit.

See [INSTALL.md](./INSTALL.md) for cross-platform paths, tool compatibility (T3 Code, Antigravity, Claude Code, Codex, Grok), and agent setup steps.

## Commands

```bash
# Start or reuse detached server and print JSON {ok, port, url, pid}
scripts/ensure-server.sh --ensure

# Check status or stop cleanly
scripts/ensure-server.sh --status
scripts/ensure-server.sh --stop

# Rebuild self-contained UI bundle (swarm_board/ui/index.html)
python3 swarm_board/ui/build.py

# Check or sync model API rates
python3 scripts/update_rates.py --check
python3 scripts/update_rates.py --sync

# Run test suite
python3 -m unittest discover -s tests -v
```

## Architecture

```text
swarm_board/
  schema.py                 # Normalized flow/agent/output schema, RateTable, UsageIndex
  safety.py                 # Path containment, secret filtering, bounded reads
  rates_override.json       # Verified model pricing overrides
  server.py                 # Zero-dep loopback HTTP + SSE server (--ensure/--hook/--status/--stop)
  adapters/
    t3_delegations.py       # Read-only T3 SQLite delegation trees + node:* continuation dedup
    agy_swarms.py           # Antigravity native swarm boards + transcripts
    cli_subagents.py        # Claude teams/subagents, Codex SQLite/sessions, Grok, drop-in JSON
    harness_runs.py         # $committee, $orchestrate, $agent-delegate + agy transcript join
    __init__.py             # BoardRegistry cache, cross-adapter dedup, Stats aggregation
  ui/
    src/                    # Vanilla HTML/CSS/JS modules
    build.py                # Zero-dep minifier/bundler (< 153,600 B limit)
    index.html              # Self-contained built UI
skills/
  swarm-board/              # Portable slash skill + board.sh wrapper + weekly rate sync docs
scripts/
  ensure-server.sh          # Portable launcher wrapper
  update_rates.py           # Rate coverage checker & LiteLLM catalog sync
  install.py                # Cross-platform macOS + Linux installer & verifier
tests/                      # Unit, live VM, safety, stats accounting, server, UI, install tests
```
