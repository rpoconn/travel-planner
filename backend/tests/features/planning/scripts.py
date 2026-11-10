"""Scripted model replies for the planning tests: each is one reply of the planner's loop,
calling one tool (`shared.llm.fake.tool_calls`)."""

from pydantic import JsonValue

from shared.llm.fake import tool_calls
from shared.llm.types import LLMMessage

Change = dict[str, JsonValue]

NO_FLIGHTS: Change = {"type": "set_travel_mode", "no_flights": True}


def ask(question: str, slot: str | None = None) -> LLMMessage:
    """Ask the user, with `slot`'s form under the question when given."""
    arguments: dict[str, JsonValue] = {"question": question}
    if slot is not None:
        arguments["slot"] = slot
    return tool_calls(("ask_user", arguments))


def search(kind: str, problem: str | None = None, *, more: bool = False) -> LLMMessage:
    arguments: dict[str, JsonValue] = {"kind": kind}
    if problem is not None:
        arguments["problem"] = problem
    if more:
        arguments["more"] = True
    return tool_calls(("search", arguments))


def present(name: str = "a") -> LLMMessage:
    """Show search `name`'s options (a turn's first search is "a")."""
    return tool_calls(("present_choices", {"search": name}))


def suggest(reason: str, *changes: Change) -> LLMMessage:
    return tool_calls(("suggest_changes", {"reason": reason, "changes": list(changes)}))


def apply(quote: str, *changes: Change) -> LLMMessage:
    return tool_calls(("apply_user_changes", {"quote": quote, "changes": list(changes)}))


def pick(*candidates: str) -> LLMMessage:
    return tool_calls(("pick_choice", {"candidates": list(candidates)}))


def decide(decision: str) -> LLMMessage:
    return tool_calls(("decide_proposal", {"decision": decision}))


def remove(item: int) -> Change:
    return {"type": "remove_item", "item": item}
