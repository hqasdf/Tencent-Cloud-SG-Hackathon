from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.agent import AdvocateSide, AgentCallMetadata, AgentRunMetadata, ClaimImportance


class AdvocateModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class AssertedFact(AdvocateModel):
    """A structured, machine-checkable factual assertion made by an agent.

    This is the HARD verification layer. Each asserted fact is compared against
    the already-computed trusted value. Agents never recalculate anything.
    """

    fact: str
    value: float | int | bool | str


ATOMIC_PROPOSITION_VERSION = "atomic-propositions-v2"
PropositionKind = Literal["FACT", "CAUSAL", "POLICY_APPLICATION", "ADVOCACY_POSITION"]
PropositionStatus = Literal["VERIFIED", "REJECTED", "ARGUMENT_ONLY"]
VerificationLevel = Literal[
    "ATOMIC_PROPOSITIONS_VERIFIED",
    "LEGACY_STRUCTURAL_ONLY",
    "UNVERIFIED",
]


def _proposition_generation_schema(schema: dict) -> None:
    """Expose verifier-required shapes without losing malformed-output auditing.

    The local record remains loadable for rejection/audit of historical output.
    Generation must satisfy one kind-specific branch; semantic truth is still
    checked exclusively by the deterministic verifier.
    """
    from copy import deepcopy

    required_by_kind = {
        "FACT": ("fact", "value", "operator", "evidenceIds"),
        "CAUSAL": ("causeFacts", "effectFact", "relationId", "evidenceIds"),
        "POLICY_APPLICATION": ("policyRuleId", "assertedRulePassed", "evidenceIds"),
        "ADVOCACY_POSITION": ("position",),
    }
    branches = []
    for kind, required in required_by_kind.items():
        properties = {"kind": {"type": "string", "const": kind}}
        for name in required:
            field = deepcopy(schema["properties"][name])
            field.pop("default", None)
            if "anyOf" in field:
                field["anyOf"] = [item for item in field["anyOf"] if item.get("type") != "null"]
            if field.get("type") == "string":
                field["minLength"] = 1
            if field.get("type") == "array":
                field["minItems"] = 1
            properties[name] = field
        branches.append({"type": "object", "properties": properties,
                         "required": ["kind", *required]})
    schema["anyOf"] = branches


def _advocate_generation_schema(schema: dict) -> None:
    """New generation requires the version the live parser already enforces."""
    field = schema["properties"]["contractVersion"]
    field.pop("default", None)
    field["anyOf"] = [item for item in field["anyOf"] if item.get("type") != "null"]
    schema["required"].append("contractVersion")


class ClaimProposition(AdvocateModel):
    """One atomic assertion supplied by an Advocate.

    The shape is intentionally flat. Gemini receives a small schema containing
    ordinary objects, enums and arrays; Python then enforces the kind-specific
    requirements. Free-form ``claim`` text remains an audit record and is never
    the object trusted by the Judge.
    """
    model_config = ConfigDict(populate_by_name=True, json_schema_extra=_proposition_generation_schema)

    proposition_id: str = Field(
        serialization_alias="propositionId", validation_alias="propositionId"
    )
    kind: PropositionKind
    fact: str | None = None
    value: float | int | bool | str | None = None
    operator: Literal["EQUALS"] | None = None
    evidence_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="evidenceIds",
        validation_alias="evidenceIds",
    )
    cause_facts: list[str] = Field(
        default_factory=list,
        serialization_alias="causeFacts",
        validation_alias="causeFacts",
    )
    effect_fact: str | None = Field(
        default=None, serialization_alias="effectFact", validation_alias="effectFact"
    )
    relation_id: str | None = Field(
        default=None, serialization_alias="relationId", validation_alias="relationId"
    )
    policy_rule_id: str | None = Field(
        default=None,
        serialization_alias="policyRuleId",
        validation_alias="policyRuleId",
    )
    asserted_rule_passed: bool | None = Field(
        default=None,
        serialization_alias="assertedRulePassed",
        validation_alias="assertedRulePassed",
    )
    asserted_policy_outcome: str | None = Field(
        default=None,
        serialization_alias="assertedPolicyOutcome",
        validation_alias="assertedPolicyOutcome",
    )
    position: str | None = None


