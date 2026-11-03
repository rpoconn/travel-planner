"""The proposal reviewer: one structured call per card, failing open."""

from features.chat.prompts import (
    LATEST_MESSAGE_MARKER,
    PREVIOUS_REPLY_MARKER,
    REVIEW_CHANGES_MARKER,
    REVIEW_SYSTEM,
)
from features.chat.review import DEFAULT_REASON, MAX_ATTEMPTS, check_changes, reviewer
from features.chat.schemas import MAX_REASON_LENGTH, ChangeVerdict, ProposedChange, ReviewVerdict
from shared.llm.fake import FakeLLM, json_reply
from shared.llm.types import LLMError

DINNER = ProposedChange(
    call_id="call_1",
    name="add_lodging",
    arguments={
        "title": "Dinner at Alfama",
        "place": {"name": "Clube de Fado"},
        "kind": "other",
        "start": "2027-05-03T19:00:00+01:00",
        "end": "2027-05-04T00:30:00+01:00",
    },
    summary="Add lodging “Dinner at Alfama” on 2027-05-03 19:00",
)
HOTEL = ProposedChange(
    call_id="call_2",
    name="add_lodging",
    arguments={"title": "Hotel Okura", "place": {"name": "Hotel Okura"}},
    summary="Add lodging “Hotel Okura” on 2027-05-02 15:00",
)


def _verdict(*rejected: tuple[int, str]) -> ReviewVerdict:
    return ReviewVerdict(
        verdicts=[
            ChangeVerdict(index=index, fits_request=False, reason=reason)
            for index, reason in rejected
        ]
    )


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


async def test_an_empty_verdict_approves_every_change() -> None:
    llm = FakeLLM([json_reply(ReviewVerdict(verdicts=[]))])

    assert await check_changes(llm, "Hotel Okura please", [HOTEL], stage="lodging") == []


async def test_rejected_changes_come_back_by_position_with_their_reason() -> None:
    verdict = ReviewVerdict(
        verdicts=[
            ChangeVerdict(index=1, fits_request=False, reason="They asked for dinner."),
            ChangeVerdict(index=2, fits_request=True, reason="The hotel they named."),
        ]
    )
    llm = FakeLLM([json_reply(verdict)])

    rejected = await check_changes(llm, "Book dinner", [DINNER, HOTEL], stage="lodging")

    assert rejected == [(0, "They asked for dinner.")]


async def test_a_rejection_without_a_reason_gets_a_default_one() -> None:
    llm = FakeLLM([json_reply(_verdict((1, "  ")))])

    assert await check_changes(llm, "x", [DINNER], stage="lodging") == [(0, DEFAULT_REASON)]


async def test_out_of_range_numbers_are_ignored() -> None:
    llm = FakeLLM([json_reply(_verdict((0, "zero"), (3, "too far"), (2, "the hotel")))])

    rejected = await check_changes(llm, "x", [DINNER, HOTEL], stage="lodging")

    assert rejected == [(1, "the hotel")]


async def test_a_full_list_numbered_from_zero_is_read_as_zero_based() -> None:
    # qwen3:8b numbers from 0 about half the time; one verdict per change gives it away.
    verdict = ReviewVerdict(
        verdicts=[
            ChangeVerdict(index=0, fits_request=True, reason="The hotel."),
            ChangeVerdict(index=1, fits_request=False, reason="A dinner."),
        ]
    )
    llm = FakeLLM([json_reply(verdict)])

    assert await check_changes(llm, "x", [HOTEL, DINNER], stage="lodging") == [(1, "A dinner.")]


async def test_a_lone_verdict_numbered_zero_is_about_the_only_change() -> None:
    llm = FakeLLM([json_reply(_verdict((0, "A dinner.")))])

    assert await check_changes(llm, "x", [DINNER], stage="lodging") == [(0, "A dinner.")]


async def test_a_full_list_with_unusable_numbers_is_read_in_order() -> None:
    verdict = ReviewVerdict(
        verdicts=[
            ChangeVerdict(index=7, fits_request=False, reason="A dinner."),
            ChangeVerdict(index=7, fits_request=True, reason="The hotel."),
        ]
    )
    llm = FakeLLM([json_reply(verdict)])

    assert await check_changes(llm, "x", [DINNER, HOTEL], stage="lodging") == [(0, "A dinner.")]


async def test_malformed_output_is_retried_then_fails_open() -> None:
    llm = FakeLLM([json_reply("{not json")] * MAX_ATTEMPTS)

    assert await check_changes(llm, "Book dinner", [DINNER], stage="lodging") == []
    assert len(llm.requests) == MAX_ATTEMPTS


