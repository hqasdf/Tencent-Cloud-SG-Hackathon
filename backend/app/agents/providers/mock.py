from __future__ import annotations

import json

from app.agents.provider import AgentCompletionRequest, LlmCompletion
from app.models.agent import AgentCaseContext, NoShowFacts, RouteDeviationFacts
from app.models.judge import JudgeCaseContext
from app.models.rebuttal import RebuttalCaseContext


class MockLlmProvider:
    """Deterministic, offline provider for the advocates, rebuttals and the Judge.

    Output is *derived* from the supplied context rather than being a fixed
    string, so mock mode stays realistic as fixtures evolve while remaining
    fully deterministic for tests. No network access, no credentials.

    The role is read from the request metadata, so one provider serves every
    agent type without any of them knowing a mock exists.
    """

    name = "mock"

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        role = request.metadata.get("role")
        if role == "judge":
            payload = _judge_output(_read_judge_context(request))
        elif role == "rebuttal":
            payload = _rebuttal_output(_read_rebuttal_context(request))
        else:
            payload = _advocate_output(request)
        return LlmCompletion(
            raw_text=json.dumps(payload, indent=2),
            provider_name=self.name,
            model_name=None,
            duration_ms=0,
        )


def _advocate_output(request: AgentCompletionRequest) -> dict:
    context = _read_context(request)
    side = request.metadata.get("side", "RIDER")
    return _rider_output(context) if side == "RIDER" else _driver_output(context)


def _read_context(request: AgentCompletionRequest) -> AgentCaseContext:
    raw = request.metadata.get("context_json")
    if not raw:
        raise ValueError("MockLlmProvider requires context_json metadata")
    return AgentCaseContext.model_validate_json(raw)


def _read_judge_context(request: AgentCompletionRequest) -> JudgeCaseContext:
    raw = request.metadata.get("context_json")
    if not raw:
        raise ValueError("MockLlmProvider requires context_json metadata")
    return JudgeCaseContext.model_validate_json(raw)


def _read_rebuttal_context(request: AgentCompletionRequest) -> RebuttalCaseContext:
    raw = request.metadata.get("context_json")
    if not raw:
        raise ValueError("MockLlmProvider requires context_json metadata")
    return RebuttalCaseContext.model_validate_json(raw)


def _rebuttal_output(context: RebuttalCaseContext) -> dict:
    """A schema-valid rebuttal derived from the opposing verified claims.

    Every reference is taken from the context, so the mock cannot produce
    something the real validator would reject. That matters: a mock that emits
    invalid references would make mock mode stop rehearsing the real pipeline.

    The first opposing claim is challenged and the rest are conceded. That is a
    deliberate, deterministic split rather than a random one, and it exercises
    both stances in a single run so the concession path is covered by the
    end-to-end flow.
    """
    policy_rule_ids = [rule.rule_id for rule in context.applicable_policy.rules]
    first_rule = policy_rule_ids[0] if policy_rule_ids else ""

    responses = []
    concessions = []
    for index, claim in enumerate(context.opposing_verified_claims):
        stance = "CHALLENGE" if index == 0 else "CONCEDE"
        if stance != "CHALLENGE":
            concessions.append(claim.claim_id)
        if stance == "CHALLENGE":
            reasoning = (
                "This states a measured value accurately, but a measurement on its own "
                "does not establish the outcome that "
                + context.own_side
                + " is asking the decision to reach."
            )
        else:
            reasoning = (
                "This states a measured value that matches the trusted record, so it is "
                "accepted as stated."
            )
        responses.append(
            {
                "targetClaimId": claim.claim_id,
                # Echo the target's own evidence so the reference is valid by
                # construction. No new evidence is introduced in rebuttal.
                "evidenceIds": list(claim.evidence_ids),
                "policyRuleIds": [first_rule] if first_rule else [],
                "reasoningSummary": reasoning,
                "stance": stance,
                "assertedFacts": [
                    {"fact": item.fact, "value": item.value}
                    for item in claim.asserted_facts[:1]
                ],
            }
        )

    if not context.opposing_verified_claims:
        summary = (
            "The opposing side has no verified claim in this record, so there is "
            "nothing to respond to."
        )
    else:
        summary = (
            f"{len(responses)} of the opposing side's verified claims were addressed: "
            "one is challenged as insufficient to support the outcome sought, and the "
            "remainder are accepted as accurate statements of the trusted record."
        )

    return {
        "side": context.own_side,
        "responses": responses,
        "concessions": concessions,
        "overallSummary": summary,
    }


