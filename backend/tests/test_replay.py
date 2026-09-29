"""Stage 6B replay tests — entirely offline.

No live Gemini call, and no live call of any kind, is made anywhere in this file.

Three layers keep that true, and the third is specific to this stage:

1. ``conftest.py`` forces ``AGENT_MODE=mock`` for the session.
2. Every test that could reach a provider injects its own through the
   orchestrator's ``provider`` seam. Several inject one that *raises on any
   call*, so "replay made no provider call" is proven by the test failing rather
   than by a count that could be misread.
3. Artefacts are written to ``tmp_path``, never to the repository's
   ``replay_artifacts/``. A test must not be able to leave a stale artefact
   behind that a later developer run would happily load.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agents.claim_verification import AdvocateClaimVerificationService
from app.agents.config import AgentSettings
from app.agents.context_builder import AdvocateContextBuilder
from app.agents.judge_validation import JudgeOutputValidationService
from app.agents.provider import AgentCompletionRequest, LlmCompletion
from app.agents.providers.mock import MockLlmProvider
from app.agents.rebuttal_validation import RebuttalValidationService
from app.benchmarks.cases import case_by_id
from app.main import app
from app.models.judge import JudgeValidationResult
from app.models.replay import (
    ANALYSIS_HASH_MISMATCH,
    ARTIFACT_NOT_FOUND,
    CASE_ID_MISMATCH,
    CLAIM_REVALIDATION_FAILED,
    DISPUTE_TYPE_MISMATCH,
    JUDGE_REVALIDATION_FAILED,
    MALFORMED_ARTIFACT,
    REBUTTAL_REVALIDATION_FAILED,
    REPLAY_VERSION_UNSUPPORTED,
    SCHEMA_VERSION_MISMATCH,
    TRUSTED_CONTEXT_HASH_MISMATCH,
)
from app.replay.artifacts import FILENAME_BY_STAGE, VerificationFingerprint
from app.replay.hashing import canonical_json, deterministic_analysis_hash, trusted_context_hash
from app.replay.plan import build_plan, expected_provider_calls
from app.replay.service import ReplayRefused, ReplayService
from app.replay.store import (
    ReplayArtifactError,
    ReplayArtifactStore,
    forbidden_keys_in,
    forbidden_values_in,
)
from app.replay.validation import ReplayValidationService
from app.repositories.case_repository import MockCaseRepository
from app.services.audit import AuditTrail
from app.services.case_service import CaseService
from app.services.dispute_analysis import DisputeAnalysisService
from app.services.resolution_orchestrator import ResolutionOrchestratorService

ROUTE_CASE = "DISP-005"
NO_SHOW_CASE = "DISP-002"

MOCK_SETTINGS = AgentSettings(mode="mock", provider="mock")

# Keys that must never appear in a stored artefact. The first group is
# credentials; the second is the deterministic answer, which replay must never
# carry because it is always recomputed.
_FORBIDDEN_ANSWER_KEYS = {
    "refundAmount",
    "recommendedAction",
    "ruling",
    "confidence",
    "overallConfidence",
    "escalationReasons",
    "resolutionMode",
    "resolutionRecommendation",
}


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class _CountingProvider:
    """Delegates to the real mock provider and records every call's role."""

    name = "counting"

    def __init__(self) -> None:
        self.calls: list[str | None] = []
        self._mock = MockLlmProvider()

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        self.calls.append(request.metadata.get("role"))
        return self._mock.complete(request)

    @property
    def count(self) -> int:
        return len(self.calls)


class _ExplodingProvider:
    """Raises on any call.

    Used to prove a negative: if replay reaches a provider, the test fails with
    an unambiguous message rather than a count that could be misread.
    """

    name = "exploding"

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        raise AssertionError(
            "a provider call was made when replay should have prevented it "
            f"(role={request.metadata.get('role')!r})"
        )


# ---------------------------------------------------------------------------
# Stricter validators — simulating a rule change since capture
# ---------------------------------------------------------------------------


class _RejectEverythingClaimVerification(AdvocateClaimVerificationService):
    def verify(self, output, context):
        outcome = super().verify(output, context)
        from app.models.advocate import ClaimRejection

        for claim in outcome.verified_claims:
            outcome.rejected_claims.append(
                ClaimRejection(
                    claim_id=claim.claim_id,
                    claim=claim.claim,
                    reason="RULE_CHANGED_SINCE_CAPTURE",
                    detail="This test's stricter rule rejects what was once verified.",
                )
            )
        outcome.verified_claims = []
        return outcome


class _RejectEverythingRebuttalValidation(RebuttalValidationService):
    def validate(self, output, context):
        outcome = super().validate(output, context)
        from app.models.rebuttal import RebuttalRejection

        for item in outcome.verified_rebuttals:
            outcome.rejected_rebuttals.append(
                RebuttalRejection(
                    target_claim_id=item.target_claim_id,
                    stance=item.stance,
                    reason="RULE_CHANGED_SINCE_CAPTURE",
                    detail="This test's stricter rule rejects what was once verified.",
                )
            )
        outcome.verified_rebuttals = []
        return outcome


class _RejectEverythingJudgeValidation(JudgeOutputValidationService):
    def validate(self, output, context):
        result = super().validate(output, context)
        from app.models.judge import JudgeValidationIssue

        result.issues.append(
            JudgeValidationIssue(
                code="RULE_CHANGED_SINCE_CAPTURE",
                detail="This test's stricter rule rejects what was once valid.",
            )
        )
        result.valid = False
        return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _load(case_id: str):
    """Load a case exactly the way the application does.

    Not ``case_by_id``. The raw fixture keeps its timeline in authoring order,
    while ``CaseService._load_case`` canonicalises it (sorted by timestamp), and
    the canonical timeline is part of the trusted context. Capturing against the
    raw fixture and replaying through the API therefore produced a spurious
    ``TRUSTED_CONTEXT_HASH_MISMATCH`` — the two sides were describing different
    contexts for the same case. Going through the service keeps capture and
    replay on the same object, which is the only way this test file can be
    testing the path a real run takes.
    """
    case = CaseService(MockCaseRepository())._load_case(case_id)
    analysis = DisputeAnalysisService().analyze(case)
    context = AdvocateContextBuilder().build(case, analysis)
    return case, analysis, context


