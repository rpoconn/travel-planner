"""A two-round tool loop against the real model `.env` selects:
`uv run pytest -m live_llm tests/shared/llm/test_tool_loop_live.py`. On Claude the call is
billed (a few cents).

This is what the planner's loop rests on: the model calls two tools at once, gets both
results back, and answers from them. Claude rejects the second request unless the first
reply's thinking blocks come back unchanged and each result carries its call's id.
"""

import logging

import pytest

from shared.llm.types import LLMClient, LLMMessage, ToolSpec, add_chunk

pytestmark = pytest.mark.live_llm
logger = logging.getLogger(__name__)

NIGHTLY_RATE = ToolSpec(
    name="nightly_rate",
    description="The nightly rate of a hotel, in US dollars.",
    parameters={
        "type": "object",
        "properties": {"hotel": {"type": "string"}},
        "required": ["hotel"],
    },
)
RATES = {"okura": "412", "hyatt": "655"}


async def test_two_parallel_tool_calls_then_an_answer(llm: LLMClient) -> None:
    history = [
        LLMMessage(role="system", content="Use the tools. Never guess a price."),
        LLMMessage(
            role="user",
            content=(
                # The anagrams give the model something to reason about before it calls
                # the tools, so the first reply usually carries thinking blocks.
                "Two hotels have names that are anagrams of 'kuroa' and 'tayht'. Look up "
                "both nightly rates at once, then tell me the difference in dollars."
            ),
        ),
    ]

    reply = LLMMessage(role="assistant")
    async for chunk in llm.stream(history, tools=[NIGHTLY_RATE], think=True):
        add_chunk(reply, chunk)
    assert reply.tool_calls, f"the model answered without tools: {reply.content!r}"
    # Shown with `--log-cli-level=INFO`: whether this run had thinking blocks to send back.
    logger.info("first reply blocks: %s", [block.get("type") for block in reply.provider_content])
    history.append(reply)
    for call in reply.tool_calls:
        hotel = str(call.arguments.get("hotel", "")).casefold()
        rate = next((rate for name, rate in RATES.items() if name in hotel), "unknown")
        history.append(
            LLMMessage(role="tool", tool_name=call.name, tool_call_id=call.id, content=rate)
        )

    final = await llm.chat(history, tools=[NIGHTLY_RATE], think=True)

    assert not final.tool_calls
    assert "243" in final.content
