"""The whole-trip gap finder: every change of place, home to home, without a transfer."""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from features.itinerary.gaps import connecting_transfers, find_gaps
from features.itinerary.schemas import FlightItem, Gap, ItineraryItem
from tests.features.itinerary.factories import (
    HOME,
    LA_TZ,
    NRT,
    OKURA,
    SENSOJI,
    SFO,
    TOKYO_TZ,
    TSUKIJI,
    activity,
    flight,
    lodging,
    transfer,
)


def _pairs(gaps: list[Gap]) -> list[tuple[UUID | None, UUID | None]]:
    return [(gap.after_item_id, gap.before_item_id) for gap in gaps]


def _round_trip() -> tuple[FlightItem, FlightItem]:
    out = flight("2027-05-01T11:00", "2027-05-02T14:30")
    home = flight(
        "2027-05-08T17:00",
        "2027-05-08T10:00",
        from_place=NRT,
        to_place=SFO,
        timezone=TOKYO_TZ,
        end_timezone=LA_TZ,
    )
    return out, home


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------


def test_leaving_home_and_getting_back_are_gaps() -> None:
    out, back = _round_trip()

    gaps = find_gaps([out, back], HOME)

    # NRT → NRT between the two flights is the same airport, so no gap there.
    assert _pairs(gaps) == [(None, out.id), (back.id, None)]
    leave, arrive = gaps
    assert (leave.from_place, leave.to_place) == (HOME, SFO)
    assert leave.leave_after is None
    assert leave.arrive_before == out.start
    assert leave.timezone == LA_TZ
    assert (arrive.from_place, arrive.to_place) == (SFO, HOME)
    assert arrive.leave_after == back.end
    assert arrive.arrive_before is None
    assert arrive.timezone == LA_TZ


def test_a_transfer_at_each_end_closes_the_home_gaps() -> None:
    out, back = _round_trip()
    to_airport = transfer(
        "2027-05-01T08:00", "2027-05-01T09:00", from_place=HOME, to_place=SFO, timezone=LA_TZ
    )
    from_airport = transfer(
        "2027-05-08T11:00", "2027-05-08T12:00", from_place=SFO, to_place=HOME, timezone=LA_TZ
    )

    assert find_gaps([to_airport, out, back, from_airport], HOME) == []


def test_home_is_never_the_same_place_as_the_first_stop() -> None:
    # Even when it carries the same name and code, getting there takes a transfer.
    out, back = _round_trip()

    assert len(find_gaps([out, back], SFO)) == 2


def test_without_an_origin_there_are_no_home_gaps() -> None:
    out, back = _round_trip()

    assert find_gaps([out, back], None) == []


def test_a_trip_without_flights_still_needs_getting_there() -> None:
    stay = lodging("2027-05-02T15:00", "2027-05-04T11:00")

    gaps = find_gaps([stay], HOME)

    assert _pairs(gaps) == [(None, stay.id), (stay.id, None)]
    assert (gaps[0].from_place, gaps[0].to_place) == (HOME, OKURA)


def test_no_items_means_no_gaps() -> None:
    assert find_gaps([], HOME) == []


# ---------------------------------------------------------------------------
# Within the trip
# ---------------------------------------------------------------------------


def test_a_late_landing_is_compared_with_the_next_days_check_in() -> None:
    late = flight("2027-05-01T17:00", "2027-05-02T23:30")
    stay = lodging("2027-05-03T15:00", "2027-05-06T11:00")

    gaps = find_gaps([late, stay], None)

    assert _pairs(gaps) == [(late.id, stay.id)]
    assert (gaps[0].from_place, gaps[0].to_place) == (NRT, OKURA)
    assert gaps[0].leave_after == late.end
    assert gaps[0].arrive_before == stay.start


def test_an_outing_on_a_middle_day_is_a_gap_both_ways() -> None:
    stay = lodging("2027-05-02T15:00", "2027-05-06T11:00")
    temple = activity("2027-05-04T10:00", "2027-05-04T12:00", place=SENSOJI)

    gaps = find_gaps([stay, temple], None)

    assert _pairs(gaps) == [(stay.id, temple.id), (temple.id, stay.id)]
    # Leaving after the night before ends; due back by the next midnight.
    assert gaps[0].leave_after == datetime(2027, 5, 4, tzinfo=ZoneInfo(TOKYO_TZ))
    assert gaps[1].arrive_before == datetime(2027, 5, 5, tzinfo=ZoneInfo(TOKYO_TZ))


def test_transfers_around_an_outing_close_it() -> None:
    stay = lodging("2027-05-02T15:00", "2027-05-06T11:00")
    there = transfer("2027-05-04T09:30", "2027-05-04T10:00", from_place=OKURA, to_place=SENSOJI)
    temple = activity("2027-05-04T10:00", "2027-05-04T12:00", place=SENSOJI)
    back = transfer("2027-05-04T12:00", "2027-05-04T12:30", from_place=SENSOJI, to_place=OKURA)

    assert find_gaps([stay, there, temple, back], None) == []


def test_consecutive_stops_at_the_same_place_need_no_transfer() -> None:
    temple = activity("2027-05-03T09:00", "2027-05-03T10:00", place=SENSOJI)
    gardens = activity("2027-05-03T10:00", "2027-05-03T11:00", place=SENSOJI, title="Gardens")
    market = activity("2027-05-03T12:00", "2027-05-03T13:00", place=TSUKIJI)

    assert _pairs(find_gaps([temple, gardens, market], None)) == [(gardens.id, market.id)]


