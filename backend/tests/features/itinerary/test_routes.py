from features.itinerary.routes import departure_problem, overlap_problem, route_problem
from features.itinerary.schemas import FlightItem
from shared.domain import Place
from tests.features.itinerary.factories import JFK, LA_TZ, NRT, SFO, TOKYO_TZ, flight, transfer

HND = Place(name="Haneda Airport", iata="HND", city="Tokyo")
KIX = Place(name="Kansai International Airport", iata="KIX", city="Osaka")

# SFO 11:00 May 1 → NRT 14:30 May 2, and the flight home a week later.
OUT = flight("2027-05-01T11:00", "2027-05-02T14:30", title="Out")
HOME = flight(
    "2027-05-08T17:00",
    "2027-05-08T10:00",
    from_place=NRT,
    to_place=SFO,
    timezone=TOKYO_TZ,
    end_timezone=LA_TZ,
    title="Home",
)


def _tokyo_flight(
    start: str, end: str, from_place: Place, to_place: Place, title: str
) -> FlightItem:
    return flight(
        start,
        end,
        from_place=from_place,
        to_place=to_place,
        timezone=TOKYO_TZ,
        end_timezone=TOKYO_TZ,
        title=title,
    )


# ---------------------------------------------------------------------------
# Overlaps
# ---------------------------------------------------------------------------


def test_flights_in_the_air_together_overlap() -> None:
    cheaper = flight("2027-05-01T13:00", "2027-05-02T16:00", title="Cheaper")

    problem = overlap_problem(cheaper, [OUT, HOME])

    assert problem is not None and problem.other is OUT
    assert problem.message == "Cheaper overlaps the flight Out"


def test_back_to_back_flights_and_cancelled_ones_do_not_overlap() -> None:
    onward = _tokyo_flight("2027-05-02T14:30", "2027-05-02T16:00", NRT, KIX, "Onward")
    cancelled = flight("2027-05-01T11:00", "2027-05-02T14:30", status="cancelled")

    assert overlap_problem(onward, [OUT]) is None
    assert overlap_problem(OUT, [cancelled]) is None


# ---------------------------------------------------------------------------
# Departures
# ---------------------------------------------------------------------------


def test_a_flight_must_leave_from_where_the_last_one_landed() -> None:
    from_jfk = flight(
        "2027-05-08T17:00",
        "2027-05-08T19:00",
        from_place=JFK,
        to_place=SFO,
        end_timezone=LA_TZ,
        title="Wrong",
    )

    problem = departure_problem(from_jfk, [OUT])

    assert problem is not None and problem.other is OUT
    assert problem.message == "Wrong leaves from JFK, but after Out the traveller is at NRT"


def test_airports_of_the_same_city_count_as_one() -> None:
    from_haneda = _tokyo_flight("2027-05-04T09:00", "2027-05-04T10:30", HND, KIX, "Haneda")

    assert departure_problem(from_haneda, [OUT]) is None


def test_a_ground_transfer_between_flights_can_move_the_traveller() -> None:
    train = transfer("2027-05-03T09:00", "2027-05-03T12:00", from_place=NRT, title="Shinkansen")
    from_osaka = _tokyo_flight("2027-05-05T09:00", "2027-05-05T10:30", KIX, HND, "Osaka")

    assert departure_problem(from_osaka, [OUT]) is not None
    assert departure_problem(from_osaka, [OUT, train]) is None


def test_the_first_flight_may_leave_from_anywhere() -> None:
    assert departure_problem(OUT, [HOME]) is None


# ---------------------------------------------------------------------------
# route_problem (a new or changed flight)
# ---------------------------------------------------------------------------


def test_a_duplicate_flight_out_strands_the_planned_one() -> None:
    # The reported bug: a cheaper SFO → NRT added the day before the planned one, which
    # then leaves SFO while the traveller is already in Tokyo.
    cheaper = flight("2027-04-30T11:00", "2027-05-01T14:30", title="Cheaper")

    problem = route_problem(cheaper, [OUT, HOME])

    assert problem is not None and problem.other is OUT
    assert problem.message == "Out leaves from SFO, but after Cheaper the traveller is at NRT"


def test_a_consistent_round_trip_has_no_problem() -> None:
    assert route_problem(HOME, [OUT]) is None
    assert route_problem(OUT, [HOME]) is None
