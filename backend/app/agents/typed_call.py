"""One bounded repair attempt for model output that violates its contract.

ReStock's gateway makes a distinction this module copies exactly, because
collapsing it is the mistake that turns a provider outage into a spend loop:

* **A provider failure is not a model-output failure.** A 429, a 503, a timeout
  or a network error means the model was never asked, or was asked and never
  answered. There is no malformed reasoning to repair, and re-asking spends a
  second request against a provider that has already refused. Those failures are
  raised by the provider as ``LlmProviderError`` and never reach the repair path
  below — not by a check, but because the exception is a different type with a
  different code.
* **A model-output failure is repairable, once.** The model answered, and what
  came back was not one JSON object matching the schema. That is worth exactly
  one short re-ask.

The third case is RydeResolve's own, and it is the one to be most careful about:
a response that parses and validates, whose *claims* are then rejected because
they cite evidence that does not exist, contradict the deterministic analysis, or
invent a monetary value. That is not a malformed response — it is an untrustworthy
one, and it is rejected by ``AdvocateClaimVerificationService`` long after this
module has returned. Repairing it would mean asking the model to rewrite rejected
claims into valid ones, which is precisely the behaviour the evidence-first design
exists to prevent. It is unrepairable here **structurally**: verification runs
downstream, so a rejection can never reach this loop.
"""

from __future__ import annotations

import dataclasses
from typing import Callable, TypeVar

from app.agents.provider import AgentCompletionRequest, LlmCompletion, LlmProvider

T = TypeVar("T")


class ModelOutputContractError(RuntimeError):
    """Base for "the model answered, and the answer violated its contract".

    Declared here rather than inferred, so the repair loop can ask a question
    with a definite answer — ``isinstance`` — instead of probing for a ``code``
    attribute and hoping every future agent error happens to carry one.

    The three agent error types (``AdvocateAgentError``, ``JudgeAgentError``,
    ``RebuttalAgentError``) derive from this. A parser that raises anything else
    is treated as unrepairable, which is the safe default: a bug in the parser is
    not something to re-ask a model about.
    """

    code: str = ""


REPAIR_INSTRUCTION = (
    "Your previous response violated the output contract. Return exactly one "
    "JSON object matching the supplied response schema. Use the exact field "
    "names. Do not use Markdown. Do not add text before or after the JSON. Do "
    "not explain. Do not emit alternatives. The first character must be { and "
    "the last character must be }. Stop immediately after the closing brace."
)

REPAIRABLE_OUTPUT_CODES: frozenset[str] = frozenset(
    {"MALFORMED_JSON", "SCHEMA_VALIDATION_FAILED"}
)
"""Output codes worth one repair.

Deliberately not a catch-all. ``OUTPUT_TRUNCATED`` is excluded because re-asking
with the same token budget truncates the same way, so a repair would spend a
request to reproduce the failure. ``WRONG_SIDE`` is excluded because a
schema-valid response that names the other side is not a shape problem.
"""

MAX_REPAIR_ATTEMPTS = 1
"""One repair. Not a retry loop, and not a configurable knob."""


def complete_typed(
    provider: LlmProvider,
    request: AgentCompletionRequest,
    parse: Callable[[str, LlmCompletion | None], T],
) -> tuple[T, LlmCompletion]:
    """Call the provider and parse, repairing malformed output at most once.

    Returns the parsed value and the completion that produced it. When a repair
    happened the returned completion **covers both attempts**, so the tokens and
    latency reported for this call are the cost of producing this output rather
    than the cost of the attempt that happened to succeed last.

    ``parse`` is the agent's own parser. Supplying it here rather than
    reimplementing validation keeps exactly one definition of what a valid
    response is — the same one that would have run without a repair.
    """
    first = provider.complete(request)
    try:
        return parse(first.raw_text, first), first
    except Exception as error:  # noqa: BLE001 - re-raised unless repairable
        if not _repairable(error):
            raise
        first_failure = error

    repaired = dataclasses.replace(
        request, user_prompt=f"{request.user_prompt}\n\n{REPAIR_INSTRUCTION}"
    )
    second = provider.complete(repaired)
    try:
        return parse(second.raw_text, second), _combined(first, second)
    except Exception as error:  # noqa: BLE001 - re-raised unless repairable
        if not _repairable(error):
            raise
        # The repair is spent, so this failure is the final one and is reported
        # as the stage's cause. It is chained to the first failure rather than
        # replacing it: an operator needs to know that the model was malformed
        # *twice*, not merely that the second response was wrong. Without the
        # chain, a repair would hide the fact that the first response was
        # unusable, and the failure would look like a single unlucky answer.
        raise error from first_failure


def _repairable(error: BaseException) -> bool:
    """True only for a model-output contract violation worth one re-ask."""
    return (
        isinstance(error, ModelOutputContractError)
        and error.code in REPAIRABLE_OUTPUT_CODES
    )


def _combined(first: LlmCompletion, second: LlmCompletion) -> LlmCompletion:
    """One completion describing both attempts.

    ``attempt_count`` already means "requests made to produce this output", so a
    repair belongs in it. Tokens and latency are summed; the text and finish
    reason come from the attempt that succeeded, because those describe the
    output rather than the cost.

    A token count is summed only when **both** attempts reported one. Adding a
    known count to an unknown one would invent a total the provider never
    reported, and this project reports unknown as unknown.
    """
    return dataclasses.replace(
        second,
        duration_ms=first.duration_ms + second.duration_ms,
        input_tokens=_sum_or_none(first.input_tokens, second.input_tokens),
        output_tokens=_sum_or_none(first.output_tokens, second.output_tokens),
        total_tokens=_sum_or_none(first.total_tokens, second.total_tokens),
        attempt_count=first.attempt_count + second.attempt_count,
    )


def _sum_or_none(left: int | None, right: int | None) -> int | None:
    if left is None or right is None:
        return None
    return left + right


__all__ = [
    "MAX_REPAIR_ATTEMPTS",
    "ModelOutputContractError",
    "REPAIRABLE_OUTPUT_CODES",
    "REPAIR_INSTRUCTION",
    "complete_typed",
]