def _judge_output(context: JudgeCaseContext) -> dict:
    """A schema-valid Judge decision derived from the trusted facts.

    Deliberately *not* a copy of the deterministic recommendation: it reaches an
    outcome by reading the same facts a real Judge would, which keeps mock mode
    an honest rehearsal of the pipeline rather than a replay of the answer.

    The reasoning text contains no currency token. A real Judge is forbidden from
    stating an amount, and the mock must not normalise something the validator
    would reject from a live model.
    """
    facts = context.deterministic_facts

    if isinstance(facts, RouteDeviationFacts):
        unexplained = facts.unexplained_deviation_distance_km
        outcome = "PARTIAL_REFUND" if unexplained > 0 else "NO_REFUND"
        if unexplained > 0:
            reasoning = (
                f"The measured deviation is {facts.deviation_percentage}% and "
                f"{unexplained} km of it has no verified explanation in the record, so an "
                "adjustment is supported by the facts."
            )
        else:
            reasoning = (
                "Every part of the measured deviation is covered by a verified route "
                "condition, so the record does not support an adjustment."
            )
    else:
        within = facts.driver_within_pickup_radius
        outcome = (
            "UPHOLD_CANCELLATION_CHARGE" if within else "REFUND_CANCELLATION_CHARGE"
        )
        reasoning = (
            "The driver was recorded "
            + ("inside" if within else "outside")
            + " the pickup radius and the recorded waiting duration is "
            + ("above" if within else "below")
            + " the policy threshold, so the charge is "
            + ("supported" if within else "not supported")
            + " by the record."
        )

    # The deterministic gate decides whether any of this may be automated.
    pending = context.resolution_mode == "HUMAN_REVIEW"
    if pending:
        reasoning += (
            " A deterministic gate requires human review, so this is an advisory "
            "summary and not an executable outcome."
        )

    evidence_ids = sorted({item.id for item in context.evidence})
    policy_rule_ids = [rule.rule_id for rule in context.applicable_policy.rules]

    return {
        "status": "PENDING_HUMAN_REVIEW" if pending else "COMPLETE",
        "recommendedOutcome": outcome,
        "acceptedRiderClaimIds": [c.claim_id for c in context.rider.verified_claims],
        "acceptedDriverClaimIds": [c.claim_id for c in context.driver.verified_claims],
        "rejectedRiderClaimIds": [],
        "rejectedDriverClaimIds": [],
        # Every verified rebuttal is cited, so the reference path is exercised
        # end to end in mock mode. A real Judge may cite none.
        "consideredRiderRebuttalIds": [
            item.rebuttal_id for item in context.verified_rider_rebuttals
        ],
        "consideredDriverRebuttalIds": [
            item.rebuttal_id for item in context.verified_driver_rebuttals
        ],
        "reasoningSummary": reasoning,
        "evidenceIds": evidence_ids,
        "policyRuleIds": policy_rule_ids,
        "uncertainties": [],
        "requiresHumanReview": pending,
    }


def _first_evidence(context: AgentCaseContext, *statuses: str) -> list[str]:
    wanted = set(statuses) or {"Verified"}
    return [item.id for item in context.evidence if item.status in wanted]


