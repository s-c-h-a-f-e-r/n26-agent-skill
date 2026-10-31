"""Cache, HTTP 429, PSU headers, transaction windows, and the consent/demo commands."""
import json, os, sys, tempfile, unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import eb  # noqa: E402

SESSION = {"accounts": [{"uid": "acc-demo", "iban_last4": "0000", "currency": "EUR"}]}
ONE_TX = {
    "transactions": [{
        "entry_reference": "demo-1",
        "transaction_amount": {"currency": "EUR", "amount": "3.00"},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-01-02",
        "creditor": {"name": "Demo Shop"},
        "remittance_information": ["Demo Shop"],
    }],
}


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = {}
        self._set("EB_CACHE", "1")
        self._set("EB_CACHE_DIR", self.tmp.name)
        self._set("EB_SESSION_PATH", os.path.join(self.tmp.name, "session.json"))
        for key in ("EB_PSU_IP", "EB_PSU_IP_ADDRESS", "EB_PSU_USER_AGENT", "EB_PSU_PRESENT", "EB_HOME", "XDG_CONFIG_HOME"):
            self._set(key, None)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def _set(self, key, value):
        if key not in self._saved:
            self._saved[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class CacheAndLimits(_Env):
    def test_second_fetch_uses_the_daily_cache_and_the_file_is_private(self):
        calls = []

        def fake(method, path, **kw):
            calls.append(path)
            self.assertEqual(method, "GET")
            self.assertTrue(kw.get("online"))
            return ONE_TX

        with patch("eb.call", side_effect=fake):
            first = eb.fetch_transactions(SESSION, "2026-01-01", "2026-01-31")
            second = eb.fetch_transactions(SESSION, "2026-01-01", "2026-01-31")
            other = eb.fetch_transactions(SESSION, "2026-02-01", "2026-02-28")
        self.assertEqual(calls, [
            "/accounts/acc-demo/transactions",
            "/accounts/acc-demo/transactions",
        ])
        self.assertEqual(first[0]["transactions"], second[0]["transactions"])
        self.assertEqual(len(other[0]["transactions"]), 1)
        files = [name for name in os.listdir(self.tmp.name) if name.endswith(".json") and name != "session.json"]
        self.assertTrue(files)
        mode = os.stat(os.path.join(self.tmp.name, files[0])).st_mode & 0o777
        self.assertEqual(mode, 0o600)
        self.assertEqual(os.stat(self.tmp.name).st_mode & 0o777, 0o700)

    def test_429_is_a_clear_error_and_does_not_echo_the_body(self):
        class Resp:
            status_code = 429
            text = '{"error":"ASPSP_RATE_LIMIT_EXCEEDED","detail":"do-not-show-this-body"}'

            def json(self):
                return {}

        with patch("eb.headers", return_value={"Authorization": "Bearer test-token"}):
            with patch("requests.request", return_value=Resp()):
                with self.assertRaises(eb.EbError) as caught:
                    eb.call("GET", "/accounts/acc-demo/balances", online=True)
        message = str(caught.exception)
        self.assertIn("HTTP 429", message)
        self.assertIn("4", message)
        self.assertNotIn("do-not-show-this-body", message)
        self.assertNotIn("Bearer", message)

    def test_429_falls_back_to_an_older_cached_copy(self):
        with patch("eb.call", return_value=ONE_TX):
            eb.fetch_transactions(SESSION, "2026-01-01", "2026-01-31")
        path = eb._cache_path("transactions", "acc-demo", {"date_from": "2026-01-01", "date_to": "2026-01-31"})
        with open(path) as fh:
            stored = json.load(fh)
        stored["fetched_on"] = "2000-01-01"
        with open(path, "w") as fh:
            json.dump(stored, fh)

        def limited(method, path, **kw):
            raise eb.EbError(eb.RATE_LIMIT_MESSAGE)

        with patch("eb.call", side_effect=limited):
            got = eb.fetch_transactions(SESSION, "2026-01-01", "2026-01-31")
        self.assertEqual(got[0]["transactions"][0]["creditor"], "Demo Shop")
        self.assertIn("HTTP 429", got[0]["notice"])
        self.assertIn("2000-01-01", got[0]["notice"])

    def test_psu_headers_are_all_or_nothing_and_only_when_someone_is_present(self):
        self.assertEqual(eb.psu_headers(), {})
        self._set("EB_PSU_IP", "203.0.113.50")
        self.assertEqual(eb.psu_headers(), {})
        self._set("EB_PSU_USER_AGENT", "DemoBrowser/1.0")
        self.assertEqual(eb.psu_headers()["Psu-Ip-Address"], "203.0.113.50")
        self.assertEqual(eb.psu_headers()["Psu-User-Agent"], "DemoBrowser/1.0")

        seen = {}

        class Resp:
            status_code = 200
            text = ""

            def json(self):
                return {"balances": []}

        def capture(method, url, headers=None, timeout=None, **kw):
            seen["headers"] = dict(headers or {})
            return Resp()

        with patch("eb.headers", side_effect=lambda: {"Authorization": "Bearer test-token"}):
            with patch("requests.request", side_effect=capture):
                eb.call("GET", "/accounts/acc-demo/balances", online=True)
                self.assertEqual(seen["headers"]["Psu-Ip-Address"], "203.0.113.50")
                self._set("EB_PSU_PRESENT", "0")
                eb.call("GET", "/accounts/acc-demo/balances", online=True)
                self.assertNotIn("Psu-Ip-Address", seen["headers"])

    def test_installed_layout_uses_the_user_config_dir(self):
        home = os.path.join(self.tmp.name, "xdg")
        self._set("XDG_CONFIG_HOME", home)
        with patch.object(eb, "BASE", "/usr/lib/python3.12/site-packages"):
            path = eb.app_home()
        self.assertEqual(path, os.path.join(home, "n26-mcp"))
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o700)

    def test_connect_prints_the_link_without_waiting_when_stdin_is_not_a_terminal(self):
        def fake(method, path, **kw):
            self.assertEqual((method, path), ("POST", "/auth"))
            return {"url": "https://bank.example/approve"}

        with patch("eb.call", side_effect=fake):
            with patch("sys.stdin.isatty", return_value=False):
                with patch("sys.stdout") as out:
                    eb.cmd_connect(argparse_days())
        text = "".join(call.args[0] for call in out.write.call_args_list if call.args)
        self.assertIn("https://bank.example/approve", text)
        self.assertIn("cannot move money", text)
        self.assertIn("n26-eb session", text)

    def test_demo_uses_the_fixture_and_does_not_call_the_bank(self):
        def explode(*args, **kwargs):
            raise AssertionError("demo contacted the bank")

        with patch("eb.call", side_effect=explode):
            with patch("sys.stdout") as out:
                eb.cmd_demo(argparse_demo())
        text = "".join(call.args[0] for call in out.write.call_args_list if call.args)
        self.assertIn("DEMO DATA", text)
        self.assertIn("StreamCo", text)
        self.assertIn("not a list of every subscription", text)

    def test_demo_gif_is_a_gif(self):
        path = os.path.join(ROOT, "docs", "demo.gif")
        with open(path, "rb") as fh:
            magic = fh.read(6)
        self.assertIn(magic, (b"GIF87a", b"GIF89a"))
        self.assertGreater(os.path.getsize(path), 1000)


def argparse_days():
    import argparse
    return argparse.Namespace(days=30)


def argparse_demo():
    import argparse
    return argparse.Namespace(input=None, today="2026-10-31")


if __name__ == "__main__":
    unittest.main()
