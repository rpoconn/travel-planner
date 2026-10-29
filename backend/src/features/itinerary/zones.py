"""Where the traveller is: the timezone an item should use, worked out from the trip's legs.

Every item carries its own IANA timezone and its times are local to it, which is only right
if that zone is where the traveller actually is then. A model planning a Tokyo dinner can
easily send the home timezone, and the dinner then lands at the wrong hour (19:00 in New
York is 08:00 the next morning in Tokyo). Flights and transfers are what move the
traveller, so the zone at any instant is the arrival zone of the last leg that landed
before it, or the first leg's departure zone before the trip gets going.

Pure functions over validated schemas, like the budget rules: the agent's tools reject a
change in the wrong zone, and the timeline warns about items already in one.
"""

from collections.abc import Iterable, Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from features.itinerary.schemas import FlightFields, ItemFields, TransferFields

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_leg(item: ItemFields) -> bool:
    return isinstance(item, FlightFields | TransferFields) and item.status != "cancelled"


def arrival_zone(leg: ItemFields) -> str:
    return leg.end_timezone or leg.timezone


def legs[Item: ItemFields](items: Iterable[Item]) -> list[Item]:
    """The active flights and transfers, in the order they land."""
    return sorted((item for item in items if _is_leg(item)), key=lambda leg: leg.end)


def zone_at(instant: datetime, ordered_legs: Sequence[ItemFields]) -> str | None:
    """The zone the traveller is in at `instant`, or None when there are no legs to go by.
    `ordered_legs` comes from `legs`."""
    if not ordered_legs:
        return None
    landed = [leg for leg in ordered_legs if leg.end <= instant]
    return arrival_zone(landed[-1]) if landed else ordered_legs[0].timezone


def same_zone(a: str, b: str, at: datetime) -> bool:
    """Whether two zones show the same local time at `at`. Compared by UTC offset, so
    aliases ("Japan" for "Asia/Tokyo") and neighbours sharing a clock both pass."""
    return at.astimezone(ZoneInfo(a)).utcoffset() == at.astimezone(ZoneInfo(b)).utcoffset()


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def zone_error(item: ItemFields, others: Sequence[ItemFields]) -> tuple[str, str] | None:
    """Why `item` isn't in the zone the traveller is in when it starts, as (problem, the
    expected zone), or None. `others` is the rest of the plan, without `item` itself."""
    # A flight's zones are its airports', which say where it goes rather than follow it.
    if isinstance(item, FlightFields) or item.status == "cancelled":
        return None
    expected = zone_at(item.start, legs(others))
    if expected is None or same_zone(item.timezone, expected, item.start):
        return None
    return (
        f"{item.title} is in {item.timezone} time, but the traveller is in {expected} then",
        expected,
    )
