"""Stage 6 bounded rebuttal contracts.

Stage 6 adds exactly one thing to the pipeline: each advocate gets a single
chance to respond to the *opposing* side's already-verified claims, before the
Judge runs. Nothing else changes.

Four constraints shape every type in this module.

1. **One round, structurally.** There is no ``round`` field on the model's
   output, and ``RebuttalCaseContext`` can only carry *initial* verified claims.
   A rebuttal therefore cannot address another rebuttal, because there is no
   field that could name one. ``MAX_REBUTTAL_ROUNDS`` is not a tunable: it is a
   statement of what the pipeline can express.

2. **No new evidence, no new policy.** A rebuttal cites only evidence and policy
   rule IDs that already exist in the trusted context. ``evidence_ids`` and
   ``policy_rule_ids`` are references to existing IDs, never carriers of new
   content.

3. **Concessions are claim references, not prose.** ``concessions`` holds target
   claim IDs, so it is machine-checkable against the stances actually taken. Free
   prose could not be validated, and an unvalidated field in a trust boundary is
   a liability.

4. **Rejected rebuttals stay visible and never become trusted input.**
   ``RebuttalSideResult`` carries both ``verified_rebuttals`` and
   ``rejected_rebuttals``; only the former is ever projected into the Judge
   context.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.advocate import (
    AdvocateClaim,
    AssertedFact,
    PipelineStage,
    VerificationSummary,
)
from app.models.agent import (
    AdvocateSide,
    AgentCallMetadata,
    ContextEvidence,
    ContextPolicy,
    ContextPolicyEvaluation,
    NoShowFacts,
    RouteDeviationFacts,
)

# The whole point of Stage 6 is that this number is 1 and cannot be raised by
# configuration. A second round would turn a bounded cross-examination into an
# open-ended debate, which is explicitly out of scope.
MAX_REBUTTAL_ROUNDS = 1


class RebuttalModel(BaseModel):
    """Base for the model's rebuttal output. Strict on purpose.

    ``extra="forbid"`` mirrors the Judge: an invented field is a schema failure
    rather than a silently ignored extra.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class RebuttalContextModel(BaseModel):
    """Base for the context we hand to a rebuttal agent.

    Lenient, because this model is *built by us* from an allow-list and is never
    parsed from untrusted input. Strictness belongs on the model's output.
    """

    model_config = ConfigDict(populate_by_name=True)


RebuttalStance = Literal["CHALLENGE", "CONCEDE", "PARTIALLY_CONCEDE"]
"""How a side responds to one opposing claim.

Deliberately three values. There is no ``REFUTE`` or ``DISPROVE``: a rebuttal
cannot establish that a verified claim is *false*, because the deterministic
facts already decided what is true. It can only argue that a claim is
incomplete, misleading, or unsupported for the conclusion drawn from it — which
is what ``CHALLENGE`` means.
"""

STANCES: tuple[str, ...] = ("CHALLENGE", "CONCEDE", "PARTIALLY_CONCEDE")

# Stances that constitute accepting (some or all of) the targeted claim.
CONCEDING_STANCES: frozenset[str] = frozenset({"CONCEDE", "PARTIALLY_CONCEDE"})


class RebuttalOutputErrorCode:
    """Why a rebuttal response could not be used.

    Mirrors ``AdvocateOutputErrorCode`` and ``JudgeOutputErrorCode`` so one
    operator can read all three. Kept separate because a truncated rebuttal and
    a truncated Judge response call for different fixes.
    """

    MALFORMED_JSON = "MALFORMED_JSON"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    WRONG_SIDE = "WRONG_SIDE"
    OUTPUT_TRUNCATED = "OUTPUT_TRUNCATED"
    REBUTTAL_ERROR = "REBUTTAL_ERROR"


# ---------------------------------------------------------------------------
# Rebuttal input (allow-list)
# ---------------------------------------------------------------------------


