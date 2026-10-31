"""Read-only MCP server for an N26 (or other Enable Banking) account.

Account information only. This module has no payment or consent-initiation
tool: connecting the bank stays on the eb.py CLI, because the owner approves
it in their bank app. Config is the same environment variables or config.json
the CLI uses. Secrets are not logged.

The official MCP Python SDK's high-level server is MCPServer (FastMCP, renamed
in mcp 2.x). Transport is stdio.
"""
import json
from contextlib import contextmanager
from datetime import date

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from typing import Annotated

import eb
from n26_mcp import __version__

OWNER_ONLY = (
    "For the account owner only. Do not show balances, transactions, payees "
    "or account identifiers in a shared channel."
)

INSTRUCTIONS = (
    "Read-only PSD2 account information for the owner's N26 account "
    "(or another bank reached through Enable Banking). There is no payment tool "
    "and no way to move money. Show balances, transactions, payees and account "
    "identifiers only to the account owner, in a private chat. Never ask for a "
    "bank password, and never reveal the private key, application id or session file. "
    "get_transactions returns at most 50 rows by default (200 maximum); narrow it with "
    "search, offset and dates, or use recurring_charges and the monthly spending resource "
    "for a summary. Balance and transaction fetches are cached for the UTC day. "
    "Unattended bank fetches are limited to about 4 per account per day."
)


def _read_only(*, local: bool) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=not local,
    )


@contextmanager
def _guard(error_type=ToolError):
    """Turn an expected client error into the SDK's anticipated-failure type.

    Anything else stays a crash, so its text (which might be unexpected) is
    not copied into the tool result.
    """
    try:
        yield
    except error_type:
        raise
    except eb.EbError as exc:
        raise error_type(str(exc)) from exc


class Account(BaseModel):
    uid: str = Field(description="Enable Banking account uid. Pass this as account to the other tools.")
    iban_last4: str = Field(description="Last four IBAN characters only.")
    currency: str = ""


class AccountsResult(BaseModel):
    bank: str = ""
    accounts: list[Account]
    owner_only: str


class BalanceLine(BaseModel):
    balance_type: str | None = None
    amount: str | None = None
    currency: str | None = None
    reference_date: str = ""


class AccountBalances(BaseModel):
    account: str
    iban_last4: str = ""
    balances: list[BalanceLine]
    notice: str | None = None


class BalancesResult(BaseModel):
    accounts: list[AccountBalances]
    owner_only: str


class Transaction(BaseModel):
    booking_date: str | None = None
    value_date: str | None = None
    amount: str | None = None
    currency: str | None = None
    direction: str | None = Field(default=None, description="DBIT for money out, CRDT for money in.")
    status: str | None = None
    creditor: str | None = None
    debtor: str | None = None
    remittance: str | None = None
    bank_code: str | None = None
    entry_reference: str | None = None
    transaction_id: str | None = None


class AccountTransactions(BaseModel):
    account: str
    iban_last4: str = ""
    pages: int = Field(description="How many Enable Banking pages were followed for this range. A cache hit reuses the earlier count.")
    matched: int = Field(description="Rows in the range after the search filter, before limit and offset.")
    returned: int
    count: int = Field(description="Same as returned. Kept so older clients still see a count.")
    offset: int
    limit: int
    truncated: bool = Field(description="True when matched rows remain after this page.")
    transactions: list[Transaction]
    notice: str | None = None


class TransactionsResult(BaseModel):
    accounts: list[AccountTransactions]
    owner_only: str


class RecurringItem(BaseModel):
    payee: str
    key: str
    direction: str
    cadence: str = Field(description="weekly, lunar, monthly, quarterly or yearly.")
    count: int
    first: str
    last: str
    typical_amount: float
    min: float
    max: float
    currency: str
    kind: str
    next_expected: str
    monthly_equivalent: float
    status: str = Field(description="active, or lapsed? when the next charge is overdue.")
    regularity: float


class RecurringResult(BaseModel):
    items: list[RecurringItem]
    monthly_total_out: float = Field(description="Sum of monthly equivalents for active outgoing charges.")
    currency: str = ""
    as_of: str
    owner_only: str
    notice: str | None = None


class ConsentResult(BaseModel):
    state: str = Field(description="ok, renew_soon, expired or unknown.")
    valid_until: str | None = None
    days_left: float | None = None
    bank: str | None = None
    accounts: int = Field(description="How many accounts the consent covers.")
    warning: str | None = None
    remote_status: str | None = Field(default=None, description="Enable Banking session status, only when remote is true.")
    owner_only: str


