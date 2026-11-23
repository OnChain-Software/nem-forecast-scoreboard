#!/usr/bin/env bash
# Daily scoreboard job. Run from cron on the forecasting machine.
# PC1's clock and crontab run in UTC, so use UTC times (AEST = UTC+10, no daylight saving):
#
#   45 2  * * *  OCS_FORECAST_DIR=... /path/to/nem-forecast-scoreboard/run_daily.sh commit   # 12:45 AEST
#   30 20 * * *  OCS_FORECAST_DIR=... /path/to/nem-forecast-scoreboard/run_daily.sh score    # 06:30 AEST next day
#
# Before 12:45 AEST, the forecast export must write today's forecast to
#   $OCS_FORECAST_DIR/YYYY-MM-DD.csv   (YYYY-MM-DD = today's date in AEST)
# with the columns region,interval_end,price_forecast,p_over_300 and 235 rows
# (5 regions x 47 half-hours ending 13:30 today to 12:30 tomorrow, AEST).
# Keep that folder private. It is not part of this repo.
set -euo pipefail
cd "$(dirname "$0")"
export TZ=Australia/Brisbane
: "${OCS_FORECAST_DIR:?set OCS_FORECAST_DIR to the private forecast folder}"
# OpenTimestamps client, if installed in a venv (PC1 blocks pip --user installs).
if [ -n "${OTS_VENV:-}" ]; then export PATH="$OTS_VENV/bin:$PATH"; fi

git pull --ff-only --quiet origin main

case "${1:-}" in
  commit)
    day=$(date +%F)
    python3 -I scoreboard.py commit "$OCS_FORECAST_DIR/$day.csv" --day "$day"
    ;;
  score)
    day=$(date -d '2 days ago' +%F)   # the window ends 12:30 the day after issue; final prices land 04:05 the day after that
    python3 -I scoreboard.py score --day "$day" --private-dir "$OCS_FORECAST_DIR"
    ;;
  *)
    echo "usage: $0 commit|score" >&2; exit 2 ;;
esac
