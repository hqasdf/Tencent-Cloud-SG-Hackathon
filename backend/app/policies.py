from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteDeviationPolicy:
    policy_id: str
    version: str
    unexplained_deviation_threshold_km: float
    required_evidence_ids: tuple[str, ...]
    auto_resolve_confidence_threshold: float


@dataclass(frozen=True)
class NoShowPolicy:
    policy_id: str
    version: str
    pickup_radius_meters: float
    minimum_wait_seconds: int
    required_evidence_ids: tuple[str, ...]
    auto_resolve_confidence_threshold: float


@dataclass(frozen=True)
class ConfidencePolicy:
    maximum_base_confidence: float
    evidence_completeness_weight: float
    policy_clarity_weight: float
    factual_consistency_weight: float
    evidence_reference_weight: float
    contradiction_penalty: float
    missing_data_penalty: float
    invalid_evidence_penalty: float


# Prototype assumptions only; they are centralized for easy replacement with approved business policy.
ROUTE_DEVIATION_POLICY_V1 = RouteDeviationPolicy(
    policy_id="ROUTE_DEVIATION_POLICY_V1",
    version="1.0.0-prototype",
    unexplained_deviation_threshold_km=0.25,
    required_evidence_ids=("E03", "E04", "E05"),
    auto_resolve_confidence_threshold=0.75,
)

NO_SHOW_POLICY_V1 = NoShowPolicy(
    policy_id="NO_SHOW_POLICY_V1",
    version="1.0.0-prototype",
    pickup_radius_meters=100.0,
    minimum_wait_seconds=300,
    required_evidence_ids=("E02", "E04", "E05", "E06"),
    auto_resolve_confidence_threshold=0.75,
)

CONFIDENCE_POLICY_V1 = ConfidencePolicy(
    maximum_base_confidence=0.95,
    evidence_completeness_weight=0.35,
    policy_clarity_weight=0.25,
    factual_consistency_weight=0.25,
    evidence_reference_weight=0.15,
    contradiction_penalty=0.30,
    missing_data_penalty=0.20,
    invalid_evidence_penalty=0.25,
)