class RebuttalCaseContext(RebuttalContextModel):
    """The ONLY information a rebuttal agent may ever see.

    The claim lists are named from the agent's own point of view (``own`` /
    ``opposing``) rather than ``rider`` / ``driver``, so the same prompt and the
    same schema serve both sides without a per-side branch.

    It deliberately never contains:

      - rejected claims from either side, in any form
      - the other side's rebuttal (there is only one round)
      - Judge output, of any round
      - ``resolutionRecommendation`` / ``refundAmount`` / confidence
      - ``escalationReasons``
      - ``analysisInput``
      - historical reputation, other cases, or conversation history

    ``resolution_mode`` is absent too. Unlike the Judge, a rebuttal agent does
    not need to know whether the case may be automated: it is arguing about
    claims, not deciding a remedy.
    """

    case_id: str = Field(serialization_alias="caseId", validation_alias="caseId")
    dispute_type: Literal["route_deviation", "no_show_charge"] = Field(
        serialization_alias="disputeType", validation_alias="disputeType"
    )
    currency: str
    own_side: AdvocateSide = Field(
        serialization_alias="ownSide", validation_alias="ownSide"
    )
    deterministic_facts: RouteDeviationFacts | NoShowFacts = Field(
        serialization_alias="deterministicFacts", validation_alias="deterministicFacts"
    )
    evidence: list[ContextEvidence]
    applicable_policy: ContextPolicy = Field(
        serialization_alias="applicablePolicy", validation_alias="applicablePolicy"
    )
    policy_evaluation: ContextPolicyEvaluation = Field(
        serialization_alias="policyEvaluation", validation_alias="policyEvaluation"
    )
    own_verified_claims: list[AdvocateClaim] = Field(
        default_factory=list,
        serialization_alias="ownVerifiedClaims",
        validation_alias="ownVerifiedClaims",
        description=(
            "This side's own verified claims, with namespaced IDs. Supplied so the "
            "agent knows what it already argued and does not rebut itself."
        ),
    )
    opposing_verified_claims: list[AdvocateClaim] = Field(
        default_factory=list,
        serialization_alias="opposingVerifiedClaims",
        validation_alias="opposingVerifiedClaims",
        description=(
            "The opposing side's verified claims, with namespaced IDs. These are the "
            "only valid rebuttal targets. Rejected opposing claims are absent by "
            "construction, so they cannot be rebutted."
        ),
    )
    allowed_stances: list[str] = Field(
        default_factory=lambda: list(STANCES),
        serialization_alias="allowedStances",
        validation_alias="allowedStances",
        description=(
            "The closed stance vocabulary. An enumeration, not a hint: it says which "
            "stances are expressible, never which one is correct."
        ),
    )
    rebuttal_round: int = Field(
        default=1, serialization_alias="rebuttalRound", validation_alias="rebuttalRound"
    )
    max_rebuttal_rounds: int = Field(
        default=MAX_REBUTTAL_ROUNDS,
        serialization_alias="maxRebuttalRounds",
        validation_alias="maxRebuttalRounds",
        description=(
            "Stated explicitly so the agent is told this is the only round. It is a "
            "description of the pipeline, not a setting."
        ),
    )


# ---------------------------------------------------------------------------
# Rebuttal output
# ---------------------------------------------------------------------------


class RebuttalResponse(RebuttalModel):
    """One side's response to one opposing verified claim.

    Every field is a reference to something that already exists in the trusted
    context, or a structured assertion checked against the deterministic
    analysis. There is no field for new evidence and no field for a remedy.
    """

    target_claim_id: str = Field(
        serialization_alias="targetClaimId", validation_alias="targetClaimId"
    )
    stance: RebuttalStance
    reasoning_summary: str = Field(
        serialization_alias="reasoningSummary", validation_alias="reasoningSummary"
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="evidenceIds",
        validation_alias="evidenceIds",
    )
    policy_rule_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="policyRuleIds",
        validation_alias="policyRuleIds",
    )
    asserted_facts: list[AssertedFact] = Field(
        default_factory=list,
        serialization_alias="assertedFacts",
        validation_alias="assertedFacts",
    )


class RebuttalOutput(RebuttalModel):
    """A side's complete rebuttal. Strictly validated before it is trusted.

    ``concessions`` holds *target claim IDs*, not prose. That makes it checkable:
    every entry must correspond to a response whose stance actually concedes
    something. A model that lists a claim as conceded while having challenged it
    is rejected rather than believed.
    """

    side: AdvocateSide
    responses: list[RebuttalResponse] = Field(default_factory=list)
    concessions: list[str] = Field(default_factory=list)
    overall_summary: str = Field(
        serialization_alias="overallSummary", validation_alias="overallSummary"
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


class RebuttalRejection(RebuttalModel):
    """A rejected rebuttal response, preserved exactly as produced.

    Never repaired, rewritten, or dropped. A hallucinated evidence ID such as
    ``E99`` stays rejected exactly as produced and remains visible to the human
    reviewer, marked as not used by the Judge.
    """

    target_claim_id: str | None = Field(
        default=None,
        serialization_alias="targetClaimId",
        validation_alias="targetClaimId",
    )
    stance: str | None = None
    reason: str
    detail: str
    evidence_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="evidenceIds",
        validation_alias="evidenceIds",
    )
    policy_rule_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="policyRuleIds",
        validation_alias="policyRuleIds",
    )


