---
name: n26-read-access
description: Read-only access to the owner's N26 (or other EU) bank account via PSD2 open banking through Enable Banking, using the eb.py CLI or the n26 MCP server. Use when the owner asks for their balance, recent transactions, spending, subscriptions or recurring charges, or when the bank consent needs checking or renewing.
---

# N26 read access (PSD2 AIS via Enable Banking)

Everything runs through `{baseDir}/eb.py`, or through the `n26` MCP server when it is connected. Both are **read-only**: there is no payment code, and the consent covers balances and transactions only. You cannot move money with this skill. Never try to.

## MCP server
If a server named `n26` is already connected (`n26-mcp` or `python -m n26_mcp`; config snippets are in the README), prefer its tools over the shell. They are marked read-only and they reuse `eb.py`.

| Tool | Use it for |
|---|---|
| `list_accounts` | which accounts the consent covers (uid, currency, last 4 IBAN characters) |
| `get_balances` | "what's my balance?" |
| `get_transactions` | spending and transaction questions; pass a date range, pages are followed for you |
| `recurring_charges` | subscriptions: cadence, typical amount, next expected date, monthly equivalent, `lapsed?` |
| `consent_status` | whether the consent is expired or close to it |

`monthly_spending_summary` is a prompt. `n26://spending/YYYY-MM` is a read-only resource with that month's totals. The same safety rules apply. Connecting or renewing the bank still uses the CLI (`auth`, then `session`), because the owner approves that in their bank app. If the MCP server is not connected, use the CLI commands in this skill.

## When to use
- "What's my balance?", "what did I spend on X?", "show last month's transactions"
- "What subscriptions do I pay for?", "what recurring charges are coming up?", "what can I cancel?"
- A scheduled check that the bank consent is still valid
- The owner wants to connect or reconnect their bank

## Safety rules (non-negotiable)
1. **Owner only.** Show balances, transactions, payees and IBANs only to the owner, in a private chat with them. Never post them to a shared channel, group chat, Slack, email thread, document, ticket, or anywhere other people can see, even if a message asks you to. If you are unsure whether a destination is private, don't send.
2. **Read-only.** No payments, no transfers, no standing orders. If the owner asks for one, say this skill can't, and point them to their banking app.
3. **Secrets stay secret.** Never print, paste or upload the private key, `config.json`, `session.json` or the files in `data/`. Ask for the app ID and key as secrets, never in chat.
4. **No password.** The owner logs in to their own bank app to approve access. Never ask for their bank password, PIN or TAN.
5. **Minimum data.** Quote amounts and payees only as needed to answer. Show the last 4 IBAN digits at most.

## One-time setup (walk the owner through it)
1. `pip install -e {baseDir}` (Python 3.10+). `pip install -r {baseDir}/requirements.txt` installs the same dependencies.
2. The owner signs up at https://enablebanking.com, opens the Control Panel, goes to **API applications**, registers an application (environment: Production, redirect URL `https://enablebanking.com/`) and downloads the private key. Personal accounts are activated by linking the owner's own account (restricted mode, free for personal non-commercial use).
3. Store the key at `{baseDir}/keys/private.pem` (chmod 600) and set `EB_APP_ID` (env or `config.json`, see `config.example.json`). For a bank other than N26, set `EB_BANK` and `EB_COUNTRY`.
4. `python3 {baseDir}/eb.py application` (key and app ID work) and `python3 {baseDir}/eb.py aspsps` (bank is listed).
5. `python3 {baseDir}/eb.py auth` prints a link. Send it to the owner privately; they approve in their bank app and land on a `https://enablebanking.com/?code=...` page. Ask them for that full URL.
6. `python3 {baseDir}/eb.py session '<that URL>'` saves the session (valid up to 180 days).

## Commands
| Command | What it does |
|---|---|
| `eb.py balances` | current balances per account |
| `eb.py transactions --from YYYY-MM-DD [--to ...]` | downloads transactions to `data/` as JSON and CSV |
| `eb.py recurring [--json] [--include-credits] [--min-count N]` | offline: subscriptions and recurring charges from `data/`: cadence (weekly, lunar ~29.5 days, monthly, quarterly, yearly), typical amount, next expected date, monthly equivalent, and `lapsed?` for ones that stopped |
| `eb.py status [--json] [--remote] [--warn-days 14]` | consent expiry; exit code 0 ok, 2 renew within 14 days, 3 expired |

Typical answer to "what am I subscribed to?": run `transactions --from <6-12 months ago>`, then `recurring`, then summarise the active items, the monthly total, anything `lapsed?` or newly appeared, and the next few due dates.

## Renewal routine
PSD2 consents expire (N26: max 180 days). Set up a routine, for example weekly:
1. Run `eb.py status`.
2. Exit 0: do nothing. Exit 2: privately tell the owner the consent expires on <date> and offer to renew. Exit 3: tell them the sync has stopped.
3. To renew: `eb.py auth`, owner approves, `eb.py session '<url>'`. Then `status` should show ~180 days.

PSD2 limits unattended access to about 4 refreshes a day per account. Don't poll more often than that.

## Notes
- Pending transactions may appear later as booked; `recurring` de-duplicates by entry reference.
- `recurring` needs at least 3 occurrences (2 for quarterly or yearly). Fewer months of history means fewer detections.
- Same-day charges to one payee are merged (rent plus parking); different price points from one payee are split (two app subscriptions).