def _with(model, **updates):
    """``model_copy(update=...)`` that refuses to silently do nothing.

    pydantic v2's ``model_copy`` accepts keys that are not fields and stores
    them outside ``__dict__``-backed serialisation, so ``model_dump`` ignores
    them. A test that mutates a misspelled field therefore passes while proving
    nothing — which is exactly how a hash-sensitivity test can rot into a
    tautology. Both failure modes are turned into errors here.
    """
    unknown = sorted(set(updates) - set(type(model).model_fields))
    if unknown:
        raise AssertionError(
            f"{type(model).__name__} has no field(s) {unknown}; "
            f"known fields: {sorted(type(model).model_fields)}"
        )
    changed = model.model_copy(update=updates)
    if changed.model_dump(by_alias=True) == model.model_dump(by_alias=True):
        raise AssertionError(
            f"mutating {sorted(updates)} on {type(model).__name__} changed nothing"
        )
    return changed


@pytest.fixture
def store(tmp_path: Path) -> ReplayArtifactStore:
    """An artefact store inside tmp_path, never the repository directory."""
    return ReplayArtifactStore(root=tmp_path / "replay_artifacts", curated_root=tmp_path / "demo_replays")


@pytest.fixture
def captured(store: ReplayArtifactStore):
    """A live mock run with every stage captured.

    Returns the case, its analysis, the trusted context, the store and the live
    result — so a test can replay against exactly the state that was captured.
    """
    case, analysis, context = _load(ROUTE_CASE)
    provider = _CountingProvider()
    service = ResolutionOrchestratorService(
        settings=MOCK_SETTINGS, provider=provider, replay_service=ReplayService(store=store, settings=MOCK_SETTINGS)
    )
    result = service.run(
        case,
        analysis,
        capture_stages=frozenset({"ADVOCATES", "REBUTTALS", "JUDGE"}),
    )
    return case, analysis, context, store, result, provider


def _service(store: ReplayArtifactStore, **overrides) -> ResolutionOrchestratorService:
    replay = ReplayService(store=store, settings=MOCK_SETTINGS, **overrides)
    return ResolutionOrchestratorService(
        settings=MOCK_SETTINGS,
        provider=_ExplodingProvider(),
        replay_service=replay,
    )


def _read_document(store: ReplayArtifactStore, case_id: str, stage: str) -> dict:
    path = store.path_for(case_id, stage)
    return json.loads(path.read_text(encoding="utf-8"))


