"""Budget rules for a plan that has a budget or spending limits.

- Every item has a cost, in the budget's currency (estimates are fine).
- An activity costs at most its category's per-person limit times the party size.
- The costs of all active items add up to no more than the budget.

Pure functions over validated schemas, returning a description of the problem or None. The
agent's tools enforce them (a change that breaks one never reaches the user); the timeline
only warns, since the user may add items or lower the budget themselves.
"""

from collections.abc import Iterable, Sequence

from features.itinerary.schemas import ActivityItemCreate, ItemFields, SpendingLimit
from shared.costs import format_money, totals_by_currency
from shared.domain import Cost, Money, Travelers

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def party_size(travelers: Travelers | None) -> int:
    # Unknown travelers count as one: the strictest reading of a per-person limit.
    return travelers.adults + travelers.children if travelers is not None else 1


def limit_for(item: ItemFields, limits: Sequence[SpendingLimit]) -> SpendingLimit | None:
    """The limit that applies to `item`, if any (only activities have categories)."""
    if not isinstance(item, ActivityItemCreate):
        return None
    return next((limit for limit in limits if limit.category == item.category), None)


def needs_cost(item: ItemFields, budget: Money | None, limits: Sequence[SpendingLimit]) -> bool:
    return budget is not None or limit_for(item, limits) is not None


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def cost_error(item: ItemFields, budget: Money | None) -> str | None:
    """Why `item`'s cost can't be counted (missing, or in another currency), or None."""
    if item.cost is None:
        return f"{item.title} has no cost"
    if budget is not None and item.cost.currency != budget.currency:
        return (
            f"{item.title} costs {format_money(item.cost)}, but the budget is in {budget.currency}"
        )
    return None


def limit_error(
    item: ItemFields, limits: Sequence[SpendingLimit], travelers: Travelers | None
) -> str | None:
    """Why `item` breaks its category's per-person limit, or None."""
    limit = limit_for(item, limits)
    if limit is None or item.cost is None:
        return None
    cap = limit.max_per_person
    category = limit.category
    if item.cost.currency != cap.currency:
        return (
            f"{item.title} costs {format_money(item.cost)}, but the {category} limit is in "
            f"{cap.currency}"
        )
    size = party_size(travelers)
    if item.cost.amount <= cap.amount * size:
        return None
    per_person = Money(amount=item.cost.amount / size, currency=cap.currency)
    return (
        f"{item.title} costs {format_money(item.cost)} ({format_money(per_person)} a person "
        f"for {size}), over the {category} limit of {format_money(cap)} a person"
    )


def total_error(budget: Money | None, costs: Iterable[Cost | None]) -> str | None:
    """Why `costs` don't fit `budget`, or None. Costs in other currencies aren't counted
    (`cost_error` reports those)."""
    if budget is None:
        return None
    spent = totals_by_currency(costs).get(budget.currency)
    if spent is None or spent <= budget.amount:
        return None
    total = Money(amount=spent, currency=budget.currency)
    over = Money(amount=spent - budget.amount, currency=budget.currency)
    return (
        f"costs total {format_money(total)}, {format_money(over)} over the "
        f"{format_money(budget)} budget"
    )