class VerifiedRebuttal(RebuttalModel):
    """A rebuttal response that passed every deterministic check.

    This is the only rebuttal shape that reaches the Judge. ``rebuttal_id`` is
    namespaced (``RIDER-RB1``) for the same reason claim IDs are: a bare ``RB1``
    from each side would be indistinguishable in a shared context.
    """

    rebuttal_id: str = Field(
        serialization_alias="rebuttalId", validation_alias="rebuttalId"
    )
    side: AdvocateSide
    target_claim_id: str = Field(
        serialization_alias="targetClaimId", validation_alias="targetClaimId"
    )
    target_claim_side: AdvocateSide = Field(
        serialization_alias="targetClaimSide", validation_alias="targetClaimSide"
    )
    stance: RebuttalStance
    reasoning_summary: str = Field(
        serialization_alias="reasoningSummary", validation_alias="reasoningSummary"
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="evidenceIds",
        validation_alias="evidenceIds",
    )
    policy_rule_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="policyRuleIds",
        validation_alias="policyRuleIds",
    )
    asserted_facts: list[AssertedFact] = Field(
        default_factory=list,
        serialization_alias="assertedFacts",
        validation_alias="assertedFacts",
    )
    verification_status: Literal["VERIFIED"] = Field(
        default="VERIFIED",
        serialization_alias="verificationStatus",
        validation_alias="verificationStatus",
    )


class RebuttalSideResult(RebuttalModel):
    """One side's rebuttal outcome, verified and rejected responses together."""

    side: AdvocateSide
    status: Literal["COMPLETE", "FAILED", "NOT_RUN"]
    overall_summary: str = ""
    verified_rebuttals: list[VerifiedRebuttal] = Field(
        default_factory=list,
        serialization_alias="verifiedRebuttals",
        validation_alias="verifiedRebuttals",
    )
    rejected_rebuttals: list[RebuttalRejection] = Field(
        default_factory=list,
        serialization_alias="rejectedRebuttals",
        validation_alias="rejectedRebuttals",
    )
    conceded_target_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="concededTargetIds",
        validation_alias="concededTargetIds",
        description=(
            "Derived by code from the verified stances, never copied from the "
            "model's own concession list."
        ),
    )
    failure_reason: str | None = Field(
        default=None, serialization_alias="failureReason", validation_alias="failureReason"
    )
    execution: AgentCallMetadata | None = None


class RebuttalVerificationSummary(VerificationSummary):
    """Verification counts across both rebuttals.

    Extends the Stage 4 summary rather than inventing a parallel shape, adding
    the one count that is specific to rebuttals: how many responses the model
    produced before verification.
    """

    generated_count: int = Field(
        default=0, serialization_alias="generatedCount", validation_alias="generatedCount"
    )


class RebuttalRunResponse(RebuttalModel):
    """The Stage 6 rebuttal layer, as surfaced.

    ``round`` and ``max_rounds`` are stated so a reader can see that exactly one
    round ran and that no further round exists to run.
    """

    case_id: str = Field(serialization_alias="caseId", validation_alias="caseId")
    dispute_type: Literal["route_deviation", "no_show_charge"] = Field(
        serialization_alias="disputeType", validation_alias="disputeType"
    )
    round: int = Field(
        default=MAX_REBUTTAL_ROUNDS,
        serialization_alias="round",
        validation_alias="round",
    )
    max_rounds: int = Field(
        default=MAX_REBUTTAL_ROUNDS,
        serialization_alias="maxRounds",
        validation_alias="maxRounds",
    )
    rider: RebuttalSideResult
    driver: RebuttalSideResult
    verification_summary: RebuttalVerificationSummary = Field(
        serialization_alias="verificationSummary",
        validation_alias="verificationSummary",
    )
    pipeline: list[PipelineStage] = Field(default_factory=list)


__all__ = [
    "CONCEDING_STANCES",
    "MAX_REBUTTAL_ROUNDS",
    "STANCES",
    "RebuttalCaseContext",
    "RebuttalContextModel",
    "RebuttalModel",
    "RebuttalOutput",
    "RebuttalOutputErrorCode",
    "RebuttalRejection",
    "RebuttalResponse",
    "RebuttalRunResponse",
    "RebuttalSideResult",
    "RebuttalStance",
    "RebuttalVerificationSummary",
    "VerifiedRebuttal",
]
