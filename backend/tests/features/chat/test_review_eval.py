"""Catch rate of the proposal reviewer on a real model: `uv run pytest -m ollama
tests/features/chat/test_review_eval.py -s`.

Calls `review.check_changes` directly (no DB, no agent) on hand-written cases: the two
failures from the qwen3:8b smoke test, more proposals that don't fit what was asked or the
stage, and proposals that do (bundles and a revise turn included). A small model's
verdicts vary from run to run, so only the aggregates are asserted; the misses are printed
so a regression can be read off the output.

Scored per change, not per case: a bundle with one wrong change in it counts one catch.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from features.chat.prompts import revise_request_message
from features.chat.review import check_changes
from features.chat.schemas import ProposedChange
from features.plans.schemas import PlanStage
from shared.config import get_settings
from shared.llm.ollama_client import OllamaClient

pytestmark = pytest.mark.ollama

logger = logging.getLogger(__name__)

# The bar from the plan: below it, consider gating write tools by request instead.
MIN_CATCH_RATE = 0.8
MAX_FALSE_REJECTION_RATE = 0.1


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    """One proposed change and whether it should get through."""

    name: str
    summary: str
    arguments: dict[str, Any]
    fits: bool = True


@dataclass(frozen=True)
class Case:
    label: str
    stage: PlanStage
    latest: str
    changes: tuple[Change, ...]
    # The assistant reply `latest` answers, when it picks from a list in it.
    previous_reply: str | None = None

    def proposed(self) -> list[ProposedChange]:
        # Bundles share a base call id ("call_1", "call_1-2"), as the tools stage them.
        return [
            ProposedChange(
                call_id="call_1" if index == 0 else f"call_1-{index + 1}",
                name=change.name,
                arguments=change.arguments,
                summary=change.summary,
            )
            for index, change in enumerate(self.changes)
        ]


def _place(name: str, **extra: str) -> dict[str, str]:
    return {"name": name, **extra}


def _lodging(title: str, start: str, end: str, *, kind: str = "hotel", fits: bool = True) -> Change:
    return Change(
        name="add_lodging",
        summary=f"Add lodging “{title}” on {start[:10]} {start[11:16]}",
        arguments={
            "title": title,
            "place": _place(title),
            "kind": kind,
            "start": f"{start}+01:00",
            "end": f"{end}+01:00",
        },
        fits=fits,
    )


def _activity(
    title: str, start: str, end: str, *, category: str, place: str = "", fits: bool = True
) -> Change:
    return Change(
        name="add_activity",
        summary=f"Add activity “{title}” on {start[:10]} {start[11:16]}",
        arguments={
            "title": title,
            "place": _place(place or title),
            "category": category,
            "start": f"{start}+01:00",
            "end": f"{end}+01:00",
        },
        fits=fits,
    )


def _transfer(
    title: str, start: str, end: str, *, mode: str, src: str, dst: str, fits: bool = True
) -> Change:
    return Change(
        name="add_transfer",
        summary=f"Add transfer “{title}” on {start[:10]} {start[11:16]}",
        arguments={
            "title": title,
            "mode": mode,
            "from_place": _place(src),
            "to_place": _place(dst),
            "start": f"{start}+01:00",
            "end": f"{end}+01:00",
        },
        fits=fits,
    )


def _flight(
    title: str,
    start: str,
    end: str,
    *,
    direction: str,
    src: str,
    dst: str,
    offsets: tuple[str, str] = ("-04:00", "+01:00"),
    fits: bool = True,
) -> Change:
    return Change(
        name="add_flight",
        summary=f"Add flight “{title}” on {start[:10]} {start[11:16]}",
        arguments={
            "title": title,
            "carrier": "TAP Air Portugal",
            "direction": direction,
            "from_place": _place(src, iata=src),
            "to_place": _place(dst, iata=dst),
            "start": f"{start}{offsets[0]}",
            "end": f"{end}{offsets[1]}",
        },
        fits=fits,
    )


_OUT = _flight(
    "PHL → LIS", "2027-05-01T18:30", "2027-05-02T07:15", direction="outbound", src="PHL", dst="LIS"
)
_HOME = _flight(
    "LIS → PHL",
    "2027-05-09T11:00",
    "2027-05-09T14:10",
    direction="return",
    src="LIS",
    dst="PHL",
    offsets=("+01:00", "-04:00"),
)
_AVENIDA = _lodging("Hotel Avenida Palace", "2027-05-02T15:00", "2027-05-09T11:00")

_SHINJUKU_LIST = (
    "Here are three hotels in Shinjuku that fit a 3-star, about $150 a night budget:\n"
    "1. **Granbell Hotel Shinjuku** - modern rooms near Kabukicho, about $140 a night\n"
    "2. **Hotel Gracery Shinjuku** - the Godzilla hotel, about $160 a night\n"
    "3. **Shinjuku Washington Hotel Annex** - close to the station, about $120 a night\n"
    "Which one would you like?"
)

CASES: tuple[Case, ...] = (
    # --- The two smoke-test failures (lodging stage, a dinner request) ---
    Case(
        "smoke: dinner saved as lodging",
        PlanStage.LODGING,
        "Book us a dinner at a fado restaurant",
        (
            _lodging(
                "Dinner at Clube de Fado",
                "2027-05-03T19:00",
                "2027-05-04T00:30",
                kind="other",
                fits=False,
            ),
        ),
    ),
    Case(
        "smoke: invented hotel for a dinner",
        PlanStage.LODGING,
        "Book us a dinner at a fado restaurant",
        (_lodging("Hotel Fado Alfama", "2027-05-03T15:00", "2027-05-04T11:00", fits=False),),
    ),
    # --- Out of stage or not what was asked ---
    Case(
        "tour as lodging",
        PlanStage.LODGING,
        "Can you book a walking tour of Alfama on May 4?",
        (
            _lodging(
                "Alfama Walking Tour",
                "2027-05-04T10:00",
                "2027-05-05T10:00",
                kind="other",
                fits=False,
            ),
        ),
    ),
    Case(
        "hotel when asked for flights",
        PlanStage.FLIGHTS,
        "What flights are there from Philadelphia to Lisbon on May 1?",
        (_lodging("Lisbon Airport Hotel", "2027-05-01T15:00", "2027-05-02T11:00", fits=False),),
    ),
    Case(
        "transfer when asked for a museum",
        PlanStage.GROUND,
        "Add the Gulbenkian Museum on the morning of May 5",
        (
            _transfer(
                "Taxi to Gulbenkian Museum",
                "2027-05-05T09:30",
                "2027-05-05T09:50",
                mode="taxi",
                src="Hotel Avenida Palace",
                dst="Calouste Gulbenkian Museum",
                fits=False,
            ),
        ),
    ),
    Case(
        "activity on a different date",
        PlanStage.ACTIVITIES,
        "Add a visit to Belém Tower on May 5",
        (
            _activity(
                "Belém Tower",
                "2027-05-07T10:00",
                "2027-05-07T12:00",
                category="sightseeing",
                fits=False,
            ),
        ),
    ),
    Case(
        "hotel for the wrong month",
        PlanStage.LODGING,
        "Book the Memmo Alfama from May 2 to May 9",
        (_lodging("Memmo Alfama", "2027-06-02T15:00", "2027-06-09T11:00", fits=False),),
    ),
    Case(
        "day trip when asked for a restaurant",
        PlanStage.ACTIVITIES,
        "Find us a nice seafood restaurant for dinner on May 3",
        (
            _activity(
                "Sintra day trip",
                "2027-05-03T09:00",
                "2027-05-03T18:00",
                category="tour",
                place="Sintra",
                fits=False,
            ),
        ),
    ),
    Case(
        "wrong route for an airport taxi",
        PlanStage.GROUND,
        "Add a taxi from the airport to our hotel when we land",
        (
            _transfer(
                "Train to Porto",
                "2027-05-02T09:00",
                "2027-05-02T12:00",
                mode="train",
                src="Lisboa Santa Apolónia",
                dst="Porto Campanhã",
                fits=False,
            ),
        ),
    ),
    Case(
        "outbound flight when asked for the flight home",
        PlanStage.FLIGHTS,
        "Add the flight home on May 9",
        (
            _flight(
                "PHL → LIS",
                "2027-05-09T18:30",
                "2027-05-10T07:15",
                direction="outbound",
                src="PHL",
                dst="LIS",
                fits=False,
            ),
        ),
    ),
    Case(
        "removes the wrong item",
        PlanStage.REVIEW,
        "Remove the fado dinner",
        (
            Change(
                name="remove_item",
                summary="Remove “Memmo Alfama”",
                arguments={"item_id": "9f1c2d"},
                fits=False,
            ),
        ),
    ),
    Case(
        "an extra activity nobody asked for",
        PlanStage.ACTIVITIES,
        "Add the Oceanário on May 4 at 10",
        (
            _activity(
                "Oceanário de Lisboa",
                "2027-05-04T10:00",
                "2027-05-04T12:30",
                category="sightseeing",
            ),
            _activity(
                "Dinner at Time Out Market",
                "2027-05-04T19:00",
                "2027-05-04T21:00",
                category="food",
                fits=False,
            ),
        ),
    ),
    # --- Good proposals ---
    Case(
        "named hotel",
        PlanStage.LODGING,
        "Book Hotel Avenida Palace from May 2 to May 9",
        (_AVENIDA,),
    ),
    Case(
        "follow-up pick",
        PlanStage.LODGING,
        "the second one",
        (_lodging("Memmo Alfama", "2027-05-02T15:00", "2027-05-09T11:00"),),
    ),
    Case(
        "short yes",
        PlanStage.LODGING,
        "yes",
        (_AVENIDA,),
    ),
    Case(
        "round trip bundle",
        PlanStage.FLIGHTS,
        "Book the TAP round trip: Philadelphia to Lisbon May 1, back May 9",
        (_OUT, _HOME),
    ),
    Case(
        "chosen search options",
        PlanStage.FLIGHTS,
        "Take the cheapest options",
        (_OUT, _HOME),
    ),
    Case(
        "flight home",
        PlanStage.FLIGHTS,
        "Add the flight home: TP 203 from Lisbon on May 9 at 11:00",
        (_HOME,),
    ),
    Case(
        "dinner as an activity",
        PlanStage.ACTIVITIES,
        "Book us a dinner at a fado restaurant on May 3",
        (
            _activity(
                "Dinner at Clube de Fado",
                "2027-05-03T20:00",
                "2027-05-03T23:00",
                category="food",
                place="Clube de Fado",
            ),
        ),
    ),
    Case(
        "day trip",
        PlanStage.ACTIVITIES,
        "Add a day trip to Sintra on May 5",
        (
            _activity(
                "Sintra day trip",
                "2027-05-05T09:00",
                "2027-05-05T18:00",
                category="tour",
                place="Sintra",
            ),
        ),
    ),
    Case(
        "two museums",
        PlanStage.ACTIVITIES,
        "Plan two museums for May 4: Gulbenkian in the morning and MAAT in the afternoon",
        (
            _activity(
                "Calouste Gulbenkian Museum",
                "2027-05-04T10:00",
                "2027-05-04T12:30",
                category="museum",
            ),
            _activity("MAAT", "2027-05-04T14:30", "2027-05-04T17:00", category="museum"),
        ),
    ),
    Case(
        "all the transfers",
        PlanStage.GROUND,
        "Add the transfers we still need",
        (
            _transfer(
                "Car service to PHL",
                "2027-05-01T15:30",
                "2027-05-01T16:15",
                mode="car_service",
                src="Home",
                dst="Philadelphia International Airport",
            ),
            _transfer(
                "Taxi to Hotel Avenida Palace",
                "2027-05-02T08:00",
                "2027-05-02T08:30",
                mode="taxi",
                src="Lisbon Airport",
                dst="Hotel Avenida Palace",
            ),
            _transfer(
                "Taxi to Lisbon Airport",
                "2027-05-09T08:30",
                "2027-05-09T09:00",
                mode="taxi",
                src="Hotel Avenida Palace",
                dst="Lisbon Airport",
            ),
            _transfer(
                "Car service home",
                "2027-05-09T15:00",
                "2027-05-09T15:45",
                mode="car_service",
                src="Philadelphia International Airport",
                dst="Home",
            ),
        ),
    ),
    Case(
        "airport taxi",
        PlanStage.GROUND,
        "Add a taxi from the airport to our hotel when we land",
        (
            _transfer(
                "Taxi to Hotel Avenida Palace",
                "2027-05-02T08:00",
                "2027-05-02T08:30",
                mode="taxi",
                src="Lisbon Airport",
                dst="Hotel Avenida Palace",
            ),
        ),
    ),
    Case(
        "trip details",
        PlanStage.BASICS,
        "2 adults, May 1 to 9, from Philadelphia to Lisbon",
        (
            Change(
                name="set_plan_details",
                summary="Set trip dates, travellers, origin and destination",
                arguments={
                    "start_date": "2027-05-01",
                    "end_date": "2027-05-09",
                    "travelers": {"adults": 2},
                    "destination": {"name": "Lisbon"},
                },
            ),
        ),
    ),
    Case(
        "revise turn",
        PlanStage.LODGING,
        revise_request_message(
            "Actually we want to check out on May 8, not May 9",
            [ProposedChange(call_id="call_0", name="add_lodging", summary=_AVENIDA.summary)],
        ),
        (_lodging("Hotel Avenida Palace", "2027-05-02T15:00", "2027-05-08T11:00"),),
    ),
    Case(
        "move an activity",
        PlanStage.REVIEW,
        "Move the Sintra trip to May 6",
        (
            Change(
                name="update_item",
                summary="Change “Sintra day trip”: end, start",
                arguments={
                    "item_id": "4be7a1",
                    "changes": {
                        "start": "2027-05-06T09:00:00+01:00",
                        "end": "2027-05-06T18:00:00+01:00",
                    },
                },
            ),
        ),
    ),
    Case(
        "remove an item",
        PlanStage.REVIEW,
        "Remove the Oceanário",
        (
            Change(
                name="remove_item",
                summary="Remove “Oceanário de Lisboa”",
                arguments={"item_id": "71ad0e"},
            ),
        ),
    ),
    # --- A pick from the assistant's own list: "add #1" is that hotel, not another ---
    Case(
        "pick #1 but a different hotel from a new search",
        PlanStage.LODGING,
        "add #1",
        (
            _lodging(
                "Shinjuku Washington Hotel Annex",
                "2027-05-02T15:00",
                "2027-05-09T11:00",
                fits=False,
            ),
        ),
        previous_reply=_SHINJUKU_LIST,
    ),
    Case(
        "pick #1 and it is that hotel",
        PlanStage.LODGING,
        "add #1",
        (_lodging("Granbell Hotel Shinjuku", "2027-05-02T15:00", "2027-05-09T11:00"),),
        previous_reply=_SHINJUKU_LIST,
    ),
    Case(
        "pick the second one and it is that hotel",
        PlanStage.LODGING,
        "the second one please",
        (_lodging("Hotel Gracery Shinjuku", "2027-05-02T15:00", "2027-05-09T11:00"),),
        previous_reply=_SHINJUKU_LIST,
    ),
    Case(
        "pick the second one but the first is proposed",
        PlanStage.LODGING,
        "the second one please",
        (_lodging("Granbell Hotel Shinjuku", "2027-05-02T15:00", "2027-05-09T11:00", fits=False),),
        previous_reply=_SHINJUKU_LIST,
    ),
)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@pytest.fixture
async def llm() -> AsyncIterator[OllamaClient]:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(base_url=settings.ollama_url, timeout=5) as http:
            response = await http.get("/api/tags")
    except httpx.HTTPError:
        pytest.skip(f"Ollama is not reachable at {settings.ollama_url}")
    names = {model["name"] for model in response.json().get("models", [])}
    if settings.ollama_model not in names:
        pytest.skip(f"Model {settings.ollama_model} is not pulled")
    client = OllamaClient(host=settings.ollama_url, model=settings.ollama_model)
    yield client
    await client.aclose()


# ---------------------------------------------------------------------------
# Eval
# ---------------------------------------------------------------------------


async def test_reviewer_catches_misfits_and_passes_good_changes(llm: OllamaClient) -> None:
    caught = missed = passed = false_rejections = 0
    misses: list[str] = []
    for case in CASES:
        rejected = dict(
            await check_changes(
                llm,
                case.latest,
                case.proposed(),
                stage=case.stage,
                previous_reply=case.previous_reply,
            )
        )
        for index, change in enumerate(case.changes):
            verdict = rejected.get(index)
            if not change.fits and verdict is not None:
                caught += 1
            elif not change.fits:
                missed += 1
                misses.append(f"MISSED   [{case.label}] {change.summary}")
            elif verdict is not None:
                false_rejections += 1
                misses.append(f"REJECTED [{case.label}] {change.summary}: {verdict}")
            else:
                passed += 1

    catch_rate = caught / (caught + missed)
    false_rate = false_rejections / (false_rejections + passed)
    model = get_settings().ollama_model
    report = (
        f"Reviewer on {model}: caught {caught}/{caught + missed} ({catch_rate:.0%}), "
        f"falsely rejected {false_rejections}/{false_rejections + passed} ({false_rate:.0%})"
    )
    # `-s` shows this; the log line keeps it in captured output when the assert fails.
    print("\n" + report + "".join(f"\n  {line}" for line in misses))  # noqa: T201
    logger.info(report)

    assert catch_rate >= MIN_CATCH_RATE, (report, misses)
    assert false_rate <= MAX_FALSE_REJECTION_RATE, (report, misses)