def _write_document(store: ReplayArtifactStore, case_id: str, stage: str, document: dict) -> None:
    store.path_for(case_id, stage).write_text(json.dumps(document), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Hashing
# ---------------------------------------------------------------------------


def test_canonical_json_is_order_independent():
    """Key order must not change the hash, or dict construction order would."""
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_analysis_hash_is_stable_for_identical_input():
    case, analysis, _ = _load(ROUTE_CASE)
    first = deterministic_analysis_hash(case, analysis)
    second = deterministic_analysis_hash(case, analysis)
    assert first == second
    assert len(first) == 64


def test_trusted_context_hash_is_stable():
    _, _, context = _load(ROUTE_CASE)
    assert trusted_context_hash(context) == trusted_context_hash(context)


def test_analysis_hash_differs_between_cases():
    a_case, a_analysis, _ = _load(ROUTE_CASE)
    b_case, b_analysis, _ = _load(NO_SHOW_CASE)
    assert deterministic_analysis_hash(a_case, a_analysis) != deterministic_analysis_hash(
        b_case, b_analysis
    )


# ---------------------------------------------------------------------------
# 2-5, 9. Hash sensitivity to trusted input
# ---------------------------------------------------------------------------


def test_deterministic_fact_change_invalidates_replay(captured):
    """A changed deterministic fact must move the hash.

    The fact mutated here is a *route-deviation* field, because the fixture is a
    route-deviation case. ``_with`` raises if the mutation is a no-op, which
    matters: an earlier version of this test set ``waiting_duration_seconds``,
    a no-show field, and the hash correctly did not move — the test was wrong,
    not the hash.
    """
    case, analysis, _, store, _, _ = captured
    changed = _with(
        analysis,
        analysis=_with(analysis.analysis, actual_route_distance_km=99.0),
    )
    assert deterministic_analysis_hash(case, changed) != deterministic_analysis_hash(case, analysis)
    result = ReplayValidationService().validate(
        store.read(case.id, "ADVOCATES")[0],
        case=case,
        analysis=changed,
        context=AdvocateContextBuilder().build(case, changed),
    )
    assert not result.valid
    assert ANALYSIS_HASH_MISMATCH in result.reason_codes


def test_evidence_change_invalidates_replay(captured):
    case, analysis, context, store, _, _ = captured
    changed_evidence = list(case.evidence)
    changed_evidence[0] = _with(
        changed_evidence[0], summary="Summary rewritten by this test."
    )
    changed_case = _with(case, evidence=changed_evidence)

    assert deterministic_analysis_hash(changed_case, analysis) != deterministic_analysis_hash(
        case, analysis
    )
    result = ReplayValidationService().validate(
        store.read(case.id, "ADVOCATES")[0],
        case=changed_case,
        analysis=analysis,
        context=AdvocateContextBuilder().build(changed_case, analysis),
    )
    assert not result.valid
    assert ANALYSIS_HASH_MISMATCH in result.reason_codes


def test_policy_threshold_change_invalidates_replay(captured):
    case, analysis, _, store, _, _ = captured
    evaluation = analysis.policy_evaluation
    rules = list(evaluation.evaluated_rules)
    rules[0] = _with(rules[0], required_value=12345)
    changed = _with(
        analysis,
        policy_evaluation=_with(evaluation, evaluated_rules=rules),
    )
    assert deterministic_analysis_hash(case, changed) != deterministic_analysis_hash(case, analysis)
    result = ReplayValidationService().validate(
        store.read(case.id, "ADVOCATES")[0],
        case=case,
        analysis=changed,
        context=AdvocateContextBuilder().build(case, changed),
    )
    assert ANALYSIS_HASH_MISMATCH in result.reason_codes


def test_resolution_mode_change_invalidates_replay(captured):
    case, analysis, _, store, _, _ = captured
    changed = _with(analysis, resolution_mode="HUMAN_REVIEW")
    assert deterministic_analysis_hash(case, changed) != deterministic_analysis_hash(case, analysis)
    result = ReplayValidationService().validate(
        store.read(case.id, "ADVOCATES")[0],
        case=case,
        analysis=changed,
        context=AdvocateContextBuilder().build(case, changed),
    )
    assert ANALYSIS_HASH_MISMATCH in result.reason_codes


def test_schema_version_change_invalidates_replay(captured):
    case, analysis, context, store, _, _ = captured
    document = _read_document(store, case.id, "ADVOCATES")
    document["schemaVersion"] = "stage4-v99"
    _write_document(store, case.id, "ADVOCATES", document)

    artifact, reasons = store.read(case.id, "ADVOCATES")
    assert reasons == []
    result = ReplayValidationService().validate(
        artifact, case=case, analysis=analysis, context=context
    )
    assert not result.valid
    assert SCHEMA_VERSION_MISMATCH in result.reason_codes


def test_unsupported_replay_version_is_rejected(captured):
    case, analysis, context, store, _, _ = captured
    document = _read_document(store, case.id, "ADVOCATES")
    document["replayVersion"] = "999"
    _write_document(store, case.id, "ADVOCATES", document)

    artifact, _ = store.read(case.id, "ADVOCATES")
    result = ReplayValidationService().validate(
        artifact, case=case, analysis=analysis, context=context
    )
    assert REPLAY_VERSION_UNSUPPORTED in result.reason_codes


def test_case_id_mismatch_invalidates_replay(captured):
    case, analysis, context, store, _, _ = captured
    document = _read_document(store, case.id, "ADVOCATES")
    document["caseId"] = "DISP-999"
    _write_document(store, case.id, "ADVOCATES", document)

    artifact, _ = store.read(case.id, "ADVOCATES")
    result = ReplayValidationService().validate(
        artifact, case=case, analysis=analysis, context=context
    )
    assert CASE_ID_MISMATCH in result.reason_codes


def test_dispute_type_mismatch_invalidates_replay(captured):
    case, analysis, context, store, _, _ = captured
    document = _read_document(store, case.id, "ADVOCATES")
    document["disputeType"] = "no_show_charge"
    _write_document(store, case.id, "ADVOCATES", document)

    artifact, _ = store.read(case.id, "ADVOCATES")
    result = ReplayValidationService().validate(
        artifact, case=case, analysis=analysis, context=context
    )
    assert DISPUTE_TYPE_MISMATCH in result.reason_codes


def test_trusted_context_change_invalidates_replay(captured):
    """A change to the context projection alone must be caught.

    The analysis hash covers the deterministic state; the context hash covers
    what was actually placed in front of the model. A context-builder change
    should move the second without necessarily moving the first, which is
    exactly why both exist.
    """
    case, analysis, context, store, _, _ = captured
    altered = _with(context, driver_response="Rewritten by this test.")
    artifact, _ = store.read(case.id, "ADVOCATES")

    assert deterministic_analysis_hash(case, analysis) == artifact.deterministic_analysis_hash
    result = ReplayValidationService().validate(
        artifact, case=case, analysis=analysis, context=altered
    )
    assert not result.valid
    assert result.reason_codes == [TRUSTED_CONTEXT_HASH_MISMATCH]


# ---------------------------------------------------------------------------
# 6-7. Hash insensitivity to run metadata
# ---------------------------------------------------------------------------


def test_timestamps_do_not_affect_the_hash(captured):
    case, analysis, _, store, _, _ = captured
    first = _read_document(store, case.id, "ADVOCATES")
    first["createdAt"] = "2000-01-01T00:00:00+00:00"
    _write_document(store, case.id, "ADVOCATES", first)

    artifact, _ = store.read(case.id, "ADVOCATES")
    result = ReplayValidationService().validate(
        artifact,
        case=case,
        analysis=analysis,
        context=AdvocateContextBuilder().build(case, analysis),
    )
    assert result.valid, result.reason_codes
    assert artifact.deterministic_analysis_hash == deterministic_analysis_hash(case, analysis)


def test_token_and_latency_metadata_do_not_affect_the_hash(captured):
    case, analysis, context, store, _, _ = captured
    document = _read_document(store, case.id, "ADVOCATES")
    for side in ("rider", "driver"):
        execution = document["payload"][side]["execution"]
        execution["durationMs"] = 987654
        execution["inputTokens"] = 111
        execution["outputTokens"] = 222
        execution["totalTokens"] = 333
    _write_document(store, case.id, "ADVOCATES", document)

    artifact, _ = store.read(case.id, "ADVOCATES")
    result = ReplayValidationService().validate(
        artifact, case=case, analysis=analysis, context=context
    )
    assert result.valid, result.reason_codes


def test_audit_metadata_does_not_affect_the_hash(captured):
    """A second live run must not invalidate an artefact captured by the first.

    Comparing the two audit trails event-for-event would be wrong: the first run
    captured artefacts and the second did not, so their trails legitimately
    differ. The property worth asserting is that running the case again — with
    its own fresh audit entries and timings — leaves the hash and the stored
    artefact's validity untouched.
    """
    case, analysis, _, store, live, _ = captured
    baseline = deterministic_analysis_hash(case, analysis)

    second = ResolutionOrchestratorService(
        settings=MOCK_SETTINGS, provider=_CountingProvider()
    ).run(case, analysis)
    assert second.audit, "the second run should have recorded something"
    assert deterministic_analysis_hash(case, analysis) == baseline

    artifact, reasons = store.read(case.id, "ADVOCATES")
    assert reasons == []
    result = ReplayValidationService().validate(
        artifact, case=case, analysis=analysis, context=AdvocateContextBuilder().build(case, analysis)
    )
    assert result.valid, result.reason_codes


# ---------------------------------------------------------------------------
# 11. Malformed artefacts
# ---------------------------------------------------------------------------


def test_malformed_artifact_is_rejected(store: ReplayArtifactStore):
    path = store.path_for(ROUTE_CASE, "ADVOCATES")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")

    artifact, reasons = store.read(ROUTE_CASE, "ADVOCATES")
    assert artifact is None
    assert reasons == [MALFORMED_ARTIFACT]


def test_artifact_with_a_mismatched_stage_field_is_rejected(captured):
    """A copied file must not satisfy a request it does not describe."""
    case, _, _, store, _, _ = captured
    document = _read_document(store, case.id, "ADVOCATES")
    document["stage"] = "JUDGE"
    _write_document(store, case.id, "ADVOCATES", document)

    artifact, reasons = store.read(case.id, "ADVOCATES")
    assert artifact is None
    assert reasons == [MALFORMED_ARTIFACT]


def test_missing_artifact_is_reported_not_raised(store: ReplayArtifactStore):
    artifact, reasons = store.read("DISP-005", "ADVOCATES")
    assert artifact is None
    assert reasons == [ARTIFACT_NOT_FOUND]


# ---------------------------------------------------------------------------
# 12-15. Re-verification
# ---------------------------------------------------------------------------


def test_stored_advocate_claims_are_reverified(captured):
    """A stored claim that cites non-existent evidence must be rejected now.

    The artefact is rewritten to look like one written by an older, laxer
    verifier: the claim cites ``E99`` while the fingerprint claims it was
    verified. Replay must not believe the fingerprint.
    """
    case, analysis, context, store, _, _ = captured
    document = _read_document(store, case.id, "ADVOCATES")
    claim = document["payload"]["rider"]["output"]["claims"][0]
    claim["evidenceIds"] = ["E99"]
    _write_document(store, case.id, "ADVOCATES", document)

    service = _service(store)
    with pytest.raises(ReplayRefused) as refused:
        service.run(case, analysis, replay_mode="ADVOCATES")
    assert CLAIM_REVALIDATION_FAILED in refused.value.reason_codes


def test_reverification_recomputes_the_split_and_excludes_rejected_claims(captured):
    """With the fingerprint agreeing, the split is still recomputed from scratch.

    The tampered claim's fingerprint is updated to match what re-verification
    will produce, so the replay proceeds — and the claim must then appear as
    rejected, absent from the verified list, and absent from the Judge context.
    """
    case, analysis, context, store, _, _ = captured
    document = _read_document(store, case.id, "ADVOCATES")
    rider = document["payload"]["rider"]
    claim = rider["output"]["claims"][0]
    claim["evidenceIds"] = ["E99"]
    rider["capturedVerification"] = {
        "verifiedSignatures": [c["claimId"] for c in rider["output"]["claims"][1:]],
        "rejected": [{"itemId": claim["claimId"], "reason": "EVIDENCE_ID_NOT_FOUND"}],
    }
    _write_document(store, case.id, "ADVOCATES", document)

    service = _service(store)
    result = service.run(case, analysis, replay_mode="ADVOCATES")

    rejected_ids = {item.claim_id for item in result.rider.rejected_claims}
    verified_ids = {item.claim_id for item in result.rider.verified_claims}
    assert claim["claimId"] in rejected_ids
    assert claim["claimId"] not in verified_ids


def test_previously_verified_claim_rejected_by_current_rules_is_refused(captured):
    """A rule change since capture is reported, not silently absorbed."""
    case, analysis, _, store, _, _ = captured
    service = _service(store, advocate_verification=_RejectEverythingClaimVerification())
    with pytest.raises(ReplayRefused) as refused:
        service.run(case, analysis, replay_mode="ADVOCATES")
    assert refused.value.reason_codes == [CLAIM_REVALIDATION_FAILED]


def test_stored_rebuttals_are_reverified(captured):
    case, analysis, _, store, _, _ = captured
    document = _read_document(store, case.id, "REBUTTALS")
    rider = document["payload"]["rider"]
    response = rider["output"]["responses"][0]
    response["evidenceIds"] = ["E99"]
    rider["capturedVerification"] = {
        "verifiedSignatures": [
            f"{item['rebuttalId']}|{item['targetClaimId']}|{item['stance']}"
            for item in []  # re-verification will reject the tampered response
        ],
        "rejected": [{"itemId": response["targetClaimId"], "reason": "EVIDENCE_ID_NOT_FOUND"}],
    }
    _write_document(store, case.id, "REBUTTALS", document)

    service = _service(store)
    result = service.run(case, analysis, replay_mode="ADVOCATES_AND_REBUTTALS")

    reasons = {
        item.reason for item in result.rebuttals.rider.rejected_rebuttals
    }
    assert "EVIDENCE_ID_NOT_FOUND" in reasons
    # The Judge must not see the tampered rebuttal.
    assert all(
        item.target_claim_id != response["targetClaimId"]
        or "E99" not in item.evidence_ids
        for item in result.rebuttals.rider.verified_rebuttals
    )


def test_previously_verified_rebuttal_rejected_by_current_rules_is_refused(captured):
    case, analysis, _, store, _, _ = captured
    service = _service(store, rebuttal_validation=_RejectEverythingRebuttalValidation())
    with pytest.raises(ReplayRefused) as refused:
        service.run(case, analysis, replay_mode="ADVOCATES_AND_REBUTTALS")
    assert refused.value.reason_codes == [REBUTTAL_REVALIDATION_FAILED]


# ---------------------------------------------------------------------------
# 16-17. Judge revalidation
# ---------------------------------------------------------------------------


def test_stored_judge_is_revalidated_and_accepted_when_still_valid(captured):
    case, analysis, _, store, _, _ = captured
    result = _service(store).run(case, analysis, replay_mode="FULL_AI")
    assert result.judge.status == "COMPLETE"
    assert result.replay_metadata.judge_replayed is True


def test_judge_replay_is_rejected_when_the_current_rules_disagree(captured):
    case, analysis, _, store, _, _ = captured
    service = _service(store, judge_validation=_RejectEverythingJudgeValidation())
    with pytest.raises(ReplayRefused) as refused:
        service.run(case, analysis, replay_mode="FULL_AI")
    assert refused.value.reason_codes == [JUDGE_REVALIDATION_FAILED]


def test_judge_replay_is_rejected_when_the_stored_output_cites_unknown_claims(captured):
    """Revalidation runs against the current context, not a stored verdict."""
    case, analysis, _, store, _, _ = captured
    document = _read_document(store, case.id, "JUDGE")
    document["payload"]["output"]["acceptedRiderClaimIds"] = ["RIDER-C99"]
    _write_document(store, case.id, "JUDGE", document)

    with pytest.raises(ReplayRefused) as refused:
        _service(store).run(case, analysis, replay_mode="FULL_AI")
    assert JUDGE_REVALIDATION_FAILED in refused.value.reason_codes


# ---------------------------------------------------------------------------
# 18-19. Rejected material stays out of the Judge context
# ---------------------------------------------------------------------------


def test_replayed_rejected_claims_never_enter_the_judge_context(captured):
    """A claim the current rules reject must not reach the Judge.

    Replayed in ``ADVOCATES`` mode, so the Judge runs live off the replayed
    advocate response. That is deliberate and is the stronger test: it shows the
    exclusion holds for the response the Judge is actually handed, not merely
    for a stored blob. Replaying ``FULL_AI`` would also work, but only for the
    uninteresting reason that a rebuttal targeting the now-rejected claim makes
    the stored rebuttals invalid — a cascade this test does not mean to exercise.
    """
    case, analysis, _, store, _, _ = captured
    document = _read_document(store, case.id, "ADVOCATES")
    rider = document["payload"]["rider"]
    claim = rider["output"]["claims"][0]
    claim["claim"] = "TAMPERED-CLAIM-TEXT"
    claim["evidenceIds"] = ["E99"]
    rider["capturedVerification"] = {
        "verifiedSignatures": [c["claimId"] for c in rider["output"]["claims"][1:]],
        "rejected": [{"itemId": claim["claimId"], "reason": "EVIDENCE_ID_NOT_FOUND"}],
    }
    _write_document(store, case.id, "ADVOCATES", document)

    provider = _CountingProvider()
    service = ResolutionOrchestratorService(
        settings=MOCK_SETTINGS,
        provider=provider,
        replay_service=ReplayService(store=store, settings=MOCK_SETTINGS),
    )
    result = service.run(case, analysis, replay_mode="ADVOCATES")

    # The claim really was rejected by the current rules — otherwise the
    # "not in the Judge context" assertion below would pass vacuously. The
    # resolution response flattens the two advocate sides onto itself, so the
    # rider's result is ``result.rider``, not ``result.advocates.rider``.
    rejected_ids = {item.claim_id for item in result.rider.rejected_claims}
    assert claim["claimId"] in rejected_ids
    assert claim["claimId"] not in {item.claim_id for item in result.rider.verified_claims}

    serialized = json.dumps(result.judge.model_dump(by_alias=True))
    assert "TAMPERED-CLAIM-TEXT" not in serialized
    assert claim["claimId"] not in result.judge.accepted_rider_claim_ids
    # And the advocates really were replayed rather than re-generated: the two
    # advocate calls are the only ones that carry no "role" in their metadata,
    # so their absence is what proves the replayed path was taken.
    assert result.replay_metadata.advocates_replayed is True
    assert provider.calls == ["rebuttal", "rebuttal", "judge"]


def test_replayed_rejected_rebuttals_never_enter_the_judge_context(captured):
    """A rebuttal the current rules reject must not reach the Judge.

    Replayed in ``ADVOCATES_AND_REBUTTALS`` mode so the Judge runs live. Under
    ``FULL_AI`` this would instead be refused with ``JUDGE_REVALIDATION_FAILED``,
    which is correct — the stored Judge ruling depended on a rebuttal that is no
    longer trusted — but it would test the refusal rather than the exclusion.
    """
    case, analysis, _, store, _, _ = captured
    document = _read_document(store, case.id, "REBUTTALS")
    rider = document["payload"]["rider"]
    response = rider["output"]["responses"][0]
    response["reasoningSummary"] = "TAMPERED-REBUTTAL-TEXT"
    response["evidenceIds"] = ["E99"]
    rider["capturedVerification"] = {
        "verifiedSignatures": [],
        "rejected": [{"itemId": response["targetClaimId"], "reason": "EVIDENCE_ID_NOT_FOUND"}],
    }
    _write_document(store, case.id, "REBUTTALS", document)

    provider = _CountingProvider()
    service = ResolutionOrchestratorService(
        settings=MOCK_SETTINGS,
        provider=provider,
        replay_service=ReplayService(store=store, settings=MOCK_SETTINGS),
    )
    result = service.run(case, analysis, replay_mode="ADVOCATES_AND_REBUTTALS")

    # The rebuttal really was rejected, so the exclusions below are not vacuous.
    rejected = [item.target_claim_id for item in result.rebuttals.rider.rejected_rebuttals]
    assert response["targetClaimId"] in rejected
    assert response["targetClaimId"] not in {
        item.target_claim_id for item in result.rebuttals.rider.verified_rebuttals
    }

    assert "TAMPERED-REBUTTAL-TEXT" not in json.dumps(result.judge.model_dump(by_alias=True))
    assert "TAMPERED-REBUTTAL-TEXT" not in json.dumps(
        [item.model_dump(by_alias=True) for item in result.rebuttals.rider.verified_rebuttals]
    )
    assert result.replay_metadata.rebuttals_replayed is True
    assert provider.calls == ["judge"]


# ---------------------------------------------------------------------------
# 20-23. Call-count planning
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "calls"),
    [
        ("NONE", 5),
        ("ADVOCATES", 3),
        ("ADVOCATES_AND_REBUTTALS", 1),
        ("FULL_AI", 0),
    ],
)
def test_expected_provider_calls_per_mode(mode, calls):
    assert expected_provider_calls(mode) == calls
    assert build_plan(mode).expected_provider_calls == calls