def _rider_output(context: AgentCaseContext) -> dict:
    facts = context.facts
    if isinstance(facts, RouteDeviationFacts):
        claims = [
            {
                "claimId": "R1",
                "claim": (
                    f"The actual route was {facts.actual_route_distance_km} km against an expected "
                    f"{facts.expected_route_distance_km} km, a {facts.deviation_percentage}% deviation."
                ),
                "evidenceIds": facts.supporting_evidence_ids,
                "policyRefs": [context.policy.rules[0].rule_id],
                "reasoningSummary": (
                    "The measured distance difference is a trusted calculation and is not covered "
                    "in full by verified route conditions."
                ),
                "importance": "HIGH",
                "assertedFacts": [
                    {"fact": "DEVIATION_PERCENTAGE", "value": facts.deviation_percentage},
                    {"fact": "DISTANCE_DIFFERENCE_KM", "value": facts.distance_difference_km},
                ],
                "disputedEvidenceIds": [],
            }
        ]
        if facts.unexplained_deviation_distance_km > 0:
            claims.append(
                {
                    "claimId": "R2",
                    "claim": (
                        f"{facts.unexplained_deviation_distance_km} km of the deviation has no verified "
                        "explanation in the case record."
                    ),
                    "evidenceIds": facts.supporting_evidence_ids,
                    "policyRefs": [context.policy.rules[0].rule_id],
                    "reasoningSummary": (
                        "Explained deviation is capped by verified conditions only, leaving the "
                        "remainder unexplained."
                    ),
                    "importance": "HIGH",
                    "assertedFacts": [
                        {
                            "fact": "UNEXPLAINED_DEVIATION_KM",
                            "value": facts.unexplained_deviation_distance_km,
                        }
                    ],
                    "disputedEvidenceIds": [],
                }
            )
        if facts.explained_deviation_distance_km > 0:
            claims.append(
                {
                    "claimId": "R3",
                    "claim": (
                        f"{facts.explained_deviation_distance_km} km of the deviation is explained by "
                        "verified conditions, which weakens part of the rider's position."
                    ),
                    "evidenceIds": facts.supporting_evidence_ids,
                    "policyRefs": [context.policy.rules[0].rule_id],
                    "reasoningSummary": "Adverse fact acknowledged rather than concealed.",
                    "importance": "MEDIUM",
                    "assertedFacts": [
                        {
                            "fact": "EXPLAINED_DEVIATION_KM",
                            "value": facts.explained_deviation_distance_km,
                        }
                    ],
                    "disputedEvidenceIds": [],
                }
            )
        outcome = (
            "PARTIAL_REFUND"
            if facts.unexplained_deviation_distance_km > 0
            else "NO_REFUND"
        )
        summary = (
            "The rider relies on the measured route deviation and the unexplained portion "
            "of the fare difference."
            if facts.unexplained_deviation_distance_km > 0
            else "The rider cannot rely on unexplained deviation: verified conditions account for the route."
        )
    else:
        facts = facts
        claims = [
            {
                "claimId": "R1",
                "claim": (
                    f"The driver was {facts.driver_distance_to_pickup_meters} m from the pickup point "
                    f"and waited {facts.waiting_duration_seconds} seconds."
                ),
                "evidenceIds": facts.supporting_evidence_ids,
                "policyRefs": [context.policy.rules[0].rule_id],
                "reasoningSummary": (
                    "If the arrival or waiting threshold is not met, the cancellation charge is "
                    "not justified."
                ),
                "importance": "HIGH",
                "assertedFacts": [
                    {"fact": "WAITING_DURATION_SECONDS", "value": facts.waiting_duration_seconds},
                    {
                        "fact": "DRIVER_PICKUP_DISTANCE_METERS",
                        "value": facts.driver_distance_to_pickup_meters,
                    },
                ],
                "disputedEvidenceIds": list(context.conflicting_evidence_ids),
            }
        ]
        outcome = (
            "UPHOLD_CHARGE"
            if facts.driver_within_pickup_radius and not context.conflicting_evidence_ids
            else "REFUND_CHARGE"
        )
        summary = (
            "The rider questions whether the arrival and waiting record justifies the charge."
        )
    return {
        "side": "RIDER",
        "summary": summary,
        "claims": claims,
        "requestedOutcome": outcome,
        "contextAcknowledged": True,
    }


