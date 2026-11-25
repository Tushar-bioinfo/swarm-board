---
name: swarm-board
description: Launch and inspect the local Swarm Board multi-agent viewer (T3 delegations, Antigravity swarms, Claude teams/subagents, Codex, Grok, committee/orchestrate) and update verified API model pricing.
---

# Swarm Board

Read-only local viewer for multi-agent runs, agent telemetry, file citations, message logs, and token/cost statistics.

## 1. Open or launch the board (default)

1. Run [board.sh](./scripts/board.sh) from the skill folder:
   ```bash
   ./scripts/board.sh ensure
   ```
   It parses or spawns the detached loopback server and prints JSON:
   ```json
   {"ok": true, "port": 18420, "url": "http://127.0.0.1:18420", "pid": 12345, "started": true}
   ```
2. If running inside T3 Code with `preview_open` available, open the returned port in the embedded preview panel:
   `preview_open(target={"kind": "environment-port", "port": <port>})`
   Otherwise, return the `http://127.0.0.1:<port>` URL directly to the user (only launch the system browser with `open` / `xdg-open` if the user asks).
3. Lifecycle notes:
   - Strictly read-only, loopback-only (`127.0.0.1`), zero external network traffic.
   - Auto-exits after 15 minutes with 0 open SSE clients (**0 MB idle RSS**).

## 2. Check status or stop

```bash
./scripts/board.sh status
./scripts/board.sh stop
```

## 3. Verify and update model API rates

Run a quick check against live local flows:

```bash
./scripts/board.sh rates --check
```

To pull the latest LiteLLM catalog into `~/.config/swarm-board/litellm-rates.json` and re-check coverage:

```bash
./scripts/board.sh rates --sync
```

If any active model is reported `[missing]` or the user asks to verify pricing against official provider docs:

1. Fetch the official provider pricing page (Anthropic, OpenAI, Google, xAI). Never guess or extrapolate prices.
2. Compute exact per-token USD rates (`1e-6` * $/MTok):
   - `input_cost_per_token`
   - `output_cost_per_token`
   - `cache_read_input_token_cost` (when published)
   - `cache_creation_input_token_cost` (when published)
3. Write the verified entry into `~/.config/swarm-board/rates_override.json` (or `swarm_board/rates_override.json` if intentionally updating repo defaults) with `"source_url"` and `"verified_at": "YYYY-MM-DD"` under `"models"`.
4. Re-run `./scripts/board.sh rates --check` to confirm `[ok]`.

## 4. Automated weekly rate sync

See [weekly-rates.md](./references/weekly-rates.md) for `launchd` (macOS), `cron` (Linux), and T3 Code `/schedule` setups.
