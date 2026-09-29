"""Detecting monetary figures in model prose.

The Judge has no amount field, and neither does a rebuttal response. That closes
the structured route for a model to state money, but prose remains open — a
model can always write a figure into a reasoning summary.

This module is shared by ``JudgeOutputValidationService`` and
``RebuttalValidationService`` rather than duplicated. Two copies of "what counts
as a monetary amount" would be free to drift, and the weaker copy would become
the effective guarantee.

It is a defence-in-depth check, not the primary control. The primary control is
that the field does not exist. A spelled-out amount ("two hundred dollars")
evades this and is caught only by a human reviewer, which is acceptable precisely
because nothing downstream consumes the prose as a number.
"""

from __future__ import annotations

import re

# Currency tokens and amounts. Deliberately broad: a false positive costs a
# rejected response that a human then reads, while a false negative lets an
# invented figure through.
_CURRENCY = r"(?:SGD|USD|MYR|IDR|THB|PHP|VND|CNY|RMB|EUR|GBP|S\$|US\$|HK\$|A\$|[$€£¥])"
_AMOUNT = r"\d+(?:[.,]\d+)?"

_PATTERNS = (
    re.compile(rf"{_CURRENCY}\s*({_AMOUNT})", re.IGNORECASE),
    re.compile(rf"({_AMOUNT})\s*{_CURRENCY}", re.IGNORECASE),
)

# Amounts are compared after rounding to cents; a model quoting a supplied fact
# should not fail on float representation.
TOLERANCE = 0.005


def monetary_amounts(text: str) -> list[float]:
    """Every monetary figure appearing in ``text``, in order of appearance."""
    amounts: list[float] = []
    for pattern in _PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(1).replace(",", "")
            try:
                amounts.append(float(raw))
            except ValueError:
                continue
    return amounts


def trusted_numeric_values(facts: object) -> list[float]:
    """Every numeric value the deterministic layer supplied.

    A model quoting ``fareDifference`` is repeating a fact it was given, which is
    allowed. A model stating a figure that appears nowhere in the facts has
    produced a number from nowhere, which is the failure this guards against.
    """
    values: list[float] = []

    def collect(node: object) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            values.append(float(node))
        elif isinstance(node, dict):
            for item in node.values():
                collect(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                collect(item)

    dump = getattr(facts, "model_dump", None)
    if callable(dump):
        collect(dump())
    return values


def invented_monetary_amounts(text: str, facts: object) -> list[float]:
    """Monetary figures in ``text`` that are absent from the trusted facts."""
    trusted = trusted_numeric_values(facts)
    return [
        amount
        for amount in monetary_amounts(text)
        if not any(abs(amount - value) <= TOLERANCE for value in trusted)
    ]


__all__ = [
    "TOLERANCE",
    "invented_monetary_amounts",
    "monetary_amounts",
    "trusted_numeric_values",
]
