"""How the Judge addresses advocate claims.

Both advocates use the same output schema, so both start their claim IDs at
``C1``. In the Judge's context the two sides are presented together, which makes
a bare ``C1`` ambiguous: ``acceptedRiderClaimIds: ["C1"]`` and
``acceptedDriverClaimIds: ["C1"]`` would be indistinguishable from a model that
copied one side's citation into the other.

That ambiguity is not cosmetic. Stage 5 requires every claim reference the Judge
makes to be validated against a claim that was actually verified, for the side it
was verified on. Without a unique address, "validate all references in code" is
unenforceable — the check would pass while the reasoning was about the wrong
party's argument.

So claims are namespaced ``RIDER-C1`` / ``DRIVER-C1`` in everything the Judge
sees and emits, and the namespace is stripped only when a human-readable ID is
needed for display.
"""

from __future__ import annotations

from app.models.advocate import AdvocateClaim
from app.models.agent import AdvocateSide

SEPARATOR = "-"


def namespace_claim_id(side: str, claim_id: str) -> str:
    """``("RIDER", "C1") -> "RIDER-C1"``.

    Idempotent: an already-namespaced ID is returned unchanged, so a value that
    round-trips through the model and back cannot accumulate prefixes.
    """
    if claim_id.startswith(f"{side}{SEPARATOR}"):
        return claim_id
    return f"{side}{SEPARATOR}{claim_id}"


def split_claim_id(reference: str) -> tuple[str | None, str]:
    """Split ``"RIDER-C1"`` into ``("RIDER", "C1")``.

    Returns ``(None, reference)`` for anything without a recognised side prefix,
    which is how an invented ID such as ``"R99"`` is detected: it resolves to no
    side, so it can never match a verified claim on either side.
    """
    for side in ("RIDER", "DRIVER"):
        prefix = f"{side}{SEPARATOR}"
        if reference.startswith(prefix):
            return side, reference[len(prefix) :]
    return None, reference


def build_claim_index(claims: list[AdvocateClaim], side: AdvocateSide) -> dict[str, AdvocateClaim]:
    """Map namespaced claim IDs to the verified claims they address."""
    return {namespace_claim_id(side, claim.claim_id): claim for claim in claims}


def known_claim_ids(rider: list[AdvocateClaim], driver: list[AdvocateClaim]) -> dict[str, str]:
    """Namespaced ID -> owning side, for every verified claim on both sides."""
    index: dict[str, str] = {}
    for side, claims in (("RIDER", rider), ("DRIVER", driver)):
        for claim in claims:
            index[namespace_claim_id(side, claim.claim_id)] = side
    return index


__all__ = [
    "SEPARATOR",
    "build_claim_index",
    "known_claim_ids",
    "namespace_claim_id",
    "split_claim_id",
]
