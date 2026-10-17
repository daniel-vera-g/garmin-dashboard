#!/usr/bin/env bash
# Garmin health dashboard.
#   ./run.sh            start the server in this terminal and open the dashboard
#   ./run.sh install    run it in the background at every login (recommended)
#   ./run.sh uninstall  stop that and remove it
#   ./run.sh status     is the background service running?
#   ./run.sh sync       one-shot sync + rebuild, no server
#   ./run.sh build      rebuild from cached data only
#   ./run.sh test       run the test suite
#   ./run.sh login      re-authenticate with Garmin
#   ./run.sh hosted-secrets  upload config + Garmin tokens for the hosted copy (docs/hosting.md)
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"
LABEL="local.garmin-dashboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT="$(.venv/bin/python -c 'import settings; print(settings.load_athlete()["app"]["port"])' 2>/dev/null || echo 8765)"
URL="http://127.0.0.1:$PORT/"

if [ ! -x .venv/bin/python ]; then
  echo "No virtualenv found. Creating one..."
  uv venv --python 3.13 .venv
  uv pip install --python .venv/bin/python -r requirements.txt
fi

write_plist() {
  mkdir -p "$HOME/Library/LaunchAgents" data
  cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/.venv/bin/python</string>
    <string>$DIR/serve.py</string>
    <string>--no-browser</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict><key>DASHBOARD_RESTART_ON_CHANGE</key><string>1</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ProcessType</key><string>Adaptive</string>
  <key>StandardOutPath</key><string>$DIR/data/serve.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/serve.log</string>
</dict>
</plist>
PLISTEOF
}

# Prefer the served page when the background service is up: that one has the
# Sync button, the bare file does not.
open_result() {
  if curl -sf "${URL}api/status" >/dev/null 2>&1; then open "$URL"; else open dashboard.html; fi
}

[ -f private/config.toml ] || [ -n "${DASHBOARD_CONFIG:-}" ] || \
  echo "note: private/config.toml missing - using placeholders. mkdir -p private && cp config.example.toml private/config.toml"

case "${1:-serve}" in
  serve) exec .venv/bin/python serve.py ;;
  login) exec .venv/bin/python login.py ;;
  build) .venv/bin/python build_dashboard.py && open_result ;;
  test)  exec .venv/bin/python -m unittest discover -s tests -t . ;;
  sync)  .venv/bin/python garmin_sync.py
         .venv/bin/python build_dashboard.py
         open_result ;;

  install)
    write_plist
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    sleep 2
    if curl -sf "${URL}api/status" >/dev/null; then
      echo "Running in the background. It will start again at every login."
      echo "Dashboard: $URL"
      open "$URL"
    else
      echo "Started, but not answering yet. Check the log:"
      echo "  tail -20 $DIR/data/serve.log"
      exit 1
    fi ;;

  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Stopped and removed. './run.sh' still works for a one-off server." ;;

  status)
    if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
      echo "service: installed"
      launchctl print "gui/$(id -u)/$LABEL" | grep -E '^\s+(state|pid) ' || true
    else
      echo "service: not installed"
    fi
    if curl -sf "${URL}api/status" >/dev/null 2>&1; then
      echo "responding: yes  ($URL)"
    else
      echo "responding: no"
    fi ;;

  hosted-secrets)
    # The hosted copy (docs/hosting.md) reads these from GitHub Actions secrets.
    # Re-run after a Coach update or ./run.sh login, so the hosted copy matches.
    command -v gh >/dev/null || { echo "needs the GitHub CLI (gh)"; exit 1; }
    [ -f private/config.toml ] || { echo "private/config.toml missing"; exit 1; }
    gh secret set DASHBOARD_CONFIG_TOML < private/config.toml
    tar -czf - -C "$HOME" .garminconnect | base64 | gh secret set GARMIN_TOKENSTORE_TGZ_B64
    if ! gh secret list | grep -q '^CACHE_KEY'; then
      openssl rand -base64 32 | gh secret set CACHE_KEY      # set once; it encrypts the cache
    fi
    echo "Secrets updated. Run it now: gh workflow run hosted.yml" ;;

  *) echo "usage: ./run.sh [serve|install|uninstall|status|sync|build|test|login|hosted-secrets]"; exit 1 ;;
esac
