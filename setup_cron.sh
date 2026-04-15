#!/usr/bin/env bash
# Installs a daily cron job that runs check_new_trials.py at 7:00 AM UTC.
# Usage:  bash setup_cron.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(command -v python3 || true)"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"

if [ -x "$VENV_PYTHON" ]; then PYTHON="$VENV_PYTHON"; fi
if [ -z "$PYTHON" ]; then echo "ERROR: python3 not found."; exit 1; fi

CRON_CMD="cd $SCRIPT_DIR && $PYTHON check_new_trials.py >> $SCRIPT_DIR/cron.log 2>&1"
CRON_SCHEDULE="0 7 * * *"
CRON_LINE="$CRON_SCHEDULE $CRON_CMD"

( crontab -l 2>/dev/null | grep -v "check_new_trials.py"; echo "$CRON_LINE" ) | crontab -

echo "Cron job installed:"
echo "  $CRON_LINE"
echo ""
echo "View with:    crontab -l"
echo "Remove with:  crontab -l | grep -v check_new_trials.py | crontab -"
