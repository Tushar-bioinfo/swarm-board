# Installing Swarm Board (macOS & Linux)

Swarm Board is zero-dependency Python 3.10+ stdlib (no `pip install`, no `npm install`, no Docker).

---

## 1. Quick Install (Human)

```bash
git clone https://github.com/Tushar-bioinfo/swarm-board.git swarm-board
cd swarm-board
python3 scripts/install.py
```

Useful flags:

- `python3 scripts/install.py --dry-run` — preview symlink and hook changes without writing
- `python3 scripts/install.py --targets antigravity,claude,codex,grok` — override auto-detection
- `python3 scripts/install.py --no-hooks` — install `/swarm-board` skill symlinks only
- `python3 scripts/install.py --verify` — validate Python version, UI build, executable bits, skills, and hooks
- `python3 scripts/install.py --uninstall` — remove only swarm-board symlinks and hooks (preserves all other configs)

### Linux only: install the systemd user unit

Linux needs this once, or `scripts/ensure-server.sh --ensure` fails with `systemctl --user start swarm-board.service failed`. Run it from the repo root (same commands as section 2, step 4):

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

**Existing Linux installs:** after upgrading, run the same block again (it overwrites the unit file), then run `scripts/ensure-server.sh --stop`. The next `--ensure` starts the updated unit.

---

## 2. Agent Setup Instructions

If you ask an AI coding agent on a new Mac or Linux machine *"Set up swarm-board using INSTALL.md"*, the agent can follow this exact 5-step checklist:

1. **Ensure repo is cloned locally** and enter its root directory (`cd /path/to/swarm-board`).
2. **Install skill symlinks & hooks** (builds `swarm_board/ui/index.html` if needed and sets `+x` permissions):
   ```bash
   python3 scripts/install.py
   ```
3. **Verify install & run test suite**:
   ```bash
   python3 scripts/install.py --verify && python3 -m unittest discover -s tests -v
   ```
4. **Linux only: install the systemd user unit** (skip on macOS). Run the block under section 1, "Linux only: install the systemd user unit", from the repo root. It fills in the repo path, the Python path, and `$HOME`.
5. **Smoke-test server lifecycle & clean up**:
   ```bash
   scripts/ensure-server.sh --ensure && scripts/ensure-server.sh --status && scripts/ensure-server.sh --stop
   ```

---

## 3. Tool Compatibility Matrix

| Environment / Orchestrator | What Swarm Board Reads | Skill (`/` command) | Automatic `PostToolUse` Hook |
| :--- | :--- | :--- | :--- |
| **T3 Code & Antigravity (IDE / CLI)** | `statev2.sqlite` (`delegate_task` trees), Antigravity swarms (`brain/<id>/scratch/swarm/`), `agy` CLI transcripts | `/swarm-board` (`~/.gemini/config/skills/swarm-board` + macOS/Linux T3 Antigravity `config/skills`) | Yes — `~/.gemini/config/hooks.json` (`swarm-board`) wakes background server on `delegate_task`, `swarm`, `invoke_subagent`, `run_workflow` |
| **Claude Code** | Agent Teams (`~/.claude/teams/*/config.json` + `~/.claude/tasks/*/*.json`), `Agent` / `Task` subagents (`~/.claude/projects/*/subagents/*.jsonl`), plus `$agent-delegate` / `$orchestrate` / `$committee` runs started from Claude Code | `/swarm-board` (`~/.claude/skills/swarm-board`) | Yes — `~/.claude/settings.json` (`hooks.PostToolUse`) wakes background server on `Agent`, `Task`, `TeamCreate`, `SendMessage`, `mcp__t3-code__delegate_task` |
| **Codex** | `spawn_agent` subagents (`~/.codex/state_5.sqlite` + `~/.codex/sessions`), plus `$agent-delegate` / `$orchestrate` / `$committee` runs started from Codex | `/swarm-board` / `$swarm-board` (`~/.codex/skills/swarm-board`) | No hook needed — invoke `/swarm-board` on demand |
| **Grok & Drop-in JSON** | `~/.grok/sessions/*/summary.json` + `usage.json`, plus drop-in JSON runs (`$AGENT_BOARD_DROPIN_DIR` or `~/.cache/agent-board/runs/*.json`) | `/swarm-board` (`~/.grok/skills/swarm-board`, linked by `scripts/install.py`) | No hook needed — invoke `/swarm-board` on demand |

---

## 4. Cross-Platform Data, Cache & Env Paths

