#!/usr/bin/env bash
# Daily scoreboard job. Run from cron on the forecasting machine, in Brisbane time:
#
#   CRON_TZ=Australia/Brisbane
#   45 12 * * *  /path/to/nem-forecast-scoreboard/run_daily.sh commit
#   30 6  * * *  /path/to/nem-forecast-scoreboard/run_daily.sh score
#
# Before 12:45, the forecasting pipeline must write tomorrow's forecast to
#   $OCS_FORECAST_DIR/YYYY-MM-DD.csv
# with the columns: region,interval_end,price_forecast,p_over_300
# Keep that folder private. It is not part of this repo.
set -euo pipefail
cd "$(dirname "$0")"
export TZ=Australia/Brisbane
: "${OCS_FORECAST_DIR:?set OCS_FORECAST_DIR to the private forecast folder}"

git pull --ff-only --quiet origin main

case "${1:-}" in
  commit)
    day=$(date -d tomorrow +%F)
    python3 -I scoreboard.py commit "$OCS_FORECAST_DIR/$day.csv" --day "$day"
    ;;
  score)
    day=$(date -d yesterday +%F)
    python3 -I scoreboard.py score --day "$day" --private-dir "$OCS_FORECAST_DIR"
    ;;
  *)
    echo "usage: $0 commit|score" >&2; exit 2 ;;
esac
