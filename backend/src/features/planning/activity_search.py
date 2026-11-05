"""Place searches for the activities stage: one per interest the user picked.

Each interest becomes a map search near the destination ("museums in Lisbon, Portugal";
restaurants carry the dietary needs). The searches run concurrently; one that fails is
logged and the others still count. What comes back is ranked by rules, scheduled into the
trip's free windows (`planning.scheduler`), and given the `add_activity` change picking it
makes. No model is involved.

Ranking: candidates that fit (well rated, within the category's spending limit and the
budget left) first; among them the interests take turns, so every interest is near the
top; each interest's places go best rated first. Misfits stay listed with a warning saying
why. A place the scheduler finds no free window for (closed whenever the trip is free) is
left out.

Prices are estimates: a place's "$" to "$$$$" level, or a typical price for its kind,
per person, times the party.
"""

import asyncio
import logging
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from pydantic import JsonValue

from features.itinerary.budget import party_size
from features.itinerary.schemas import ActivityCategory, active
from features.planning.lodging_search import destination_zone
from features.planning.scheduler import Request, Slot, schedule
from features.planning.schemas import Candidate, CandidateChange, RunSearch
from features.planning.search_types import SearchContext, SearchFailedError, unseen
from features.plans.schemas import ActivityPreferences, PlanSnapshot, active_budget
from shared.costs import format_money, totals_by_currency
from shared.domain import Money, Place
from shared.places.types import PlaceOption, PlaceQuery, PlaceSearchError

logger = logging.getLogger(__name__)

# How many activities an offer lists (the slot takes up to 8).
TOP_CANDIDATES = 10
# Places rated below this are listed with a warning.
MIN_RATING = 4.0

# What each interest searches the map for.
INTEREST_SEARCHES: dict[ActivityCategory, str] = {
    "sightseeing": "tourist attractions",
    "food": "restaurants",
    "tour": "guided tours",
    "museum": "museums",
    "outdoors": "parks",
    "nightlife": "bars",
    "shopping": "shopping",
    "relax": "spas",
    "other": "things to do",
}

# Per person, in the plan's currency: by the listing's price level, else by kind of place.
PRICE_LEVEL_ESTIMATES: dict[int, int] = {1: 15, 2: 35, 3: 70, 4: 120}
CATEGORY_ESTIMATES: dict[ActivityCategory, int] = {
    "sightseeing": 15,
    "food": 35,
    "tour": 40,
    "museum": 15,
    "outdoors": 0,
    "nightlife": 30,
    "shopping": 0,
    "relax": 60,
    "other": 20,
}

_MAX_TITLE = 200
_MAX_ADDRESS = 500
_MAX_URL = 2000
_MAX_WHAT = 100


@dataclass(frozen=True)
class _Found:
    """A place one interest's search found, with what picking it would cost."""

    key: str  # unique within the offer
    category: ActivityCategory
    option: PlaceOption
    cost: Money
    warnings: list[str]


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def _preferences(plan: PlanSnapshot) -> ActivityPreferences:
    prefs = plan.preferences.activities
    if prefs is None:
        # The engine asks for the preferences before it offers a search.
        raise SearchFailedError("Tell me what you like to do first.")
    return prefs


def _destination(plan: PlanSnapshot) -> Place:
    if plan.destination is None:
        raise SearchFailedError("The trip needs a destination first.")
    return plan.destination


def place_query(plan: PlanSnapshot, interest: ActivityCategory) -> PlaceQuery:
    """The search for one interest: "vegetarian restaurants" near "Lisbon, Portugal"."""
    prefs = _preferences(plan)
    destination = _destination(plan)
    what = INTEREST_SEARCHES[interest]
    if interest == "food" and prefs.dietary.strip():
        what = f"{prefs.dietary.strip()} {what}"
    # dict.fromkeys drops repeats ("Lisbon, Lisbon") and keeps the order.
    near = ", ".join(dict.fromkeys(p for p in (destination.name, destination.country) if p))
    return PlaceQuery(what=what[:_MAX_WHAT], near=near, lat=destination.lat, lng=destination.lng)


