check: lint format-check type fe-check test

test:
    uv run --group dev pytest

# Short aliases.
t: test
ci: check

lint:
    uv run --group dev ruff check .

format:
    uv run --group dev ruff format .

format-check:
    uv run --group dev ruff format --check .

type:
    uv run --group dev pyright

fe-syntax:
    sh -c 'command -v node >/dev/null 2>&1 || { echo "node not found; skipping fe-syntax"; exit 0; }; node --check app/static/index.js; node --check app/static/selection_utils.js; node --check app/static/sort_utils.js; node --check app/static/state_defaults.js; node --check app/static/ui_utils.js'

fe-assets:
    uv run --group dev python scripts/check_frontend_assets.py

fe-unit:
    uv run --group dev pytest tests/unit/test_frontend_selection_utils.py tests/unit/test_frontend_sort_utils.py tests/unit/test_frontend_state_defaults.py tests/unit/test_frontend_ui_utils.py

fe-check: fe-syntax fe-assets

# macOS launchd helpers (no Docker).
#
# - Runbook: `openspec/specs/macos-launchd/context.md`
# - LaunchAgent: `~/Library/LaunchAgents/com.codex-lb.plist` (label: `com.codex-lb`)
# - Logs: `~/.codex-lb/logs/codex-lb.{out,err}.log`
#
# Notes:
# - `launchd-install` writes/updates the plist using this repo path and the `uv tool` binary path.
# - `launchd-start/restart/stop/status/logs` assume the plist already exists.
launchd-status:
    launchctl print "gui/$(id -u)/com.codex-lb"

launchd-restart:
    launchctl kickstart -k "gui/$(id -u)/com.codex-lb"

launchd-stop:
    launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.codex-lb.plist"

launchd-start:
    launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.codex-lb.plist" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.codex-lb.plist"
    launchctl kickstart -k "gui/$(id -u)/com.codex-lb"

launchd-logs:
    tail -f "$HOME/.codex-lb/logs/codex-lb.out.log" "$HOME/.codex-lb/logs/codex-lb.err.log"

launchd-install:
    #!/usr/bin/env bash
    set -eu
    REPO="{{justfile_directory()}}"
    cd "$REPO"

    uv tool install --editable . --force
    BIN_DIR="$(uv tool dir --bin)"
    CODEX_LB_BIN="$BIN_DIR/codex-lb"

    LOG_DIR="$HOME/.codex-lb/logs"
    PLIST="$HOME/Library/LaunchAgents/com.codex-lb.plist"
    mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"

    cat > "$PLIST" <<EOF
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
        <string>$REPO</string>
    
        <key>ProgramArguments</key>
        <array>
          <string>$CODEX_LB_BIN</string>
          <string>--host</string>
          <string>127.0.0.1</string>
          <string>--port</string>
          <string>2455</string>
        </array>
    
        <key>StandardOutPath</key>
        <string>$LOG_DIR/codex-lb.out.log</string>
    
        <key>StandardErrorPath</key>
        <string>$LOG_DIR/codex-lb.err.log</string>
      </dict>
    </plist>
    EOF

    launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    launchctl kickstart -k "gui/$(id -u)/com.codex-lb"

    echo "OK: installed $PLIST"
    echo "OK: logs $LOG_DIR/codex-lb.{out,err}.log"
