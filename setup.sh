#!/bin/bash
# Setup / install for the visa bot. Safe to re-run. Works on any Mac.
#
#   ./setup.sh           create venv, install deps, generate launchd plists
#   ./setup.sh --load    the above, then load the agents (start polling)
set -euo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"

# ---- Python: needs 3.10+; the macOS system python3 is too old on many machines ----
PY=""
for c in /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.10 \
         /usr/local/bin/python3.12 /usr/local/bin/python3.11 /usr/local/bin/python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && \
       "$c" -c 'import sys; exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
        PY="$c"; break
    fi
done
if [ -z "$PY" ]; then
    echo "ERROR: need Python 3.10 or newer. Install it with:  brew install python@3.12" >&2
    exit 1
fi
echo "Using $PY ($("$PY" --version))"

# ---- macOS TCC: launchd agents cannot read ~/Documents, ~/Desktop or ~/Downloads.
# A bot placed there hangs silently during Python startup with no error anywhere. ----
case "$HERE" in
    "$HOME"/Documents/*|"$HOME"/Desktop/*|"$HOME"/Downloads/*)
        echo "ERROR: $HERE is inside a macOS protected folder." >&2
        echo "       A background launchd agent cannot read it, and the bot will hang" >&2
        echo "       silently at startup. Move this directory to ~/visabot and re-run." >&2
        exit 1;;
esac

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt
mkdir -p state logs recon tests/fixtures

if [ ! -f .env ]; then
    cp .env.example .env
    echo ">>> Created .env — EDIT IT with your AIS + Pushover credentials: $HERE/.env"
fi
chmod 600 .env

# ---- generate launchd plists for THIS machine's paths ----
mkdir -p launchd
gen_plist() {   # $1 = label suffix, $2 = cli flag, $3 = schedule xml
cat > "launchd/com.shawn.visabot.$1.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.shawn.visabot.$1</string>
    <key>ProgramArguments</key>
    <array>
        <string>$HERE/.venv/bin/python</string>
        <string>-m</string><string>visabot.main</string><string>$2</string>
    </array>
    <key>WorkingDirectory</key><string>$HERE</string>
$3
    <key>StandardOutPath</key><string>$HERE/logs/launchd-$1.log</string>
    <key>StandardErrorPath</key><string>$HERE/logs/launchd-$1.log</string>
</dict>
</plist>
PLIST
}
gen_plist poller --poll '    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>120</integer>'
gen_plist digest --digest '    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>'
echo "Generated launchd/*.plist for $HERE"

if [ "${1:-}" = "--load" ]; then
    cp launchd/*.plist ~/Library/LaunchAgents/
    for j in poller digest; do
        launchctl bootout "gui/$(id -u)/com.shawn.visabot.$j" 2>/dev/null || true
        launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.shawn.visabot.$j.plist
    done
    echo "Agents loaded. Watch with:  tail -f $HERE/logs/app.log"
else
    cat <<EOF

Setup OK. Next:
  1. Fill in .env      (AIS_EMAIL, AIS_PASSWORD, PUSHOVER_USER_KEY, PUSHOVER_APP_TOKEN)
  2. .venv/bin/python -m visabot.main --test-notify
  3. .venv/bin/python -m visabot.main --sweep      # login + all 7 consulates
  4. ./setup.sh --load                             # start polling in the background
EOF
fi
