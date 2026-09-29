"""Stage 5 Judge contracts.

The Judge is the third AI role in RydeResolve, and the one most able to do harm:
unlike an advocate, it produces something a human is meant to act on. Everything
in this module exists to keep that power bounded.

Three rules shape the design.

1. **The Judge reasons; code decides.** ``JudgeOutput`` carries a recommendation
   and its justification. It carries no executable instruction, and the
   deterministic layer that actually authorises an action is represented
   separately by ``DeterministicResolution``.

2. **No monetary field exists.** ``JudgeOutput`` has no amount field at all, and
   the model is configured to *forbid* unknown keys. A refund cannot be smuggled
   through a field that does not exist, and a hallucinated ``refundAmount`` key
   becomes a schema failure rather than a silently ignored extra.

3. **Rejected claims are not input.** ``JudgeCaseContext`` is built from an
   allow-list that only ever includes *verified* claims. A rejected claim cannot
   reach the Judge prompt because there is no field to carry it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.advocate import (
    AdvocateClaim,
    AdvocateSideResult,
    PipelineStage,
    VerificationSummary,
)
from app.models.agent import (
    AgentCallMetadata,
    AgentMode,
    AgentRunMetadata,
    ContextEvidence,
    ContextPolicy,
    ContextPolicyEvaluation,
    NoShowFacts,
    RouteDeviationFacts,
)
from app.models.explanation import DecisionCounterfactual, DecisionExplanation
from app.models.rebuttal import RebuttalRunResponse, VerifiedRebuttal
from app.models.replay import ReplayMetadata

JudgeModelBase = BaseModel


class JudgeModel(BaseModel):
    """Base for Judge-facing contracts.

    ``extra="forbid"`` is load-bearing rather than tidiness. It is what turns an
    invented ``refundAmount`` or ``finalCharge`` key from a silently discarded
    extra into a validation failure, which is the difference between the Judge
    being unable to state a monetary remedy and merely being asked not to.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class JudgeCaseModel(BaseModel):
    """Base for the context the Judge receives.

    Deliberately lenient about extra keys: this model is *built by us* from an
    allow-list, so it is never parsed from untrusted input. Strictness belongs on
    the Judge's output, not on our own construction path.
    """

    model_config = ConfigDict(populate_by_name=True)


# ---------------------------------------------------------------------------
# Status and outcomes
# ---------------------------------------------------------------------------

JudgeStatus = Literal["NOT_RUN", "COMPLETE", "FAILED", "PENDING_HUMAN_REVIEW"]
"""Explicit Judge states.

``NOT_RUN`` and ``FAILED`` are never interchangeable with ``COMPLETE``. A Judge
that was never reached, or whose output was unusable, must not be presented as
having decided anything.
"""

JudgeSkipReason = Literal["ADVOCATE_INPUT_INCOMPLETE"]
"""Why the Judge did not run at all.

A missing advocate side is not a Judge failure — the Judge was never asked. The
distinction matters because "the Judge failed" invites a retry, while "an input
was missing" invites fixing the input.
"""

JudgeOutcome = Literal[
    "NO_REFUND",
    "PARTIAL_REFUND",
    "FULL_FARE_DIFFERENCE_REFUND",
    "UPHOLD_CANCELLATION_CHARGE",
    "REFUND_CANCELLATION_CHARGE",
]
"""Outcomes the Judge may recommend.

These are exactly the non-escalation values ``ResolutionEngine`` can emit, so the
Judge cannot introduce a second remedy vocabulary. ``HUMAN_REVIEW`` is excluded
on purpose: escalation is a deterministic decision, and a model that could
recommend it could also appear to authorise it.
"""

# Outcomes permitted per dispute type. A route case cannot "uphold a cancellation
# charge", and a no-show case has no fare deviation to refund.
JUDGE_ALLOWED_OUTCOMES: dict[str, frozenset[str]] = {
    "route_deviation": frozenset(
        {"NO_REFUND", "PARTIAL_REFUND", "FULL_FARE_DIFFERENCE_REFUND"}
    ),
    "no_show_charge": frozenset(
        {"UPHOLD_CANCELLATION_CHARGE", "REFUND_CANCELLATION_CHARGE"}
    ),
}

# The deterministic actions that move money, mapped to the outcomes the Judge is
# allowed to recommend. Used only to check that the Judge's recommendation does
# not contradict a deterministic refusal to automate.
_EXECUTABLE_ACTIONS = frozenset(
    {
        "NO_REFUND",
        "PARTIAL_REFUND",
        "FULL_FARE_DIFFERENCE_REFUND",
        "UPHOLD_CANCELLATION_CHARGE",
        "REFUND_CANCELLATION_CHARGE",
    }
)


