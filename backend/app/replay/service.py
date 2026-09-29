"""Replay: substitute the source of an AI stage's output, and nothing else.

Two operations live here, and they are deliberately asymmetric.

**Capture** records what a model said. It runs only after a stage has completed
*and* its deterministic verification has run, and it refuses to write a failed
stage. A failed run may be interesting to a developer, but it must never become
an artefact that a later run could treat as a stage's output.

**Replay** loads a stored artefact, checks it against the current case state,
re-runs the current verification over it, and returns the response object the
live orchestrator would have returned. Everything downstream is unchanged: the
Judge context builder, the validators, the deterministic engines and the
frontend all see the same shapes they always saw.

The one rule that governs every method here: **a stored artefact is never trusted
because it was verified once.** Re-verification is not a sanity check bolted on
top; it is the mechanism that produces the trusted/rejected split. The artefact
contains raw model output and no verdict at all, so there is no stored conclusion
that could be believed by mistake.

When replay is refused, ``ReplayRefused`` is raised and the orchestrator reports
``REPLAY_INVALID``. Nothing falls back to a live call. That is the point: a
silent fallback would spend the quota the feature exists to protect, and it would
make the run's provenance unknowable — a reviewer could no longer tell whether
the Judge's argument came from storage or from a model called in its place.
"""

from __future__ import annotations

import time

from app.agents.claim_verification import AdvocateClaimVerificationService
from app.agents.config import AgentSettings
from app.agents.context_builder import AdvocateContextBuilder
from app.agents.judge_validation import JudgeOutputValidationService
from app.agents.rebuttal_context_builder import RebuttalContextBuilder
from app.agents.rebuttal_validation import RebuttalValidationService
from app.models.advocate import (
    AdvocateRunResponse,
    AdvocateSideResult,
    PipelineStage,
    VerificationSummary,
)
from app.models.analysis import CaseAnalysisResponse
from app.models.agent import (
    AgentCallMetadata,
    AgentCaseContext,
    AgentRunMetadata,
)
from app.models.case import DisputeCase
from app.models.judge import JudgeCaseContext, JudgeOutput
from app.models.rebuttal import (
    MAX_REBUTTAL_ROUNDS,
    RebuttalOutput,
    RebuttalRunResponse,
    RebuttalSideResult,
    RebuttalVerificationSummary,
)
from app.models.replay import (
    CLAIM_REVALIDATION_FAILED,
    JUDGE_REVALIDATION_FAILED,
    REBUTTAL_REVALIDATION_FAILED,
    ReplayValidationResult,
)
from app.replay.artifacts import (
    AdvocateArtifact,
    JudgeArtifact,
    RebuttalArtifact,
    StoredAdvocateSide,
    StoredJudgePayload,
    StoredRebuttalSide,
    VerificationFingerprint,
    now_iso,
)
from app.replay.hashing import deterministic_analysis_hash, trusted_context_hash
from app.replay.store import ReplayArtifactStore
from app.replay.validation import ReplayValidationService
from app.replay.versions import (
    ADVOCATE_SCHEMA_VERSION,
    JUDGE_SCHEMA_VERSION,
    REBUTTAL_SCHEMA_VERSION,
    REPLAY_FORMAT_VERSION,
)
from app.services.audit import (
    ADVOCATES_REPLAYED,
    JUDGE_REPLAYED,
    REBUTTALS_REPLAYED,
    REPLAY_ARTIFACT_LOADED,
    REPLAY_ARTIFACT_WRITTEN,
    REPLAY_FALLBACK_BLOCKED,
    REPLAY_INVALID,
    REPLAY_REQUESTED,
    REPLAY_VALID,
    REPLAY_VALIDATION_STARTED,
    AuditTrail,
)

RIDER_ADVOCATE_STAGE = "RIDER_ADVOCATE"
DRIVER_ADVOCATE_STAGE = "DRIVER_ADVOCATE"
CLAIM_VERIFICATION_STAGE = "CLAIM_VERIFICATION"
JUDGE_STAGE = "JUDGE"


class ReplayRefused(RuntimeError):
    """A requested replay could not be trusted. No live call was made instead."""

    def __init__(self, reason_codes: list[str]) -> None:
        self.reason_codes = sorted(set(reason_codes))
        super().__init__("REPLAY_INVALID: " + ", ".join(self.reason_codes))