async def test_a_retry_after_malformed_output_can_still_reject() -> None:
    llm = FakeLLM([json_reply({"verdicts": "no"}), json_reply(_verdict((1, "dinner")))])

    assert await check_changes(llm, "x", [DINNER], stage="lodging") == [(0, "dinner")]


async def test_an_unreachable_model_fails_open() -> None:
    llm = FakeLLM([LLMError("Could not reach Ollama")])

    assert await check_changes(llm, "x", [DINNER], stage="lodging") == []


async def test_no_changes_need_no_call() -> None:
    llm = FakeLLM()

    assert await check_changes(llm, "x", [], stage="lodging") == []
    assert llm.requests == []


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


async def test_request_lists_the_changes_then_the_latest_message() -> None:
    llm = FakeLLM([json_reply(ReviewVerdict(verdicts=[]))])
    review = reviewer(llm, "lodging")

    await review("Book us a dinner at a fado restaurant", [DINNER, HOTEL])

    (request,) = llm.requests
    assert not request.streamed and request.tools == []
    # Thinking on for this call only: the eval's catch rate depends on it.
    assert request.think is True
    assert request.format is not None and "verdicts" in request.format["properties"]
    # The reason comes before the verdict, so the model describes the change first.
    assert list(request.format["properties"]["verdicts"]["items"]["properties"]) == [
        "index",
        "reason",
        "fits_request",
    ]
    assert "$ref" not in str(request.format)
    system, user = request.messages
    assert system.content == REVIEW_SYSTEM
    assert user.content.startswith("Current stage: lodging: find somewhere to stay")
    # The kind, place and times show what a change really is.
    assert (
        f"{REVIEW_CHANGES_MARKER}\n1. add_lodging: Add lodging “Dinner at Alfama” on "
        "2027-05-03 19:00 (place: Clube de Fado; kind: other; "
        "start: 2027-05-03T19:00:00+01:00; end: 2027-05-04T00:30:00+01:00)\n"
        "2. add_lodging: Add lodging “Hotel Okura” on 2027-05-02 15:00 (place: Hotel Okura)"
    ) in user.content
    # The user's message is last: the offline model reads it from after the marker.
    assert user.content.endswith(f"{LATEST_MESSAGE_MARKER}\nBook us a dinner at a fado restaurant")


async def test_an_update_shows_the_changed_fields() -> None:
    update = ProposedChange(
        call_id="call_3",
        name="update_item",
        arguments={"item_id": "abc", "changes": {"start": "2027-05-03T10:00:00+09:00"}},
        summary="Change “Hotel Okura”: start",
    )
    llm = FakeLLM([json_reply(ReviewVerdict(verdicts=[]))])

    await check_changes(llm, "Check in at 10", [update], stage="lodging")

    assert (
        "1. update_item: Change “Hotel Okura”: start (start: 2027-05-03T10:00:00+09:00)"
        in llm.requests[0].messages[1].content
    )


async def test_a_long_reason_is_cut_rather_than_failing_the_verdict() -> None:
    # The prompt schema has no maxLength, so the model doesn't know about the limit.
    llm = FakeLLM([json_reply(_verdict((1, "word " * 200)))])

    ((position, reason),) = await check_changes(llm, "x", [DINNER], stage="lodging")

    assert position == 0
    assert len(reason) <= MAX_REASON_LENGTH and reason.endswith("…")


async def test_the_reply_the_user_answers_comes_before_the_changes() -> None:
    # "add #1" means nothing without the list it picks from.
    listing = "1. Granbell Hotel Shinjuku\n2. Shinjuku Washington Hotel Annex"
    llm = FakeLLM([json_reply(ReviewVerdict(verdicts=[]))])
    review = reviewer(llm, "lodging", listing)

    await review("add #1", [HOTEL])

    user = llm.requests[0].messages[1].content
    assert f"{PREVIOUS_REPLY_MARKER}\n{listing}\n\n{REVIEW_CHANGES_MARKER}\n1. " in user
    assert user.endswith(f"{LATEST_MESSAGE_MARKER}\nadd #1")
    assert "the change fits only when it is that option, by name" in REVIEW_SYSTEM


async def test_without_a_previous_reply_the_request_has_no_section_for_it() -> None:
    llm = FakeLLM([json_reply(ReviewVerdict(verdicts=[]))])

    await check_changes(llm, "Hotel Okura please", [HOTEL], stage="lodging", previous_reply="")

    assert PREVIOUS_REPLY_MARKER not in llm.requests[0].messages[1].content