def test_plan_reports_live_and_replayed_stages():
    plan = build_plan("ADVOCATES_AND_REBUTTALS")
    assert plan.live_stages == ["JUDGE"]
    assert plan.replayed_stages == ["ADVOCATES", "REBUTTALS"]
    assert plan.advocates_replayed and plan.rebuttals_replayed
    assert not plan.judge_replayed


@pytest.mark.parametrize(
    ("mode", "calls"),
    [
        ("NONE", 5),
        ("ADVOCATES", 3),
        ("ADVOCATES_AND_REBUTTALS", 1),
        ("FULL_AI", 0),
    ],
)
def test_actual_call_count_matches_the_plan(captured, mode, calls):
    """The plan is not documentation — it is checked against real behaviour."""
    case, analysis, _, store, _, _ = captured
    provider = _CountingProvider()
    service = ResolutionOrchestratorService(
        settings=MOCK_SETTINGS,
        provider=provider,
        replay_service=ReplayService(store=store, settings=MOCK_SETTINGS),
    )
    service.run(case, analysis, replay_mode=mode)
    assert provider.count == calls
    assert build_plan(mode).expected_provider_calls == calls


def test_advocates_mode_leaves_only_the_rebuttals_and_judge_live(captured):
    case, analysis, _, store, _, _ = captured
    provider = _CountingProvider()
    service = ResolutionOrchestratorService(
        settings=MOCK_SETTINGS,
        provider=provider,
        replay_service=ReplayService(store=store, settings=MOCK_SETTINGS),
    )
    service.run(case, analysis, replay_mode="ADVOCATES")
    assert provider.calls == ["rebuttal", "rebuttal", "judge"]