def test_cancelled_items_are_ignored() -> None:
    stay = lodging("2027-05-02T15:00", "2027-05-06T11:00")
    dropped = activity("2027-05-04T10:00", "2027-05-04T12:00", status="cancelled")

    assert find_gaps([stay, dropped], None) == []


# ---------------------------------------------------------------------------
# Arriving after check-in, leaving before check-out
# ---------------------------------------------------------------------------


def test_a_ride_to_the_hotel_after_a_late_landing_covers_the_arrival() -> None:
    # Lands 18:00, check-in opens at 15:00, the taxi from Narita gets there at 19:30.
    out = flight("2027-05-01T11:00", "2027-05-02T18:00")
    stay = lodging("2027-05-02T15:00", "2027-05-05T11:00")
    taxi = transfer("2027-05-02T18:30", "2027-05-02T19:30", from_place=NRT, to_place=OKURA)

    assert find_gaps([out, stay, taxi], None) == []


def test_a_late_landing_without_a_ride_is_still_a_gap() -> None:
    out = flight("2027-05-01T11:00", "2027-05-02T18:00")
    stay = lodging("2027-05-02T15:00", "2027-05-05T11:00")

    assert _pairs(find_gaps([out, stay], None)) == [(out.id, stay.id)]


def test_a_ride_from_home_arriving_after_check_in_closes_the_home_gap() -> None:
    # No flights: a car from home starts at check-in time and arrives two hours later.
    stay = lodging("2027-05-02T15:00", "2027-05-05T11:00")
    car = transfer("2027-05-02T15:00", "2027-05-02T17:00", from_place=HOME, to_place=OKURA)

    gaps = find_gaps([stay, car], HOME)

    assert _pairs(gaps) == [(stay.id, None)]  # only the way home is left


def test_leaving_the_hotel_before_check_out_covers_the_departure() -> None:
    stay = lodging("2027-05-02T15:00", "2027-05-05T11:00")
    taxi = transfer("2027-05-05T08:00", "2027-05-05T09:00", from_place=OKURA, to_place=NRT)
    home = flight(
        "2027-05-05T12:00",
        "2027-05-05T06:00",
        from_place=NRT,
        to_place=SFO,
        timezone=TOKYO_TZ,
        end_timezone=LA_TZ,
    )

    assert find_gaps([stay, taxi, home], None) == []


def test_a_ride_elsewhere_after_check_in_does_not_move_the_check_in() -> None:
    # Landed before check-in; the evening taxi goes to dinner, not to the hotel.
    out = flight("2027-05-01T11:00", "2027-05-02T14:30")
    stay = lodging("2027-05-02T15:00", "2027-05-05T11:00")
    taxi = transfer("2027-05-02T19:00", "2027-05-02T19:30", from_place=OKURA, to_place=TSUKIJI)

    assert _pairs(find_gaps([out, stay, taxi], None)) == [(out.id, stay.id)]


# ---------------------------------------------------------------------------
# Transfers that go with an item
# ---------------------------------------------------------------------------


def test_the_rides_to_and_from_an_outing_go_with_it() -> None:
    stay = lodging("2027-05-02T15:00", "2027-05-06T11:00")
    there = transfer("2027-05-04T09:30", "2027-05-04T10:00", from_place=OKURA, to_place=SENSOJI)
    temple = activity("2027-05-04T10:00", "2027-05-04T12:00", place=SENSOJI)
    back = transfer("2027-05-04T12:00", "2027-05-04T12:30", from_place=SENSOJI, to_place=OKURA)

    rides = connecting_transfers(temple.id, [stay, there, temple, back])

    assert [ride.id for ride in rides] == [there.id, back.id]


def test_a_stay_takes_only_its_check_in_and_check_out_rides() -> None:
    out = flight("2027-05-01T11:00", "2027-05-02T18:00")
    stay = lodging("2027-05-02T15:00", "2027-05-06T11:00")
    arrival = transfer("2027-05-02T18:30", "2027-05-02T19:30", from_place=NRT, to_place=OKURA)
    there = transfer("2027-05-04T09:30", "2027-05-04T10:00", from_place=OKURA, to_place=SENSOJI)
    temple = activity("2027-05-04T10:00", "2027-05-04T12:00", place=SENSOJI)
    back = transfer("2027-05-04T12:00", "2027-05-04T12:30", from_place=SENSOJI, to_place=OKURA)
    departure = transfer("2027-05-06T08:00", "2027-05-06T09:00", from_place=OKURA, to_place=NRT)
    items: list[ItineraryItem] = [out, stay, arrival, there, temple, back, departure]

    rides = connecting_transfers(stay.id, items)

    assert [ride.id for ride in rides] == [arrival.id, departure.id]


def test_a_ride_to_somewhere_else_stays() -> None:
    temple = activity("2027-05-04T10:00", "2027-05-04T12:00", place=SENSOJI)
    onward = transfer("2027-05-04T12:00", "2027-05-04T12:30", from_place=OKURA, to_place=TSUKIJI)

    assert connecting_transfers(temple.id, [temple, onward]) == []


def test_removing_a_transfer_takes_no_others() -> None:
    out = flight("2027-05-01T11:00", "2027-05-02T18:00")
    train = transfer("2027-05-02T18:30", "2027-05-02T19:30", from_place=NRT, to_place=SENSOJI)
    taxi = transfer("2027-05-02T19:30", "2027-05-02T19:45", from_place=SENSOJI, to_place=OKURA)

    assert connecting_transfers(train.id, [out, train, taxi]) == []
