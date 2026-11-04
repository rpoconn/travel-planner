"""Placing activities in the trip's free windows: which day, which window, and what rules a
window out (flights, other plans, opening hours). Pure: no database."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from features.planning.scheduler import Request, is_open, schedule
from shared.places.types import OpeningHours
from tests.features.itinerary.factories import TOKYO_TZ, activity
from tests.features.plans.builders import outbound_flight, return_flight, snapshot

TOKYO = ZoneInfo(TOKYO_TZ)
# The builders' trip: May 1-4 2027, in Tokyo.


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2027, 5, day, hour, minute, tzinfo=TOKYO)


def _every_day(opens: time, closes: time, *, but: int | None = None) -> list[OpeningHours]:
    return [OpeningHours(weekday=d, opens=opens, closes=closes) for d in range(7) if d != but]


# ---------------------------------------------------------------------------
# Days and windows
# ---------------------------------------------------------------------------


def test_activities_spread_over_the_days() -> None:
    plan = snapshot()
    requests = [Request("a", "museum"), Request("b", "museum"), Request("c", "food")]

    slots = schedule(plan, requests, TOKYO_TZ)

    assert slots["a"].start == _at(1, 10)
    assert slots["a"].end == _at(1, 12)
    assert slots["b"].start == _at(2, 10)
    # Restaurants take the dinner window.
    assert (slots["c"].start, slots["c"].end) == (_at(3, 19, 30), _at(3, 21))
    assert slots["c"].zone == TOKYO_TZ


def test_flights_keep_activities_to_the_time_at_the_destination() -> None:
    # Out: lands May 2 14:30. Home: leaves May 4 17:00. Only May 3 has no flight.
    plan = snapshot([outbound_flight(), return_flight()])
    requests = [Request(key, "museum") for key in "abc"]

    slots = schedule(plan, requests, TOKYO_TZ)

    assert slots["a"].start == _at(3, 10)
    assert slots["b"].start == _at(3, 14, 30)
    # Then a travel day: May 4's morning ends before the airport run (three hours before
    # the 17:00 flight); May 2's are before the landing.
    assert slots["c"].start == _at(4, 10)


def test_a_window_next_to_other_plans_is_skipped() -> None:
    lunch_cruise = activity("2027-05-01T12:00", "2027-05-01T14:30", title="Lunch cruise")
    plan = snapshot([lunch_cruise])

    slots = schedule(plan, [Request("a", "museum")], TOKYO_TZ)

    # May 1 has an activity already, so May 2 goes first.
    assert slots["a"].start == _at(2, 10)
    alone = schedule(snapshot([lunch_cruise]), [Request("a", "food")], TOKYO_TZ)
    assert alone["a"].start == _at(2, 19, 30)


def test_buffers_keep_room_for_a_transfer() -> None:
    # Ends at 14:15, so the 14:30 window is too close; 10:00-12:00 ends before 12:30 - 30m.
    early = activity("2027-05-01T12:30", "2027-05-01T14:15", title="Tea ceremony")
    plan = snapshot([early], start_date=date(2027, 5, 1), end_date=date(2027, 5, 1))

    slots = schedule(plan, [Request("a", "museum"), Request("b", "shopping")], TOKYO_TZ)

    assert slots["a"].start == _at(1, 10)
    assert "b" not in slots


def test_closed_days_are_skipped() -> None:
    # May 1 2027 is a Saturday (weekday 5).
    hours = _every_day(time(9), time(17), but=5)

    slots = schedule(snapshot(), [Request("a", "museum", hours)], TOKYO_TZ)

    assert slots["a"].start == _at(2, 10)


def test_nothing_is_placed_without_trip_dates() -> None:
    undated = snapshot(start_date=None, end_date=None)

    assert schedule(undated, [Request("a", "food")], TOKYO_TZ) == {}


# ---------------------------------------------------------------------------
# Opening hours
# ---------------------------------------------------------------------------


def test_opening_hours() -> None:
    saturday = _every_day(time(18), time(2))  # a bar, open past midnight

    assert is_open(None, _at(1, 3), _at(1, 5))
    assert is_open(saturday, _at(1, 21, 30), _at(1, 23, 30))
    assert not is_open(saturday, _at(1, 10), _at(1, 12))
    all_day = _every_day(time(0), time(0))
    assert is_open(all_day, _at(1, 10), _at(1, 12))
    lunch_only = [OpeningHours(weekday=5, opens=time(12), closes=time(15))]
    assert not is_open(lunch_only, _at(1, 14), _at(1, 16))