| Purpose | Linux | macOS | Env Override |
| :--- | :--- | :--- | :--- |
| **Server runtime state (`server.json`, PID, logs)** | `$XDG_CACHE_HOME/swarm-board` or `~/.cache/swarm-board` | `~/Library/Caches/swarm-board` | `SWARM_BOARD_STATE_DIR` |
| **T3 Code userdata & Antigravity providers** | `~/.t3/userdata` | `~/Library/Application Support/T3 Code/userdata` (or `~/.t3/userdata`) | `T3_USERDATA_DIR` |
| **Antigravity brain roots** | `~/.t3/userdata/providers/antigravity/*/antigravity-acp/brain`, `~/.gemini/*/brain` | `~/Library/Application Support/T3 Code/userdata/providers/antigravity/*/antigravity-acp/brain`, `~/.t3/...`, `~/.gemini/*/brain` | `T3_USERDATA_DIR` |
| **Rates overrides (verified & LiteLLM cache)** | `~/.config/swarm-board/rates_override.json`, `~/.config/swarm-board/litellm-rates.json` | `~/.config/swarm-board/rates_override.json`, `~/.config/swarm-board/litellm-rates.json` | `SWARM_BOARD_RATES_OVERRIDE` |
| **Server bind host & port scan** | `127.0.0.1:18420..18449` (`tailscale` optional) | `127.0.0.1:18420..18449` | `SWARM_BOARD_HOST`, `SWARM_BOARD_PORT` |
| **User host/preview config** | `~/.config/swarm-board/config.json` | `~/.config/swarm-board/config.json` | `SWARM_BOARD_CONFIG`, `SWARM_BOARD_OPEN_IN_PREVIEW` |
| **Idle auto-exit timeout** | `7200` seconds (2h; 0 MB idle RSS when closed) | `7200` seconds (2h; 0 MB idle RSS when closed) | `SWARM_BOARD_IDLE_SECONDS` or `config.json` (`idle_seconds`) |
| **Drop-in custom JSON flows** | `~/.cache/agent-board/runs/*.json` | `~/.cache/agent-board/runs/*.json` | `AGENT_BOARD_DROPIN_DIR` |

### Viewing a Remote Linux VM Board from Your Mac (Tailscale Mode)

When Swarm Board runs on a headless Linux VM and you want to open it directly in Chrome/Safari on your Mac without an SSH tunnel (and without opening T3 Code's VM preview panel), create `~/.config/swarm-board/config.json` on the VM:

```json
{
  "host": "tailscale",
  "open_in_preview": false
}
```

Or pass `--host tailscale` / `SWARM_BOARD_HOST=tailscale`. In Tailscale mode, Swarm Board binds **only** the VM's Tailscale `100.64.0.0/10` IPv4 interface plus a companion `127.0.0.1` loopback listener on the same port (never `0.0.0.0` or public/LAN interfaces), validates Tailscale/MagicDNS/loopback `Host` headers, and returns both `url` (`http://<tailscale-ip>:18420`, for Mac) and `local_url` (`http://127.0.0.1:18420`, for VM).

### Linux: run the board as its own systemd user service

Agent sessions run in their own systemd scopes, and systemd stops everything in a scope when the session ends. A board started from inside a session would stop with it. On Linux, step 4 of the setup checklist (section 2) installs one user unit. `ensure` then starts that unit, and starting a unit that is already running does nothing.

- The unit reads the host and idle time from `config.json`, and it uses the default state directory.
- Launches that pass `--port`, `--host`, `--idle-seconds`, `--state-dir`, or `--ui-dir`, or that set `SWARM_BOARD_HOST`, `SWARM_BOARD_PORT`, `SWARM_BOARD_IDLE_SECONDS`, `SWARM_BOARD_STATE_DIR`, `SWARM_BOARD_CONFIG`, or `XDG_CACHE_HOME`, still start a direct child process.
- The unit does not start at boot. After a reboot, the board starts on the next `ensure` or hook call.
- Each server holds `server.lock` in its state directory for its whole life. A second server for the same state directory exits at once instead of overwriting `server.json`.
- macOS has no systemd, so it always uses the direct child process.

---

## 5. Model Pricing & Weekly Updates

Swarm Board estimates API costs locally using layered read-only lookup tables:

1. `$SWARM_BOARD_RATES_OVERRIDE`
2. `~/.config/swarm-board/rates_override.json`
3. `<repo>/swarm_board/rates_override.json`
4. T3 Code's `usage-model-rates.json`
5. `~/.config/swarm-board/litellm-rates.json`

Check active flow model coverage or sync LiteLLM pricing:

```bash
python3 scripts/update_rates.py --check
python3 scripts/update_rates.py --sync
```

Or ask `/swarm-board` to update missing model prices from official provider pages, or set up weekly automation (`launchd` on macOS, `cron` on Linux, or `/schedule` in T3 Code) using [weekly-rates.md](./skills/swarm-board/references/weekly-rates.md).
