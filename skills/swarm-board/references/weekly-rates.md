# Weekly Model Rate Sync

Keep local API cost estimates current without manual work by refreshing `~/.config/swarm-board/litellm-rates.json` once a week.

## Rate precedence order

Swarm Board layers pricing sources from highest to lowest priority:

1. `$SWARM_BOARD_RATES_OVERRIDE` (optional env JSON file)
2. `~/.config/swarm-board/rates_override.json` (user verified overrides)
3. `<repo>/swarm_board/rates_override.json` (shared repo overrides)
4. T3 Code's `~/.t3/userdata/usage-model-rates.json` (or `~/Library/Application Support/T3 Code/userdata/usage-model-rates.json` on macOS)
5. `~/.config/swarm-board/litellm-rates.json` (synced by `scripts/update_rates.py --sync`)

---

## Option A — macOS (`launchd`)

Create `~/Library/LaunchAgents/com.swarm-board.rates.plist` (replace `/path/to/swarm-board` with your clone path):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.swarm-board.rates</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/path/to/swarm-board/scripts/update_rates.py</string>
    <string>--sync</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>0</integer>
    <key>Hour</key>
    <integer>9</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/tmp/swarm-board-rates.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/swarm-board-rates.log</string>
</dict>
</plist>
```

Load it:

```bash
launchctl load -w ~/Library/LaunchAgents/com.swarm-board.rates.plist
```

---

## Option B — Linux (`cron`)

Add via `crontab -e`:

```cron
0 9 * * 0 python3 /path/to/swarm-board/scripts/update_rates.py --sync >/tmp/swarm-board-rates.log 2>&1
```

---

## Option C — T3 Code `/schedule` (full official verification)

For missing or newly released models where you want an agent to verify official provider pages, run a weekly `/schedule` prompt:

> Run `./scripts/board.sh rates --sync`. If any active models remain `[missing]` or new frontier models were released this week, check the official Anthropic / OpenAI / Google / xAI pricing pages, add verified per-token USD rates (`input_cost_per_token`, `output_cost_per_token`, `cache_read_input_token_cost`, `cache_creation_input_token_cost`, `source_url`, `verified_at`) to `~/.config/swarm-board/rates_override.json`, and run `./scripts/board.sh rates --check` to confirm coverage.
