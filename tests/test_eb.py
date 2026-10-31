"""Offline tests: python3 -m unittest discover -s tests"""
import json, os, subprocess, sys, tempfile, unittest
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import eb  # noqa: E402

SAMPLE = os.path.join(ROOT, "sample", "transactions.json")
TODAY = date(2026, 10, 31)


class Recurring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = eb.detect_recurring(eb.load_transactions([SAMPLE]), today=TODAY, include_credits=True)
        cls.by = {i["key"]: i for i in cls.res["items"]}

    def test_cadences(self):
        want = {"streamco europe": "monthly", "veggie box weekly": "weekly", "moon magazine": "lunar",
                "domainhost": "yearly", "example hausverwaltung": "monthly", "phoneco mobile": "monthly"}
        for key, cadence in want.items():
            self.assertIn(key, self.by, key)
            self.assertEqual(self.by[key]["cadence"], cadence, key)

    def test_same_day_charges_merge(self):
        self.assertEqual(self.by["example hausverwaltung"]["typical_amount"], 940.00)

    def test_one_off_does_not_break_series(self):
        p = self.by["phoneco mobile"]
        self.assertEqual((p["typical_amount"], p["count"]), (9.99, 6))

    def test_next_expected_and_monthly_equivalent(self):
        self.assertEqual(self.by["streamco europe"]["next_expected"], "2026-11-03")
        self.assertEqual(self.by["domainhost"]["next_expected"], "2026-11-12")
        self.assertAlmostEqual(self.by["veggie box weekly"]["monthly_equivalent"], 18.50 * 30.4375 / 7, places=1)
        self.assertAlmostEqual(self.by["domainhost"]["monthly_equivalent"], 1.25, places=2)

    def test_lapsed_gym(self):
        self.assertEqual(self.by["fit club berlin"]["status"], "lapsed?")

    def test_noise_ignored(self):
        for k in ("corner market", "bakery example", "cafe sample"):
            self.assertNotIn(k, self.by)

    def test_credits_flagged_and_excluded_from_total(self):
        self.assertEqual(self.by["example employer"]["direction"], "in")
        active_out = [i for i in self.res["items"] if i["direction"] == "out" and i["status"] == "active"]
        self.assertAlmostEqual(self.res["monthly_total_out"], sum(i["monthly_equivalent"] for i in active_out), places=2)

    def test_cli_json(self):
        out = subprocess.run([sys.executable, os.path.join(ROOT, "eb.py"), "recurring", "--input", SAMPLE,
                              "--today", "2026-10-31", "--json"], capture_output=True, text=True, check=True)
        self.assertGreaterEqual(len(json.loads(out.stdout)["items"]), 6)


class Status(unittest.TestCase):
    def session(self, days):
        vu = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        return {"access": {"valid_until": vu}, "aspsp": {"name": "N26"}, "accounts": [{"uid": "x"}]}

    def test_states(self):
        self.assertEqual(eb.consent_status(self.session(100))["state"], "ok")
        self.assertEqual(eb.consent_status(self.session(10))["state"], "renew_soon")
        self.assertEqual(eb.consent_status(self.session(-1))["state"], "expired")

    def test_cli_exit_code(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "session.json")
            json.dump(self.session(5), open(p, "w"))
            env = dict(os.environ, EB_SESSION_PATH=p)
            r = subprocess.run([sys.executable, os.path.join(ROOT, "eb.py"), "status"], env=env,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertIn("WARNING", r.stdout)


if __name__ == "__main__":
    unittest.main()