def _driver_output(context: AgentCaseContext) -> dict:
    facts = context.facts
    if isinstance(facts, RouteDeviationFacts):
        condition_types = [condition.type for condition in facts.verified_conditions]
        condition_evidence = [
            evidence_id
            for condition in facts.verified_conditions
            for evidence_id in condition.evidence_ids
        ]
        claims = [
            {
                "claimId": "D1",
                "claim": (
                    f"Verified route conditions explain {facts.explained_deviation_distance_km} km "
                    f"of the {facts.distance_difference_km} km deviation."
                    if condition_types
                    else "No verified route condition explains the measured deviation."
                ),
                "evidenceIds": condition_evidence or facts.supporting_evidence_ids,
                "policyRefs": [context.policy.rules[0].rule_id],
                "reasoningSummary": (
                    "Only conditions backed by verified evidence reduce the explained deviation."
                ),
                "importance": "HIGH",
                "assertedFacts": [
                    {
                        "fact": "EXPLAINED_DEVIATION_KM",
                        "value": facts.explained_deviation_distance_km,
                    }
                ],
                "disputedEvidenceIds": [],
            }
        ]
        if facts.unexplained_deviation_distance_km > 0:
            claims.append(
                {
                    "claimId": "D2",
                    "claim": (
                        f"{facts.unexplained_deviation_distance_km} km of deviation remains "
                        "unexplained by verified conditions."
                    ),
                    "evidenceIds": condition_evidence or facts.supporting_evidence_ids,
                    "policyRefs": [context.policy.rules[0].rule_id],
                    "reasoningSummary": "Adverse fact acknowledged rather than concealed.",
                    "importance": "HIGH",
                    "assertedFacts": [
                        {
                            "fact": "UNEXPLAINED_DEVIATION_KM",
                            "value": facts.unexplained_deviation_distance_km,
                        }
                    ],
                    "disputedEvidenceIds": [],
                }
            )
        return {
            "side": "DRIVER",
            "summary": (
                f"The driver relies on {len(condition_types)} verified route condition(s) covering part of the deviation."
                if condition_types
                else "The driver has no verified route condition to rely on in this record."
            ),
            "claims": claims,
            "requestedOutcome": "NO_REFUND" if condition_types else "HUMAN_REVIEW",
            "contextAcknowledged": True,
        }

    claims = [
        {
            "claimId": "D1",
            "claim": (
                f"The driver arrived {facts.driver_distance_to_pickup_meters} m from the pickup point, "
                f"{'inside' if facts.driver_within_pickup_radius else 'outside'} the required radius, "
                f"and waited {facts.waiting_duration_seconds} seconds."
            ),
            "evidenceIds": facts.supporting_evidence_ids,
            "policyRefs": [context.policy.rules[0].rule_id],
            "reasoningSummary": "The trusted arrival and waiting measurements decide the no-show rule.",
            "importance": "HIGH",
            "assertedFacts": [
                {
                    "fact": "WITHIN_PICKUP_RADIUS",
                    "value": facts.driver_within_pickup_radius,
                },
                {"fact": "WAITING_DURATION_SECONDS", "value": facts.waiting_duration_seconds},
            ],
            "disputedEvidenceIds": list(context.conflicting_evidence_ids),
        }
    ]
    if context.conflicting_evidence_ids:
        claims.append(
            {
                "claimId": "D2",
                "claim": (
                    "Critical arrival evidence is recorded as conflicting, so the no-show "
                    "charge cannot be fully established from this record."
                ),
                "evidenceIds": list(context.conflicting_evidence_ids),
                "policyRefs": [context.policy.rules[0].rule_id],
                "reasoningSummary": "Adverse fact acknowledged rather than concealed.",
                "importance": "HIGH",
                "assertedFacts": [],
                "disputedEvidenceIds": list(context.conflicting_evidence_ids),
            }
        )
    return {
        "side": "DRIVER",
        "summary": (
            "The driver relies on the recorded arrival inside the pickup radius and the waiting duration."
            if facts.driver_within_pickup_radius and not context.conflicting_evidence_ids
            else "The driver acknowledges conflicting arrival evidence limits the case for the charge."
        ),
        "claims": claims,
        "requestedOutcome": (
            "UPHOLD_CHARGE"
            if facts.driver_within_pickup_radius and not context.conflicting_evidence_ids
            else "HUMAN_REVIEW"
        ),
        "contextAcknowledged": True,
    }
