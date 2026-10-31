"""MCP server tests against the synthetic sample. The Enable Banking HTTP layer is mocked.

Run with the rest of the suite: python3 -m unittest discover -s tests
"""
import json, os, sys, tempfile, unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import eb  # noqa: E402
from mcp import Client, MCPError  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402

SAMPLE = os.path.join(ROOT, "sample", "transactions.json")
TODAY = "2026-10-31"
ACCOUNT = "acc-sample-0001"
SESSION_ID = "sample-session-id"


def _session(days):
    valid = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    return {
        "session_id": SESSION_ID,
        "created": "2026-05-01T00:00:00+00:00",
        "access": {"valid_until": valid},
        "aspsp": {"name": "N26", "country": "DE"},
        "accounts": [{"uid": ACCOUNT, "iban_last4": "0000", "currency": "EUR"}],
    }


def _sample_transactions():
    with open(SAMPLE) as f:
        return json.load(f)


class McpTools(unittest.IsolatedAsyncioTestCase):
    """An in-memory MCP client lists and calls every read-only tool."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.session_path = os.path.join(self.tmp.name, "session.json")
        with open(self.session_path, "w") as f:
            json.dump(_session(120), f)
        self._prev_session = os.environ.get("EB_SESSION_PATH")
        self._prev_cache = os.environ.get("EB_CACHE")
        os.environ["EB_SESSION_PATH"] = self.session_path
        os.environ["EB_CACHE"] = "0"
        self.http = []
        self.txs = _sample_transactions()
        self.patcher = patch("eb.call", side_effect=self._fake_call)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        if self._prev_session is None:
            os.environ.pop("EB_SESSION_PATH", None)
        else:
            os.environ["EB_SESSION_PATH"] = self._prev_session
        if self._prev_cache is None:
            os.environ.pop("EB_CACHE", None)
        else:
            os.environ["EB_CACHE"] = self._prev_cache
        self.tmp.cleanup()

    def _fake_call(self, method, path, **kw):
        if method != "GET":
            raise AssertionError(f"read-only client issued {method} {path}")
        params = kw.get("params") or {}
        self.http.append({"method": method, "path": path, "params": dict(params)})
        if path == f"/accounts/{ACCOUNT}/balances":
            return {"balances": [{
                "balance_type": "CLBD",
                "balance_amount": {"amount": "1500.00", "currency": "EUR"},
                "reference_date": "2026-10-31",
            }]}
        if path == f"/accounts/{ACCOUNT}/transactions":
            if "continuation_key" not in params:
                return {"transactions": self.txs[:3], "continuation_key": "p2"}
            if params.get("continuation_key") != "p2":
                raise AssertionError(params.get("continuation_key"))
            return {"transactions": self.txs[3:]}
        if path == f"/sessions/{SESSION_ID}":
            return {"status": "AUTHORIZED"}
        raise AssertionError(f"unexpected {method} {path}")

    def _server(self):
        from n26_mcp.server import mcp
        return mcp

    async def test_client_lists_every_read_only_tool(self):
        async with Client(self._server()) as client:
            self.assertIn("owner", (client.instructions or "").lower())
            self.assertIn("payment", (client.instructions or "").lower())
            listed = await client.list_tools()
            by_name = {tool.name: tool for tool in listed.tools}
            self.assertEqual(
                set(by_name),
                {"list_accounts", "get_balances", "get_transactions", "recurring_charges", "consent_status"},
            )
            for name, tool in by_name.items():
                self.assertTrue(tool.annotations and tool.annotations.read_only_hint, name)
                self.assertFalse(tool.annotations.destructive_hint, name)
                self.assertTrue(tool.annotations.idempotent_hint, name)
                self.assertIn("read-only", (tool.description or "").lower(), name)
                self.assertTrue(tool.input_schema)
                self.assertTrue(tool.output_schema)
            self.assertFalse(by_name["list_accounts"].annotations.open_world_hint)
            self.assertTrue(by_name["get_balances"].annotations.open_world_hint)
            self.assertTrue(by_name["get_transactions"].annotations.open_world_hint)
            required = by_name["get_transactions"].input_schema.get("required") or []
            self.assertIn("date_from", required)
            self.assertNotIn("date_to", required)
            prompts = await client.list_prompts()
            self.assertIn("monthly_spending_summary", {p.name for p in prompts.prompts})
            templates = await client.list_resource_templates()
            uris = {t.uri_template for t in templates.resource_templates}
            self.assertIn("n26://spending/{month}", uris)

    async def test_list_accounts_is_local_and_hides_the_session(self):
        async with Client(self._server()) as client:
            result = await client.call_tool("list_accounts", {})
        self.assertFalse(result.is_error, result.content)
        body = result.structured_content
        self.assertEqual(body["bank"], "N26")
        self.assertEqual(body["accounts"], [{
            "uid": ACCOUNT, "iban_last4": "0000", "currency": "EUR",
        }])
        self.assertEqual(len(body["accounts"][0]["iban_last4"]), 4)
        self.assertIn("owner", body["owner_only"].lower())
        blob = json.dumps(body)
        self.assertNotIn(SESSION_ID, blob)
        self.assertNotIn("PRIVATE", blob)
        self.assertEqual(self.http, [])

    async def test_get_balances(self):
        async with Client(self._server()) as client:
            result = await client.call_tool("get_balances", {"account": ACCOUNT})
        self.assertFalse(result.is_error, result.content)
        body = result.structured_content
        self.assertEqual(body["accounts"][0]["balances"], [{
            "balance_type": "CLBD",
            "amount": "1500.00",
            "currency": "EUR",
            "reference_date": "2026-10-31",
        }])
        self.assertEqual(self.http[0]["path"], f"/accounts/{ACCOUNT}/balances")
        self.assertEqual(self.http[0]["method"], "GET")

    async def test_get_transactions_follows_pagination(self):
        async with Client(self._server()) as client:
            result = await client.call_tool("get_transactions", {
                "date_from": "2024-01-01",
                "date_to": "2026-10-31",
                "account": ACCOUNT,
                "limit": 200,
            })
        self.assertFalse(result.is_error, result.content)
        body = result.structured_content
        block = body["accounts"][0]
        self.assertEqual(block["pages"], 2)
        self.assertEqual(block["matched"], len(self.txs))
        self.assertEqual(block["returned"], len(self.txs))
        self.assertFalse(block["truncated"])
        self.assertEqual(
            {t["entry_reference"] for t in block["transactions"]},
            {t["entry_reference"] for t in self.txs},
        )
        tx_calls = [c for c in self.http if c["path"].endswith("/transactions")]
        self.assertEqual(len(tx_calls), 2)
        self.assertEqual(tx_calls[0]["params"]["date_from"], "2024-01-01")
        self.assertEqual(tx_calls[0]["params"]["date_to"], "2026-10-31")
        self.assertNotIn("continuation_key", tx_calls[0]["params"])
        self.assertEqual(tx_calls[1]["params"]["continuation_key"], "p2")
        self.assertEqual(tx_calls[1]["params"]["date_from"], "2024-01-01")
        self.assertIn("owner", body["owner_only"].lower())

    async def test_get_transactions_defaults_to_newest_page_and_can_search(self):
        async with Client(self._server()) as client:
            default = await client.call_tool("get_transactions", {"date_from": "2024-01-01", "account": ACCOUNT})
            searched = await client.call_tool("get_transactions", {
                "date_from": "2024-01-01", "account": ACCOUNT, "search": "streamco",
            })
            first = await client.call_tool("get_transactions", {
                "date_from": "2024-01-01", "account": ACCOUNT, "limit": 10, "offset": 0,
            })
            second = await client.call_tool("get_transactions", {
                "date_from": "2024-01-01", "account": ACCOUNT, "limit": 10, "offset": 10,
            })
        self.assertFalse(default.is_error, default.content)
        page = default.structured_content["accounts"][0]
        self.assertEqual(page["limit"], 50)
        self.assertEqual(page["returned"], 50)
        self.assertGreater(page["matched"], 50)
        self.assertTrue(page["truncated"])
        dates = [t["booking_date"] for t in page["transactions"]]
        self.assertEqual(dates, sorted(dates, reverse=True))
        hits = searched.structured_content["accounts"][0]
        self.assertGreater(hits["matched"], 0)
        self.assertTrue(all("streamco" in (t["creditor"] or "").lower() for t in hits["transactions"]))
        a = {t["entry_reference"] for t in first.structured_content["accounts"][0]["transactions"]}
        b = {t["entry_reference"] for t in second.structured_content["accounts"][0]["transactions"]}
        self.assertEqual(len(a), 10)
        self.assertTrue(a.isdisjoint(b))

    async def test_recurring_charges_reuse_the_detector(self):
        expected = eb.detect_recurring(
            eb.load_transactions([SAMPLE]), today=date.fromisoformat(TODAY), include_credits=True,
        )
        async with Client(self._server()) as client:
            result = await client.call_tool("recurring_charges", {
                "date_from": "2024-01-01",
                "include_credits": True,
                "today": TODAY,
            })
        self.assertFalse(result.is_error, result.content)
        body = result.structured_content
        self.assertEqual(body["items"], expected["items"])
        self.assertEqual(body["monthly_total_out"], expected["monthly_total_out"])
        self.assertEqual(body["as_of"], TODAY)
        self.assertGreaterEqual(len(body["items"]), 6)
        self.assertIn("owner", body["owner_only"].lower())

    async def test_consent_status_warns_and_can_check_remote_without_leaking_the_session_id(self):
        with open(self.session_path, "w") as f:
            json.dump(_session(5), f)
        async with Client(self._server()) as client:
            soon = await client.call_tool("consent_status", {"warn_days": 14, "remote": True})
        self.assertFalse(soon.is_error, soon.content)
        body = soon.structured_content
        self.assertEqual(body["state"], "renew_soon")
        self.assertLessEqual(body["days_left"], 14)
        self.assertIn("WARNING", body["warning"])
        self.assertEqual(body["bank"], "N26")
        self.assertEqual(body["accounts"], 1)
        self.assertEqual(body["remote_status"], "AUTHORIZED")
        self.assertNotIn(SESSION_ID, json.dumps(body))
        self.assertTrue(any(c["path"] == f"/sessions/{SESSION_ID}" for c in self.http))

        with open(self.session_path, "w") as f:
            json.dump(_session(-1), f)
        async with Client(self._server()) as client:
            expired = await client.call_tool("consent_status", {})
        self.assertEqual(expired.structured_content["state"], "expired")
        self.assertIn("EXPIRED", expired.structured_content["warning"])

    async def test_unknown_account_and_bad_date_are_tool_errors(self):
        async with Client(self._server()) as client:
            missing = await client.call_tool("get_balances", {"account": "no-such-account"})
            bad_date = await client.call_tool("get_transactions", {"date_from": "31-10-2026"})
            self.assertTrue(missing.is_error)
            self.assertTrue(bad_date.is_error)
            # The server process (this client session) is still usable.
            ok = await client.call_tool("list_accounts", {})
        self.assertFalse(ok.is_error, ok.content)

    async def test_missing_session_is_a_tool_error_not_a_crash(self):
        os.environ["EB_SESSION_PATH"] = os.path.join(self.tmp.name, "missing.json")
        async with Client(self._server()) as client:
            first = await client.call_tool("list_accounts", {})
            second = await client.call_tool("consent_status", {})
        self.assertTrue(first.is_error)
        self.assertIn("session", first.content[0].text.lower())
        self.assertTrue(second.is_error)

    async def test_monthly_prompt_and_spending_resource(self):
        async with Client(self._server()) as client:
            prompt = await client.get_prompt("monthly_spending_summary", {"month": "2026-10"})
            text = prompt.messages[0].content.text
            self.assertIn("2026-10", text)
            self.assertIn("owner", text.lower())
            self.assertIn("recurring_charges", text)
            resource = await client.read_resource("n26://spending/2026-10")
        summary = json.loads(resource.contents[0].text)
        self.assertEqual(summary["month"], "2026-10")
        self.assertEqual(summary["currency"], "EUR")
        want_n, want_out, want_in = _october_oracle(self.txs)
        self.assertEqual(summary["transaction_count"], want_n)
        self.assertAlmostEqual(summary["total_out"], want_out, places=2)
        self.assertAlmostEqual(summary["total_in"], want_in, places=2)
        self.assertAlmostEqual(summary["net"], round(want_in - want_out, 2), places=2)
        self.assertTrue(summary["top_outflows"])
        self.assertIn("owner", summary["owner_only"].lower())
        tx_calls = [c for c in self.http if c["path"].endswith("/transactions")]
        self.assertEqual(len(tx_calls), 2)
        self.assertEqual(tx_calls[0]["params"]["date_from"], "2026-10-01")
        self.assertEqual(tx_calls[0]["params"]["date_to"], "2026-10-31")
        async with Client(self._server()) as client:
            with self.assertRaises(MCPError) as caught:
                await client.read_resource("n26://spending/not-a-month")
        message = str(caught.exception)
        self.assertIn("YYYY-MM", message)
        self.assertNotIn("total_out", message)


class McpStdio(unittest.IsolatedAsyncioTestCase):
    """The stdio entry point starts and serves the local tools."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.session_path = os.path.join(self.tmp.name, "session.json")
        with open(self.session_path, "w") as f:
            json.dump(_session(120), f)

    def tearDown(self):
        self.tmp.cleanup()

    async def test_stdio_lists_tools_and_reads_the_session(self):
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PYTHONPATH": ROOT,
            "EB_SESSION_PATH": self.session_path,
        }
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "n26_mcp"],
            cwd=ROOT,
            env=env,
        )
        async with Client(params) as client:
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            self.assertIn("list_accounts", names)
            self.assertIn("consent_status", names)
            accounts = await client.call_tool("list_accounts", {})
            status = await client.call_tool("consent_status", {})
        self.assertFalse(accounts.is_error, accounts.content)
        self.assertEqual(accounts.structured_content["accounts"][0]["uid"], ACCOUNT)
        self.assertFalse(status.is_error, status.content)
        self.assertEqual(status.structured_content["state"], "ok")
        self.assertNotIn(SESSION_ID, json.dumps(status.structured_content))


