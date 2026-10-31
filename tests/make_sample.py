"""Regenerate sample/transactions.json: synthetic data in Enable Banking's format. No real people or accounts."""
import json, os, random
from datetime import date, timedelta

random.seed(42)
TX = []


def tx(d, amount, name, debit=True, ref=None):
    TX.append({
        "entry_reference": f"sample-{len(TX):04d}",
        "transaction_amount": {"currency": "EUR", "amount": f"{amount:.2f}"},
        "creditor": {"name": name} if debit else None,
        "debtor": None if debit else {"name": name},
        "credit_debit_indicator": "DBIT" if debit else "CRDT",
        "status": "BOOK", "booking_date": d.isoformat(), "value_date": d.isoformat(),
        "remittance_information": [ref or name],
    })


def monthly(start, n, dom):
    y, m = start
    for _ in range(n):
        yield date(y, m, min(dom, 28))
        m += 1
        if m == 13:
            y, m = y + 1, 1


for d in monthly((2026, 4), 7, 3):
    tx(d, 12.99, "StreamCo Europe B.V.")                 # monthly subscription
for d in monthly((2026, 3), 8, 1):
    tx(d, 900.00, "Example Hausverwaltung GmbH")         # rent ...
    tx(d, 40.00, "EXAMPLE HAUSVERWALTUNG")               # ... plus parking, same day
for d in monthly((2026, 4), 4, 10):
    tx(d, 29.90, "Fit Club Berlin")                      # gym that stopped in July
for d in monthly((2026, 5), 6, 20):
    tx(d, 9.99, "PhoneCo Mobile")                        # phone plan ...
tx(date(2026, 8, 2), 49.00, "PhoneCo Mobile")            # ... and a one-off accessory
d = date(2026, 5, 2)
while d <= date(2026, 10, 30):
    tx(d, 18.50, "Veggie Box Weekly")                    # weekly
    d += timedelta(days=7)
for i in range(8):
    tx(date(2026, 4, 1) + timedelta(days=round(i * 29.53)), 4.99, "Moon Magazine")  # lunar
tx(date(2025, 11, 12), 15.00, "DomainHost.com")          # yearly
tx(date(2024, 11, 12), 15.00, "DOMAINHOST")
d = date(2026, 4, 1)
while d <= date(2026, 10, 30):
    tx(d, round(random.uniform(8, 95), 2), random.choice(["Corner Market", "Bakery Example", "Cafe Sample"]))
    d += timedelta(days=random.randint(1, 6))           # irregular noise
for d in monthly((2026, 4), 7, 27):
    tx(d, 3200.00, "Example Employer GmbH", debit=False)  # salary (credit)

TX.sort(key=lambda t: t["booking_date"], reverse=True)
out = os.path.join(os.path.dirname(__file__), "..", "sample", "transactions.json")
with open(out, "w") as f:
    json.dump(TX, f, indent=1)
bundled = os.path.join(os.path.dirname(__file__), "..", "n26_mcp", "demo_transactions.json")
with open(bundled, "w") as f:
    json.dump(TX, f, indent=1)
print(f"{len(TX)} transactions -> {os.path.normpath(out)}")