mcp = MCPServer(
    name="n26",
    title="N26 read-only",
    instructions=INSTRUCTIONS,
    version=__version__,
    log_level="WARNING",
)


def _iban_last4(acc):
    return str(acc.get("iban_last4") or "").replace(" ", "")[-4:]


def _warning(status, warn_days):
    state = status.get("state")
    if state == "renew_soon":
        return (
            f"WARNING: consent expires within {warn_days} days "
            f"({status.get('valid_until')}). Renew with `n26-eb connect` and approve again in your bank app."
        )
    if state == "expired":
        return "EXPIRED: consent has ended. Run `n26-eb connect`, approve in your bank app, then paste the redirect URL."
    if state == "unknown":
        return "No consent expiry on the session file."
    return None


@mcp.tool(title="List accounts", annotations=_read_only(local=True))
def list_accounts() -> AccountsResult:
    """Read-only list of accounts covered by the current bank consent.

    Returns each account uid, its currency, and the last four IBAN characters
    from the local session file. It does not call the bank and cannot move money.
    Show the result only to the account owner.
    """
    with _guard():
        session = eb.load_session()
        bank_name = (session.get("aspsp") or {}).get("name") or ""
        accounts = []
        for acc in session.get("accounts") or []:
            uid = acc.get("uid")
            if not uid:
                continue
            accounts.append(Account(uid=uid, iban_last4=_iban_last4(acc), currency=acc.get("currency") or ""))
        return AccountsResult(bank=bank_name, accounts=accounts, owner_only=OWNER_ONLY)


@mcp.tool(title="Get balances", annotations=_read_only(local=False))
def get_balances(
    account: Annotated[str | None, Field(description="Account uid from list_accounts. Omit for every consented account.")] = None,
) -> BalancesResult:
    """Read-only balances for the consented accounts.

    Shows balance type, amount, currency and reference date. There is no payment
    action. Show the result only to the account owner.
    """
    with _guard():
        blocks = eb.fetch_balances(eb.load_session(), account, required=True)
        return BalancesResult(
            accounts=[
                AccountBalances(
                    account=block["account"],
                    iban_last4=block["iban_last4"],
                    balances=[BalanceLine(**line) for line in block["balances"]],
                    notice=block.get("notice"),
                )
                for block in blocks
            ],
            owner_only=OWNER_ONLY,
        )


@mcp.tool(title="Get transactions", annotations=_read_only(local=False))
def get_transactions(
    date_from: Annotated[str, Field(description="Start date, inclusive, YYYY-MM-DD.")],
    date_to: Annotated[str | None, Field(description="End date, inclusive, YYYY-MM-DD. Omit to leave the end open.")] = None,
    account: Annotated[str | None, Field(description="Account uid from list_accounts. Omit for every consented account.")] = None,
    search: Annotated[str | None, Field(description="Case-insensitive text match on payee (creditor or debtor) or remittance.")] = None,
    limit: Annotated[int, Field(ge=1, le=200, description="How many matching rows to return, newest first. Default 50. Maximum 200.")] = 50,
    offset: Annotated[int, Field(ge=0, description="How many matching rows to skip. Use with limit to page.")] = 0,
) -> TransactionsResult:
    """Read-only transactions for a date range.

    Follows Enable Banking continuation pages, then returns the newest matching
    rows only (default 50, maximum 200) so a long history does not fill the
    context. Filter with search, or narrow date_from and date_to. For a summary,
    use recurring_charges or the monthly spending resource instead of raising
    the limit. Debits are negative amounts with direction DBIT. There is no
    payment action. Show the result only to the account owner.
    """
    with _guard():
        blocks = eb.fetch_transactions(eb.load_session(), date_from, date_to, account, required=True)
        accounts = []
        for block in blocks:
            page = eb.select_transactions(block["transactions"], search=search, limit=limit, offset=offset)
            accounts.append(AccountTransactions(
                account=block["account"],
                iban_last4=block["iban_last4"],
                pages=block["pages"],
                matched=page["matched"],
                returned=page["returned"],
                count=page["returned"],
                offset=page["offset"],
                limit=page["limit"],
                truncated=page["truncated"],
                transactions=[Transaction(**row) for row in page["transactions"]],
                notice=block.get("notice"),
            ))
        return TransactionsResult(accounts=accounts, owner_only=OWNER_ONLY)


