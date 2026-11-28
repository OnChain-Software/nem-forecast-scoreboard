# NEM Forecast Scoreboard

A public, day-by-day record of OnChain Software's 24-hour-ahead NEM price forecasts, scored against AEMO's 12:30 pre-dispatch.

Live page: **https://onchain-software.github.io/nem-forecast-scoreboard/** (served by GitHub Pages from `main`).

## How it works

| When (AEST) | Step | What becomes public |
|---|---|---|
| Issue day I, by 13:00 | `run_daily.sh commit` | The SHA-256 hash of the forecast, plus an OpenTimestamps (Bitcoin) proof. The forecast itself stays private. |
| I+2, from 06:30 | `run_daily.sh score` | The forecast file, checked against the hash, and one scored row per region in `data/results.csv`. |

- **Scored window:** the 47 half-hours ending 13:30 on I through 12:30 on I+1. Every one starts after the 13:00 deadline, so none is under way when the forecast is locked in.
- **AEMO comparator:** the pre-dispatch run published about 12:31 on I (file labelled `…1300`), over the same half-hours. It is the first run after day-ahead bids close.
- **Actual price:** the mean of the six 5-minute dispatch prices in each half-hour, from the `Public_Prices` daily files. The window spans two market days, and the second day's file is published at 04:05 on I+2. That is why scoring runs on I+2.
- **Spike score:** the Brier score for "price above A$300/MWh". OCS gives a probability. AEMO's forecast counts as 1 if its price is above A$300 and 0 otherwise. Lower is better.
- **Price error:** mean absolute error in A$/MWh. Lower is better.

The forecast may use any public data published before 12:45 on I, including AEMO's 12:30 pre-dispatch.

## Rules

1. Every day is published, including days AEMO wins.
2. If no hash is committed, the day is `missed`. If the forecast doesn't match its hash, it is `hash_mismatch`. If the hash is committed after 13:00, the day is `late` and left out of the totals.
3. A forecast hash is committed once and never replaced. A scored day is never re-scored. The script refuses both.
4. Scoring rules are fixed. Any change gets a dated note on the page and in this README.

## Changes

- **2026-11-28, forecast inputs.** From issue day 2026-11-29, the OCS forecast also uses AEMO's 12:30 pre-dispatch run (the `…1300` file, published about 12:32), which is the comparator run. Before this, it used only the 12:00 run, which ends at 04:00 on I+1. The scoring rules are unchanged, and earlier rows are not re-scored.

## Forecast file format

Write the forecast to `$OCS_FORECAST_DIR/YYYY-MM-DD.csv`, where the date is the issue day. Keep that folder private and outside this repo. The file must have exactly 235 rows: 5 regions × 47 half-hours.

```
region,interval_end,price_forecast,p_over_300
NSW1,2026-11-24 13:30,85.20,0.01
...
VIC1,2026-11-25 12:30,72.10,0.00
```

`interval_end` is in AEST. `p_over_300` is the probability, from 0 to 1, that the half-hour price exceeds A$300/MWh.

## Setup on the forecasting machine (PC1)

```bash
gh repo clone OnChain-Software/nem-forecast-scoreboard     # PC1 pushes over gh HTTPS
python3 -m venv ~/scoreboard_export/venv-ots && ~/scoreboard_export/venv-ots/bin/pip install opentimestamps-client
# crontab (PC1 runs in UTC; AEST = UTC+10):
#   45 2  * * *  OCS_FORECAST_DIR=/path/to/private OTS_VENV=$HOME/scoreboard_export/venv-ots /path/to/run_daily.sh commit
#   30 20 * * *  OCS_FORECAST_DIR=/path/to/private OTS_VENV=$HOME/scoreboard_export/venv-ots /path/to/run_daily.sh score
```

`scoreboard.py` uses only the Python standard library. NEMWeb's `Current/` folders keep about two weeks of files, so a missed scoring run can be re-run within that window. A missed forecast deadline can't be made up.

## Verify a forecast yourself

```bash
sha256sum forecasts/2026-11-24.csv     # compare with hashes/2026-11-24.sha256
ots verify hashes/2026-11-24.sha256.ots
git log --format='%H %cI' -- hashes/2026-11-24.sha256
```

## Tests

```bash
python3 -m unittest discover tests
```

Market data source: AEMO (NEMWeb). Not endorsed by AEMO.
