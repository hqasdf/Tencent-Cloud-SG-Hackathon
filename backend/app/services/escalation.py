from __future__ import annotations

from app.models.analysis import DeterministicConfidence, NoShowAnalysis, PolicyEvaluation, RouteDeviationAnalysis
from app.services.evidence_validation import EvidenceValidationResult


class EscalationEngine:
    def decide(
        self,
        analysis: RouteDeviationAnalysis | NoShowAnalysis,
        policy_evaluation: PolicyEvaluation,
        confidence: DeterministicConfidence,
        evidence_validation: EvidenceValidationResult,
        auto_resolve_threshold: float,
    ) -> tuple[str, list[str]]:
        reasons: list[str] = []
        if analysis.contradictory_evidence:
            reasons.append("CONTRADICTORY_EVIDENCE")
        if analysis.missing_evidence_ids:
            reasons.append("MISSING_CRITICAL_EVIDENCE")
        if not evidence_validation.valid:
            reasons.append("INVALID_EVIDENCE_REFERENCES")
        if policy_evaluation.overall_outcome == "POLICY_NOT_APPLICABLE":
            reasons.append("POLICY_NOT_CLEARLY_APPLICABLE")
        if confidence.overall_confidence < auto_resolve_threshold:
            reasons.append("LOW_CONFIDENCE")
        return ("HUMAN_REVIEW", reasons) if reasons else ("AUTO_RESOLVE", [])