@mcp.tool(title="Recurring charges", annotations=_read_only(local=False))
def recurring_charges(
    date_from: Annotated[str, Field(description="How far back to look, inclusive, YYYY-MM-DD. Six to twelve months works well.")],
    date_to: Annotated[str | None, Field(description="End date, inclusive, YYYY-MM-DD. Omit to leave the end open.")] = None,
    account: Annotated[str | None, Field(description="Account uid from list_accounts. Omit for every consented account.")] = None,
    include_credits: Annotated[bool, Field(description="Also detect recurring incoming payments, such as salary.")] = False,
    min_count: Annotated[int, Field(ge=2, le=36, description="Minimum charges before a series counts. Quarterly and yearly series need only 2.")] = 3,
    today: Annotated[str | None, Field(description="Judge lapsed charges as of this YYYY-MM-DD. Defaults to the real date.")] = None,
) -> RecurringResult:
    """Read-only detection of subscriptions and other recurring charges.

    Same detector as `eb.py recurring`: cadence (weekly, lunar, monthly,
    quarterly, yearly), typical amount, next expected date and monthly
    equivalent. A charge that is overdue is marked lapsed?. Pagination is
    handled inside the tool. There is no payment action. Show the result only
    to the account owner.
    """
    with _guard():
        as_of = None
        if today:
            try:
                as_of = date.fromisoformat(today)
            except ValueError:
                raise eb.EbError("today must be YYYY-MM-DD.") from None
        blocks = eb.fetch_transactions(eb.load_session(), date_from, date_to, account, required=True)
        rows = eb.rows_from_flat([row for block in blocks for row in block["transactions"]])
        found = eb.detect_recurring(rows, today=as_of, include_credits=include_credits, min_count=min_count)
        notices = [block["notice"] for block in blocks if block.get("notice")]
        return RecurringResult(**found, owner_only=OWNER_ONLY, notice=notices[0] if notices else None)


@mcp.tool(title="Consent status", annotations=_read_only(local=False))
def consent_status(
    warn_days: Annotated[int, Field(ge=1, le=365, description="Warn when this many days or fewer remain.")] = 14,
    remote: Annotated[bool, Field(description="Also ask Enable Banking for the live session status.")] = False,
) -> ConsentResult:
    """Read-only consent expiry: valid until, days left, and a warning.

    N26 consents last at most 180 days. state is ok, renew_soon, expired or
    unknown. This does not renew the consent and cannot move money. Renew with
    `n26-eb connect`. Show the result only to the account owner.
    """
    with _guard():
        session = eb.load_session()
        status = eb.consent_status(session, warn_days=warn_days)
        remote_status = None
        if remote:
            remote_status = eb.call("GET", f"/sessions/{session['session_id']}").get("status")
        return ConsentResult(
            state=status["state"],
            valid_until=status.get("valid_until"),
            days_left=status.get("days_left"),
            bank=status.get("bank"),
            accounts=status.get("accounts") or 0,
            warning=_warning(status, warn_days),
            remote_status=remote_status,
            owner_only=OWNER_ONLY,
        )


@mcp.resource(
    "n26://spending/{month}",
    name="monthly_spending",
    title="Monthly spending summary",
    description="Read-only totals for one calendar month (YYYY-MM). For the account owner only. There is no payment action.",
    mime_type="application/json",
)
def monthly_spending(month: str) -> str:
    """Read-only monthly spending summary. month is YYYY-MM."""
    with _guard(ResourceError):
        start, end = eb.month_bounds(month)
        blocks = eb.fetch_transactions(eb.load_session(), start, end)
        flat_txs = [row for block in blocks for row in block["transactions"]]
        summary = eb.summarise_month(flat_txs, month)
        summary["owner_only"] = OWNER_ONLY
        return json.dumps(summary)


@mcp.prompt(
    title="Monthly spending summary",
    description="Draft a monthly spending summary the agent may show only to the account owner.",
)
def monthly_spending_summary(
    month: Annotated[str, Field(description="Calendar month, YYYY-MM. Leave blank for the latest complete month.")] = "",
) -> str:
    """Read-only prompt for a monthly spending summary."""
    when = month or "the latest complete calendar month"
    return (
        f"Prepare a monthly spending summary for {when} for the account owner only. "
        "Use the read-only tools: call get_transactions for that month and recurring_charges "
        "over at least the previous six months. Report money in, money out, the largest outflows, "
        "active recurring charges with cadence, typical amount, next expected date and monthly "
        "equivalent, and anything marked lapsed. You can also read the resource n26://spending/YYYY-MM. "
        "Do not include a full IBAN, the private key, the application id, or the session file. "
        "Do not propose or attempt a payment."
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
