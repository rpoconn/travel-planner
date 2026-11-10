"""Deterministic `HotelProvider` for tests and `HOTEL_PROVIDER=fake` (offline dev / E2E).

Without scripted replies it makes up obviously fake but well-formed places to stay for the
query ("Example ..."), two per star class asked for (or three classes when any will do),
so the lodging search runs with no network access or API key.
"""

from collections import deque
from collections.abc import Iterable
from datetime import time
from decimal import Decimal

from shared.domain import Money
from shared.hotels.types import HotelOption, HotelQuery, HotelSearchError

FakeHotelReply = list[HotelOption] | HotelSearchError

# (name, price per night for the party, rating) of two hotels in each star class.
_HOTELS: dict[int, tuple[tuple[str, int, float], ...]] = {
    2: (("Example Budget Inn", 90, 4.1), ("Example Station Inn", 75, 3.9)),
    3: (("Example Midtown Hotel", 160, 4.3), ("Example Riverside Hotel", 140, 4.5)),
    4: (("Example Grand Hotel", 280, 4.6), ("Example Harbour Hotel", 250, 4.4)),
    5: (("Example Palace", 520, 4.8), ("Example Royal", 450, 4.7)),
}
_RENTALS = (("Example Old Town Apartment", 140), ("Example Riverside Loft", 210))


def _rate(amount: int, query: HotelQuery) -> tuple[Money, Money]:
    nightly = Money(amount=Decimal(amount), currency=query.currency)
    total = Money(amount=Decimal(amount * query.nights), currency=query.currency)
    return nightly, total


def canned_hotels(query: HotelQuery) -> list[HotelOption]:
    """Two hotels per star class asked for (2-4 stars when any), or two rentals."""
    if query.vacation_rentals:
        options: list[HotelOption] = []
        for name, price in _RENTALS:
            nightly, total = _rate(price, query)
            options.append(
                HotelOption(
                    name=name,
                    kind="vacation_rental",
                    address=query.location,
                    check_in_time=time(16, 0),
                    check_out_time=time(10, 0),
                    nightly_rate=nightly,
                    total_rate=total,
                    rating=4.6,
                    reviews=87,
                    amenities=["Kitchen", "Wi-Fi"],
                )
            )
        return options
    hotels: list[HotelOption] = []
    for stars in query.stars or (2, 3, 4):
        for name, price, rating in _HOTELS[stars]:
            nightly, total = _rate(price, query)
            hotels.append(
                HotelOption(
                    name=name,
                    address=query.location,
                    check_in_time=time(15, 0),
                    check_out_time=time(11, 0),
                    nightly_rate=nightly,
                    total_rate=total,
                    stars=stars,
                    rating=rating,
                    reviews=100 * stars,
                    amenities=["Free Wi-Fi", "Breakfast"],
                )
            )
    return hotels


class FakeHotelProvider:
    def __init__(self, replies: Iterable[FakeHotelReply] = ()) -> None:
        self._replies: deque[FakeHotelReply] = deque(replies)
        self.searches: list[HotelQuery] = []

    def queue(self, *replies: FakeHotelReply) -> None:
        self._replies.extend(replies)

    async def search(self, query: HotelQuery) -> list[HotelOption]:
        self.searches.append(query)
        if not self._replies:
            return canned_hotels(query)
        reply = self._replies.popleft()
        if isinstance(reply, HotelSearchError):
            raise reply
        return reply

    async def aclose(self) -> None:
        return None
