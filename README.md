# n26-agent-skill

Give your AI agent **read-only** access to your N26 account (or most other EU banks) through PSD2 open banking. A Python CLI and a small stdio MCP server. No payment code.

- `eb.py`: an Enable Banking client: consent, balances, transactions, plus offline `recurring` (subscription finder) and `status` (consent expiry).
- `n26-mcp`: the same client behind a Model Context Protocol server, for Claude Desktop, Claude Code, Cursor and any other MCP client.
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
   python3 -m venv .venv && . .venv/bin/activate    # Python 3.10+
   pip install -e .                                 # CLI and the n26-mcp command
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

## MCP server

`n26-mcp` speaks MCP over **stdio**. It uses the official MCP Python SDK (`mcp` 2.x). In that SDK the high-level server class is `MCPServer`, which replaced `FastMCP`. Claude Desktop, Claude Code, Cursor and other MCP clients start it as a local process.

The server imports `eb.py`. It does not contain a second client, and it has **no payment or consent-initiation tool**. Connecting and renewing the bank stays on the CLI (`auth`, then `session`), because you approve that in your bank app. The tools do not write transaction files; they return data to the client. Config is the same as the CLI: environment variables win, then `config.json`. In an MCP client, set the variables in the server config and use **absolute paths** for the key and the session file.

Every tool is marked read-only (`readOnlyHint`). Balances, transactions and payees are for **you** only. Do not add this server to a shared workspace where other people can see tool output.

| Tool | What the model gets |
|---|---|
| `list_accounts` | Consented accounts: uid, currency, last 4 IBAN characters. Local session only. |
| `get_balances` | Balance type, amount, currency, reference date. |
| `get_transactions` | Flat transactions for a date range. Continuation pages are followed inside the tool. |
| `recurring_charges` | The same detector as `eb.py recurring`: cadence, typical amount, next expected date, monthly equivalent, `lapsed?`. |
| `consent_status` | Expiry, days left, and a warning. `remote: true` also asks Enable Banking for the session status. |

There is also a prompt, `monthly_spending_summary`, and a resource `n26://spending/YYYY-MM` with that month's totals. Neither can move money.

### Run it

```bash
n26-mcp                         # after pip install -e . or pipx install
python3 -m n26_mcp

# uv, with no separate install step. Use a local checkout, or git once this is on the default branch.
uvx --from /absolute/path/to/n26-agent-skill n26-mcp
uvx --from git+https://github.com/s-c-h-a-f-e-r/n26-agent-skill n26-mcp

pipx install /absolute/path/to/n26-agent-skill
```

Finish `eb.py auth` and `eb.py session` before expecting balances. The server only reads.

### Claude Desktop

`claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`, Linux: `~/.config/Claude/`):

```json
{
  "mcpServers": {
    "n26": {
      "command": "uvx",
      "args": ["--from", "/absolute/path/to/n26-agent-skill", "n26-mcp"],
      "env": {
        "EB_APP_ID": "your-application-id",
        "EB_KEY_PATH": "/absolute/path/to/keys/private.pem",
        "EB_SESSION_PATH": "/absolute/path/to/session.json",
        "EB_BANK": "N26",
        "EB_COUNTRY": "DE"
      }
    }
  }
}
```

### Claude Code

`--transport stdio` has to sit between the last `--env` and the server name, otherwise the name is parsed as another variable.

```bash
claude mcp add \
  --env EB_APP_ID=your-application-id \
  --env EB_KEY_PATH=/absolute/path/to/keys/private.pem \
  --env EB_SESSION_PATH=/absolute/path/to/session.json \
  --env EB_BANK=N26 \
  --env EB_COUNTRY=DE \
  --transport stdio n26 \
  -- uvx --from /absolute/path/to/n26-agent-skill n26-mcp
```

### Cursor

Project file `.cursor/mcp.json`, or `~/.cursor/mcp.json` for every project. `${env:NAME}` keeps the secret out of the file. Put the real values in your environment, not in git.

```json
{
  "mcpServers": {
    "n26": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "/absolute/path/to/n26-agent-skill", "n26-mcp"],
      "env": {
        "EB_APP_ID": "${env:EB_APP_ID}",
        "EB_KEY_PATH": "${env:EB_KEY_PATH}",
        "EB_SESSION_PATH": "${env:EB_SESSION_PATH}",
        "EB_BANK": "N26",
        "EB_COUNTRY": "DE"
      }
    }
  }
}
```

A `python -m` form, after `pip install -e /absolute/path/to/n26-agent-skill`, is the same block with `"command": "python3"` and `"args": ["-m", "n26_mcp"]`.

## Consent renewal
N26 consents last at most 180 days. Run `eb.py status` from a weekly routine (cron, or your agent's scheduler); when it exits 2, run `auth` + `session` again. PSD2 caps unattended refreshes at about 4 a day.

## Privacy
- Keys, `config.json`, `session.json` and `data/` are gitignored. Keep it that way.
- Your agent should show balances and transactions only to you, never in shared channels (see `SKILL.md`). The MCP tools repeat that on every result. Do not point a shared Cursor or Claude project at this server if other people can read its output.

## Tests
```bash
python3 -m unittest discover -s tests     # offline, uses sample/transactions.json and a mocked Enable Banking API
python3 tests/make_sample.py              # regenerate the synthetic sample
```

GitHub Actions runs that suite on Python 3.10–3.13 (`.github/workflows/tests.yml`).

## Credits
- [noskillish/bankmcp](https://github.com/noskillish/bankmcp): the leading Enable Banking MCP server. Want more banks, features and surface area? Use that. This repo is the small, N26-tested, read-only, PSD2-licensed route: one client, a CLI and a stdio MCP server, and no unofficial N26 API.
- [Enable Banking API samples](https://github.com/enablebanking/enablebanking-api-samples), on which the client is based.

## Disclaimer
Not affiliated with N26 or Enable Banking. Not financial advice. Check your bank's and Enable Banking's terms before connecting.

## License
MIT, see [LICENSE](LICENSE).