def test_full_ai_mode_calls_nothing_at_all(captured):
    """The strongest form: a provider that raises is never reached."""
    case, analysis, _, store, _, _ = captured
    result = _service(store).run(case, analysis, replay_mode="FULL_AI")
    assert result.judge.status == "COMPLETE"
    assert result.replay_metadata.replayed


# ---------------------------------------------------------------------------
# 24. No silent fallback
# ---------------------------------------------------------------------------


def test_invalid_replay_never_triggers_a_provider_call(captured):
    case, analysis, _, store, _, _ = captured
    document = _read_document(store, case.id, "ADVOCATES")
    document["deterministicAnalysisHash"] = "0" * 64
    _write_document(store, case.id, "ADVOCATES", document)

    # The injected provider raises on any call, so reaching one fails the test.
    service = _service(store)
    with pytest.raises(ReplayRefused) as refused:
        service.run(case, analysis, replay_mode="ADVOCATES")
    assert ANALYSIS_HASH_MISMATCH in refused.value.reason_codes


def test_refused_replay_records_that_no_fallback_happened(captured):
    """A refused replay must record both facts: that it was invalid, and that
    it did not quietly fall back to a provider call.

    Checked against an explicit ``AuditTrail``, because a refusal produces no
    response for the trail to travel on. An earlier version of this test
    appended ``REPLAY_FALLBACK_BLOCKED`` to a local list inside its own
    ``except`` block and then asserted the list contained it — a tautology that
    would have passed even if the service recorded nothing at all.
    """
    case, analysis, _, store, _, _ = captured
    store.path_for(case.id, "ADVOCATES").unlink()

    audit = AuditTrail()
    replay = ReplayService(store=store, settings=MOCK_SETTINGS)
    with pytest.raises(ReplayRefused) as refused:
        replay.replay_advocates(
            case, analysis, AdvocateContextBuilder().build(case, analysis), audit
        )

    assert ARTIFACT_NOT_FOUND in refused.value.reason_codes
    names = audit.names()
    assert "REPLAY_INVALID" in names
    assert "REPLAY_FALLBACK_BLOCKED" in names


