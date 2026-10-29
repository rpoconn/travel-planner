"""Trip cost totals against the budget, spelled out for the agent.

Small models don't add up a plan's costs reliably and will call a price "within budget"
without checking, so the totals are computed here and put in front of them.

Amounts are only summed within one currency: there are no exchange rates here, so costs
in other currencies are listed separately rather than converted.
"""

from collections.abc import Iterable
from decimal import Decimal

from shared.domain import Money

# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------


def totals_by_currency(costs: Iterable[Money | None]) -> dict[str, Decimal]:
    """The sum of `costs` per currency (items without a cost are skipped)."""
    totals: dict[str, Decimal] = {}
    for cost in costs:
        if cost is not None:
            totals[cost.currency] = totals.get(cost.currency, Decimal(0)) + cost.amount
    return totals


def _format(amount: Decimal, currency: str) -> str:
    return f"{amount:,.2f} {currency}"


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def budget_summary(budget: Money | None, totals: dict[str, Decimal]) -> str | None:
    """A line like "Costs so far: 635.78 USD of the 5,000.00 USD budget (4,364.22 USD
    left)", or None when there is neither a budget nor any cost."""
    if budget is None:
        if not totals:
            return None
        spent = ", ".join(_format(amount, code) for code, amount in sorted(totals.items()))
        return f"Costs so far: {spent} (no budget set)"
    code = budget.currency
    spent_in_budget = totals.get(code, Decimal(0))
    left = budget.amount - spent_in_budget
    status = f"{_format(left, code)} left" if left >= 0 else f"{_format(-left, code)} over"
    summary = (
        f"Costs so far: {_format(spent_in_budget, code)} of the "
        f"{_format(budget.amount, code)} budget ({status})"
    )
    others = sorted((other, amount) for other, amount in totals.items() if other != code)
    if others:
        listed = ", ".join(_format(amount, other) for other, amount in others)
        summary += f"; not counted, in other currencies: {listed}"
    return summary


def over_budget_note(budget: Money | None, totals: dict[str, Decimal]) -> str | None:
    """A note like "puts the trip at 5,240.00 USD, over the 5,000.00 USD budget", or None
    when the totals fit the budget (or there is none)."""
    if budget is None:
        return None
    spent = totals.get(budget.currency, Decimal(0))
    if spent <= budget.amount:
        return None
    return (
        f"puts the trip at {_format(spent, budget.currency)}, over the "
        f"{_format(budget.amount, budget.currency)} budget"
    )
