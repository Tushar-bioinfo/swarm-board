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
   It reuses or spawns the detached server and prints JSON, e.g.:
   ```json
   {"ok": true, "host": "127.0.0.1", "port": 18420, "url": "http://127.0.0.1:18420", "local_url": "http://127.0.0.1:18420", "pid": 12345, "started": true, "open_in_preview": true}
   ```
2. Route the URL based on `open_in_preview` and `host`:
   - If `open_in_preview` is `false` or `host` is not `127.0.0.1` (for example, a remote VM serving over Tailscale): **do not auto-call `preview_open`** (unless the user explicitly asks to open it inside T3 Preview) and do not launch a browser on the VM/host. Always return **both** links in your reply: the primary Tailscale `url` (for the Mac browser, e.g. `http://100.73.34.46:18420`) **and** the VM loopback `local_url` (`http://127.0.0.1:<port>`) so the user can check it on the VM when needed.
   - Otherwise (`open_in_preview` is `true` and `host` is `127.0.0.1`): if running inside T3 Code with `preview_open` available, open the returned port in the embedded preview panel:
     `preview_open(target={"kind": "environment-port", "port": <port>})`
     Otherwise return `url` directly (only launch the system browser with `open` / `xdg-open` if the user asks).
3. Lifecycle & config notes:
   - Strictly read-only, zero external network traffic; binds strictly to loopback (`127.0.0.1`) by default, or to the Tailscale IPv4 (`100.64.0.0/10`) when configured via `--host tailscale`, `SWARM_BOARD_HOST=tailscale`, or `~/.config/swarm-board/config.json` (`{"host": "tailscale", "open_in_preview": false}`).
   - Auto-exits after 2 hours with 0 open SSE clients (**0 MB idle RSS**). Customize via `--idle-seconds`, `SWARM_BOARD_IDLE_SECONDS`, or `"idle_seconds"` in `~/.config/swarm-board/config.json` (e.g., `3600` for 1h, `10800` for 3h).

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

## 5. Worker role labels & task summaries

Swarm Board automatically derives crisp 2–3 word sibling-differentiating `label`s (Map & Breadcrumb) and 1-sentence `task` descriptions (Agent Detail heading) offline with zero latency. To optionally enrich labels in the background with a configured local CLI model (persisted in `~/.cache/swarm-board/labels-cache.json`):

```bash
./scripts/board.sh labels --status
./scripts/board.sh labels --preview
./scripts/board.sh labels --set agy:gemini-3.8-flash-low
./scripts/board.sh labels --run --limit 5
./scripts/board.sh labels --disable
./scripts/board.sh labels --clear
```
