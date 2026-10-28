from collections.abc import Sequence
from datetime import date, timedelta

from features.itinerary.schemas import ItineraryItem, Timeline, WarningKind
from features.itinerary.timeline import build_timeline, item_days
from tests.features.itinerary.factories import (
    JFK,
    LAX,
    NRT,
    OKURA,
    PLAN_ID,
    SENSOJI,
    SFO,
    SYD,
    TOKYO_TZ,
    TSUKIJI,
    activity,
    flight,
    lodging,
    transfer,
)


def _timeline(
    items: Sequence[ItineraryItem],
    start_date: date | None = date(2027, 5, 1),
    end_date: date | None = date(2027, 5, 8),
) -> Timeline:
    return build_timeline(
        plan_id=PLAN_ID, version=3, start_date=start_date, end_date=end_date, items=items
    )


def _warnings(timeline: Timeline, kind: WarningKind) -> list[date | None]:
    return [w.date for w in timeline.warnings if w.kind == kind]


def _titles_by_day(timeline: Timeline) -> list[list[str]]:
    return [[entry.item.title for entry in day.entries] for day in timeline.days]


# ---------------------------------------------------------------------------
# Day range
# ---------------------------------------------------------------------------


def test_plan_without_dates_or_items_has_no_days() -> None:
    timeline = _timeline([], start_date=None, end_date=None)

    assert timeline.days == []
    assert timeline.warnings == []
    assert timeline.version == 3


def test_days_cover_the_plan_dates() -> None:
    timeline = _timeline([], end_date=date(2027, 5, 3))

    assert [day.date for day in timeline.days] == [
        date(2027, 5, 1),
        date(2027, 5, 2),
        date(2027, 5, 3),
    ]
    assert [day.index for day in timeline.days] == [0, 1, 2]


def test_days_come_from_items_when_the_plan_has_no_dates() -> None:
    timeline = _timeline(
        [
            activity("2027-06-10T09:00", "2027-06-10T11:00"),
            activity("2027-06-12T09:00", "2027-06-12T11:00"),
        ],
        start_date=None,
        end_date=None,
    )

    assert [day.date for day in timeline.days] == [
        date(2027, 6, 10),
        date(2027, 6, 11),
        date(2027, 6, 12),
    ]
    assert _warnings(timeline, "outside_trip") == []


def test_items_outside_the_plan_dates_widen_the_range_and_warn() -> None:
    early = activity("2027-04-30T09:00", "2027-04-30T11:00", title="Early bird")

    timeline = _timeline([early], end_date=date(2027, 5, 2))

    assert timeline.days[0].date == date(2027, 4, 30)
    assert _titles_by_day(timeline) == [["Early bird"], [], []]
    assert _warnings(timeline, "outside_trip") == [date(2027, 4, 30)]


# ---------------------------------------------------------------------------
# Cards across midnight and timezones
# ---------------------------------------------------------------------------


def test_overnight_flight_appears_on_both_days() -> None:
    red_eye = flight(
        "2027-05-01T23:00",
        "2027-05-02T07:30",
        from_place=SFO,
        to_place=JFK,
        end_timezone="America/New_York",
    )

    timeline = _timeline([red_eye], end_date=date(2027, 5, 2))

    day_one, day_two = (day.entries for day in timeline.days)
    assert [(e.continues_from_previous_day, e.continues_to_next_day) for e in day_one] == [
        (False, True)
    ]
    assert [(e.continues_from_previous_day, e.continues_to_next_day) for e in day_two] == [
        (True, False)
    ]
    # The night is spent on the plane, so it doesn't need lodging.
    assert _warnings(timeline, "no_lodging") == []


def test_westbound_dateline_flight_spans_three_local_days() -> None:
    # 22:00 PDT May 1 + 15h in the air = 06:00 AEST May 3.
    long_haul = flight(
        "2027-05-01T22:00",
        "2027-05-03T06:00",
        from_place=LAX,
        to_place=SYD,
        end_timezone="Australia/Sydney",
    )

    timeline = _timeline([long_haul], end_date=date(2027, 5, 3))

    assert long_haul.end - long_haul.start == timedelta(hours=15)
    assert long_haul.start.utcoffset() == timedelta(hours=-7)
    assert long_haul.end.utcoffset() == timedelta(hours=10)
    assert _titles_by_day(timeline) == [["LAX → SYD"]] * 3
    middle = timeline.days[1].entries[0]
    assert middle.continues_from_previous_day and middle.continues_to_next_day


def test_eastbound_dateline_flight_stays_on_its_departure_day() -> None:
    # Departs 01:00 JST May 9 and lands 19:00 PDT May 8: an "earlier" local date.
    home = flight(
        "2027-05-09T01:00",
        "2027-05-08T19:00",
        from_place=NRT,
        to_place=SFO,
        timezone=TOKYO_TZ,
        end_timezone="America/Los_Angeles",
    )

    assert item_days(home) == (date(2027, 5, 9), date(2027, 5, 9))