class ReplayService:
    """Capture and replay AI-stage artefacts. Never computes a deterministic value."""

    def __init__(
        self,
        *,
        store: ReplayArtifactStore | None = None,
        validation: ReplayValidationService | None = None,
        advocate_verification: AdvocateClaimVerificationService | None = None,
        rebuttal_validation: RebuttalValidationService | None = None,
        judge_validation: JudgeOutputValidationService | None = None,
        advocate_context_builder: AdvocateContextBuilder | None = None,
        rebuttal_context_builder: RebuttalContextBuilder | None = None,
        settings: AgentSettings | None = None,
    ) -> None:
        self._store = store or ReplayArtifactStore()
        self._validation = validation or ReplayValidationService()
        # The *current* verification services. Injected so a test can prove that
        # changing a rule changes what a replay accepts, which is the whole
        # reason replay re-verifies instead of storing a verdict.
        self._advocate_verification = (
            advocate_verification or AdvocateClaimVerificationService()
        )
        self._rebuttal_validation = rebuttal_validation or RebuttalValidationService()
        self._judge_validation = judge_validation or JudgeOutputValidationService()
        self._advocate_context_builder = advocate_context_builder or AdvocateContextBuilder()
        self._rebuttal_context_builder = (
            rebuttal_context_builder or RebuttalContextBuilder()
        )
        self._settings = settings or AgentSettings.from_environment()

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture_advocates(
        self,
        case: DisputeCase,
        analysis: CaseAnalysisResponse,
        context: AgentCaseContext,
        advocate_run: AdvocateRunResponse,
        raw_outputs: dict[str, object],
        audit: AuditTrail | None = None,
    ) -> str | None:
        """Store both advocates' raw output, after verification has run.

        Refuses when either side failed. A one-sided artefact would describe a
        case state the pipeline can never replay into a decision, so writing it
        would create a file that looks reusable and is not.
        """
        rider_raw = raw_outputs.get("RIDER")
        driver_raw = raw_outputs.get("DRIVER")
        if rider_raw is None or driver_raw is None:
            return None

        artifact = AdvocateArtifact(
            replay_version=REPLAY_FORMAT_VERSION,
            schema_version=ADVOCATE_SCHEMA_VERSION,
            created_at=now_iso(),
            case_id=case.id,
            dispute_type=case.dispute_type,
            deterministic_analysis_hash=deterministic_analysis_hash(case, analysis),
            trusted_context_hash=trusted_context_hash(context),
            provider=advocate_run.agent_run.provider,
            model=advocate_run.agent_run.model,
            payload={
                "rider": _stored_advocate_side(advocate_run.rider, rider_raw),
                "driver": _stored_advocate_side(advocate_run.driver, driver_raw),
            },
        )
        path = self._store.write(artifact)
        if audit is not None:
            audit.record(
                REPLAY_ARTIFACT_WRITTEN,
                stage="ADVOCATES",
                case_id=case.id,
                path=str(path.name),
            )
        return str(path)

    def capture_rebuttals(
        self,
        case: DisputeCase,
        analysis: CaseAnalysisResponse,
        context: AgentCaseContext,
        rebuttal_run: RebuttalRunResponse,
        raw_outputs: dict[str, object],
        audit: AuditTrail | None = None,
    ) -> str | None:
        """Store both sides' raw rebuttal output, after verification has run.

        Unlike the advocates, a side that failed is stored *as a failure*. A
        missing rebuttal is a normal outcome that does not block the Judge, so
        reproducing it faithfully is the honest thing to do — inventing an empty
        rebuttal to fill the gap would not be.
        """
        artifact = RebuttalArtifact(
            replay_version=REPLAY_FORMAT_VERSION,
            schema_version=REBUTTAL_SCHEMA_VERSION,
            created_at=now_iso(),
            case_id=case.id,
            dispute_type=case.dispute_type,
            deterministic_analysis_hash=deterministic_analysis_hash(case, analysis),
            trusted_context_hash=trusted_context_hash(context),
            provider=_rebuttal_provider(rebuttal_run),
            model=_rebuttal_model(rebuttal_run),
            payload={
                "rider": _stored_rebuttal_side(
                    rebuttal_run.rider, raw_outputs.get("RIDER")
                ),
                "driver": _stored_rebuttal_side(
                    rebuttal_run.driver, raw_outputs.get("DRIVER")
                ),
            },
        )
        path = self._store.write(artifact)
        if audit is not None:
            audit.record(
                REPLAY_ARTIFACT_WRITTEN,
                stage="REBUTTALS",
                case_id=case.id,
                path=str(path.name),
            )
        return str(path)

    def capture_judge(
        self,
        case: DisputeCase,
        analysis: CaseAnalysisResponse,
        context: AgentCaseContext,
        judge_output: JudgeOutput,
        execution: AgentCallMetadata | None = None,
        audit: AuditTrail | None = None,
    ) -> str | None:
        """Store the Judge's raw output, after validation has passed.

        Called only on the validated path, so a stored Judge artefact always
        implies "this was valid when captured". Replay does not rely on that
        implication — it revalidates — but it does mean an artefact is never
        written for output the code had already refused.
        """
        artifact = JudgeArtifact(
            replay_version=REPLAY_FORMAT_VERSION,
            schema_version=JUDGE_SCHEMA_VERSION,
            created_at=now_iso(),
            case_id=case.id,
            dispute_type=case.dispute_type,
            deterministic_analysis_hash=deterministic_analysis_hash(case, analysis),
            trusted_context_hash=trusted_context_hash(context),
            provider=execution.provider if execution else "",
            model=execution.model if execution else None,
            payload=StoredJudgePayload(output=judge_output, execution=execution),
        )
        path = self._store.write(artifact)
        if audit is not None:
            audit.record(
                REPLAY_ARTIFACT_WRITTEN,
                stage="JUDGE",
                case_id=case.id,
                path=str(path.name),
            )
        return str(path)

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def replay_advocates(
        self,
        case: DisputeCase,
        analysis: CaseAnalysisResponse,
        context: AgentCaseContext,
        audit: AuditTrail | None = None,
    ) -> tuple[AdvocateRunResponse, AdvocateArtifact]:
        """Rebuild the Stage 4 advocate response from stored raw output.

        Returns the artefact alongside the response so the caller can report
        provenance — which artefact, captured when, from which provider — without
        re-reading the file.
        """
        artifact = self._load_validated(
            case.id, "ADVOCATES", case=case, analysis=analysis, context=context, audit=audit
        )

        results: dict[str, AdvocateSideResult] = {}
        reasons: list[str] = []
        for stored in (artifact.payload.rider, artifact.payload.driver):
            outcome = self._advocate_verification.verify(stored.output, context)
            fingerprint = VerificationFingerprint.of(
                [claim.claim_id for claim in outcome.verified_claims],
                [(claim.claim_id, claim.reason) for claim in outcome.rejected_claims],
            )
            if not stored.captured_verification.matches(
                fingerprint.verified_signatures,
                [(item.item_id, item.reason) for item in fingerprint.rejected],
            ):
                # The rules moved since capture. The stored argument would mean
                # something different now, so the honest answer is "re-capture".
                reasons.append(CLAIM_REVALIDATION_FAILED)
            results[stored.side] = _advocate_side_result(stored, outcome)

        if reasons:
            self._refuse(artifact, reasons, audit)
        assert isinstance(artifact, AdvocateArtifact)

        if audit is not None:
            audit.record(
                ADVOCATES_REPLAYED,
                case_id=case.id,
                artifact_created_at=artifact.created_at,
                provider=artifact.provider,
                model=artifact.model,
            )

        verified = sum(len(result.verified_claims) for result in results.values())
        rejected = sum(len(result.rejected_claims) for result in results.values())
        rejection_reasons = sorted(
            {
                claim.reason
                for result in results.values()
                for claim in result.rejected_claims
            }
        )
        return (
            AdvocateRunResponse(
                case_id=case.id,
                dispute_type=case.dispute_type,
                rider=results["RIDER"],
                driver=results["DRIVER"],
                agent_run=_replayed_run_metadata(artifact, self._settings),
                verification_summary=VerificationSummary(
                    verified_count=verified,
                    rejected_count=rejected,
                    rejection_reasons=rejection_reasons,
                ),
                pipeline=_advocate_pipeline(),
            ),
            artifact,
        )

    def replay_rebuttals(
        self,
        case: DisputeCase,
        analysis: CaseAnalysisResponse,
        context: AgentCaseContext,
        advocate_run: AdvocateRunResponse,
        audit: AuditTrail | None = None,
    ) -> tuple[RebuttalRunResponse, RebuttalArtifact]:
        """Rebuild the Stage 6 rebuttal response from stored raw output."""
        artifact = self._load_validated(
            case.id, "REBUTTALS", case=case, analysis=analysis, context=context, audit=audit
        )
        assert isinstance(artifact, RebuttalArtifact)

        blocked = _blocked_reason(advocate_run)
        if blocked is not None:
            # The same asymmetry as the live pipeline: without an initial
            # argument from both sides there is nothing to cross-examine.
            #
            # Unreachable today, because replaying advocates can only yield two
            # COMPLETE sides — a stored artefact requires both. Kept because the
            # invariant it encodes belongs to the pipeline, not to this mode, and
            # a future mode that mixed a replayed advocate with a live one would
            # need it.
            reason = (
                "Rebuttal did not run: "
                + " and ".join(blocked)
                + " did not produce an initial argument, so there is no verified case "
                "to cross-examine."
            )
            return (
                RebuttalRunResponse(
                    case_id=case.id,
                    dispute_type=case.dispute_type,
                    rider=RebuttalSideResult(
                        side="RIDER", status="NOT_RUN", failure_reason=reason
                    ),
                    driver=RebuttalSideResult(
                        side="DRIVER", status="NOT_RUN", failure_reason=reason
                    ),
                    verification_summary=RebuttalVerificationSummary(
                        verified_count=0, rejected_count=0, rejection_reasons=[]
                    ),
                    pipeline=_rebuttal_pipeline("NOT_RUN"),
                ),
                artifact,
            )

        results: dict[str, RebuttalSideResult] = {}
        reasons: list[str] = []
        for stored in (artifact.payload.rider, artifact.payload.driver):
            result, side_reasons = self._replay_rebuttal_side(
                case, analysis, context, advocate_run, stored
            )
            results[stored.side] = result
            reasons.extend(side_reasons)

        if reasons:
            self._refuse(artifact, reasons, audit)

        if audit is not None:
            audit.record(
                REBUTTALS_REPLAYED,
                case_id=case.id,
                artifact_created_at=artifact.created_at,
                provider=artifact.provider,
                model=artifact.model,
            )

        verified = sum(len(result.verified_rebuttals) for result in results.values())
        rejected = sum(len(result.rejected_rebuttals) for result in results.values())
        return (
            RebuttalRunResponse(
                case_id=case.id,
                dispute_type=case.dispute_type,
                round=MAX_REBUTTAL_ROUNDS,
                max_rounds=MAX_REBUTTAL_ROUNDS,
                rider=results["RIDER"],
                driver=results["DRIVER"],
                verification_summary=RebuttalVerificationSummary(
                    verified_count=verified,
                    rejected_count=rejected,
                    rejection_reasons=sorted(
                        {
                            item.reason
                            for result in results.values()
                            for item in result.rejected_rebuttals
                        }
                    ),
                    generated_count=sum(
                        result.execution.generated_claim_count if result.execution else 0
                        for result in results.values()
                    ),
                ),
                pipeline=_rebuttal_pipeline("COMPLETE"),
            ),
            artifact,
        )

    def replay_judge(
        self,
        judge_context: JudgeCaseContext,
        audit: AuditTrail | None = None,
    ) -> tuple[JudgeOutput, AgentCallMetadata | None, JudgeArtifact]:
        """Return the stored Judge output, **revalidated against the current context**.

        The revalidation is the whole point of this method. A stored Judge output
        that no longer passes the current validator must not be used, and the
        check runs against the context built for *this* request — not against a
        copy of the context from capture time.
        """
        case_id = judge_context.case_id
        artifact, load_reasons = self._store.read(case_id, "JUDGE")
        if audit is not None:
            audit.record(REPLAY_ARTIFACT_LOADED, stage="JUDGE", case_id=case_id, found=artifact is not None)
        if artifact is None or load_reasons:
            self._refuse_raw(case_id, "JUDGE", load_reasons, audit)
        assert isinstance(artifact, JudgeArtifact)

        payload = artifact.payload
        validation = self._judge_validation.validate(payload.output, judge_context)
        if audit is not None:
            audit.record(
                REPLAY_VALIDATION_STARTED,
                stage="JUDGE",
                case_id=case_id,
                valid=validation.valid,
                issue_count=len(validation.issues),
            )
        if not validation.valid:
            self._refuse(artifact, [JUDGE_REVALIDATION_FAILED], audit)

        if audit is not None:
            audit.record(REPLAY_VALID, stage="JUDGE", case_id=case_id)
            audit.record(
                JUDGE_REPLAYED,
                case_id=case_id,
                artifact_created_at=artifact.created_at,
                provider=artifact.provider,
                model=artifact.model,
            )
        return payload.output, replayed_call_metadata(payload.execution), artifact

    # ------------------------------------------------------------------
    # Loading and refusal
    # ------------------------------------------------------------------

    def _load_validated(
        self,
        case_id: str,
        stage: str,
        *,
        case: DisputeCase,
        analysis: CaseAnalysisResponse,
        context: AgentCaseContext,
        audit: AuditTrail | None,
    ):
        """Load an artefact and check it against the current case state.

        Raises ``ReplayRefused`` rather than returning a sentinel, because every
        caller must handle refusal and an ignored return value would be a silent
        fallback to live calls — the exact behaviour this stage forbids.
        """
        if audit is not None:
            audit.record(REPLAY_REQUESTED, stage=stage, case_id=case_id)

        artifact, load_reasons = self._store.read(case_id, stage)
        if audit is not None:
            audit.record(
                REPLAY_ARTIFACT_LOADED,
                stage=stage,
                case_id=case_id,
                found=artifact is not None,
            )
        if artifact is None or load_reasons:
            self._refuse_raw(case_id, stage, load_reasons, audit)
        assert artifact is not None

        if audit is not None:
            audit.record(REPLAY_VALIDATION_STARTED, stage=stage, case_id=case_id)

        result: ReplayValidationResult = self._validation.validate(
            artifact, case=case, analysis=analysis, context=context
        )
        if not result.valid:
            self._refuse(artifact, result.reason_codes, audit)
        if audit is not None:
            audit.record(REPLAY_VALID, stage=stage, case_id=case_id)
        return artifact

    def _replay_rebuttal_side(
        self,
        case: DisputeCase,
        analysis: CaseAnalysisResponse,
        context: AgentCaseContext,
        advocate_run: AdvocateRunResponse,
        stored: StoredRebuttalSide,
    ) -> tuple[RebuttalSideResult, list[str]]:
        if stored.output is None:
            # Reproduce the stored failure. Nothing is invented to fill the gap.
            return (
                RebuttalSideResult(
                    side=stored.side,
                    status="FAILED" if stored.status == "FAILED" else "NOT_RUN",
                    failure_reason=stored.failure_reason,
                    execution=stored.execution,
                ),
                [],
            )

        rebuttal_context = self._rebuttal_context_builder.build(
            case,
            analysis,
            context,
            advocate_run.rider,
            advocate_run.driver,
            stored.side,
        )
        outcome = self._rebuttal_validation.validate(stored.output, rebuttal_context)

        signatures = [
            f"{item.rebuttal_id}|{item.target_claim_id}|{item.stance}"
            for item in outcome.verified_rebuttals
        ]
        rejected = [
            (item.target_claim_id or "", item.reason)
            for item in outcome.rejected_rebuttals
        ]
        reasons: list[str] = []
        if not stored.captured_verification.matches(signatures, rejected):
            reasons.append(REBUTTAL_REVALIDATION_FAILED)

        return (
            RebuttalSideResult(
                side=stored.side,
                status="COMPLETE",
                overall_summary=stored.output.overall_summary,
                verified_rebuttals=outcome.verified_rebuttals,
                rejected_rebuttals=outcome.rejected_rebuttals,
                conceded_target_ids=outcome.conceded_target_ids(),
                execution=replayed_call_metadata(stored.execution),
            ),
            reasons,
        )

    def _refuse(self, artifact, reason_codes: list[str], audit: AuditTrail | None) -> None:
        self._refuse_raw(artifact.case_id, artifact.stage, reason_codes, audit)

    @staticmethod
    def _refuse_raw(
        case_id: str, stage: str, reason_codes: list[str], audit: AuditTrail | None
    ) -> None:
        """Refuse, and record that no fallback happened."""
        codes = sorted(set(reason_codes))
        if audit is not None:
            audit.record(
                REPLAY_INVALID, stage=stage, case_id=case_id, reason_codes=codes
            )
            # Recorded explicitly rather than left implicit. "We did not call the
            # provider" is a claim worth having in the trail, because it is the
            # behaviour a future change is most likely to break by accident.
            audit.record(
                REPLAY_FALLBACK_BLOCKED,
                stage=stage,
                case_id=case_id,
                reason="REPLAY_REQUESTED_BUT_INVALID",
            )
        raise ReplayRefused(codes)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stored_advocate_side(
    result: AdvocateSideResult, raw: object
) -> StoredAdvocateSide:
    return StoredAdvocateSide(
        side=result.side,
        output=raw,
        captured_verification=VerificationFingerprint.of(
            [claim.claim_id for claim in result.verified_claims],
            [(claim.claim_id, claim.reason) for claim in result.rejected_claims],
        ),
        execution=result.execution,
    )


