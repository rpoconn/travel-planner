"""The offline fake's planning replies (LLM_PROVIDER=fake)."""

from datetime import date

from features.chat.schemas import GuardrailVerdict
from features.planning.extractor import extraction_schema
from features.planning.offline import OFFLINE_ANSWER, extract_reply, planning_reply
from features.planning.prompts import question_messages
from features.plans.schemas import SlotName
from shared.llm.fake import FakeRequest
from shared.llm.types import LLMMessage
from tests.features.plans.builders import snapshot

TODAY = date(2026, 10, 15)


def _request(slot: SlotName, content: str) -> FakeRequest:
    messages = [LLMMessage(role="system", content="..."), LLMMessage(role="user", content=content)]
    return FakeRequest(messages, [], extraction_schema(slot), streamed=False, think=False)


def test_a_known_phrase_is_answered() -> None:
    assert extract_reply(SlotName.TRAVELERS, "Two adults.", TODAY) == {
        "status": "answered",
        "value": {"adults": 2},
    }


def test_relative_dates_count_from_today() -> None:
    reply = extract_reply(SlotName.DATES, "next month for a week", TODAY)

    assert reply["value"] == {"dates": {"start": "2026-11-01", "end": "2026-11-08"}}


def test_a_question_and_anything_else() -> None:
    assert extract_reply(SlotName.DATES, "When is it cheapest?", TODAY)["status"] == "question"
    assert extract_reply(SlotName.DATES, "what's 2+2", TODAY)["status"] == "unrelated"


def test_the_slot_is_read_from_the_requested_schema() -> None:
    reply = planning_reply(_request(SlotName.DESTINATION, "lisbon"))

    assert reply is not None
    assert '"Lisbon"' in reply.content and '"answered"' in reply.content


def test_a_question_request_gets_the_offline_answer() -> None:
    messages = question_messages(snapshot(), "Is May warm?", None)

    reply = planning_reply(FakeRequest(messages, [], None, streamed=True))

    assert reply is not None and reply.content == OFFLINE_ANSWER


def test_other_requests_are_left_to_the_chat_fake() -> None:
    guardrail = FakeRequest([], [], GuardrailVerdict.model_json_schema(), streamed=False)
    agent = FakeRequest([LLMMessage(role="system", content="You plan trips")], [], None, True)

    assert planning_reply(guardrail) is None
    assert planning_reply(agent) is None
