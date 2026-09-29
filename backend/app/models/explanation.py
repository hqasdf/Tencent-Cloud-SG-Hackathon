"""Deterministic explanation and counterfactual contracts.

Stage 6 strengthens "why this decision?" and "what would have changed it?"
without asking a model to write either. Both are projections of data that
already exists: the verified claims, the verified rebuttals, the policy
evaluation and the deterministic recommendation.

Two rules shape this module.

1. **No new factual claims.** Every statement here is assembled from values the
   deterministic layer already produced. An explanation service that could
   generate new facts would be a second, unverified analysis engine.

2. **No invented thresholds.** A counterfactual may only name a threshold that
   exists in the PolicyTwin evaluation. "If the waiting time had been under 300
   seconds" is legitimate because 300 is the policy's own `requiredValue`.
   "Maybe the rider called the driver" is not a counterfactual, it is a story,
   and there is no field here that could carry one.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExplanationModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class ExplainedClaim(ExplanationModel):
    """A verified claim that the decision rests on."""

    claim_id: str = Field(serialization_alias="claimId", validation_alias="claimId")
    side: Literal["RIDER", "DRIVER"]
    claim: str
    accepted: bool = Field(
        default=True,
        description=(
            "Whether the Judge's validated output accepted this claim. False means "
            "the claim was verified as a factual assertion but the Judge did not "
            "rely on it."
        ),
    )


class ExplainedRebuttal(ExplanationModel):
    """A verified rebuttal relevant to the decision.

    Only verified rebuttals appear. A rejected rebuttal is never part of the
    explanation of a decision, because it was never part of the decision.
    """

    rebuttal_id: str = Field(
        serialization_alias="rebuttalId", validation_alias="rebuttalId"
    )
    side: Literal["RIDER", "DRIVER"]
    target_claim_id: str = Field(
        serialization_alias="targetClaimId", validation_alias="targetClaimId"
    )
    stance: str
    reasoning_summary: str = Field(
        serialization_alias="reasoningSummary", validation_alias="reasoningSummary"
    )
    considered_by_judge: bool = Field(
        default=False,
        serialization_alias="consideredByJudge",
        validation_alias="consideredByJudge",
        description=(
            "Whether the Judge cited this rebuttal. A rebuttal can be verified and "
            "still not cited, and saying so is more honest than implying every "
            "verified rebuttal carried weight."
        ),
    )


class ExplainedFact(ExplanationModel):
    """A deterministic fact, with the policy threshold it was measured against.

    ``threshold`` is copied from the PolicyTwin rule's ``requiredValue``, never
    invented, and is ``None`` for rules that are not threshold rules (evidence
    presence, evidence consistency).
    """

    fact: str
    value: float | int | bool | str
    rule_id: str = Field(serialization_alias="ruleId", validation_alias="ruleId")
    threshold: float | int | bool | str | None = None
    passed: bool
    description: str


class ExplainedRule(ExplanationModel):
    """One PolicyTwin rule and how it resolved."""

    rule_id: str = Field(serialization_alias="ruleId", validation_alias="ruleId")
    description: str
    passed: bool
    actual_value: float | int | bool | str = Field(
        serialization_alias="actualValue", validation_alias="actualValue"
    )
    required_value: float | int | bool | str = Field(
        serialization_alias="requiredValue", validation_alias="requiredValue"
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="evidenceIds",
        validation_alias="evidenceIds",
    )


class DecisionExplanation(ExplanationModel):
    """Why the decision came out the way it did.

    Assembled from trusted references and deterministic execution. The
    ``judge_advisory_outcome`` field is included for contrast and is explicitly
    *not* the decision: it is what the AI recommended, which the deterministic
    layer may or may not agree with.
    """

    accepted_claims: list[ExplainedClaim] = Field(
        default_factory=list,
        serialization_alias="acceptedClaims",
        validation_alias="acceptedClaims",
    )
    relevant_rebuttals: list[ExplainedRebuttal] = Field(
        default_factory=list,
        serialization_alias="relevantRebuttals",
        validation_alias="relevantRebuttals",
    )
    decisive_facts: list[ExplainedFact] = Field(
        default_factory=list,
        serialization_alias="decisiveFacts",
        validation_alias="decisiveFacts",
    )
    policy_rules: list[ExplainedRule] = Field(
        default_factory=list,
        serialization_alias="policyRules",
        validation_alias="policyRules",
    )
    final_deterministic_action: str = Field(
        serialization_alias="finalDeterministicAction",
        validation_alias="finalDeterministicAction",
    )
    ruling: str
    deterministic_basis: str = Field(
        serialization_alias="deterministicBasis",
        validation_alias="deterministicBasis",
        description=(
            "The deterministic engine's own explanation. Copied, never paraphrased "
            "by a model."
        ),
    )
    judge_advisory_outcome: str | None = Field(
        default=None,
        serialization_alias="judgeAdvisoryOutcome",
        validation_alias="judgeAdvisoryOutcome",
    )
    judge_advisory_differs: bool = Field(
        default=False,
        serialization_alias="judgeAdvisoryDiffers",
        validation_alias="judgeAdvisoryDiffers",
        description=(
            "True when the AI's recommendation and the deterministic action "
            "disagree. Surfaced rather than hidden: a disagreement is exactly what "
            "a reviewer needs to see."
        ),
    )


class CounterfactualThreshold(ExplanationModel):
    """One policy threshold and what crossing it would have meant.

    ``direction`` is derived from how the rule is evaluated, not chosen: a
    minimum-wait rule fails when the wait goes *below* its threshold, while a
    pickup-radius rule fails when the distance goes *above* it.
    """

    rule_id: str = Field(serialization_alias="ruleId", validation_alias="ruleId")
    fact: str
    actual_value: float | int | bool | str = Field(
        serialization_alias="actualValue", validation_alias="actualValue"
    )
    threshold: float | int | bool | str = Field(
        serialization_alias="threshold", validation_alias="threshold"
    )
    direction: Literal["BELOW", "ABOVE"]
    passed: bool
    statement: str


class DecisionCounterfactual(ExplanationModel):
    """What would have changed the decision.

    Every entry is a policy threshold from the PolicyTwin evaluation. There is no
    field for a hypothetical event, because a hypothetical event is not a
    counterfactual a deterministic engine can reason about.
    """

    thresholds: list[CounterfactualThreshold] = Field(
        default_factory=list,
        serialization_alias="thresholds",
        validation_alias="thresholds",
    )
    statement: str
    deterministic_basis: str = Field(
        serialization_alias="deterministicBasis",
        validation_alias="deterministicBasis",
        description="The deterministic engine's own counterfactual text, copied.",
    )
    generated_by: Literal["DETERMINISTIC_ENGINE"] = Field(
        default="DETERMINISTIC_ENGINE",
        serialization_alias="generatedBy",
        validation_alias="generatedBy",
        description=(
            "Stated explicitly so a reader can tell this was not written by the "
            "model. No AI-generated counterfactual exists in this system."
        ),
    )


__all__ = [
    "CounterfactualThreshold",
    "DecisionCounterfactual",
    "DecisionExplanation",
    "ExplainedClaim",
    "ExplainedFact",
    "ExplainedRebuttal",
    "ExplainedRule",
]