def _stored_rebuttal_side(
    result: RebuttalSideResult, raw: object
) -> StoredRebuttalSide:
    return StoredRebuttalSide(
        side=result.side,
        status=result.status,
        output=raw if result.status == "COMPLETE" else None,
        captured_verification=VerificationFingerprint.of(
            [
                f"{item.rebuttal_id}|{item.target_claim_id}|{item.stance}"
                for item in result.verified_rebuttals
            ],
            [
                (item.target_claim_id or "", item.reason)
                for item in result.rejected_rebuttals
            ],
        ),
        failure_reason=result.failure_reason,
        execution=result.execution,
    )


def _advocate_side_result(stored: StoredAdvocateSide, outcome) -> AdvocateSideResult:
    return AdvocateSideResult(
        side=stored.side,
        status="COMPLETE",
        summary=stored.output.summary,
        requested_outcome=stored.output.requested_outcome,
        context_acknowledged=stored.output.context_acknowledged,
        verified_claims=outcome.verified_claims,
        rejected_claims=outcome.rejected_claims,
        warnings=outcome.warnings,
        execution=replayed_call_metadata(stored.execution),
    )


def replayed_call_metadata(captured: AgentCallMetadata | None) -> AgentCallMetadata | None:
    """Describe a replayed call honestly.

    Token counts are ``None`` and latency is ``0``, because *this request* spent
    no tokens and made no call. Reporting the captured figures here would
    attribute last week's cost to this run. The captured values remain available
    on the artefact and are surfaced through ``ReplayMetadata``.
    """
    if captured is None:
        return None
    return AgentCallMetadata(
        provider=captured.provider,
        model=captured.model,
        duration_ms=0,
        generated_claim_count=captured.generated_claim_count,
        verified_claim_count=captured.verified_claim_count,
        rejected_claim_count=captured.rejected_claim_count,
        rejection_reasons=list(captured.rejection_reasons),
        malformed_output=False,
    )


