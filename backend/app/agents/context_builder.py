from __future__ import annotations

from app.models.agent import (
    AgentCaseContext,
    ContextEvidence,
    ContextPolicy,
    ContextPolicyEvaluation,
    ContextPolicyRule,
    ContextRouteCondition,
    ContextRuleEvaluation,
    ContextTimelineEvent,
    NoShowFacts,
    RouteDeviationFacts,
)
from app.models.analysis import (
    CaseAnalysisResponse,
    NoShowAnalysis,
    RouteDeviationAnalysis,
)
from app.models.case import DisputeCase
from app.policies import NO_SHOW_POLICY_V1, ROUTE_DEVIATION_POLICY_V1

# Human-readable descriptions for the prototype rules an advocate may cite.
ROUTE_RULE_DESCRIPTIONS = {
    "ROUTE_REQUIRED_EVIDENCE": "Required route, deviation, and fare evidence must be present.",
    "ROUTE_UNEXPLAINED_DEVIATION": "Unexplained route deviation must meet the prototype adjustment threshold.",
    "ROUTE_EVIDENCE_CONSISTENCY": "Critical route evidence must not be contradictory.",
}

NO_SHOW_RULE_DESCRIPTIONS = {
    "NO_SHOW_REQUIRED_EVIDENCE": "Arrival, waiting, and cancellation evidence must be present.",
    "NO_SHOW_PICKUP_RADIUS": "Driver must arrive within the configured prototype pickup radius.",
    "NO_SHOW_WAIT_TIME": "Driver must wait at least the configured prototype threshold.",
    "NO_SHOW_EVIDENCE_CONSISTENCY": "Critical GPS and timestamp evidence must not conflict.",
}


class AdvocateContextBuilder:
    """Builds the trusted AgentCaseContext using an explicit allow-list.

    This class is the safety boundary of Stage 4. It projects *only* the facts,
    evidence, and policy the advocates are allowed to see.

    It deliberately never copies:
      - resolution_recommendation (the answer)
      - refund_amount / recommended_action / ruling
      - confidence scores or penalties
      - resolution_mode / escalation_reasons
      - analysis_input (raw calculation source)
      - other cases, ratings, history, or fraud signals
    """

    def build(self, case: DisputeCase, analysis: CaseAnalysisResponse) -> AgentCaseContext:
        missing, conflicting = _evidence_flags(analysis)
        return AgentCaseContext(
            case_id=case.id,
            dispute_type=case.dispute_type,
            rider_complaint=case.rider_complaint,
            driver_response=case.driver_response,
            currency=case.fare.currency,
            timeline=[
                ContextTimelineEvent(
                    id=event.id,
                    timestamp=event.timestamp,
                    type=event.type,
                    description=event.description,
                    evidence_ids=list(event.evidence_ids),
                    severity=event.severity,
                )
                for event in case.timeline
            ],
            evidence=[
                ContextEvidence(
                    id=item.id,
                    type=item.type,
                    timestamp=item.timestamp,
                    source=item.source,
                    summary=item.summary,
                    status=item.status,
                )
                for item in case.evidence
            ],
            facts=self._facts(analysis),
            policy=self._policy(case),
            policy_evaluation=self._policy_evaluation(analysis),
            missing_evidence_ids=missing,
            conflicting_evidence_ids=conflicting,
        )

    @staticmethod
    def _facts(analysis: CaseAnalysisResponse) -> RouteDeviationFacts | NoShowFacts:
        facts = analysis.analysis
        if isinstance(facts, RouteDeviationAnalysis):
            return RouteDeviationFacts(
                expected_route_distance_km=facts.expected_route_distance_km,
                actual_route_distance_km=facts.actual_route_distance_km,
                distance_difference_km=facts.distance_difference_km,
                deviation_percentage=facts.deviation_percentage,
                expected_trip_duration_minutes=facts.expected_trip_duration_minutes,
                actual_trip_duration_minutes=facts.actual_trip_duration_minutes,
                duration_difference_minutes=facts.duration_difference_minutes,
                expected_fare=facts.expected_fare,
                actual_fare=facts.actual_fare,
                fare_difference=facts.fare_difference,
                explained_deviation_distance_km=facts.explained_deviation_distance_km,
                unexplained_deviation_distance_km=facts.unexplained_deviation_distance_km,
                verified_conditions=[
                    ContextRouteCondition(
                        type=condition.type,
                        explained_distance_km=condition.explained_distance_km,
                        evidence_ids=list(condition.evidence_ids),
                    )
                    for condition in facts.conditions
                ],
                supporting_evidence_ids=list(facts.supporting_evidence_ids),
            )
        if isinstance(facts, NoShowAnalysis):
            return NoShowFacts(
                driver_distance_to_pickup_meters=facts.driver_distance_to_pickup_meters,
                driver_within_pickup_radius=facts.driver_within_pickup_radius,
                driver_arrival_timestamp=facts.driver_arrival_timestamp,
                cancellation_timestamp=facts.cancellation_timestamp,
                waiting_duration_seconds=facts.waiting_duration_seconds,
                cancellation_charge_amount=facts.cancellation_charge_amount,
                rider_message_evidence_ids=list(facts.rider_message_evidence_ids),
                driver_message_evidence_ids=list(facts.driver_message_evidence_ids),
                supporting_evidence_ids=list(facts.supporting_evidence_ids),
            )
        raise ValueError(f"Unsupported dispute type for advocate context: {analysis.dispute_type}")

    @staticmethod
    def _policy(case: DisputeCase) -> ContextPolicy:
        if case.dispute_type == "route_deviation":
            policy = ROUTE_DEVIATION_POLICY_V1
            descriptions = ROUTE_RULE_DESCRIPTIONS
        else:
            policy = NO_SHOW_POLICY_V1
            descriptions = NO_SHOW_RULE_DESCRIPTIONS
        return ContextPolicy(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            dispute_type=case.dispute_type,
            rules=[
                ContextPolicyRule(rule_id=rule_id, description=description)
                for rule_id, description in descriptions.items()
            ],
        )

    @staticmethod
    def _policy_evaluation(analysis: CaseAnalysisResponse) -> ContextPolicyEvaluation:
        evaluation = analysis.policy_evaluation
        return ContextPolicyEvaluation(
            policy_id=evaluation.policy_id,
            policy_version=evaluation.policy_version,
            evaluated_rules=[
                ContextRuleEvaluation(
                    rule_id=rule.rule_id,
                    description=rule.description,
                    passed=rule.passed,
                    actual_value=rule.actual_value,
                    required_value=rule.required_value,
                    evidence_ids=list(rule.evidence_ids),
                )
                for rule in evaluation.evaluated_rules
            ],
            passed_rules=list(evaluation.passed_rules),
            failed_rules=list(evaluation.failed_rules),
            supporting_evidence_ids=list(evaluation.supporting_evidence_ids),
            overall_outcome=evaluation.overall_outcome,
        )


def _evidence_flags(analysis: CaseAnalysisResponse) -> tuple[list[str], list[str]]:
    facts = analysis.analysis
    return list(facts.missing_evidence_ids), (
        [] if not facts.contradictory_evidence else _conflicting_from_rules(analysis)
    )


def _conflicting_from_rules(analysis: CaseAnalysisResponse) -> list[str]:
    evaluation = analysis.policy_evaluation
    flagged: list[str] = []
    seen: set[str] = set()
    for rule in evaluation.evaluated_rules:
        if rule.passed:
            continue
        for evidence_id in rule.evidence_ids:
            if evidence_id not in seen:
                seen.add(evidence_id)
                flagged.append(evidence_id)
    return flagged