def test_item_ending_at_midnight_stays_on_its_start_day() -> None:
    late = activity("2027-05-01T21:00", "2027-05-02T00:00", title="Golden Gai")

    timeline = _timeline([late], end_date=date(2027, 5, 2))

    assert _titles_by_day(timeline) == [["Golden Gai"], []]
    assert timeline.days[0].entries[0].continues_to_next_day is False


def test_entries_are_sorted_by_actual_start_time() -> None:
    lunch = activity("2027-05-03T12:00", "2027-05-03T13:00", place=TSUKIJI)
    morning = activity("2027-05-03T09:00", "2027-05-03T11:00", place=SENSOJI)

    timeline = _timeline([lunch, morning], start_date=date(2027, 5, 3), end_date=date(2027, 5, 3))

    assert _titles_by_day(timeline) == [["Senso-ji", "Tsukiji Outer Market"]]


# ---------------------------------------------------------------------------
# Lodging spans and gaps
# ---------------------------------------------------------------------------


def test_multi_day_lodging_is_one_span_not_daily_cards() -> None:
    stay = lodging("2027-05-02T15:00", "2027-05-06T11:00")

    timeline = _timeline([stay])

    assert [(s.item.id, s.start_day_index, s.end_day_index) for s in timeline.lodging] == [
        (stay.id, 1, 5)
    ]
    assert all(day.entries == [] for day in timeline.days)
    # Nights of May 2-5 are covered; May 8 is the last day and needs no night.
    assert _warnings(timeline, "no_lodging") == [
        date(2027, 5, 1),
        date(2027, 5, 6),
        date(2027, 5, 7),
    ]


def test_cancelled_items_are_shown_but_ignored_by_warnings() -> None:
    cancelled = lodging("2027-05-01T15:00", "2027-05-03T11:00", status="cancelled")
    clash = activity("2027-05-01T15:00", "2027-05-01T16:00", status="cancelled")
    museum = activity("2027-05-01T15:30", "2027-05-01T17:00", place=TSUKIJI)

    timeline = _timeline([cancelled, clash, museum], end_date=date(2027, 5, 3))

    assert len(timeline.lodging) == 1
    assert len(timeline.days[0].entries) == 2
    assert _warnings(timeline, "no_lodging") == [date(2027, 5, 1), date(2027, 5, 2)]
    assert _warnings(timeline, "overlap") == []


# ---------------------------------------------------------------------------
# Overlaps and transfers
# ---------------------------------------------------------------------------


def test_overlapping_items_warn_but_back_to_back_items_do_not() -> None:
    first = activity("2027-05-03T09:00", "2027-05-03T11:00", place=SENSOJI)
    back_to_back = activity("2027-05-03T11:00", "2027-05-03T12:00", place=SENSOJI, title="Tea")
    clash = activity("2027-05-03T10:30", "2027-05-03T11:30", place=SENSOJI, title="Clash")

    timeline = _timeline([first, back_to_back, clash])
    overlaps = [w for w in timeline.warnings if w.kind == "overlap"]

    assert [set(w.item_ids) for w in overlaps] == [
        {first.id, clash.id},
        {clash.id, back_to_back.id},
    ]


def test_double_booked_lodging_warns() -> None:
    a = lodging("2027-05-01T15:00", "2027-05-04T11:00")
    b = lodging("2027-05-03T15:00", "2027-05-05T11:00", title="Park Hyatt")

    assert _warnings(_timeline([a, b]), "overlap") == [date(2027, 5, 3)]


def test_missing_transfer_from_airport_to_hotel() -> None:
    arrive = flight("2027-05-01T11:00", "2027-05-02T14:30")
    stay = lodging("2027-05-02T17:00", "2027-05-06T11:00")

    timeline = _timeline([arrive, stay])
    missing = [w for w in timeline.warnings if w.kind == "missing_transfer"]

    assert [(w.date, w.item_ids, w.message) for w in missing] == [
        (
            date(2027, 5, 2),
            [arrive.id, stay.id],
            "No transfer from Narita International Airport to Hotel Okura",
        )
    ]


def test_a_transfer_between_places_clears_the_warning() -> None:
    arrive = flight("2027-05-01T11:00", "2027-05-02T14:30")
    train = transfer("2027-05-02T15:00", "2027-05-02T16:30", from_place=NRT, to_place=OKURA)
    stay = lodging("2027-05-02T17:00", "2027-05-06T11:00")

    assert _warnings(_timeline([arrive, train, stay]), "missing_transfer") == []


def test_consecutive_items_at_the_same_place_need_no_transfer() -> None:
    temple = activity("2027-05-03T09:00", "2027-05-03T10:00", place=SENSOJI)
    gardens = activity("2027-05-03T10:00", "2027-05-03T11:00", place=SENSOJI, title="Gardens")
    market = activity("2027-05-03T12:00", "2027-05-03T13:00", place=TSUKIJI)

    timeline = _timeline([temple, gardens, market])
    missing = [w for w in timeline.warnings if w.kind == "missing_transfer"]

    assert [w.item_ids for w in missing] == [[gardens.id, market.id]]
