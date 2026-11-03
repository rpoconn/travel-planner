"""The proposal reviewer: one narrow structured-output check, by the same model, of the
changes the agent staged in a turn, before the user sees the card.

The tools catch what a rule can state (an overnight stay, a cited URL). This catches
"right tool, wrong thing": a stage's own tool used for something that isn't that stage's,
like a dinner proposed with add_lodging. The agent withdraws what it rejects (see
`features.chat.agent`); the approval card stays the last safety net, so the reviewer fails
open.
"""

import logging
from collections.abc import Awaitable, Callable, Sequence

from pydantic import ValidationError

from features.chat.prompts import review_messages
from features.chat.schemas import ProposedChange, ReviewVerdict
from features.plans.schemas import PlanStage
from shared.llm.schema import inline_schema
from shared.llm.types import LLMClient, LLMError

logger = logging.getLogger(__name__)

# How many times to ask again when the model's JSON doesn't match the schema.
MAX_ATTEMPTS = 2
# Given when the model rejects a change without saying why.
DEFAULT_REASON = "it doesn't match what was asked for in this stage"

# Reason before answering, whatever `llm_think` says for the chat. On qwen3:8b this took
# the eval's catch rate from 8/12 to 10/12 with no false rejections, for ~3s more per
# proposing turn (the reviewer's output is short, so it's mostly the thinking).
REVIEW_THINK = True
# Inlined: small models follow `$ref`s unreliably, and the verdict list nests a model.
_VERDICT_SCHEMA = inline_schema(ReviewVerdict)

# `(latest, changes)` -> the rejected changes as (position in `changes`, reason).
ReviewFn = Callable[[str, Sequence[ProposedChange]], Awaitable[list[tuple[int, str]]]]


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


def _positions(verdict: ReviewVerdict, count: int) -> list[int | None]:
    """Which change (0-based) each verdict is about; None when it can't be told.

    qwen3:8b numbers the changes from 0 about as often as from 1, despite the prompt. So
    when there is one verdict per change, numbers that run 0..count-1 are read as 0-based,
    and numbers that fit neither way are ignored in favour of the list's order. Otherwise
    (some changes left out, as the schema allows) the numbers are read as listed, from 1."""
    indices = [item.index for item in verdict.verdicts]
    if len(indices) == count:
        if sorted(indices) == list(range(1, count + 1)):
            return [index - 1 for index in indices]
        if sorted(indices) == list(range(count)):
            return list(indices)
        return list(range(count))
    return [index - 1 if 1 <= index <= count else None for index in indices]


def _rejected(verdict: ReviewVerdict, count: int) -> list[tuple[int, str]]:
    """The rejected changes by 0-based position (see `_positions`)."""
    rejected: dict[int, str] = {}
    for item, position in zip(verdict.verdicts, _positions(verdict, count), strict=True):
        if item.fits_request:
            continue
        if position is None:
            logger.warning("Reviewer judged change %d of %d; ignored", item.index, count)
            continue
        rejected.setdefault(position, item.reason.strip() or DEFAULT_REASON)
    return sorted(rejected.items())


async def check_changes(
    llm: LLMClient, latest: str, changes: Sequence[ProposedChange], *, stage: PlanStage
) -> list[tuple[int, str]]:
    """The changes in `changes` that don't fit `latest` or `stage`, as (position, reason).
    Fails open (rejects nothing) when the model's output stays malformed or the model is
    unreachable: the user still approves every change on the card."""
    if not changes:
        return []
    messages = review_messages(stage, latest, changes)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            reply = await llm.chat(messages, format=_VERDICT_SCHEMA, think=REVIEW_THINK)
        except LLMError as exc:
            # The agent's answer is already streamed; don't turn it into an error.
            logger.error("Reviewer call failed; failing open: %s", exc)
            return []
        try:
            verdict = ReviewVerdict.model_validate_json(reply.content)
        except ValidationError as exc:
            logger.warning(
                "Reviewer returned malformed output (attempt %d/%d): %s",
                attempt,
                MAX_ATTEMPTS,
                exc.errors(include_url=False)[:3],
            )
            continue
        rejected = _rejected(verdict, len(changes))
        logger.info("Reviewer: %d of %d change(s) rejected", len(rejected), len(changes))
        return rejected

    logger.error("Reviewer failed %d times; failing open", MAX_ATTEMPTS)
    return []


def reviewer(llm: LLMClient, stage: PlanStage) -> ReviewFn:
    """`check_changes` bound to a model and the plan's stage, as the agent calls it."""

    async def review(latest: str, changes: Sequence[ProposedChange]) -> list[tuple[int, str]]:
        return await check_changes(llm, latest, changes, stage=stage)

    return review