async def _search_all(ctx: SearchContext, queries: Sequence[PlaceQuery]) -> list[list[PlaceOption]]:
    """Every query's places, searched at once. A failed search counts as nothing found,
    unless every one failed."""
    results = await asyncio.gather(
        *(ctx.places.search(query) for query in queries), return_exceptions=True
    )
    found: list[list[PlaceOption]] = []
    errors: list[PlaceSearchError] = []
    for query, result in zip(queries, results, strict=True):
        if isinstance(result, PlaceSearchError):
            logger.warning("Place search for %r failed: %s", query.what, result)
            errors.append(result)
            found.append([])
        elif isinstance(result, BaseException):
            raise result
        else:
            found.append(result)
    if errors and len(errors) == len(queries):
        raise SearchFailedError(str(errors[0]))
    return found


# ---------------------------------------------------------------------------
# Prices and fit
# ---------------------------------------------------------------------------


def _currency(plan: PlanSnapshot) -> str:
    budget = active_budget(plan.budget)
    return budget.currency if budget is not None else "USD"


def estimate(option: PlaceOption, category: ActivityCategory, plan: PlanSnapshot) -> Money:
    """What going costs the whole party, roughly."""
    level = option.price_level
    per_person = PRICE_LEVEL_ESTIMATES[level] if level else CATEGORY_ESTIMATES[category]
    amount = Decimal(per_person * party_size(plan.travelers))
    return Money(amount=amount, currency=_currency(plan))


def budget_left(plan: PlanSnapshot) -> Money | None:
    """The budget less what the plan's items already cost, or None without a budget."""
    budget = active_budget(plan.budget)
    if budget is None:
        return None
    costs = [item.cost for item in active(plan.items)]
    spent = totals_by_currency(costs).get(budget.currency, Decimal(0))
    return Money(amount=budget.amount - spent, currency=budget.currency)


def misses(
    option: PlaceOption, category: ActivityCategory, cost: Money, plan: PlanSnapshot
) -> list[str]:
    """How `option` falls short, one phrase each; empty when it fits."""
    found: list[str] = []
    if option.rating is not None and option.rating < MIN_RATING:
        found.append(f"rated {option.rating:.1f}")
    limit = next((limit for limit in plan.limits if limit.category == category), None)
    size = party_size(plan.travelers)
    if limit is not None and limit.max_per_person.currency == cost.currency:
        per_person = Money(amount=cost.amount / size, currency=cost.currency)
        if per_person.amount > limit.max_per_person.amount:
            found.append(
                f"about {format_money(per_person)} a person, over the {category} limit of "
                f"{format_money(limit.max_per_person)}"
            )
    left = budget_left(plan)
    if left is not None and left.currency == cost.currency and cost.amount > left.amount:
        found.append(f"about {format_money(cost)}, more than the budget has left")
    return found


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def _best_first(options: Sequence[PlaceOption]) -> list[PlaceOption]:
    return sorted(options, key=lambda o: (-(o.rating or 0.0), -(o.reviews or 0)))


def rank(
    plan: PlanSnapshot,
    interests: Sequence[ActivityCategory],
    results: Sequence[Sequence[PlaceOption]],
) -> list[_Found]:
    """Every place found, best first: those that fit, the interests taking turns."""
    # Each interest's places, best first; a turn takes the next one not listed yet. The
    # same place can turn up for two interests (a museum's café): it is listed once, and
    # the interest that found it second takes its next place instead.
    queues = [deque(_best_first(options)) for options in results]
    seen: set[str] = set()
    turns: list[_Found] = []
    while any(queues):
        for interest, queue in zip(interests, queues, strict=True):
            while queue and queue[0].name.strip().casefold() in seen:
                queue.popleft()
            if not queue:
                continue
            option = queue.popleft()
            seen.add(option.name.strip().casefold())
            cost = estimate(option, interest, plan)
            turns.append(
                _Found(
                    key=str(len(turns) + 1),
                    category=interest,
                    option=option,
                    cost=cost,
                    warnings=misses(option, interest, cost, plan),
                )
            )
    # Stable: within fits and misfits the turns keep their order.
    return sorted(turns, key=lambda found: bool(found.warnings))


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


def _when(slot: Slot) -> str:
    start, end = slot.start, slot.end
    return f"{start:%a} {start:%b} {start.day}, {start:%H:%M}-{end:%H:%M}"


