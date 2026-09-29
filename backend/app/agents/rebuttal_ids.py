"""How rebuttals are addressed.

Both sides produce rebuttal responses, so a bare index such as ``RB1`` would be
ambiguous once both sides appear in the same Judge context — exactly the problem
``judge_claims`` solves for claims. Rebuttals are therefore namespaced
``RIDER-RB1`` / ``DRIVER-RB1``.

Rebuttal IDs are *assigned by code*, never by the model. A model that could name
its own rebuttal could name one that does not exist, or reuse another side's
name; instead the index is positional, derived from the order of the responses
that actually passed verification.

Note the deliberate asymmetry with claim IDs: a claim ID is authored by the
advocate and merely prefixed, because the Judge must be able to cite a claim the
advocate itself named. A rebuttal ID has no author-supplied component at all.
"""

from __future__ import annotations

from app.models.agent import AdvocateSide

SEPARATOR = "-"
MARKER = "RB"


def namespace_rebuttal_id(side: AdvocateSide, index: int) -> str:
    """``("RIDER", 1) -> "RIDER-RB1"``.

    Indexing starts at 1 so the ID reads naturally to a human reviewer.
    """
    return f"{side}{SEPARATOR}{MARKER}{index}"


def split_rebuttal_id(reference: str) -> tuple[AdvocateSide | None, int | None]:
    """Split ``"RIDER-RB1"`` into ``("RIDER", 1)``.

    Returns ``(None, None)`` for anything that is not a well-formed namespaced
    rebuttal ID, which is how an invented reference is detected: it resolves to
    no side and no index, so it can never match a verified rebuttal.
    """
    for side in ("RIDER", "DRIVER"):
        prefix = f"{side}{SEPARATOR}{MARKER}"
        if reference.startswith(prefix):
            suffix = reference[len(prefix) :]
            if suffix.isdigit():
                return side, int(suffix)  # type: ignore[return-value]
            return None, None
    return None, None


def known_rebuttal_ids(
    rider: list[object], driver: list[object]
) -> dict[str, str]:
    """Namespaced rebuttal ID -> owning side, for every verified rebuttal.

    Reads ``rebuttal_id`` off each item, so it accepts ``VerifiedRebuttal``
    instances without importing them and creating a cycle.
    """
    index: dict[str, str] = {}
    for side, rebuttals in (("RIDER", rider), ("DRIVER", driver)):
        for rebuttal in rebuttals:
            rebuttal_id = getattr(rebuttal, "rebuttal_id", None)
            if rebuttal_id:
                index[rebuttal_id] = side
    return index


__all__ = [
    "MARKER",
    "SEPARATOR",
    "known_rebuttal_ids",
    "namespace_rebuttal_id",
    "split_rebuttal_id",
]