class PropositionVerification(AdvocateModel):
    """Server-owned result for one proposition; never accepted from a model."""

    proposition_id: str = Field(
        serialization_alias="propositionId", validation_alias="propositionId"
    )
    kind: PropositionKind
    status: PropositionStatus
    reason: str | None = None
    detail: str
    trusted_summary: str | None = Field(
        default=None,
        serialization_alias="trustedSummary",
        validation_alias="trustedSummary",
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="evidenceIds",
        validation_alias="evidenceIds",
    )
    policy_rule_ids: list[str] = Field(
        default_factory=list,
        serialization_alias="policyRuleIds",
        validation_alias="policyRuleIds",
    )


class AdvocateClaim(AdvocateModel):
    claim_id: str = Field(serialization_alias="claimId", validation_alias="claimId")
    claim: str
    evidence_ids: list[str] = Field(serialization_alias="evidenceIds", validation_alias="evidenceIds")
    policy_refs: list[str] = Field(serialization_alias="policyRefs", validation_alias="policyRefs")
    reasoning_summary: str = Field(serialization_alias="reasoningSummary", validation_alias="reasoningSummary")
    importance: ClaimImportance
    asserted_facts: list[AssertedFact] = Field(default_factory=list, serialization_alias="assertedFacts", validation_alias="assertedFacts")
    disputed_evidence_ids: list[str] = Field(default_factory=list, serialization_alias="disputedEvidenceIds", validation_alias="disputedEvidenceIds")
    propositions: list[ClaimProposition] = Field(default_factory=list)


class AdvocateOutput(AdvocateModel):
    """Shared output schema. Rider and Driver use the EXACT same schema.

    There is deliberately no `position` field: the side already expresses who is
    advocating, so a separate supports/opposes axis would be redundant.
    """
    model_config = ConfigDict(populate_by_name=True, json_schema_extra=_advocate_generation_schema)

    contract_version: Literal["atomic-propositions-v2"] | None = Field(
        default=None,
        serialization_alias="contractVersion",
        validation_alias="contractVersion",
        description=(
            "Required for new provider output. Missing means a historical pre-atomic "
            "artifact whose claims receive legacy structural-only classification."
        ),
    )
    side: AdvocateSide
    summary: str
    claims: list[AdvocateClaim]
    requested_outcome: str = Field(serialization_alias="requestedOutcome", validation_alias="requestedOutcome")
    context_acknowledged: bool = Field(serialization_alias="contextAcknowledged", validation_alias="contextAcknowledged")


class ClaimRejection(AdvocateModel):
    """A rejected claim, kept visible for audit and debugging.

    Rejected claims are NEVER repaired, rewritten, or dropped. A hallucinated
    evidence ID such as E99 stays rejected exactly as produced.
    """

    claim_id: str = Field(serialization_alias="claimId", validation_alias="claimId")
    claim: str
    reason: str
    detail: str
    evidence_ids: list[str] = Field(default_factory=list, serialization_alias="evidenceIds", validation_alias="evidenceIds")
    policy_refs: list[str] = Field(default_factory=list, serialization_alias="policyRefs", validation_alias="policyRefs")
    verification_level: VerificationLevel = Field(
        default="UNVERIFIED",
        serialization_alias="verificationLevel",
        validation_alias="verificationLevel",
    )
    propositions: list[PropositionVerification] = Field(default_factory=list)


