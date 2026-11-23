"""Offline tests: python3 -m unittest discover tests"""
import csv
import datetime as dt
import os
import shutil
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scoreboard as sb  # noqa: E402

DAY = dt.date(2026, 11, 22)


def write_forecast(path, price=50.0, p=0.1, drop=0):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(sb.FORECAST_FIELDS)
        rows = [(g, sb.fmt(t), price, p) for g in sb.REGIONS for t in sb.day_intervals(DAY)]
        for r in rows[: len(rows) - drop]:
            w.writerow(r)


class Intervals(unittest.TestCase):
    def test_window(self):
        ends = sb.day_intervals(DAY)
        self.assertEqual(len(ends), 47)
        self.assertEqual(ends[0], dt.datetime(2026, 11, 22, 13, 30))   # first half-hour starts at the deadline
        self.assertEqual(ends[-1], dt.datetime(2026, 11, 23, 12, 30))


class Forecast(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_valid(self):
        path = os.path.join(self.tmp, "f.csv")
        write_forecast(path)
        self.assertEqual(len(sb.read_forecast(path, DAY)), 235)

    def test_incomplete_rejected(self):
        path = os.path.join(self.tmp, "f.csv")
        write_forecast(path, drop=1)
        with self.assertRaises(ValueError):
            sb.read_forecast(path, DAY)

    def test_bad_probability_rejected(self):
        path = os.path.join(self.tmp, "f.csv")
        write_forecast(path, p=1.5)
        with self.assertRaises(ValueError):
            sb.read_forecast(path, DAY)


class Scoring(unittest.TestCase):
    def test_brier_and_mae(self):
        ends = sb.day_intervals(DAY)
        actual = {("SA1", t): (500.0 if i == 0 else 100.0) for i, t in enumerate(ends)}
        aemo = {("SA1", t): (400.0 if i < 2 else 90.0) for i, t in enumerate(ends)}
        ocs = {("SA1", t): ((450.0, 0.8) if i == 0 else (100.0, 0.0)) for i, t in enumerate(ends)}
        s = sb.score_region("SA1", DAY, ocs, aemo, actual)
        self.assertEqual(s["spikes"], 1)
        self.assertAlmostEqual(s["ocs_spike_score"], round(0.2 ** 2 / 47, 6))
        self.assertAlmostEqual(s["aemo_spike_score"], round(1 / 47, 6))   # one false alarm
        self.assertAlmostEqual(s["ocs_price_error"], round(50 / 47, 2))
        self.assertAlmostEqual(s["aemo_price_error"], round((100 + 300 + 45 * 10) / 47, 2))

    def test_missing_aemo_data(self):
        self.assertIsNone(sb.score_region("SA1", DAY, {}, {}, {}))


class EndToEnd(unittest.TestCase):
    """commit then score, with the AEMO downloads replaced by fixed data."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root, self.private = os.path.join(self.tmp, "repo"), os.path.join(self.tmp, "private")
        for d in ("data", "hashes", "forecasts"):
            os.makedirs(os.path.join(self.root, d))
        os.makedirs(self.private)
        self._root, sb.ROOT = sb.ROOT, self.root
        flat = {(g, t): 80.0 for g in sb.REGIONS for t in sb.day_intervals(DAY)}
        self._pd, self._act = sb.aemo_predispatch, sb.actual_prices
        sb.aemo_predispatch = lambda day: dict(flat)
        sb.actual_prices = lambda day: dict(flat)
        self._which, sb.shutil.which = sb.shutil.which, lambda name: None

    def tearDown(self):
        sb.ROOT, sb.aemo_predispatch, sb.actual_prices = self._root, self._pd, self._act
        sb.shutil.which = self._which
        shutil.rmtree(self.tmp)

    def args(self, **kw):
        return types.SimpleNamespace(day=DAY.isoformat(), no_git=True, private_dir=self.private, **kw)

    def rows(self):
        with open(os.path.join(self.root, "data", "results.csv"), newline="") as fh:
            return list(csv.DictReader(fh))

    def test_commit_then_score(self):
        f = os.path.join(self.private, f"{DAY}.csv")
        write_forecast(f, price=70.0, p=0.0)
        sb.cmd_commit(types.SimpleNamespace(day=DAY.isoformat(), no_git=True, forecast=f))
        sb.cmd_score(self.args())
        rows = self.rows()
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r["status"] == "ok" for r in rows))
        self.assertEqual(rows[0]["ocs_price_error"], "10.0")
        self.assertTrue(os.path.exists(os.path.join(self.root, "forecasts", f"{DAY}.csv")))

    def test_missed(self):
        sb.cmd_score(self.args())
        self.assertTrue(all(r["status"] == "missed" for r in self.rows()))

    def test_tampered_forecast(self):
        f = os.path.join(self.private, f"{DAY}.csv")
        write_forecast(f)
        sb.cmd_commit(types.SimpleNamespace(day=DAY.isoformat(), no_git=True, forecast=f))
        write_forecast(f, price=99.0)  # changed after the hash was committed
        sb.cmd_score(self.args())
        self.assertTrue(all(r["status"] == "hash_mismatch" for r in self.rows()))

    def test_no_rescoring_and_no_recommit(self):
        sb.cmd_score(self.args())
        with self.assertRaises(SystemExit):
            sb.cmd_score(self.args())
        f = os.path.join(self.private, f"{DAY}.csv")
        write_forecast(f)
        sb.cmd_commit(types.SimpleNamespace(day=DAY.isoformat(), no_git=True, forecast=f))
        with self.assertRaises(SystemExit):
            sb.cmd_commit(types.SimpleNamespace(day=DAY.isoformat(), no_git=True, forecast=f))


if __name__ == "__main__":
    unittest.main()
