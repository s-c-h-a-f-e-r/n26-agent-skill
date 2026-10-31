#!/usr/bin/env python3
"""Read-only Enable Banking client for agents. Tested with N26 (DE).

Account information (PSD2 AIS) only. There is no payment code in this file.
Your private key is read from disk to sign API tokens and is never printed.
The MCP server in n26_mcp/ imports this module; it does not carry a second client.

Config comes from environment variables or an optional config.json next to
this file (environment wins):

  EB_APP_ID        your Enable Banking application ID (required for API calls)
  EB_KEY_PATH      path to your application's private key   (default keys/private.pem)
  EB_BANK          bank (ASPSP) name                          (default N26)
  EB_COUNTRY       bank country code                          (default DE)
  EB_REDIRECT_URL  redirect URL registered on your app        (default https://enablebanking.com/)
  EB_SESSION_PATH  where the session file lives               (default session.json)
  EB_DATA_DIR      where transactions are written             (default data/)
"""
import argparse, csv, json, os, re, statistics, sys, uuid
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs

API = "https://api.enablebanking.com"
BASE = os.path.dirname(os.path.abspath(__file__))


class EbError(Exception):
    """Expected failure, such as missing config or an HTTP error.

    The CLI turns this into sys.exit. The MCP server turns it into a tool error.
    The message is scrubbed so a private key or bearer token cannot leak out.
    """

    def __init__(self, message):
        super().__init__(public_error(str(message)))


def public_error(message):
    lowered = message.lower()
    if any(tok in lowered for tok in ("private key", "begin private", "begin rsa", "bearer ")):
        return "Enable Banking request failed. The detail was hidden because it looked like key material."
    return message


# ---------------------------------------------------------------- config

def _file_config():
    p = os.path.join(BASE, "config.json")
    if os.path.exists(p):
        with open(p) as f:
            return {k.upper(): v for k, v in json.load(f).items()}
    return {}


def cfg(key, default=None):
    v = os.environ.get(key)
    if v:
        return v
    return _file_config().get(key, default)


def _path(key, default):
    p = cfg(key, default)
    return p if os.path.isabs(p) else os.path.join(BASE, p)


def key_path():
    return _path("EB_KEY_PATH", os.path.join("keys", "private.pem"))


def session_path():
    return _path("EB_SESSION_PATH", "session.json")


def data_dir():
    return _path("EB_DATA_DIR", "data")


def app_id():
    a = cfg("EB_APP_ID")
    if not a:
        raise EbError("EB_APP_ID is not set. Export it or put it in config.json (see README).")
    return a


# ---------------------------------------------------------------- API

