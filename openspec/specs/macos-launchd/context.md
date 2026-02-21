# macOS always-on (launchd, no Docker)

Run `codex-lb` continuously on macOS **without Docker**, while still using **your local repo checkout**.

This setup:

- Installs `codex-lb` as a `uv tool` in **editable** mode, so the `codex-lb` command runs *your working tree code*.
- Uses a `launchd` **LaunchAgent** to keep it running in the background and restart on crash/login.

## Prereqs

- `uv` installed
- This repo checked out locally

## 1) Install your repo as an editable tool

From the repo root:

```bash
uv tool install --editable .
```

Notes:

- Editable means code changes in your checkout take effect after a restart (no reinstall needed).
- If dependencies change (`pyproject.toml` / `uv.lock`), reinstall with:

```bash
uv tool install --editable . --force
```

## 2) Locate the installed executable (absolute path)

`launchd` does not load your interactive shell config, so don’t rely on `PATH`. Use an absolute path:

```bash
BIN_DIR="$(uv tool dir --bin)"
echo "$BIN_DIR/codex-lb"
```

## 3) Create the LaunchAgent plist

Create `~/Library/LaunchAgents/com.codex-lb.plist` and set:

- `ProgramArguments[0]` to the absolute `codex-lb` path from step 2
- log paths to absolute paths (avoid `~`)

Template:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.codex-lb</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>WorkingDirectory</key>
    <string>/ABS/PATH/TO/THIS/REPO</string>

    <key>ProgramArguments</key>
    <array>
      <string>/ABS/PATH/TO/uv-tool-bin/codex-lb</string>
      <string>--host</string>
      <string>127.0.0.1</string>
      <string>--port</string>
      <string>2455</string>
    </array>

    <key>StandardOutPath</key>
    <string>/ABS/PATH/TO/HOME/.codex-lb/logs/codex-lb.out.log</string>

    <key>StandardErrorPath</key>
    <string>/ABS/PATH/TO/HOME/.codex-lb/logs/codex-lb.err.log</string>

    <!-- Optional: set env vars here (launchd does not read your shell rc files). -->
    <!--
    <key>EnvironmentVariables</key>
    <dict>
      <key>CODEX_LB_ACCESS_LOG_ENABLED</key>
      <string>true</string>
    </dict>
    -->
  </dict>
</plist>
```

## 4) Load, start, restart

Prepare log directory:

```bash
mkdir -p "$HOME/.codex-lb/logs"
```

Load + start:

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.codex-lb.plist" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.codex-lb.plist"
launchctl kickstart -k "gui/$(id -u)/com.codex-lb"
```

Restart (after code changes):

```bash
launchctl kickstart -k "gui/$(id -u)/com.codex-lb"
```

Status:

```bash
launchctl print "gui/$(id -u)/com.codex-lb"
```

## 5) Logs

```bash
tail -f "$HOME/.codex-lb/logs/codex-lb.out.log" "$HOME/.codex-lb/logs/codex-lb.err.log"
```

## Troubleshooting

- **Ports in use**: default proxy port is `2455`. The OAuth callback uses `1455` when adding accounts. Ensure they’re free.
- **Multiple instances**: don’t run a separate foreground `uvx/uv run` instance while the LaunchAgent is running.
- **PATH issues**: always use the absolute `codex-lb` path in `ProgramArguments`.
- **Multi-machine**: if you’re tunneling with SSH, run one authority instance and have clients port-forward to it.