class JudgeOutputErrorCode:
    """Why a Judge response could not be used.

    Mirrors ``AdvocateOutputErrorCode`` so the same operator can read both. Kept
    separate because a truncated Judge response and a truncated advocate response
    call for different fixes.
    """

    MALFORMED_JSON = "MALFORMED_JSON"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    OUTPUT_TRUNCATED = "OUTPUT_TRUNCATED"
    JUDGE_ERROR = "JUDGE_ERROR"


# ---------------------------------------------------------------------------
# Judge input (allow-list)
# ---------------------------------------------------------------------------


class JudgeSideClaims(JudgeCaseModel):
    """One side's *verified* claims, and whether there were any.

    ``verified_claim_count`` is stated explicitly even though it is derivable
    from the list, because the zero-claim case is the one that must be
    unmistakable in the prompt: an advocate that completed with nothing trusted
    is very different from one whose claims were all rejected, and the Judge must
    not be left to infer which it is looking at.

    The camelCase aliases matter here for the same reason they matter everywhere
    else in the prompt: this object is serialized into the Judge's context, and a
    payload that mixes ``verifiedClaims`` with ``verified_claim_count`` invites a
    model producing structured output to echo the wrong convention back.
    """

    side: Literal["RIDER", "DRIVER"]
    verified_claim_count: int = Field(
        serialization_alias="verifiedClaimCount", validation_alias="verifiedClaimCount"
    )
    has_verified_claims: bool = Field(
        serialization_alias="hasVerifiedClaims", validation_alias="hasVerifiedClaims"
    )
    verified_claims: list[AdvocateClaim] = Field(
        default_factory=list,
        serialization_alias="verifiedClaims",
        validation_alias="verifiedClaims",
    )


class JudgeCaseContext(JudgeCaseModel):
    """The ONLY information the Judge may ever see.

    Produced by an explicit allow-list projection, exactly like
    ``AgentCaseContext``. It deliberately never contains:

      - ``resolutionRecommendation`` / ``recommendedAction`` / ``ruling``
      - ``refundAmount`` or any calculated money
      - confidence scores or penalties
      - ``escalationReasons`` (the *mode* is supplied; the reasons are the answer)
      - ``analysisInput`` (raw calculation source)
      - rejected claims, in any form
      - frontend decision text, hidden Judge criteria, other cases, or history

    ``resolution_mode`` is the one piece of deterministic decision state that is
    included, because the Judge cannot know whether an executable decision is
    even permitted without it. It says *whether* automation is allowed, never
    *what* the answer is.
    """

    case_id: str = Field(serialization_alias="caseId", validation_alias="caseId")
    dispute_type: Literal["route_deviation", "no_show_charge"] = Field(
        serialization_alias="disputeType", validation_alias="disputeType"
    )
    currency: str
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
    rider: JudgeSideClaims
    driver: JudgeSideClaims
    verified_rider_rebuttals: list[VerifiedRebuttal] = Field(
        default_factory=list,
        serialization_alias="verifiedRiderRebuttals",
        validation_alias="verifiedRiderRebuttals",
        description=(
            "The Rider's rebuttal responses that passed deterministic verification. "
            "Rejected rebuttals are absent entirely — not summarised, not labelled, "
            "not counted."
        ),
    )
    verified_driver_rebuttals: list[VerifiedRebuttal] = Field(
        default_factory=list,
        serialization_alias="verifiedDriverRebuttals",
        validation_alias="verifiedDriverRebuttals",
        description=(
            "The Driver's rebuttal responses that passed deterministic verification."
        ),
    )
    resolution_mode: Literal["AUTO_RESOLVE", "HUMAN_REVIEW"] = Field(
        serialization_alias="resolutionMode", validation_alias="resolutionMode"
    )
    allowed_outcomes: list[str] = Field(
        default_factory=list,
        serialization_alias="allowedOutcomes",
        validation_alias="allowedOutcomes",
        description=(
            "The complete outcome vocabulary for this dispute type. Supplied so "
            "the model chooses from a closed set instead of inventing a remedy. "
            "It is an enumeration, not a hint: it says which outcomes are "
            "expressible, never which one is correct."
        ),
    )


# ---------------------------------------------------------------------------
# Judge output
# ---------------------------------------------------------------------------


