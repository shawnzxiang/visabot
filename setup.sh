#!/bin/bash
# One-time setup for the visa bot. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

PY=/opt/homebrew/bin/python3.10   # system python3 is 3.7.9 — too old

if [ ! -d .venv ]; then
    "$PY" -m venv .venv
fi
.venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt

mkdir -p state logs recon tests/fixtures

if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    echo ">>> Created .env — EDIT IT NOW with your AIS + Gmail credentials: $(pwd)/.env"
else
    chmod 600 .env
fi

echo "Setup OK. Next steps:"
echo "  1. Fill in .env (AIS_EMAIL, AIS_PASSWORD, GMAIL_APP_PASSWORD)"
echo "  2. .venv/bin/python -m visabot.main --test-email"
echo "  3. .venv/bin/python -m visabot.main --recon"
echo "  4. .venv/bin/python -m visabot.main --once"
echo "  5. Go live:"
echo "     cp launchd/*.plist ~/Library/LaunchAgents/"
echo "     launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/com.shawn.visabot.poller.plist"
echo "     launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/com.shawn.visabot.digest.plist"
