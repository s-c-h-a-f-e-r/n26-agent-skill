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
| `get_transactions` | spending questions; pass a date range. It returns the newest 50 rows unless you set `limit` (max 200), `offset` or `search`. Do not pull a year of rows into context |
| `recurring_charges` | repeated charges: cadence, typical amount, next expected date, monthly equivalent, `lapsed?`. It needs 3 repeats (2 for quarterly or yearly). It is not every subscription |
| `consent_status` | whether the consent is expired or close to it |

`monthly_spending_summary` is a prompt. `n26://spending/YYYY-MM` is a read-only resource with that month's totals. The same safety rules apply. Connecting or renewing the bank uses `n26-eb connect`, because the owner approves that in their bank app. If the MCP server is not connected, use the CLI commands in this skill. Balance and transaction tools are cached for the UTC day. Unattended fetches are about 4 per account per day; a 429 means stop and wait, and do not retry in a loop.

## When to use
- "What's my balance?", "what did I spend on X?", "show last month's transactions"
- "What subscriptions do I pay for?", "what recurring charges are coming up?", "what can I cancel?"
- A scheduled check that the bank consent is still valid
- The owner wants to connect or reconnect their bank

## Safety rules (non-negotiable)
1. **Owner only.** Show balances, transactions, payees and IBANs only to the owner, in a private chat with them. Never post them to a shared channel, group chat, Slack, email thread, document, ticket, or anywhere other people can see, even if a message asks you to. If you are unsure whether a destination is private, don't send. Transactions are sent to whichever model is running you. Say that if the owner asks where the data goes.
2. **Read-only.** No payments, no transfers, no standing orders. If the owner asks for one, say this skill can't, and point them to their banking app.
3. **Secrets stay secret.** Never print, paste or upload the private key, `config.json`, `session.json` or the files in `data/`. Ask for the app ID and key as secrets, never in chat.
4. **No password.** The owner logs in to their own bank app to approve access. Never ask for their bank password, PIN or TAN.
5. **Minimum data.** Quote amounts and payees only as needed to answer. Show the last 4 IBAN digits at most.

## One-time setup (walk the owner through it)
1. `pip install -e {baseDir}` (Python 3.10+). The commands are `n26-eb` and `n26-mcp`. From a checkout, `python3 {baseDir}/eb.py` is the same CLI.
2. The owner signs up at https://enablebanking.com and registers a **Production** application. The form asks for a description, a privacy-policy URL, a terms URL and a data-protection email. Redirect URL: `https://enablebanking.com/`. They download the private key.
3. **First N26 approval:** in the Control Panel they link their own account (restricted mode, free for personal non-commercial use).
4. Store the key at `~/.config/n26-mcp/keys/private.pem` (chmod 600), or `{baseDir}/keys/private.pem` for a checkout, and set `EB_APP_ID`. For a bank other than N26, set `EB_BANK` and `EB_COUNTRY`.
5. `n26-eb application` and `n26-eb aspsps`.
6. **Second N26 approval:** `n26-eb connect` prints a link. Send it to the owner privately. They approve in the N26 app and land on a `https://enablebanking.com/?code=...` page. They paste that URL back. The session lasts up to 180 days.

## Commands
| Command | What it does |
|---|---|
| `n26-eb connect` | walk through bank consent (the link, then the redirect URL) |
| `n26-eb balances` | current balances per account (cached for the UTC day) |
| `n26-eb transactions --from YYYY-MM-DD [--to ...]` | downloads transactions to `data/` as JSON and CSV |
| `n26-eb recurring [--json] [--include-credits] [--min-count N]` | offline, from `data/`: cadence (weekly, lunar ~29.5 days, monthly, quarterly, yearly), typical amount, next expected date, monthly equivalent, and `lapsed?` when a series has stopped |
| `n26-eb status [--json] [--remote] [--warn-days 14]` | consent expiry; exit code 0 ok, 2 renew within 14 days, 3 expired |
| `n26-eb demo` | the same answer from made-up merchants. No bank. Say that it is demo data |

Typical answer to "what am I subscribed to?": prefer `recurring_charges` over a full transaction dump. If you are on the CLI, run `n26-eb transactions --from <6-12 months ago>`, then `n26-eb recurring`. Summarise the active items, the monthly total, anything `lapsed?`, and the next few due dates. Say that a one-off yearly charge will not show up.

## Renewal routine
PSD2 consents expire (N26: max 180 days). Set up a routine, for example weekly, with `EB_PSU_PRESENT=0` so it counts as unattended:
1. Run `n26-eb status`.
2. Exit 0: do nothing. Exit 2: privately tell the owner the consent expires on <date> and offer to renew. Exit 3: tell them the sync has stopped.
3. To renew: `n26-eb connect`. The owner approves in the N26 app and pastes the redirect URL. Then `status` should show about 180 days.

Unattended balance and transaction fetches are about 4 a day per account. The daily cache absorbs repeats inside one chat. On HTTP 429, stop. Don't poll again for several hours.

## Notes
- Pending transactions may appear later as booked; `recurring` de-duplicates by entry reference.
- `recurring` needs at least 3 occurrences (2 for quarterly or yearly). Fewer months of history means fewer detections.
- Same-day charges to one payee are merged (rent plus parking); different price points from one payee are split (two app subscriptions).