class JudgeOutput(JudgeModel):
    """The Judge's structured response. Strictly validated before it is trusted.

    There is no amount field and no action field. The Judge recommends an outcome
    from a fixed vocabulary; the deterministic layer supplies every number and
    decides whether the recommendation may be actioned at all.
    """

    status: Literal["COMPLETE", "PENDING_HUMAN_REVIEW"]
    recommended_outcome: JudgeOutcome = Field(
        serialization_alias="recommendedOutcome", validation_alias="recommendedOutcome"
    )
    accepted_rider_claim_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="acceptedRiderClaimIds",
        validation_alias="acceptedRiderClaimIds",
    )
    accepted_driver_claim_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="acceptedDriverClaimIds",
        validation_alias="acceptedDriverClaimIds",
    )
    rejected_rider_claim_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="rejectedRiderClaimIds",
        validation_alias="rejectedRiderClaimIds",
    )
    rejected_driver_claim_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="rejectedDriverClaimIds",
        validation_alias="rejectedDriverClaimIds",
    )
    reasoning_summary: str = Field(
        serialization_alias="reasoningSummary", validation_alias="reasoningSummary"
    )
    considered_rider_rebuttal_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="consideredRiderRebuttalIds",
        validation_alias="consideredRiderRebuttalIds",
        description=(
            "Rider rebuttals the Judge took into account. Optional: a Judge may "
            "reach a conclusion without relying on any rebuttal, and an empty list "
            "is a valid answer rather than a failure to engage."
        ),
    )
    considered_driver_rebuttal_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="consideredDriverRebuttalIds",
        validation_alias="consideredDriverRebuttalIds",
    )
    evidence_ids: list[str] = Field(
        default_factory=list, serialization_alias="evidenceIds", validation_alias="evidenceIds"
    )
    policy_rule_ids: list[str] = Field(
        default_factory=list, serialization_alias="policyRuleIds", validation_alias="policyRuleIds"
    )
    uncertainties: list[str] = Field(default_factory=list)
    requires_human_review: bool = Field(
        serialization_alias="requiresHumanReview", validation_alias="requiresHumanReview"
    )


class JudgeValidationIssue(JudgeModel):
    code: str
    detail: str


class JudgeValidationResult(JudgeModel):
    valid: bool
    issues: list[JudgeValidationIssue] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Judge result, as surfaced
# ---------------------------------------------------------------------------


class JudgeResult(JudgeModel):
    """The validated Judge outcome.

    ``status`` is computed by code, not copied from the model. When the
    deterministic layer requires human review the status is
    ``PENDING_HUMAN_REVIEW`` regardless of what the model claimed, and an
    executable recommendation is refused rather than downgraded silently.
    """

    status: JudgeStatus
    skip_reason: JudgeSkipReason | None = Field(
        default=None, serialization_alias="skipReason", validation_alias="skipReason"
    )
    recommended_outcome: str | None = Field(
        default=None, serialization_alias="recommendedOutcome", validation_alias="recommendedOutcome"
    )
    executable: bool = False
    accepted_rider_claim_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="acceptedRiderClaimIds",
        validation_alias="acceptedRiderClaimIds",
    )
    accepted_driver_claim_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="acceptedDriverClaimIds",
        validation_alias="acceptedDriverClaimIds",
    )
    rejected_rider_claim_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="rejectedRiderClaimIds",
        validation_alias="rejectedRiderClaimIds",
    )
    rejected_driver_claim_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="rejectedDriverClaimIds",
        validation_alias="rejectedDriverClaimIds",
    )
    reasoning_summary: str = Field(
        default="", serialization_alias="reasoningSummary", validation_alias="reasoningSummary"
    )
    considered_rider_rebuttal_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="consideredRiderRebuttalIds",
        validation_alias="consideredRiderRebuttalIds",
    )
    considered_driver_rebuttal_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="consideredDriverRebuttalIds",
        validation_alias="consideredDriverRebuttalIds",
    )
    evidence_ids: list[str] = Field(
        default_factory=list, serialization_alias="evidenceIds", validation_alias="evidenceIds"
    )
    policy_rule_ids: list[str] = Field(
        default_factory=list, serialization_alias="policyRuleIds", validation_alias="policyRuleIds"
    )
    uncertainties: list[str] = Field(default_factory=list)
    requires_human_review: bool = Field(
        default=False, serialization_alias="requiresHumanReview", validation_alias="requiresHumanReview"
    )
    failure_reason: str | None = Field(
        default=None, serialization_alias="failureReason", validation_alias="failureReason"
    )
    validation_issues: list[JudgeValidationIssue] = Field(
        default_factory=list, serialization_alias="validationIssues", validation_alias="validationIssues"
    )
    execution: AgentCallMetadata | None = None


