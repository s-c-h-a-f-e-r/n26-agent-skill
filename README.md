# n26-agent-skill

Give your AI agent **read-only** access to your N26 account (or most other EU banks) through PSD2 open banking. One Python file, two dependencies, no server.

- `eb.py`: a small Enable Banking client: consent, balances, transactions, plus offline `recurring` (subscription finder) and `status` (consent expiry) commands.
- `SKILL.md`: an agent skill (AgentSkills format: Grok Bot, Claude skills, OpenClaw) telling the agent when and how to use it, the renewal routine, and the safety rules.

There is **no payment code** in this repo. The consent it asks for covers balances and transactions only.

## Why PSD2 via Enable Banking, not an unofficial N26 API
| | This (PSD2 AIS via Enable Banking) | Unofficial N26 API / scrapers |
|---|---|---|
| Legal basis | EU PSD2 account information service through a licensed provider | Reverse-engineered private API |
| Your password | Never shared: you approve in your bank app | Agent needs your login and 2FA |
| Bank terms | Sanctioned route | Breaks N26's terms, can get you locked out |
| Access | Read-only, time-boxed (max 180 days), revocable | Whatever your login can do |
| Stability | Standard API across 2,000+ banks | Breaks when the app changes |

## What it costs
Enable Banking's restricted production mode is free when you link your own accounts for personal, non-commercial use. Every user creates **their own** Enable Banking application. Never share an app ID plus key.

## Setup (about 15 minutes)
1. **Install**
   ```bash
   git clone <this repo> && cd n26-agent-skill
   python3 -m venv .venv && . .venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Create your Enable Banking app**
   1. Sign up at https://enablebanking.com and open the Control Panel.
   2. Go to **API applications** > register a new application. Environment: **Production**. Redirect URL: `https://enablebanking.com/`. Use: personal.
   3. Download the private key (`.pem`) and note the **application ID**.
   4. Activate it by linking your own account in the Control Panel (restricted mode).
3. **Configure**
   ```bash
   mkdir -p keys && mv ~/Downloads/<your-key>.pem keys/private.pem && chmod 600 keys/private.pem
   export EB_APP_ID=<your-application-id>     # or copy config.example.json to config.json
   # other banks: export EB_BANK="Revolut" EB_COUNTRY=DE
   ```
4. **Check**
   ```bash
   python3 eb.py application   # key and app ID work
   python3 eb.py aspsps        # your bank is listed
   ```
5. **Connect your bank**
   ```bash
   python3 eb.py auth          # prints a link: open it, approve in your bank app
   python3 eb.py session 'https://enablebanking.com/?state=...&code=...'   # the URL you landed on
   ```
6. **Use it**
   ```bash
   python3 eb.py balances
   python3 eb.py transactions --from 2026-01-01
   python3 eb.py recurring           # or --json
   python3 eb.py status              # exit 2 within 14 days of expiry, 3 when expired
   ```
7. **Give it to your agent**: copy or symlink this folder into your agent's skills directory (it reads `SKILL.md`). Hand the app ID and key to the agent as secrets, never in chat.

### Example `recurring` output (synthetic sample)
```
$ python3 eb.py recurring --input sample/transactions.json --today 2026-10-31
payee                        cadence  typical  range          count  last        next        per_month  status
Example Hausverwaltung GmbH  monthly  940.00   940.00-940.00  8      2026-10-01  2026-11-01  940.00     active
Veggie Box Weekly            weekly   18.50    18.50-18.50    26     2026-10-24  2026-10-31  80.44      active
StreamCo Europe B.V.         monthly  12.99    12.99-12.99    7      2026-10-03  2026-11-03  12.99      active
PhoneCo Mobile               monthly  9.99     9.99-9.99      6      2026-10-20  2026-11-20  9.99       active
Moon Magazine                lunar    4.99     4.99-4.99      8      2026-10-25  2026-11-24  5.14       active
DomainHost.com               yearly   15.00    15.00-15.00    2      2025-11-12  2026-11-12  1.25       active
Fit Club Berlin              monthly  29.90    29.90-29.90    4      2026-07-10  2026-08-10  29.90      lapsed?

Active recurring outflow, monthly equivalent: 1049.81 EUR
```
How it works: transactions are grouped by normalised payee; same-day charges merge, different price points split; the gap between charges is matched to weekly, lunar (~29.5 days, drifting day of month), monthly (same day of month), quarterly or yearly; the median amount gives the typical charge and the monthly equivalent; a charge more than a few days overdue is marked `lapsed?`.

## Consent renewal
N26 consents last at most 180 days. Run `eb.py status` from a weekly routine (cron, or your agent's scheduler); when it exits 2, run `auth` + `session` again. PSD2 caps unattended refreshes at about 4 a day.

## Privacy
- Keys, `config.json`, `session.json` and `data/` are gitignored. Keep it that way.
- Your agent should show balances and transactions only to you, never in shared channels (see `SKILL.md`).

## Tests
```bash
python3 -m unittest discover -s tests     # offline, uses sample/transactions.json
python3 tests/make_sample.py              # regenerate the synthetic sample
```

## Credits
- [noskillish/bankmcp](https://github.com/noskillish/bankmcp): the leading Enable Banking MCP server. Want an MCP server with more features? Use that. This repo is the zero-server, single-file alternative.
- [Enable Banking API samples](https://github.com/enablebanking/enablebanking-api-samples), on which the client is based.

## Disclaimer
Not affiliated with N26 or Enable Banking. Not financial advice. Check your bank's and Enable Banking's terms before connecting.

## License
MIT, see [LICENSE](LICENSE).
