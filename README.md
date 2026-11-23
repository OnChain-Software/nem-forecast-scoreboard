# NEM Forecast Scoreboard

A public, day-by-day record of OnChain Software's day-ahead NEM price forecasts, scored against AEMO's 12:30 pre-dispatch.

Live page: **https://onchain-software.github.io/nem-forecast-scoreboard/** (served by GitHub Pages from `main`).

## How it works

| When (AEST) | Step | What becomes public |
|---|---|---|
| D−1, by 13:00 | `run_daily.sh commit` | The SHA-256 hash of the forecast for market day D, plus an OpenTimestamps proof. The forecast itself stays private. |
| D+1, from 06:00 | `run_daily.sh score` | The forecast file, checked against the hash, and one scored row per region in `data/results.csv`. |

- **Market day D:** 04:00 on D to 04:00 on D+1, which is 48 half-hours labelled by interval end.
- **AEMO comparator:** the pre-dispatch run published around 12:31 on D−1, in the file labelled `…1300`. It is the first run after day-ahead bids close.
- **Actual price:** the mean of the six 5-minute dispatch prices in each half-hour, taken from the `Public_Prices` daily file.
- **Spike score:** the Brier score for "price above A$300/MWh". OCS gives a probability. AEMO's forecast counts as 1 if its price is above A$300 and 0 otherwise. Lower is better.
- **Price error:** mean absolute error in A$/MWh. Lower is better.

## Rules

1. Every day is published, including days AEMO wins.
2. No hash committed means the day is `missed`. A forecast that doesn't match its hash is marked `hash_mismatch`. A hash committed after 13:00 on D−1 is marked `late` and left out of the totals.
3. A forecast hash is committed once and never replaced. A scored day is never re-scored. The script refuses both.
4. Scoring rules are fixed. Any change gets a dated note on the page and in this README.

## Forecast file format

The forecast is written to `$OCS_FORECAST_DIR/YYYY-MM-DD.csv`. Keep that folder private, outside this repo. It must contain exactly 240 rows: 5 regions × 48 half-hours.

```
region,interval_end,price_forecast,p_over_300
NSW1,2026-11-24 04:30,85.20,0.01
...
VIC1,2026-11-25 04:00,72.10,0.00
```

`interval_end` is in AEST. `p_over_300` is the probability, from 0 to 1, that the half-hour price is above A$300/MWh.

## Setup on the forecasting machine

```bash
git clone git@github.com:OnChain-Software/nem-forecast-scoreboard.git
pip install opentimestamps-client     # optional but recommended; provides `ots`
export OCS_FORECAST_DIR=/path/to/private/forecasts
crontab -e
#   CRON_TZ=Australia/Brisbane
#   45 12 * * *  OCS_FORECAST_DIR=/path/to/private/forecasts /path/to/nem-forecast-scoreboard/run_daily.sh commit
#   30 6  * * *  OCS_FORECAST_DIR=/path/to/private/forecasts /path/to/nem-forecast-scoreboard/run_daily.sh score
```

The forecasting pipeline must write tomorrow's file before 12:45. It may use any public data published before then, including AEMO's 12:30 pre-dispatch.

`scoreboard.py` uses only the Python standard library. NEMWeb's `Current/` folders keep about two weeks of files, so a missed scoring run can be re-run within that window. Forecast deadlines can't be made up.

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