class NoPaymentPath(unittest.TestCase):
    def test_server_and_client_have_no_payment_initiation(self):
        banned = ("/payments", "payment_initiation", "initiate_payment", "sepa-credit")
        for rel in ("eb.py", os.path.join("n26_mcp", "server.py")):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                text = fh.read().lower()
            for needle in banned:
                self.assertNotIn(needle, text, rel)
        with open(os.path.join(ROOT, "n26_mcp", "server.py"), encoding="utf-8") as fh:
            server = fh.read()
        # The bearer header is built in eb.py and must not be logged or printed here.
        self.assertNotIn("print(", server)
        self.assertNotIn("Authorization", server)
        self.assertNotIn('"POST"', server)
        self.assertNotIn("'POST'", server)


def _october_oracle(txs):
    """Independent total for 2026-10 from the raw sample, not from the server helper."""
    total_out = total_in = 0.0
    n = 0
    for t in txs:
        if not str(t.get("booking_date") or "").startswith("2026-10"):
            continue
        n += 1
        amt = float(t["transaction_amount"]["amount"])
        if t.get("credit_debit_indicator") == "DBIT":
            total_out += amt
        else:
            total_in += amt
    return n, round(total_out, 2), round(total_in, 2)


if __name__ == "__main__":
    unittest.main()