class VerifiedClaim(AdvocateModel):
    """Audit-facing claim whose structured propositions passed code checks.

    ``claim`` is the model's original wording and remains visible for audit. It
    is never sent to the Judge. ``trusted_summary`` is assembled by code from the
    verified propositions and is the only textual claim projection downstream.
    """

    claim_id: str = Field(serialization_alias="claimId", validation_alias="claimId")
    claim: str
    trusted_summary: str = Field(
        serialization_alias="trustedSummary", validation_alias="trustedSummary"
    )
    evidence_ids: list[str] = Field(serialization_alias="evidenceIds", validation_alias="evidenceIds")
    policy_refs: list[str] = Field(serialization_alias="policyRefs", validation_alias="policyRefs")
    reasoning_summary: str = Field(serialization_alias="reasoningSummary", validation_alias="reasoningSummary")
    importance: ClaimImportance
    asserted_facts: list[AssertedFact] = Field(default_factory=list, serialization_alias="assertedFacts", validation_alias="assertedFacts")
    disputed_evidence_ids: list[str] = Field(default_factory=list, serialization_alias="disputedEvidenceIds", validation_alias="disputedEvidenceIds")
    verification_level: Literal["ATOMIC_PROPOSITIONS_VERIFIED"] = Field(
        default="ATOMIC_PROPOSITIONS_VERIFIED",
        serialization_alias="verificationLevel",
        validation_alias="verificationLevel",
    )
    propositions: list[PropositionVerification] = Field(default_factory=list)


class TrustedClaim(AdvocateModel):
    """Minimal claim projection supplied to Rebuttal agents and the Judge."""

    claim_id: str = Field(serialization_alias="claimId", validation_alias="claimId")
    claim: str = Field(description="Code-generated summary of verified propositions.")
    evidence_ids: list[str] = Field(serialization_alias="evidenceIds", validation_alias="evidenceIds")
    policy_refs: list[str] = Field(serialization_alias="policyRefs", validation_alias="policyRefs")
    importance: ClaimImportance
    asserted_facts: list[AssertedFact] = Field(
        default_factory=list,
        serialization_alias="assertedFacts",
        validation_alias="assertedFacts",
    )
    propositions: list[PropositionVerification] = Field(default_factory=list)


class ClaimWarning(AdvocateModel):
    """A non-blocking observation. Prose contradictions are warnings only."""

    claim_id: str = Field(serialization_alias="claimId", validation_alias="claimId")
    code: str
    detail: str


class AdvocateSideResult(AdvocateModel):
    side: AdvocateSide
    status: Literal["COMPLETE", "FAILED"]
    summary: str
    requested_outcome: str | None = Field(default=None, serialization_alias="requestedOutcome", validation_alias="requestedOutcome")
    context_acknowledged: bool = Field(default=False, serialization_alias="contextAcknowledged", validation_alias="contextAcknowledged")
    verified_claims: list[VerifiedClaim] = Field(default_factory=list, serialization_alias="verifiedClaims", validation_alias="verifiedClaims")
    rejected_claims: list[ClaimRejection] = Field(default_factory=list, serialization_alias="rejectedClaims", validation_alias="rejectedClaims")
    warnings: list[ClaimWarning] = Field(default_factory=list)
    failure_reason: str | None = Field(default=None, serialization_alias="failureReason", validation_alias="failureReason")
    execution: AgentCallMetadata | None = None


class VerificationSummary(AdvocateModel):
    verified_count: int = Field(serialization_alias="verifiedCount", validation_alias="verifiedCount")
    rejected_count: int = Field(serialization_alias="rejectedCount", validation_alias="rejectedCount")
    rejection_reasons: list[str] = Field(default_factory=list, serialization_alias="rejectionReasons", validation_alias="rejectionReasons")


class PipelineStage(AdvocateModel):
    stage: str
    status: Literal["COMPLETE", "FAILED", "NOT_RUN"]


class AdvocateRunResponse(AdvocateModel):
    case_id: str = Field(serialization_alias="caseId", validation_alias="caseId")
    dispute_type: Literal["route_deviation", "no_show_charge"] = Field(serialization_alias="disputeType", validation_alias="disputeType")
    rider: AdvocateSideResult
    driver: AdvocateSideResult
    agent_run: AgentRunMetadata = Field(serialization_alias="agentRun", validation_alias="agentRun")
    verification_summary: VerificationSummary = Field(serialization_alias="verificationSummary", validation_alias="verificationSummary")
    pipeline: list[PipelineStage]
