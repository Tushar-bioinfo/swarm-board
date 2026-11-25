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
- `python3 scripts/install.py --targets antigravity,claude,codex` — override auto-detection
- `python3 scripts/install.py --no-hooks` — install `/swarm-board` skill symlinks only
- `python3 scripts/install.py --verify` — validate Python version, UI build, executable bits, skills, and hooks
- `python3 scripts/install.py --uninstall` — remove only swarm-board symlinks and hooks (preserves all other configs)

---

## 2. Agent Setup Instructions

If you ask an AI coding agent on a new Mac or Linux machine *"Set up swarm-board using INSTALL.md"*, the agent can follow this exact 4-step checklist:

1. **Ensure repo is cloned locally** and enter its root directory (`cd /path/to/swarm-board`).
2. **Install skill symlinks & hooks** (builds `swarm_board/ui/index.html` if needed and sets `+x` permissions):
   ```bash
   python3 scripts/install.py
   ```
3. **Verify install & run test suite**:
   ```bash
   python3 scripts/install.py --verify && python3 -m unittest discover -s tests -v
   ```
4. **Smoke-test server lifecycle & clean up**:
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
| **Grok & Drop-in JSON** | `~/.grok/sessions/*/summary.json` + `usage.json`, plus drop-in JSON runs (`$AGENT_BOARD_DROPIN_DIR` or `~/.cache/agent-board/runs/*.json`) | Launches from any host shell via `scripts/ensure-server.sh` | Same host hook if started from T3 / Antigravity / Claude Code |

---

## 4. Cross-Platform Data, Cache & Env Paths

| Purpose | Linux | macOS | Env Override |
| :--- | :--- | :--- | :--- |
| **Server runtime state (`server.json`, PID, logs)** | `$XDG_CACHE_HOME/swarm-board` or `~/.cache/swarm-board` | `~/Library/Caches/swarm-board` | `SWARM_BOARD_STATE_DIR` |
| **T3 Code userdata & Antigravity providers** | `~/.t3/userdata` | `~/Library/Application Support/T3 Code/userdata` (or `~/.t3/userdata`) | `T3_USERDATA_DIR` |
| **Antigravity brain roots** | `~/.t3/userdata/providers/antigravity/*/antigravity-acp/brain`, `~/.gemini/*/brain` | `~/Library/Application Support/T3 Code/userdata/providers/antigravity/*/antigravity-acp/brain`, `~/.t3/...`, `~/.gemini/*/brain` | `T3_USERDATA_DIR` |
| **Rates overrides (verified & LiteLLM cache)** | `~/.config/swarm-board/rates_override.json`, `~/.config/swarm-board/litellm-rates.json` | `~/.config/swarm-board/rates_override.json`, `~/.config/swarm-board/litellm-rates.json` | `SWARM_BOARD_RATES_OVERRIDE` |
| **Server bind host & port scan** | `127.0.0.1:18420..18449` | `127.0.0.1:18420..18449` | `SWARM_BOARD_PORT` |
| **Idle auto-exit timeout** | `900` seconds (0 MB idle RSS when closed) | `900` seconds (0 MB idle RSS when closed) | `SWARM_BOARD_IDLE_SECONDS` |
| **Drop-in custom JSON flows** | `~/.cache/agent-board/runs/*.json` | `~/.cache/agent-board/runs/*.json` | `AGENT_BOARD_DROPIN_DIR` |

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
