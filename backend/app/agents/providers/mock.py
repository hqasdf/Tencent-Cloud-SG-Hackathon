from __future__ import annotations

import json

from app.agents.provider import AgentCompletionRequest, LlmCompletion
from app.models.agent import AgentCaseContext, NoShowFacts, RouteDeviationFacts


class MockLlmProvider:
    """Deterministic, offline advocate provider.

    Output is *derived* from the supplied context rather than being a fixed
    string, so mock mode stays realistic as fixtures evolve while remaining
    fully deterministic for tests. No network access, no credentials.
    """

    name = "mock"

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        context = _read_context(request)
        side = request.metadata.get("side", "RIDER")
        payload = _rider_output(context) if side == "RIDER" else _driver_output(context)
        return LlmCompletion(
            raw_text=json.dumps(payload, indent=2),
            provider_name=self.name,
            model_name=None,
            duration_ms=0,
        )


def _read_context(request: AgentCompletionRequest) -> AgentCaseContext:
    raw = request.metadata.get("context_json")
    if not raw:
        raise ValueError("MockLlmProvider requires context_json metadata")
    return AgentCaseContext.model_validate_json(raw)


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