def headers():
    import jwt as pyjwt  # imported lazily so offline commands need no deps
    iat = int(datetime.now(timezone.utc).timestamp())
    with open(key_path(), "rb") as f:
        key = f.read()
    token = pyjwt.encode(
        {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": iat, "exp": iat + 3600},
        key, algorithm="RS256", headers={"typ": "JWT", "alg": "RS256", "kid": app_id()},
    )
    return {"Authorization": f"Bearer {token}"}


def call(method, path, **kw):
    import requests
    # Consent setup uses POST /auth and POST /sessions. There is no payment API.
    if method not in ("GET", "POST"):
        raise EbError("Only GET and POST (consent/session) are used by this read-only client.")
    r = requests.request(method, API + path, headers=headers(), timeout=60, **kw)
    if r.status_code != 200:
        raise EbError(f"Error {r.status_code} on {method} {path}: {r.text}")
    return r.json()


def load_session():
    p = session_path()
    if not os.path.exists(p):
        raise EbError("No session file. Run: eb.py auth, approve in your bank app, then eb.py session <redirect-url>")
    with open(p) as f:
        return json.load(f)


def bank():
    return {"name": cfg("EB_BANK", "N26"), "country": cfg("EB_COUNTRY", "DE")}


# ---------------------------------------------------------------- commands: setup

def cmd_application(a):
    app = call("GET", "/application")
    print(json.dumps({k: app.get(k) for k in ("name", "active", "environment", "redirect_urls", "countries") if k in app}, indent=2))


def cmd_aspsps(a):
    b = bank()
    country = a.country or b["country"]
    data = call("GET", "/aspsps", params={"country": country})
    needle = (a.name or b["name"]).lower()
    hits = [x for x in data.get("aspsps", []) if needle in x.get("name", "").lower()]
    print(f"{len(data.get('aspsps', []))} banks listed for {country}.")
    for x in hits:
        print(json.dumps({k: x.get(k) for k in ("name", "country", "psu_types", "maximum_consent_validity") if k in x}, indent=2))
    if not hits:
        sys.exit(f"No bank matching '{needle}' in {country}.")


def cmd_auth(a):
    valid_until = (datetime.now(timezone.utc) + timedelta(days=a.days)).isoformat()
    body = {"access": {"valid_until": valid_until}, "aspsp": bank(), "state": str(uuid.uuid4()),
            "redirect_url": cfg("EB_REDIRECT_URL", "https://enablebanking.com/"), "psu_type": "personal"}
    res = call("POST", "/auth", json=body)
    print(f"valid_until: {valid_until}")
    print(f"Open this link, log in to your bank and approve read access:\n{res['url']}")
    print("Then run: eb.py session '<the full URL you were redirected to>'")


def cmd_session(a):
    code = a.code
    if code.startswith("http"):
        q = parse_qs(urlparse(code).query)
        if "code" not in q:
            sys.exit(f"No code in redirect URL (error: {q.get('error')})")
        code = q["code"][0]
    s = call("POST", "/sessions", json={"code": code})
    accounts = []
    for acc in s.get("accounts", []):
        if isinstance(acc, str):
            accounts.append({"uid": acc})
        else:
            accounts.append({"uid": acc.get("uid"),
                             "iban_last4": ((acc.get("account_id") or {}).get("iban") or "")[-4:],
                             "currency": acc.get("currency")})
    out = {"session_id": s["session_id"], "created": datetime.now(timezone.utc).isoformat(),
           "access": s.get("access"), "aspsp": s.get("aspsp"), "accounts": accounts}
    p = session_path()
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(out, f, indent=2)
    print(f"Saved session ({len(accounts)} account(s)). Consent valid until {(out['access'] or {}).get('valid_until')}.")


def selected_accounts(session, account=None, required=False):
    """Accounts on the session, optionally one uid. required=True errors when it is missing."""
    accs = [a for a in (session.get("accounts") or []) if a.get("uid")]
    if account:
        accs = [a for a in accs if a.get("uid") == account]
        if required and not accs:
            raise EbError(f"No consented account with uid {account!r}. Call list_accounts.")
    return accs


def _last4(acc):
    return str(acc.get("iban_last4") or "").replace(" ", "")[-4:]


def _require_date(label, value):
    if not value:
        return
    try:
        date.fromisoformat(value)
    except ValueError:
        raise EbError(f"{label} must be YYYY-MM-DD.") from None


def fetch_balances(session, account=None, required=False):
    """GET balances for each selected account. Read-only."""
    out = []
    for acc in selected_accounts(session, account, required=required):
        uid = acc["uid"]
        res = call("GET", f"/accounts/{uid}/balances")
        balances = []
        for b in res.get("balances") or []:
            amt = b.get("balance_amount") or {}
            balances.append({
                "balance_type": b.get("balance_type"),
                "amount": amt.get("amount"),
                "currency": amt.get("currency"),
                "reference_date": b.get("reference_date") or "",
            })
        out.append({
            "account": uid,
            "iban_last4": _last4(acc),
            "currency": acc.get("currency") or "",
            "balances": balances,
        })
    return out


def fetch_transactions(session, date_from, date_to=None, account=None, required=False):
    """GET transactions, following continuation_key until the bank has no more pages."""
    _require_date("date_from", date_from)
    _require_date("date_to", date_to)
    if not date_from:
        raise EbError("date_from must be YYYY-MM-DD.")
    out = []
    for acc in selected_accounts(session, account, required=required):
        uid = acc["uid"]
        params = {"date_from": date_from}
        if date_to:
            params["date_to"] = date_to
        raw, pages = [], 0
        while True:
            res = call("GET", f"/accounts/{uid}/transactions", params=params)
            raw.extend(res.get("transactions") or [])
            pages += 1
            ck = res.get("continuation_key")
            if not ck:
                break
            if pages >= 100:
                raise EbError("Stopped after 100 transaction pages.")
            params["continuation_key"] = ck
        out.append({
            "account": uid,
            "iban_last4": _last4(acc),
            "pages": pages,
            "transactions": [flat(t) for t in raw],
            "raw": raw,
        })
    return out


# ---------------------------------------------------------------- commands: read

def cmd_balances(a):
    for block in fetch_balances(load_session(), a.account):
        print(f"Account {block['account'][:8]}:")
        for b in block["balances"]:
            print(f"  {b.get('balance_type')}: {b.get('amount')} {b.get('currency')}  ({b.get('reference_date') or ''})")


def flat(t):
    amt = t.get("transaction_amount") or {}
    sign = "-" if t.get("credit_debit_indicator") == "DBIT" else ""
    ri = t.get("remittance_information") or []
    return {
        "booking_date": t.get("booking_date"), "value_date": t.get("value_date"),
        "amount": f"{sign}{amt.get('amount')}", "currency": amt.get("currency"),
        "direction": t.get("credit_debit_indicator"), "status": t.get("status"),
        "creditor": (t.get("creditor") or {}).get("name"), "debtor": (t.get("debtor") or {}).get("name"),
        "remittance": " | ".join(ri) if isinstance(ri, list) else ri,
        "bank_code": (t.get("bank_transaction_code") or {}).get("description"),
        "entry_reference": t.get("entry_reference"), "transaction_id": t.get("transaction_id"),
    }


def cmd_transactions(a):
    blocks = fetch_transactions(load_session(), a.date_from, a.date_to, a.account)
    d = data_dir()
    os.makedirs(d, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for block in blocks:
        uid = block["account"]
        txs = block["raw"]
        base = os.path.join(d, f"tx_{uid[:8]}_{a.date_from}_{stamp}")
        with open(base + ".json", "w") as f:
            json.dump(txs, f, indent=2, ensure_ascii=False)
        with open(base + ".csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(flat({}).keys()))
            w.writeheader()
            w.writerows(block["transactions"])
        print(f"Account {uid[:8]}: {len(txs)} transactions, {block['pages']} page(s)\n  {base}.json\n  {base}.csv")


# ---------------------------------------------------------------- status

def consent_status(s, today=None, warn_days=14):
    now = today or datetime.now(timezone.utc)
    vu = (s.get("access") or {}).get("valid_until")
    if not vu:
        return {"state": "unknown", "valid_until": None, "days_left": None}
    exp = datetime.fromisoformat(vu.replace("Z", "+00:00"))
    days = (exp - now).total_seconds() / 86400
    state = "expired" if days <= 0 else ("renew_soon" if days <= warn_days else "ok")
    return {"state": state, "valid_until": exp.isoformat(), "days_left": round(days, 1),
            "bank": (s.get("aspsp") or {}).get("name"), "accounts": len(s.get("accounts") or [])}


def cmd_status(a):
    st = consent_status(load_session(), warn_days=a.warn_days)
    if a.remote:
        sid = load_session()["session_id"]
        st["remote_status"] = call("GET", f"/sessions/{sid}").get("status")
    if a.json:
        print(json.dumps(st, indent=2))
    else:
        print(f"Bank: {st.get('bank')}  accounts: {st.get('accounts')}")
        print(f"Consent valid until: {st['valid_until']}  ({st['days_left']} days left)")
        if "remote_status" in st:
            print(f"Enable Banking session status: {st['remote_status']}")
        if st["state"] == "renew_soon":
            print(f"WARNING: consent expires within {a.warn_days} days. Run `eb.py auth` and approve again.")
        elif st["state"] == "expired":
            print("EXPIRED: run `eb.py auth`, approve in your bank app, then `eb.py session <url>`.")
        else:
            print("OK")
    sys.exit({"ok": 0, "renew_soon": 2, "expired": 3}.get(st["state"], 1))


# ---------------------------------------------------------------- recurring

CADENCES = {  # name: (period days, tolerance days, months per period for monthly equivalent)
    "weekly": (7.0, 1.5, 7 / 30.4375),
    "lunar": (29.53, 1.2, 29.53 / 30.4375),
    "monthly": (30.4375, 4.0, 1.0),
    "quarterly": (91.3, 10.0, 3.0),
    "yearly": (365.25, 20.0, 12.0),
}
_NOISE = re.compile(r"\b(gmbh|ag|se|kg|ug|bv|ltd|inc|llc|sarl|sa|co|com|www|de|eu)\b|[^a-z ]")


def norm_payee(name):
    """'Octopus Energy Germany GmbH' and 'OCTOPUS ENERGY GERMANY' become one payee."""
    s = (name or "").lower()
    s = re.sub(r"\.(com|de|eu|net|io)\b", " ", s).replace(".", "")
    s = _NOISE.sub(" ", s)
    return " ".join(s.split()) or "(unknown)"


def _num(x):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return None


def rows_from_flat(items):
    """Normalise flat transaction dicts (flat() or the CSV) into detector rows."""
    rows = []
    for t in items:
        amt = _num(t.get("amount"))
        d = t.get("booking_date") or t.get("value_date")
        if amt is None or not d:
            continue
        debit = t.get("direction") == "DBIT" or amt < 0
        payee = (t.get("creditor") if debit else t.get("debtor")) or t.get("remittance") or ""
        rows.append({"date": date.fromisoformat(d[:10]), "amount": abs(amt), "debit": debit,
                     "currency": t.get("currency") or "", "payee": payee.strip(),
                     "key": norm_payee(payee), "id": t.get("entry_reference") or t.get("transaction_id")})
    seen, out = set(), []
    for r in rows:  # de-duplicate overlapping exports
        k = (r["id"], r["date"], r["amount"]) if r["id"] else (r["key"], r["date"], r["amount"], len(out))
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def load_transactions(paths):
    """Accept raw Enable Banking JSON or the flat CSV this tool writes. Returns normalised rows."""
    items = []
    for p in paths:
        if p.endswith(".json"):
            with open(p) as f:
                raw = json.load(f)
            raw = raw.get("transactions", raw) if isinstance(raw, dict) else raw
            items.extend(flat(t) for t in raw)
        else:
            with open(p, newline="") as f:
                items.extend(csv.DictReader(f))
    return rows_from_flat(items)


def _add_months(d, n, dom):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(dom, last))


def _classify(dates):
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    if not gaps:
        return None, 0.0
    med = statistics.median(gaps)
    best = None
    for name, (period, tol, _) in CADENCES.items():
        hits = sum(1 for g in gaps if abs(g - period) <= tol)
        score = hits / len(gaps)
        if abs(med - period) <= tol and score >= 0.75:
            if best is None or abs(med - period) / period < abs(med - CADENCES[best[0]][0]) / CADENCES[best[0]][0]:
                best = (name, score)
    if best and best[0] in ("monthly", "lunar"):
        # Monthly bills keep their day of month; lunar ones drift about a day a month.
        doms = [d.day for d in dates]
        spread = max(doms) - min(doms)
        drift = all(28.5 <= g <= 30.5 for g in gaps) and spread >= len(gaps) * 0.7 and len(dates) >= 4
        best = ("lunar" if drift else "monthly", best[1])
    return best if best else (None, 0.0)


def _amount_clusters(txs, rel=0.15):
    """Split one payee's charges into price bands (Apple 2.99 vs 7.99 are separate subscriptions)."""
    bands = []
    for t in sorted(txs, key=lambda x: x["amount"]):
        if bands and abs(t["amount"] - statistics.median(x["amount"] for x in bands[-1])) <= rel * t["amount"]:
            bands[-1].append(t)
        else:
            bands.append([t])
    return bands


def _series(txs, today, min_count, max_cv):
    by_day = defaultdict(list)
    for t in txs:
        by_day[t["date"]].append(t)
    days = sorted(by_day)
    amounts = [sum(x["amount"] for x in by_day[d]) for d in days]  # same-day charges merge (rent + parking)
    cadence, score = _classify(days)
    need = 2 if cadence in ("yearly", "quarterly") else min_count
    if not cadence or len(days) < need:
        return None
    typical = statistics.median(amounts)
    cv = (statistics.pstdev(amounts) / typical) if typical else 0
    if cv > max_cv:
        return None
    period, tol, months = CADENCES[cadence]
    last = days[-1]
    if cadence == "monthly":
        nxt = _add_months(last, 1, round(statistics.median(d.day for d in days)))
    elif cadence == "quarterly":
        nxt = _add_months(last, 3, last.day)
    elif cadence == "yearly":
        nxt = _add_months(last, 12, last.day)
    else:
        nxt = last + timedelta(days=round(period))
    sample = by_day[last][0]
    return {
        "payee": sample["payee"], "key": sample["key"], "direction": "out" if sample["debit"] else "in",
        "cadence": cadence, "count": len(days), "first": days[0].isoformat(), "last": last.isoformat(),
        "typical_amount": round(typical, 2), "min": round(min(amounts), 2), "max": round(max(amounts), 2),
        "currency": sample["currency"], "kind": "fixed" if cv <= 0.05 else "variable",
        "next_expected": nxt.isoformat(), "monthly_equivalent": round(typical / months, 2),
        "status": "active" if (today - nxt).days <= tol + 3 else "lapsed?", "regularity": round(score, 2),
    }


def detect_recurring(rows, today=None, include_credits=False, min_count=3, max_cv=0.5):
    """Group by normalised payee, find a cadence, estimate amount, next date and monthly cost."""
    today = today or date.today()
    groups = defaultdict(list)
    for r in rows:
        if r["debit"] or include_credits:
            groups[(r["key"], r["debit"])].append(r)
    found = []
    for txs in groups.values():
        whole = _series(txs, today, min_count, max_cv)
        if whole:
            found.append(whole)
            continue
        for band in _amount_clusters(txs):
            s = _series(band, today, min_count, max_cv)
            if s:
                found.append(s)
    found.sort(key=lambda x: (x["direction"] != "out", x["status"] != "active", -x["monthly_equivalent"]))
    out_active = [f for f in found if f["direction"] == "out" and f["status"] == "active"]
    return {"items": found, "monthly_total_out": round(sum(f["monthly_equivalent"] for f in out_active), 2),
            "currency": (out_active[0]["currency"] if out_active else ""), "as_of": today.isoformat()}


def month_bounds(month):
    """'YYYY-MM' -> (first day, last day) as ISO dates."""
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        raise EbError("month must be YYYY-MM.")
    try:
        start = date.fromisoformat(month + "-01")
    except ValueError:
        raise EbError("month must be YYYY-MM.") from None
    if start.month == 12:
        end = date(start.year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(start.year, start.month + 1, 1) - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def summarise_month(flat_txs, month):
    """Totals for one calendar month from flat transaction dicts. Read-only."""
    month_bounds(month)
    total_out = total_in = 0.0
    count = 0
    currency = ""
    by_payee = defaultdict(float)
    for t in flat_txs:
        booked = (t.get("booking_date") or t.get("value_date") or "")[:7]
        if booked != month:
            continue
        amt = _num(t.get("amount"))
        if amt is None:
            continue
        count += 1
        currency = currency or (t.get("currency") or "")
        debit = t.get("direction") == "DBIT" or (t.get("direction") != "CRDT" and amt < 0)
        abs_amt = abs(amt)
        if debit:
            total_out += abs_amt
            payee = ((t.get("creditor") or t.get("remittance") or "(unknown)") or "").strip() or "(unknown)"
            by_payee[payee] += abs_amt
        else:
            total_in += abs_amt
    top = sorted(by_payee.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    return {
        "month": month,
        "transaction_count": count,
        "total_out": round(total_out, 2),
        "total_in": round(total_in, 2),
        "net": round(total_in - total_out, 2),
        "currency": currency,
        "top_outflows": [{"payee": payee, "amount": round(amount, 2)} for payee, amount in top],
    }


def _latest_inputs():
    d = data_dir()
    if not os.path.isdir(d):
        sys.exit("No data/ directory. Run `eb.py transactions --from YYYY-MM-DD` first, or pass --input.")
    files = sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json"))
    if not files:
        sys.exit("No transaction JSON in data/. Run `eb.py transactions --from YYYY-MM-DD` first.")
    return files


def cmd_recurring(a):
    paths = a.input or _latest_inputs()
    today = date.fromisoformat(a.today) if a.today else None
    res = detect_recurring(load_transactions(paths), today=today, include_credits=a.include_credits,
                           min_count=a.min_count)
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return
    cols = ["payee", "cadence", "typical", "range", "count", "last", "next", "per_month", "status"]
    rows = [[f["payee"][:28] + ("" if f["direction"] == "out" else " (in)"), f["cadence"],
             f"{f['typical_amount']:.2f}", f"{f['min']:.2f}-{f['max']:.2f}", str(f["count"]), f["last"],
             f["next_expected"], f"{f['monthly_equivalent']:.2f}", f["status"]] for f in res["items"]]
    w = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c) for i, c in enumerate(cols)]
    print("  ".join(c.ljust(w[i]) for i, c in enumerate(cols)))
    for r in rows:
        print("  ".join(v.ljust(w[i]) for i, v in enumerate(r)))
    print(f"\nActive recurring outflow, monthly equivalent: {res['monthly_total_out']:.2f} {res['currency']}")


# ---------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description="Read-only Enable Banking client for agents (tested with N26 DE)")
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("application", help="check your Enable Banking app").set_defaults(f=cmd_application)
    x = sp.add_parser("aspsps", help="check your bank is supported")
    x.add_argument("--name"); x.add_argument("--country"); x.set_defaults(f=cmd_aspsps)
    x = sp.add_parser("auth", help="start (or renew) bank consent")
    x.add_argument("--days", type=int, default=180); x.set_defaults(f=cmd_auth)
    x = sp.add_parser("session", help="finish consent with the redirect URL")
    x.add_argument("code", help="code, or the full redirect URL"); x.set_defaults(f=cmd_session)
    x = sp.add_parser("balances"); x.add_argument("--account"); x.set_defaults(f=cmd_balances)
    x = sp.add_parser("transactions"); x.add_argument("--from", dest="date_from", required=True)
    x.add_argument("--to", dest="date_to"); x.add_argument("--account"); x.set_defaults(f=cmd_transactions)
    x = sp.add_parser("status", help="consent expiry; exit 2 if within --warn-days, 3 if expired")
    x.add_argument("--warn-days", type=int, default=14); x.add_argument("--json", action="store_true")
    x.add_argument("--remote", action="store_true", help="also ask Enable Banking for the session status")
    x.set_defaults(f=cmd_status)
    x = sp.add_parser("recurring", help="detect subscriptions and recurring charges (offline)")
    x.add_argument("--input", nargs="+", help="transaction JSON/CSV files (default: all JSON in data/)")
    x.add_argument("--json", action="store_true"); x.add_argument("--include-credits", action="store_true")
    x.add_argument("--min-count", type=int, default=3); x.add_argument("--today", help="YYYY-MM-DD, for testing")
    x.set_defaults(f=cmd_recurring)
    a = p.parse_args()
    try:
        a.f(a)
    except EbError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
