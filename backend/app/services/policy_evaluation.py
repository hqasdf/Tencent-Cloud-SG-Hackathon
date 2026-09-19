from __future__ import annotations

from app.models.analysis import NoShowAnalysis, PolicyEvaluation, RouteDeviationAnalysis, RuleEvaluation
from app.policies import NoShowPolicy, RouteDeviationPolicy


class PolicyEvaluationService:
    def evaluate_route(self, analysis: RouteDeviationAnalysis, policy: RouteDeviationPolicy) -> PolicyEvaluation:
        required_evidence = RuleEvaluation(
            rule_id="ROUTE_REQUIRED_EVIDENCE",
            description="Required route, deviation, and fare evidence must be present.",
            passed=not analysis.missing_evidence_ids,
            actual_value=len(analysis.required_evidence_ids) - len(analysis.missing_evidence_ids),
            required_value=len(analysis.required_evidence_ids),
            evidence_ids=analysis.required_evidence_ids,
        )
        unexplained_threshold = RuleEvaluation(
            rule_id="ROUTE_UNEXPLAINED_DEVIATION",
            description="Unexplained route deviation must meet the prototype adjustment threshold.",
            passed=analysis.unexplained_deviation_distance_km >= policy.unexplained_deviation_threshold_km,
            actual_value=analysis.unexplained_deviation_distance_km,
            required_value=policy.unexplained_deviation_threshold_km,
            evidence_ids=analysis.supporting_evidence_ids,
        )
        consistent_evidence = RuleEvaluation(
            rule_id="ROUTE_EVIDENCE_CONSISTENCY",
            description="Critical route evidence must not be contradictory.",
            passed=not analysis.contradictory_evidence,
            actual_value=analysis.contradictory_evidence,
            required_value=False,
            evidence_ids=analysis.required_evidence_ids,
        )
        rules = [required_evidence, unexplained_threshold, consistent_evidence]
        if not required_evidence.passed or not consistent_evidence.passed:
            outcome = "POLICY_NOT_APPLICABLE"
        elif unexplained_threshold.passed:
            outcome = "ELIGIBLE_ROUTE_ADJUSTMENT"
        else:
            outcome = "NO_ELIGIBLE_UNEXPLAINED_DEVIATION"
        return self._response(policy.policy_id, policy.version, rules, analysis.supporting_evidence_ids, outcome)

    def evaluate_no_show(self, analysis: NoShowAnalysis, policy: NoShowPolicy) -> PolicyEvaluation:
        required_evidence = RuleEvaluation(
            rule_id="NO_SHOW_REQUIRED_EVIDENCE",
            description="Arrival, waiting, and cancellation evidence must be present.",
            passed=not analysis.missing_evidence_ids,
            actual_value=len(analysis.required_evidence_ids) - len(analysis.missing_evidence_ids),
            required_value=len(analysis.required_evidence_ids),
            evidence_ids=analysis.required_evidence_ids,
        )
        arrival_radius = RuleEvaluation(
            rule_id="NO_SHOW_PICKUP_RADIUS",
            description="Driver must arrive within the configured prototype pickup radius.",
            passed=analysis.driver_within_pickup_radius,
            actual_value=analysis.driver_distance_to_pickup_meters,
            required_value=policy.pickup_radius_meters,
            evidence_ids=["E02"],
        )
        waiting = RuleEvaluation(
            rule_id="NO_SHOW_WAIT_TIME",
            description="Driver must wait at least the configured prototype threshold.",
            passed=analysis.waiting_duration_seconds >= policy.minimum_wait_seconds,
            actual_value=analysis.waiting_duration_seconds,
            required_value=policy.minimum_wait_seconds,
            evidence_ids=["E04", "E07"],
        )
        consistency = RuleEvaluation(
            rule_id="NO_SHOW_EVIDENCE_CONSISTENCY",
            description="Critical GPS and timestamp evidence must not conflict.",
            passed=not analysis.contradictory_evidence,
            actual_value=analysis.contradictory_evidence,
            required_value=False,
            evidence_ids=analysis.required_evidence_ids,
        )
        rules = [required_evidence, arrival_radius, waiting, consistency]
        if not required_evidence.passed or not consistency.passed:
            outcome = "POLICY_NOT_APPLICABLE"
        elif arrival_radius.passed and waiting.passed:
            outcome = "UPHOLD_CANCELLATION_CHARGE"
        else:
            outcome = "REFUND_CANCELLATION_CHARGE"
        return self._response(policy.policy_id, policy.version, rules, analysis.supporting_evidence_ids, outcome)

    @staticmethod
    def _response(policy_id: str, version: str, rules: list[RuleEvaluation], evidence_ids: list[str], outcome: str) -> PolicyEvaluation:
        return PolicyEvaluation(
            policy_id=policy_id,
            policy_version=version,
            evaluated_rules=rules,
            passed_rules=[rule.rule_id for rule in rules if rule.passed],
            failed_rules=[rule.rule_id for rule in rules if not rule.passed],
            supporting_evidence_ids=evidence_ids,
            overall_outcome=outcome,
        )