def _replayed_run_metadata(artifact, settings: AgentSettings) -> AgentRunMetadata:
    return AgentRunMetadata(
        # The configured mode, not a claim about provenance. Provenance is
        # stated in ReplayMetadata, which the UI badges as REPLAYED.
        mode=settings.mode,
        provider=artifact.provider or "replay",
        model=artifact.model,
        prompt_version=settings.prompt_version,
        duration_ms=0,
    )


def _advocate_pipeline() -> list[PipelineStage]:
    return [
        PipelineStage(stage="CASE_RECEIVED", status="COMPLETE"),
        PipelineStage(stage="EVIDENCE_VALIDATED", status="COMPLETE"),
        PipelineStage(stage="DETERMINISTIC_ANALYSIS", status="COMPLETE"),
        PipelineStage(stage=RIDER_ADVOCATE_STAGE, status="COMPLETE"),
        PipelineStage(stage=DRIVER_ADVOCATE_STAGE, status="COMPLETE"),
        PipelineStage(stage=CLAIM_VERIFICATION_STAGE, status="COMPLETE"),
        PipelineStage(stage=JUDGE_STAGE, status="NOT_RUN"),
    ]


def _rebuttal_pipeline(status: str) -> list[PipelineStage]:
    return [
        PipelineStage(stage="RIDER_REBUTTAL", status=status),
        PipelineStage(stage="DRIVER_REBUTTAL", status=status),
        PipelineStage(stage="REBUTTAL_VERIFICATION", status=status),
    ]


def _blocked_reason(advocate_run: AdvocateRunResponse) -> list[str] | None:
    failed = [
        side.side
        for side in (advocate_run.rider, advocate_run.driver)
        if side.status != "COMPLETE"
    ]
    return failed or None


def _rebuttal_provider(rebuttal_run: RebuttalRunResponse) -> str:
    for side in (rebuttal_run.rider, rebuttal_run.driver):
        if side.execution is not None:
            return side.execution.provider
    return ""


def _rebuttal_model(rebuttal_run: RebuttalRunResponse) -> str | None:
    for side in (rebuttal_run.rider, rebuttal_run.driver):
        if side.execution is not None and side.execution.model:
            return side.execution.model
    return None


__all__ = ["ReplayRefused", "ReplayService", "replayed_call_metadata"]