def test_audit_trail_records_the_replay_sequence(captured):
    case, analysis, _, store, _, _ = captured
    result = _service(store).run(case, analysis, replay_mode="FULL_AI")
    names = [event.event for event in result.audit]
    for expected in (
        "REPLAY_REQUESTED",
        "REPLAY_ARTIFACT_LOADED",
        "REPLAY_VALIDATION_STARTED",
        "REPLAY_VALID",
        "ADVOCATES_REPLAYED",
        "REBUTTALS_REPLAYED",
        "JUDGE_REPLAYED",
    ):
        assert expected in names, expected
    assert "REPLAY_INVALID" not in names
    assert "REPLAY_FALLBACK_BLOCKED" not in names


def test_audit_trail_records_a_refused_replay_as_invalid():
    case, analysis, context = _load(ROUTE_CASE)

    audit = AuditTrail()
    replay = ReplayService(store=ReplayArtifactStore(root=Path("/nonexistent-xyz")), settings=MOCK_SETTINGS)
    with pytest.raises(ReplayRefused):
        replay.replay_advocates(case, analysis, context, audit)
    names = audit.names()
    assert "REPLAY_INVALID" in names
    assert "REPLAY_FALLBACK_BLOCKED" in names


# ---------------------------------------------------------------------------
# 25-26. Zero network
# ---------------------------------------------------------------------------


def test_cli_dry_run_makes_zero_provider_calls(monkeypatch, tmp_path, capsys):
    """A dry run must not resolve a provider, let alone call one."""
    import app.agents.providers.registry as registry
    from app.replay import cli

    def _explode(*args, **kwargs):
        raise AssertionError("a dry run resolved a provider")

    monkeypatch.setattr(registry, "get_provider", _explode)
    monkeypatch.setenv("REPLAY_ARTIFACT_DIR", str(tmp_path / "artifacts"))

    assert cli.main(["capture", "--case", ROUTE_CASE, "--stage", "all"]) == 0
    assert cli.main(["replay", "--case", ROUTE_CASE, "--mode", "FULL_AI"]) == 0
    assert cli.main(["plan"]) == 0
    output = capsys.readouterr().out
    assert "DRY RUN" in output
    # Nothing was written.
    assert not (tmp_path / "artifacts").exists()


def test_cli_plan_reports_every_mode():
    from app.replay import cli

    assert cli.main(["plan"]) == 0


def test_replay_package_imports_no_network_client():
    """A static guarantee: replay has no way to reach the network by itself.

    The empirical proof is the egress-guard run; this is the structural one, and
    it is the one that catches a future import added by reflex.
    """
    package = Path(__file__).resolve().parent.parent / "app" / "replay"
    forbidden = ("import socket", "import requests", "import httpx", "import urllib", "import http.client")
    for module in package.glob("*.py"):
        source = module.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in source, f"{module.name} imports {needle}"


def test_replay_tests_make_no_live_call(captured):
    """Every fixture in this module runs against the mock transport."""
    _, _, _, _, result, provider = captured
    assert provider.calls == [None, None, "rebuttal", "rebuttal", "judge"]
    assert result.agent_run.provider == "counting"


# ---------------------------------------------------------------------------
# 27-28. Provenance metadata
# ---------------------------------------------------------------------------


def test_replayed_run_reports_replayed_metadata(captured):
    case, analysis, _, store, _, _ = captured
    result = _service(store).run(case, analysis, replay_mode="FULL_AI")
    metadata = result.replay_metadata
    assert metadata.mode == "FULL_AI"
    assert metadata.replayed is True
    assert metadata.valid is True
    assert metadata.advocates_replayed and metadata.rebuttals_replayed and metadata.judge_replayed
    assert metadata.artifact_created_at
    assert metadata.artifact_analysis_hash == metadata.current_analysis_hash
    assert "revalidated" in metadata.note
    assert "No model was called" in metadata.note


def test_live_run_reports_live_metadata(captured):
    case, analysis, _, _, live, _ = captured
    metadata = live.replay_metadata
    assert metadata.mode == "NONE"
    assert metadata.replayed is False
    assert metadata.valid is True
    assert metadata.reason_codes == []
    assert metadata.artifact_analysis_hash is None
    assert "configured provider" in metadata.note


def test_advocates_mode_reports_only_the_advocates_as_replayed(captured):
    case, analysis, _, store, _, _ = captured
    metadata = _service(store).run(case, analysis, replay_mode="ADVOCATES").replay_metadata
    assert metadata.advocates_replayed is True
    assert metadata.rebuttals_replayed is False
    assert metadata.judge_replayed is False


def test_replay_metadata_uses_the_documented_wire_names(captured):
    """The serialized field names are a contract with the frontend.

    Reading ``metadata.advocates_replayed`` in Python proves nothing about what
    a client receives, and that gap is not hypothetical: the model once
    serialized ``advocates_replayed`` while the frontend read
    ``advocatesReplayed``, so a replayed run rendered a REPLAYED badge with an
    empty stage list. Only the serialized form catches it, so the exact key set
    is asserted here.
    """
    case, analysis, _, store, _, _ = captured
    result = _service(store).run(case, analysis, replay_mode="FULL_AI")
    wire = result.model_dump(by_alias=True, mode="json")["replayMetadata"]

    assert set(wire) == {
        "mode",
        "replayed",
        "advocatesReplayed",
        "rebuttalsReplayed",
        "judgeReplayed",
        "valid",
        "reasonCodes",
        "currentAnalysisHash",
        "artifactAnalysisHash",
        "artifactCreatedAt",
        "artifactVersion",
        "capturedProvider",
        "capturedModel",
        "note",
    }
    assert wire["advocatesReplayed"] is True
    assert wire["rebuttalsReplayed"] is True
    assert wire["judgeReplayed"] is True


