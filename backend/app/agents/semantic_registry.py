"""Deterministic semantic support registry for Advocate propositions.

This registry maps structured fact names to evidence metadata and registers the
small set of causal relationships code actually calculates. It never parses
evidence summaries or model prose. A citation is useful only when its typed case
record can establish the specific fact being asserted.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.agent import AgentCaseContext


FACT_EVIDENCE_TYPES: dict[str, frozenset[str]] = {
    # Route and duration measurements come from route telemetry.
    "EXPECTED_DISTANCE_KM": frozenset({"gps"}),
    "ACTUAL_DISTANCE_KM": frozenset({"gps"}),
    "DISTANCE_DIFFERENCE_KM": frozenset({"gps"}),
    "DEVIATION_PERCENTAGE": frozenset({"gps"}),
    "EXPECTED_DURATION_MINUTES": frozenset({"gps"}),
    "ACTUAL_DURATION_MINUTES": frozenset({"gps"}),
    "DURATION_DIFFERENCE_MINUTES": frozenset({"gps"}),
    # The quote is recorded at booking; final fare and difference are ledger data.
    "EXPECTED_FARE": frozenset({"trip event", "fare"}),
    "ACTUAL_FARE": frozenset({"fare"}),
    "FARE_DIFFERENCE": frozenset({"fare"}),
    # Explained/unexplained distance is calculated from GPS and verified conditions.
    "EXPLAINED_DEVIATION_KM": frozenset({"gps", "traffic", "chat"}),
    "UNEXPLAINED_DEVIATION_KM": frozenset({"gps", "traffic", "chat"}),
    # No-show measurements.
    "DRIVER_PICKUP_DISTANCE_METERS": frozenset({"gps"}),
    "WITHIN_PICKUP_RADIUS": frozenset({"gps"}),
    "WAITING_DURATION_SECONDS": frozenset({"trip event", "gps", "chat"}),
    "CANCELLATION_CHARGE_AMOUNT": frozenset({"fare", "trip event"}),
}


FACT_LABELS: dict[str, str] = {
    "EXPECTED_DISTANCE_KM": "Expected route distance",
    "ACTUAL_DISTANCE_KM": "Actual route distance",
    "DISTANCE_DIFFERENCE_KM": "Route distance difference",
    "DEVIATION_PERCENTAGE": "Route deviation percentage",
    "EXPECTED_DURATION_MINUTES": "Expected trip duration",
    "ACTUAL_DURATION_MINUTES": "Actual trip duration",
    "DURATION_DIFFERENCE_MINUTES": "Trip duration difference",
    "EXPECTED_FARE": "Quoted fare",
    "ACTUAL_FARE": "Final fare",
    "FARE_DIFFERENCE": "Fare difference",
    "EXPLAINED_DEVIATION_KM": "Explained route deviation",
    "UNEXPLAINED_DEVIATION_KM": "Unexplained route deviation",
    "DRIVER_PICKUP_DISTANCE_METERS": "Driver distance from pickup",
    "WITHIN_PICKUP_RADIUS": "Driver within pickup radius",
    "WAITING_DURATION_SECONDS": "Waiting duration",
    "CANCELLATION_CHARGE_AMOUNT": "Cancellation charge",
}


@dataclass(frozen=True)
class CausalRelation:
    relation_id: str
    dispute_type: str
    cause_facts: frozenset[str]
    effect_fact: str
    summary: str


CAUSAL_RELATIONS: dict[str, CausalRelation] = {
    "ROUTE_DISTANCES_DETERMINE_DISTANCE_DIFFERENCE": CausalRelation(
        relation_id="ROUTE_DISTANCES_DETERMINE_DISTANCE_DIFFERENCE",
        dispute_type="route_deviation",
        cause_facts=frozenset({"EXPECTED_DISTANCE_KM", "ACTUAL_DISTANCE_KM"}),
        effect_fact="DISTANCE_DIFFERENCE_KM",
        summary="Code calculates route distance difference from expected and actual route distance.",
    ),
    "ROUTE_DISTANCES_DETERMINE_DEVIATION_PERCENTAGE": CausalRelation(
        relation_id="ROUTE_DISTANCES_DETERMINE_DEVIATION_PERCENTAGE",
        dispute_type="route_deviation",
        cause_facts=frozenset({"EXPECTED_DISTANCE_KM", "ACTUAL_DISTANCE_KM"}),
        effect_fact="DEVIATION_PERCENTAGE",
        summary="Code calculates deviation percentage from expected and actual route distance.",
    ),
    "FARES_DETERMINE_FARE_DIFFERENCE": CausalRelation(
        relation_id="FARES_DETERMINE_FARE_DIFFERENCE",
        dispute_type="route_deviation",
        cause_facts=frozenset({"EXPECTED_FARE", "ACTUAL_FARE"}),
        effect_fact="FARE_DIFFERENCE",
        summary="Code calculates fare difference from quoted and final fare.",
    ),
    "PICKUP_DISTANCE_DETERMINES_RADIUS_STATUS": CausalRelation(
        relation_id="PICKUP_DISTANCE_DETERMINES_RADIUS_STATUS",
        dispute_type="no_show_charge",
        cause_facts=frozenset({"DRIVER_PICKUP_DISTANCE_METERS"}),
        effect_fact="WITHIN_PICKUP_RADIUS",
        summary="Code compares pickup distance with the configured radius.",
    ),
}


def supporting_evidence_ids(
    context: AgentCaseContext,
    fact: str,
    candidates: list[str] | None = None,
) -> list[str]:
    """Return usable cited evidence whose typed record can support ``fact``."""

    allowed_types = FACT_EVIDENCE_TYPES.get(fact, frozenset())
    candidate_set = set(candidates) if candidates is not None else None
    return [
        item.id
        for item in context.evidence
        if (candidate_set is None or item.id in candidate_set)
        and item.status == "Verified"
        and item.type.strip().lower() in allowed_types
    ]


def fact_summary(fact: str, value: object) -> str:
    """Render trusted factual text from code-owned fact names and values."""

    return f"{FACT_LABELS.get(fact, fact)} is {value}."


__all__ = [
    "CAUSAL_RELATIONS",
    "FACT_EVIDENCE_TYPES",
    "CausalRelation",
    "fact_summary",
    "supporting_evidence_ids",
]
