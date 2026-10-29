from decimal import Decimal

from shared.costs import budget_summary, format_money, totals_by_currency
from shared.domain import Money

BUDGET = Money(amount=Decimal(5000))


def test_totals_are_summed_per_currency_and_skip_missing_costs() -> None:
    costs = [
        Money(amount=Decimal("138.66")),
        None,
        Money(amount=Decimal("252.12")),
        Money(amount=Decimal(32000), currency="JPY"),
    ]

    assert totals_by_currency(costs) == {"USD": Decimal("390.78"), "JPY": Decimal(32000)}


def test_budget_summary_shows_what_is_left() -> None:
    summary = budget_summary(BUDGET, {"USD": Decimal("635.78")})

    assert summary == "Costs so far: 635.78 USD of the 5,000.00 USD budget (4,364.22 USD left)"


def test_budget_summary_shows_an_overrun() -> None:
    summary = budget_summary(BUDGET, {"USD": Decimal(5240)})

    assert summary is not None and "(240.00 USD over)" in summary


def test_budget_summary_lists_other_currencies_without_converting_them() -> None:
    summary = budget_summary(BUDGET, {"USD": Decimal(100), "JPY": Decimal(32000)})

    assert summary is not None
    assert summary.startswith("Costs so far: 100.00 USD of the 5,000.00 USD budget")
    assert summary.endswith("not counted, in other currencies: 32,000.00 JPY")


def test_budget_summary_without_a_budget() -> None:
    assert budget_summary(None, {}) is None
    assert budget_summary(None, {"USD": Decimal(15)}) == "Costs so far: 15.00 USD (no budget set)"


def test_format_money() -> None:
    assert format_money(Money(amount=Decimal("1380.5"), currency="EUR")) == "1,380.50 EUR"
