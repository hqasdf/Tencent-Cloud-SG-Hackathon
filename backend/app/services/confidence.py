from __future__ import annotations

from app.models.analysis import DeterministicConfidence, NoShowAnalysis, PolicyEvaluation, RouteDeviationAnalysis
from app.policies import ConfidencePolicy
from app.services.evidence_validation import EvidenceValidationResult


class ConfidenceEngine:
    def calculate(
        self,
        analysis: RouteDeviationAnalysis | NoShowAnalysis,
        policy_evaluation: PolicyEvaluation,
        evidence_validation: EvidenceValidationResult,
        policy: ConfidencePolicy,
    ) -> DeterministicConfidence:
        required_count = len(analysis.required_evidence_ids)
        evidence_completeness = (required_count - len(analysis.missing_evidence_ids)) / required_count if required_count else 0
        policy_clarity = 0.0 if policy_evaluation.overall_outcome == "POLICY_NOT_APPLICABLE" else 1.0
        factual_consistency = 0.5 if analysis.contradictory_evidence else 1.0
        reference_quality = 1.0 if evidence_validation.valid else 0.0
        contradiction_penalty = policy.contradiction_penalty if analysis.contradictory_evidence else 0.0
        missing_penalty = policy.missing_data_penalty if analysis.missing_evidence_ids else 0.0
        invalid_penalty = policy.invalid_evidence_penalty if not evidence_validation.valid else 0.0
        weighted = (
            evidence_completeness * policy.evidence_completeness_weight
            + policy_clarity * policy.policy_clarity_weight
            + factual_consistency * policy.factual_consistency_weight
            + reference_quality * policy.evidence_reference_weight
        )
        overall = max(0.0, min(1.0, policy.maximum_base_confidence * weighted - contradiction_penalty - missing_penalty - invalid_penalty))
        return DeterministicConfidence(
            evidence_completeness=round(evidence_completeness, 2),
            policy_clarity=round(policy_clarity, 2),
            factual_consistency=round(factual_consistency, 2),
            invalid_evidence_penalty=round(invalid_penalty, 2),
            contradiction_penalty=round(contradiction_penalty, 2),
            missing_data_penalty=round(missing_penalty, 2),
            overall_confidence=round(overall, 2),
            prototype_notice="Prototype operational confidence metric: deterministic routing heuristic, not a statistically calibrated probability.",
        )
