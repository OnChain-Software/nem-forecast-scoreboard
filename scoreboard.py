#!/usr/bin/env python3
"""OCS vs AEMO public forecast scoreboard.

Two daily steps, both run from cron on the forecasting machine:

  commit  D-1, before 13:00 AEST: hash tomorrow's forecast, timestamp the hash,
          and commit the hash (not the forecast) to this public repo.
  score   D+1, after 06:00 AEST: reveal the forecast, check it against the
          committed hash, score it and AEMO's 12:30 pre-dispatch against
          actual prices, and append the rows to data/results.csv.

Standard library only. All times are NEM time (AEST, UTC+10, no daylight saving).

Market day D runs from 04:00 on D to 04:00 on D+1: 48 half-hours, labelled by
interval end, 04:30 on D through 04:00 on D+1.
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
REGIONS = ["NSW1", "QLD1", "SA1", "TAS1", "VIC1"]
SPIKE = 300.0  # A$/MWh
DEADLINE = dt.time(13, 0)  # forecast hash must be committed by 13:00 AEST on D-1
NEMWEB = "https://nemweb.com.au/Reports/Current"
RESULT_FIELDS = [
    "market_day", "region", "status", "spikes", "intervals",
    "ocs_spike_score", "aemo_spike_score", "ocs_price_error", "aemo_price_error",
    "forecast_sha256",
]
FORECAST_FIELDS = ["region", "interval_end", "price_forecast", "p_over_300"]


# ---------- helpers ----------

def day_intervals(day):
    """The 48 half-hour interval ends of market day `day` (a date)."""
    start = dt.datetime.combine(day, dt.time(4, 0))
    return [start + dt.timedelta(minutes=30 * k) for k in range(1, 49)]


def fmt(ts):
    return ts.strftime("%Y-%m-%d %H:%M")


def today_aest():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=10))).date()


def sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def git(*args, check=True):
    return subprocess.run(["git", "-C", ROOT, *args], check=check,
                          capture_output=True, text=True).stdout.strip()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "nem-forecast-scoreboard"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def nemweb_file(folder, pattern):
    """Download the newest NEMWeb file in `folder` whose name matches `pattern`."""
    listing = fetch(f"{NEMWEB}/{folder}/").decode("utf-8", "replace")
    names = sorted(set(re.findall(pattern, listing)))
    if not names:
        raise FileNotFoundError(f"No file matching {pattern} in {folder} (it may have aged out of Current/)")
    raw = fetch(f"{NEMWEB}/{folder}/{names[-1]}")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        return zf.read(zf.namelist()[0]).decode("utf-8", "replace")


def mms_table(text, group, table):
    """Rows of one table from an AEMO MMS-format CSV, as dicts."""
    header, out = None, []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3 or row[1] != group or row[2] != table:
            continue
        if row[0] == "I":
            header = row
        elif row[0] == "D" and header:
            out.append(dict(zip(header, row)))
    return out


def parse_nem_time(s):
    return dt.datetime.strptime(s.strip().strip('"'), "%Y/%m/%d %H:%M:%S")


# ---------- AEMO data ----------

def aemo_predispatch(day):
    """AEMO's 12:30 D-1 pre-dispatch for market day `day`: {(region, interval_end): price}.

    This is the run published about 12:31 on D-1 (file labelled 13:00). It is the
    first run after day-ahead bids close, and the first to cover all of day D.
    """
    prev = (day - dt.timedelta(days=1)).strftime("%Y%m%d")
    text = nemweb_file("PredispatchIS_Reports", rf"PUBLIC_PREDISPATCHIS_{prev}1300_\d+\.zip")
    wanted = set(day_intervals(day))
    out = {}
    for r in mms_table(text, "PREDISPATCH", "REGION_PRICES"):
        if r.get("INTERVENTION", "0") != "0":
            continue
        ts = parse_nem_time(r["DATETIME"])
        if ts in wanted:
            out[(r["REGIONID"], ts)] = float(r["RRP"])
    return out


def actual_prices(day):
    """Actual 30-minute prices for market day `day`: the mean of the six 5-minute
    dispatch prices in each half-hour (how the NEM settles since 5-minute settlement)."""
    text = nemweb_file("Public_Prices", rf"PUBLIC_PRICES_{day.strftime('%Y%m%d')}0000_\d+\.zip")
    five = {}
    for r in mms_table(text, "DREGION", ""):
        if r.get("INTERVENTION", "0") != "0":
            continue
        five[(r["REGIONID"], parse_nem_time(r["SETTLEMENTDATE"]))] = float(r["RRP"])
    out = {}
    for region in REGIONS:
        for end in day_intervals(day):
            vals = [five.get((region, end - dt.timedelta(minutes=5 * k))) for k in range(6)]
            if all(v is not None for v in vals):
                out[(region, end)] = sum(vals) / 6
    return out


# ---------- forecast file ----------

def read_forecast(path, day):
    """Validate an OCS forecast file and return {(region, interval_end): (price, p_spike)}."""
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != FORECAST_FIELDS:
            raise ValueError(f"forecast columns must be exactly {FORECAST_FIELDS}")
        rows = list(reader)
    out = {}
    for r in rows:
        ts = dt.datetime.strptime(r["interval_end"], "%Y-%m-%d %H:%M")
        p = float(r["p_over_300"])
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"p_over_300 out of range in row {r}")
        out[(r["region"], ts)] = (float(r["price_forecast"]), p)
    expected = {(g, t) for g in REGIONS for t in day_intervals(day)}
    if set(out) != expected:
        missing, extra = expected - set(out), set(out) - expected
        raise ValueError(f"forecast must have exactly 5 regions x 48 intervals; "
                         f"missing {len(missing)}, unexpected {len(extra)}")
    return out


# ---------- scoring ----------

def score_region(region, day, ocs, aemo, actual):
    """Score one region-day. Returns a dict of RESULT_FIELDS (without status/hash)."""
    ends = day_intervals(day)
    if not all((region, t) in actual and (region, t) in aemo for t in ends):
        return None
    y = [1.0 if actual[(region, t)] > SPIKE else 0.0 for t in ends]
    p_ocs = [ocs[(region, t)][1] for t in ends]
    p_aemo = [1.0 if aemo[(region, t)] > SPIKE else 0.0 for t in ends]
    n = len(ends)
    return {
        "spikes": int(sum(y)),
        "intervals": n,
        "ocs_spike_score": round(sum((p - o) ** 2 for p, o in zip(p_ocs, y)) / n, 6),
        "aemo_spike_score": round(sum((p - o) ** 2 for p, o in zip(p_aemo, y)) / n, 6),
        "ocs_price_error": round(sum(abs(ocs[(region, t)][0] - actual[(region, t)]) for t in ends) / n, 2),
        "aemo_price_error": round(sum(abs(aemo[(region, t)] - actual[(region, t)]) for t in ends) / n, 2),
    }


# ---------- results file ----------

def results_path():
    return os.path.join(ROOT, "data", "results.csv")


def existing_days():
    path = results_path()
    if not os.path.exists(path):
        return set()
    with open(path, newline="") as fh:
        return {r["market_day"] for r in csv.DictReader(fh)}


def append_results(rows):
    path = results_path()
    new = not os.path.exists(path)
    with open(path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=RESULT_FIELDS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in RESULT_FIELDS})


def blank_rows(day, status, digest=""):
    return [{"market_day": day.isoformat(), "region": g, "status": status, "forecast_sha256": digest}
            for g in REGIONS]


# ---------- commands ----------

def cmd_commit(args):
    day = dt.date.fromisoformat(args.day) if args.day else today_aest() + dt.timedelta(days=1)
    read_forecast(args.forecast, day)  # refuse to commit a malformed forecast
    digest = sha256_file(args.forecast)
    rel = f"hashes/{day.isoformat()}.sha256"
    path = os.path.join(ROOT, rel)
    if os.path.exists(path):
        sys.exit(f"{rel} already exists. A forecast is committed once and never replaced.")
    with open(path, "w") as fh:
        fh.write(f"{digest}  forecasts/{day.isoformat()}.csv\n")
    stamped = [rel]
    if shutil.which("ots"):
        r = subprocess.run(["ots", "stamp", path], capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(path + ".ots"):
            stamped.append(rel + ".ots")
        else:
            print("warning: OpenTimestamps stamp failed; committing the hash without it", file=sys.stderr)
    else:
        print("warning: 'ots' not installed; committing the hash without an independent timestamp", file=sys.stderr)
    if not args.no_git:
        git("add", *stamped)
        git("commit", "-m", f"Forecast hash for market day {day.isoformat()}")
        git("push", "origin", "HEAD")
    print(f"committed {rel} ({digest})")


def hash_commit_time(rel):
    """Committer time of the first commit that added `rel`, as naive AEST."""
    out = git("log", "--diff-filter=A", "--format=%cI", "--", rel, check=False)
    if not out:
        return None
    first = dt.datetime.fromisoformat(out.splitlines()[-1])
    return first.astimezone(dt.timezone(dt.timedelta(hours=10))).replace(tzinfo=None)


def cmd_score(args):
    day = dt.date.fromisoformat(args.day) if args.day else today_aest() - dt.timedelta(days=1)
    if day.isoformat() in existing_days():
        sys.exit(f"{day} is already scored. Rows are never re-scored or replaced.")
    rel_hash = f"hashes/{day.isoformat()}.sha256"
    hash_path = os.path.join(ROOT, rel_hash)
    if not os.path.exists(hash_path):
        rows = blank_rows(day, "missed")
    else:
        committed = open(hash_path).read().split()[0]
        src = os.path.join(args.private_dir, f"{day.isoformat()}.csv")
        if not os.path.exists(src) or sha256_file(src) != committed:
            rows = blank_rows(day, "hash_mismatch", committed)
        else:
            dest = os.path.join(ROOT, "forecasts", f"{day.isoformat()}.csv")
            shutil.copyfile(src, dest)
            ocs = read_forecast(dest, day)
            aemo, actual = aemo_predispatch(day), actual_prices(day)
            when = None if args.no_git else hash_commit_time(rel_hash)
            deadline = dt.datetime.combine(day - dt.timedelta(days=1), DEADLINE)
            status = "late" if when and when > deadline else "ok"
            rows = []
            for g in REGIONS:
                s = score_region(g, day, ocs, aemo, actual)
                if s is None:
                    rows.append({"market_day": day.isoformat(), "region": g,
                                 "status": "no_aemo_data", "forecast_sha256": committed})
                else:
                    rows.append({"market_day": day.isoformat(), "region": g, "status": status,
                                 "forecast_sha256": committed, **s})
    append_results(rows)
    if not args.no_git:
        to_add = ["data/results.csv"]
        if os.path.exists(os.path.join(ROOT, "forecasts", f"{day.isoformat()}.csv")):
            to_add.append(f"forecasts/{day.isoformat()}.csv")
        if shutil.which("ots"):
            subprocess.run(["ots", "upgrade", hash_path + ".ots"], capture_output=True)
            if os.path.exists(hash_path + ".ots"):
                to_add.append(rel_hash + ".ots")
        git("add", *to_add)
        git("commit", "-m", f"Score market day {day.isoformat()}")
        git("push", "origin", "HEAD")
    for r in rows:
        print(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("commit", help="hash and commit tomorrow's forecast")
    c.add_argument("forecast", help="path to the forecast CSV (kept private until scoring)")
    c.add_argument("--day", help="market day YYYY-MM-DD (default: tomorrow)")
    c.add_argument("--no-git", action="store_true", help="write files only; do not commit or push")
    s = sub.add_parser("score", help="reveal and score a market day")
    s.add_argument("--day", help="market day YYYY-MM-DD (default: yesterday)")
    s.add_argument("--private-dir", default=os.environ.get("OCS_FORECAST_DIR", ""),
                   help="folder holding the private forecast files, named YYYY-MM-DD.csv "
                        "(default: $OCS_FORECAST_DIR)")
    s.add_argument("--no-git", action="store_true", help="write files only; do not commit or push")
    args = ap.parse_args()
    {"commit": cmd_commit, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    main()