# ---------------------------------------------------------------------------
# Deterministic layer, and the final orchestration object
# ---------------------------------------------------------------------------


class DeterministicResolution(JudgeModel):
    """What code is actually willing to do. The authoritative half of the result.

    Every field here comes from ``ResolutionEngine`` / ``ConfidenceEngine`` /
    ``EscalationEngine`` and is computed without reference to the Judge. The
    Judge may agree or disagree with ``recommended_action``; it can never change
    it, and it can never change ``refund_amount``.
    """

    ruling: str
    recommended_action: str = Field(
        serialization_alias="recommendedAction", validation_alias="recommendedAction"
    )
    refund_amount: float = Field(
        serialization_alias="refundAmount", validation_alias="refundAmount"
    )
    currency: str
    resolution_mode: Literal["AUTO_RESOLVE", "HUMAN_REVIEW"] = Field(
        serialization_alias="resolutionMode", validation_alias="resolutionMode"
    )
    confidence: float
    escalation_reasons: list[str] = Field(
        default_factory=list,
        serialization_alias="escalationReasons",
        validation_alias="escalationReasons",
    )
    explanation: str
    counterfactual_explanation: str = Field(
        serialization_alias="counterfactualExplanation",
        validation_alias="counterfactualExplanation",
    )


class AuditEvent(JudgeModel):
    """An observable pipeline event. Never carries secrets or model reasoning."""

    event: str
    timestamp: str
    metadata: dict[str, object] = Field(default_factory=dict)


class CaseResolutionResponse(JudgeModel):
    """The Stage 6 orchestration result.

    The layers are separate top-level fields on purpose: ``rider`` / ``driver``
    hold the initial arguments, ``rebuttals`` holds the cross-examination, and
    ``judge`` and ``deterministic_resolution`` hold the advisory and the
    authoritative conclusions. Merging any of them would blur which layer
    produced which statement, and merging the last two would make an advisory
    recommendation look like an authorisation — the single most dangerous
    presentation error this pipeline could make.

    ``explanation`` and ``counterfactual`` are deterministic projections. They
    are siblings of ``judge`` rather than children of it, because they are
    computed without reference to the Judge at all.
    """

    case_id: str = Field(serialization_alias="caseId", validation_alias="caseId")
    dispute_type: Literal["route_deviation", "no_show_charge"] = Field(
        serialization_alias="disputeType", validation_alias="disputeType"
    )
    rider: AdvocateSideResult
    driver: AdvocateSideResult
    rebuttals: RebuttalRunResponse
    agent_run: AgentRunMetadata = Field(
        serialization_alias="agentRun", validation_alias="agentRun"
    )
    verification_summary: VerificationSummary = Field(
        serialization_alias="verificationSummary", validation_alias="verificationSummary"
    )
    judge: JudgeResult
    deterministic_resolution: DeterministicResolution = Field(
        serialization_alias="deterministicResolution",
        validation_alias="deterministicResolution",
    )
    explanation: DecisionExplanation = Field(
        serialization_alias="explanation", validation_alias="explanation"
    )
    counterfactual: DecisionCounterfactual = Field(
        serialization_alias="counterfactual", validation_alias="counterfactual"
    )
    replay_metadata: ReplayMetadata = Field(
        default_factory=ReplayMetadata,
        serialization_alias="replayMetadata",
        validation_alias="replayMetadata",
        description=(
            "Provenance for this run. Present on every response, including a "
            "fully live one, so the absence of replay is stated rather than "
            "inferred. The frontend badges a replayed run from this field and "
            "never has to guess whether a model was actually called."
        ),
    )
    pipeline: list[PipelineStage] = Field(default_factory=list)
    audit: list[AuditEvent] = Field(default_factory=list)


__all__ = [
    "AuditEvent",
    "CaseResolutionResponse",
    "DecisionCounterfactual",
    "DecisionExplanation",
    "DeterministicResolution",
    "JUDGE_ALLOWED_OUTCOMES",
    "JudgeCaseContext",
    "JudgeModel",
    "JudgeOutcome",
    "JudgeOutput",
    "JudgeOutputErrorCode",
    "JudgeResult",
    "JudgeSideClaims",
    "JudgeSkipReason",
    "JudgeStatus",
    "JudgeValidationIssue",
    "JudgeValidationResult",
]