def test_live_metadata_uses_the_same_wire_names(captured):
    """A live run must be distinguishable on the wire, not only in Python."""
    case, analysis, _, _, live, _ = captured
    wire = live.model_dump(by_alias=True, mode="json")["replayMetadata"]
    assert wire["mode"] == "NONE"
    assert wire["replayed"] is False
    assert wire["advocatesReplayed"] is False
    assert wire["rebuttalsReplayed"] is False
    assert wire["judgeReplayed"] is False


# ---------------------------------------------------------------------------
# 29-33. Deterministic values are recomputed, never replayed
# ---------------------------------------------------------------------------


def test_artifacts_contain_no_deterministic_answer(captured):
    """The structural guarantee: there is no refund in the artefact to replay."""
    case, _, _, store, _, _ = captured
    for stage in ("ADVOCATES", "REBUTTALS", "JUDGE"):
        document = _read_document(store, case.id, stage)
        found = _find_keys(document, _FORBIDDEN_ANSWER_KEYS)
        assert not found, f"{stage} artefact carries {sorted(found)}"


def test_refund_is_recomputed_from_the_current_analysis(captured):
    case, analysis, _, store, live, _ = captured
    replayed = _service(store).run(case, analysis, replay_mode="FULL_AI")
    assert (
        replayed.deterministic_resolution.refund_amount
        == analysis.resolution_recommendation.refund_amount
    )
    assert replayed.deterministic_resolution == live.deterministic_resolution


def test_confidence_is_recomputed_from_the_current_analysis(captured):
    case, analysis, _, store, live, _ = captured
    replayed = _service(store).run(case, analysis, replay_mode="FULL_AI")
    assert (
        replayed.deterministic_resolution.confidence
        == analysis.confidence.overall_confidence
    )
    assert replayed.deterministic_resolution.confidence == live.deterministic_resolution.confidence


def test_escalation_is_recomputed_from_the_current_analysis(captured):
    case, analysis, _, store, live, _ = captured
    replayed = _service(store).run(case, analysis, replay_mode="FULL_AI")
    assert (
        replayed.deterministic_resolution.escalation_reasons
        == list(analysis.escalation_reasons)
    )
    assert replayed.deterministic_resolution.escalation_reasons == list(
        live.deterministic_resolution.escalation_reasons
    )


def test_resolution_mode_is_recomputed_from_the_current_analysis(captured):
    case, analysis, _, store, _, _ = captured
    replayed = _service(store).run(case, analysis, replay_mode="FULL_AI")
    assert replayed.deterministic_resolution.resolution_mode == analysis.resolution_mode


def test_human_review_remains_authoritative_under_replay(tmp_path: Path):
    """A replayed run cannot promote a HUMAN_REVIEW case to executable.

    Uses ``tmp_path`` rather than ``tempfile.TemporaryDirectory``: the latter's
    cleanup is a recursive delete outside the pytest-managed tree, which is both
    unnecessary and unwelcome in sandboxes that restrict bulk deletion.
    """
    human_case = _find_human_review_case()
    if human_case is None:
        pytest.skip("no HUMAN_REVIEW fixture available")
    case, analysis, context = _load(human_case)
    assert analysis.resolution_mode == "HUMAN_REVIEW"

    store = ReplayArtifactStore(root=tmp_path / "artifacts")
    ResolutionOrchestratorService(
        settings=MOCK_SETTINGS,
        provider=_CountingProvider(),
        replay_service=ReplayService(store=store, settings=MOCK_SETTINGS),
    ).run(case, analysis, capture_stages=frozenset({"ADVOCATES", "REBUTTALS", "JUDGE"}))

    replayed = _service(store).run(case, analysis, replay_mode="FULL_AI")

    assert replayed.deterministic_resolution.resolution_mode == "HUMAN_REVIEW"
    assert replayed.judge.status == "PENDING_HUMAN_REVIEW"
    assert replayed.judge.executable is False


def test_replay_cannot_alter_policytwin_thresholds(captured):
    case, analysis, _, store, live, _ = captured
    replayed = _service(store).run(case, analysis, replay_mode="FULL_AI")
    assert replayed.explanation == live.explanation
    assert replayed.counterfactual == live.counterfactual

    # The artefact carries no threshold either, so one cannot be replayed in.
    for stage in ("ADVOCATES", "REBUTTALS", "JUDGE"):
        document = _read_document(store, case.id, stage)
        assert not _find_keys(document, {"requiredValue", "actualValue", "passedRules"})


def test_replayed_run_matches_the_live_run_on_every_deterministic_field(captured):
    case, analysis, _, store, live, _ = captured
    replayed = _service(store).run(case, analysis, replay_mode="FULL_AI")
    assert replayed.deterministic_resolution == live.deterministic_resolution
    assert replayed.explanation == live.explanation
    assert replayed.counterfactual == live.counterfactual
    assert replayed.pipeline == live.pipeline


# ---------------------------------------------------------------------------
# 34-36. Nothing sensitive is stored
# ---------------------------------------------------------------------------


def test_no_secret_is_serialized(captured):
    case, _, _, store, _, _ = captured
    for stage in ("ADVOCATES", "REBUTTALS", "JUDGE"):
        document = _read_document(store, case.id, stage)
        assert forbidden_keys_in(document) == []
        assert forbidden_values_in(document) == []


def test_store_refuses_to_write_a_credential(store: ReplayArtifactStore):
    case, analysis, context = _load(ROUTE_CASE)
    provider = _CountingProvider()
    service = ResolutionOrchestratorService(
        settings=MOCK_SETTINGS, provider=provider, replay_service=ReplayService(store=store, settings=MOCK_SETTINGS)
    )
    outcome = service._advocates.run_with_raw(case, analysis, context)
    run = outcome.response
    run.agent_run.model = "Bearer sk-not-a-real-key"

    from app.replay.service import ReplayService as _ReplayService

    replay = _ReplayService(store=store, settings=MOCK_SETTINGS)
    with pytest.raises(ReplayArtifactError):
        replay.capture_advocates(case, analysis, context, run, outcome.raw_outputs)


def test_no_prompt_is_serialized(captured):
    """The artefact holds model output, never the request that produced it."""
    case, _, _, store, _, _ = captured
    for stage in ("ADVOCATES", "REBUTTALS", "JUDGE"):
        document = _read_document(store, case.id, stage)
        assert not _find_keys(document, {"prompt", "systemPrompt", "userPrompt", "contextJson"})
        # The serialized document must not contain the rendered context either.
        assert "You are the Rider advocate" not in json.dumps(document)