def _place(option: PlaceOption, destination: Place) -> dict[str, JsonValue]:
    place = {
        "name": option.name[:_MAX_TITLE],
        "address": option.address[:_MAX_ADDRESS] if option.address else None,
        "city": destination.city or destination.name,
        "country": destination.country,
        "lat": option.lat,
        "lng": option.lng,
    }
    return {key: value for key, value in place.items() if value is not None}


def _subtitle(found: _Found) -> str:
    option = found.option
    parts = [option.kind or found.category.capitalize()]
    if option.rating is not None:
        reviews = f" ({option.reviews:,} reviews)" if option.reviews else ""
        parts.append(f"rated {option.rating:.1f}{reviews}")
    if option.price_level is not None:
        parts.append("$" * option.price_level)
    return " · ".join(parts)


def _price_detail(cost: Money, party: int) -> str:
    if cost.amount == 0:
        return "Usually free"
    people = "1 person" if party == 1 else f"{party} people"
    return f"About {format_money(cost)} for {people} (estimate)"


def _add_activity(found: _Found, slot: Slot, destination: Place) -> CandidateChange:
    option = found.option
    arguments: dict[str, JsonValue] = {
        "title": option.name[:_MAX_TITLE],
        # Local wall-clock times in `timezone`, as every item takes them.
        "start": slot.start.strftime("%Y-%m-%dT%H:%M:%S"),
        "end": slot.end.strftime("%Y-%m-%dT%H:%M:%S"),
        "timezone": slot.zone,
        "place": _place(option, destination),
        "category": found.category,
        "cost": {**found.cost.model_dump(mode="json"), "basis": "estimate"},
    }
    if found.category == "tour":
        arguments["booking_required"] = True
    if option.link and len(option.link) <= _MAX_URL:
        arguments["source_url"] = option.link
    summary = f"Add activity “{arguments['title']}” on {_when(slot)}"
    return CandidateChange(name="add_activity", arguments=arguments, summary=summary)


def _candidate(
    number: int, found: _Found, slot: Slot, plan: PlanSnapshot, destination: Place
) -> Candidate:
    option = found.option
    details = [_when(slot), _price_detail(found.cost, party_size(plan.travelers))]
    if option.address:
        details.append(option.address[:_MAX_ADDRESS])
    return Candidate(
        id=str(number),
        title=option.name[:_MAX_TITLE],
        subtitle=_subtitle(found),
        details=details,
        warnings=found.warnings,
        # An estimate, so it's in the details rather than shown as a price.
        price=None,
        source_url=option.link if option.link and len(option.link) <= _MAX_URL else None,
        changes=[_add_activity(found, slot, destination)],
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _zone(plan: PlanSnapshot) -> str:
    zone = destination_zone(plan)
    if zone is not None:
        return zone
    legs = [item for item in plan.items if item.type in ("flight", "transfer")]
    if legs:
        # The scheduler follows the legs; this is only where it starts looking.
        return legs[0].end_timezone or legs[0].timezone
    name = plan.destination.name if plan.destination else "the destination"
    raise SearchFailedError(
        f"I couldn't work out the local time in {name}. Go back to the basics and give the "
        "nearest city with an airport."
    )


async def activities(ctx: SearchContext, plan: PlanSnapshot, action: RunSearch) -> list[Candidate]:
    """Things to do for the plan's interests, each in its own free window, best first."""
    prefs = _preferences(plan)
    destination = _destination(plan)
    zone = _zone(plan)
    interests = list(dict.fromkeys(prefs.interests))
    results = await _search_all(ctx, [place_query(plan, interest) for interest in interests])
    # Places already offered ("More options") are left out before scheduling, so they
    # don't hold free windows the next best ones could use.
    ranked = [
        found
        for found in rank(plan, interests, results)
        if unseen(ctx, found.option.name[:_MAX_TITLE])
    ]
    requests = [Request(f.key, f.category, f.option.hours) for f in ranked]
    slots = schedule(plan, requests, zone)
    placed = [found for found in ranked if found.key in slots][:TOP_CANDIDATES]
    if ranked and not placed:
        raise SearchFailedError(
            "There's no free time left on the trip for these. Go back and free up a day, "
            "or pick other things to do."
        )
    return [
        _candidate(number, found, slots[found.key], plan, destination)
        for number, found in enumerate(placed, start=1)
    ]