def test_no_chain_of_thought_is_serialized(captured):
    """A bare ``reasoning`` key is blocked; ``reasoningSummary`` is allowed.

    The distinction is deliberate. ``reasoningSummary`` is a short, human-facing
    artefact the schema asks for. ``reasoning`` would be the model's internal
    deliberation, and the forbidden-key check treats it as such.
    """
    from app.replay.store import forbidden_keys_in as check

    assert check({"reasoningSummary": "x"}) == []
    assert check({"reasoning": "x"}) == ["reasoning"]
    assert check({"chainOfThought": "x"}) == ["chainOfThought"]

    case, _, _, store, _, _ = captured
    for stage in ("ADVOCATES", "REBUTTALS", "JUDGE"):
        document = _read_document(store, case.id, stage)
        assert not _find_keys(document, {"chainOfThought", "reasoning", "thoughts", "scratchpad"})


# ---------------------------------------------------------------------------
# 37-40. API behaviour
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("REPLAY_ARTIFACT_DIR", str(tmp_path / "api_artifacts"))
    monkeypatch.delenv("REPLAY_API_ENABLED", raising=False)
    return TestClient(app)


def test_advocates_run_endpoint_is_unchanged(client: TestClient):
    response = client.post(f"/api/cases/{ROUTE_CASE}/advocates/run")
    assert response.status_code == 200
    assert sorted(response.json().keys()) == [
        "agentRun",
        "caseId",
        "disputeType",
        "driver",
        "pipeline",
        "rider",
        "verificationSummary",
    ]


def test_default_resolution_run_is_live_and_non_replay(client: TestClient):
    response = client.post(f"/api/cases/{ROUTE_CASE}/resolution/run")
    assert response.status_code == 200
    metadata = response.json()["replayMetadata"]
    assert metadata["mode"] == "NONE"
    assert metadata["replayed"] is False
    assert metadata["valid"] is True


def test_explicit_none_mode_is_live(client: TestClient):
    response = client.post(
        f"/api/cases/{ROUTE_CASE}/resolution/run", json={"replayMode": "NONE"}
    )
    assert response.status_code == 200
    assert response.json()["replayMetadata"]["replayed"] is False


@pytest.mark.parametrize("mode", ["ADVOCATES", "ADVOCATES_AND_REBUTTALS", "FULL_AI"])
def test_replay_is_forbidden_over_http_unless_enabled(client: TestClient, mode):
    response = client.post(
        f"/api/cases/{ROUTE_CASE}/resolution/run", json={"replayMode": mode}
    )
    assert response.status_code == 403
    assert "REPLAY_API_ENABLED" in str(response.json()["detail"])


def test_unknown_replay_mode_is_rejected(client: TestClient):
    response = client.post(
        f"/api/cases/{ROUTE_CASE}/resolution/run", json={"replayMode": "NOT_A_MODE"}
    )
    assert response.status_code == 422


def test_replay_over_http_when_enabled(monkeypatch, tmp_path):
    """The full path a demo would take: capture locally, then replay over HTTP."""
    store = ReplayArtifactStore(root=tmp_path / "api_artifacts")
    case, analysis, context = _load(ROUTE_CASE)
    ResolutionOrchestratorService(
        settings=MOCK_SETTINGS,
        provider=_CountingProvider(),
        replay_service=ReplayService(store=store, settings=MOCK_SETTINGS),
    ).run(case, analysis, capture_stages=frozenset({"ADVOCATES", "REBUTTALS", "JUDGE"}))

    monkeypatch.setenv("REPLAY_ARTIFACT_DIR", str(tmp_path / "api_artifacts"))
    monkeypatch.setenv("REPLAY_API_ENABLED", "1")
    client = TestClient(app)

    response = client.post(
        f"/api/cases/{ROUTE_CASE}/resolution/run", json={"replayMode": "FULL_AI"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["replayMetadata"]["replayed"] is True
    # Everything the frontend renders is present.
    assert body["judge"]["status"] == "COMPLETE"
    assert body["deterministicResolution"]["refundAmount"] is not None
    assert body["rebuttals"]["verificationSummary"] is not None
    assert body["explanation"] and body["counterfactual"]


def test_stale_artifact_reports_reason_codes_over_http(monkeypatch, tmp_path):
    store = ReplayArtifactStore(root=tmp_path / "stale_artifacts")
    case, analysis, context = _load(ROUTE_CASE)
    ResolutionOrchestratorService(
        settings=MOCK_SETTINGS,
        provider=_CountingProvider(),
        replay_service=ReplayService(store=store, settings=MOCK_SETTINGS),
    ).run(case, analysis, capture_stages=frozenset({"ADVOCATES", "REBUTTALS", "JUDGE"}))

    document = json.loads(
        (tmp_path / "stale_artifacts" / ROUTE_CASE / FILENAME_BY_STAGE["ADVOCATES"]).read_text(
            encoding="utf-8"
        )
    )
    document["deterministicAnalysisHash"] = "f" * 64
    (tmp_path / "stale_artifacts" / ROUTE_CASE / FILENAME_BY_STAGE["ADVOCATES"]).write_text(
        json.dumps(document), encoding="utf-8"
    )

    monkeypatch.setenv("REPLAY_ARTIFACT_DIR", str(tmp_path / "stale_artifacts"))
    monkeypatch.setenv("REPLAY_API_ENABLED", "1")
    client = TestClient(app)

    response = client.post(
        f"/api/cases/{ROUTE_CASE}/resolution/run", json={"replayMode": "FULL_AI"}
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["status"] == "REPLAY_INVALID"
    assert ANALYSIS_HASH_MISMATCH in detail["reasonCodes"]
    assert "No provider call was made" in detail["message"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_keys(node: object, names: set[str]) -> set[str]:
    """Every key in a JSON-shaped structure whose name is in ``names``."""
    found: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in names:
                    found.add(key)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(node)
    return found


def _find_human_review_case() -> str | None:
    """The first fixture whose deterministic analysis escalates to a human.

    Enumerated from the repository rather than a hard-coded id list, so adding a
    fixture cannot silently turn the dependent test into a skip. An earlier
    version imported a non-existent ``all_case_ids`` and unpacked a 3-tuple into
    2 names; the broad ``except`` swallowed both, so it always returned ``None``
    and the test always skipped while appearing to pass.
    """
    for case in MockCaseRepository().list_cases():
        try:
            _, analysis, _ = _load(case.id)
        except Exception:  # noqa: BLE001 - a broken fixture is not this test's concern
            continue
        if analysis.resolution_mode == "HUMAN_REVIEW":
            return case.id
    return None
