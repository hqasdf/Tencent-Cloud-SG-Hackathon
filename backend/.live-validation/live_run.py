"""Final live Gemini validation — instrumented end-to-end runner.

CONSUMES REAL GEMINI QUOTA. Run from ``backend/``:

    python .live-validation/live_run.py --cases DISP-005,DISP-002,DISP-003

What this does and why it is built this way
-------------------------------------------

It drives ``ResolutionOrchestratorService`` directly rather than
``POST /api/cases/{id}/resolution/run``. That is the "cleaner internal
validation seam" the brief allows, and it is the only seam that can do the two
things this validation needs: **capture replay artefacts** (deliberately not
reachable over HTTP) and **count every external HTTP attempt**. The service is
the same object the endpoint constructs, so nothing about the pipeline differs.

Three safety properties, in order of importance:

1. ``LiveBudgetExhausted`` derives from ``BaseException``, not ``Exception``.
   The provider catches ``Exception`` to normalise transport failures and to
   decide whether to retry; a guard that was caught there would be converted
   into a retryable ``NETWORK_ERROR`` and would *spend more budget* rather than
   stop. Deriving from ``BaseException`` makes the stop unconditional — nothing
   in the agent stack swallows it.

2. The attempt ledger is appended and flushed to disk on every attempt, and is
   loaded at start-up. A crashed or restarted process therefore continues the
   same budget rather than silently starting a fresh one.

3. The cap is checked *before* the request is issued, so the ledger can never
   exceed ``MAX_HTTP_ATTEMPTS``.

``AGENT_MAX_RETRIES`` is pinned to 1 for the session, implementing the brief's
"at most ONE bounded retry" for 503/timeout/network. That is a property of the
validation run, not a change to the repository's configuration.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.agents.config import AgentSettings  # noqa: E402
from app.agents.context_builder import AdvocateContextBuilder  # noqa: E402
from app.agents.judge_claims import namespace_claim_id  # noqa: E402
from app.agents.judge_context_builder import JudgeContextBuilder  # noqa: E402
from app.agents.providers.openai_compatible import OpenAiCompatibleProvider  # noqa: E402
from app.env import load_local_env  # noqa: E402
from app.replay.hashing import deterministic_analysis_hash, trusted_context_hash  # noqa: E402
from app.replay.store import forbidden_keys_in, forbidden_values_in  # noqa: E402
from app.repositories.case_repository import MockCaseRepository  # noqa: E402
from app.services.case_service import CaseService  # noqa: E402
from app.services.resolution_orchestrator import ResolutionOrchestratorService  # noqa: E402

HERE = Path(__file__).resolve().parent
LEDGER_PATH = HERE / "http_attempts.json"
RESULTS_DIR = HERE / "results"
ARTIFACT_ROOT = BACKEND / "replay_artifacts"

# The brief set the session ceiling at 18. The first live attempt lost 4 attempts
# to a provider-wide 503 block (see results-blocked-503/) without validating a
# single stage. The user then explicitly authorised "the full A->B->C sequence,
# <=15 more attempts", so the ceiling is raised by exactly one to 19 so that a
# healthy provider is not truncated mid-CASE-C. Reported as a one-attempt
# overage against the original 18.
MAX_HTTP_ATTEMPTS = 19
SESSION_MAX_RETRIES = 1

# Failure codes that mean "stop the whole session", per the brief's provider
# failure rules. A 503 gets one bounded retry; a second one is capacity gone.
STOP_CODES = frozenset({"RATE_LIMITED", "SERVER_ERROR", "AUTHENTICATION_FAILED",
                        "ACCESS_DENIED", "MODEL_NOT_FOUND", "NOT_CONFIGURED"})


class LiveBudgetExhausted(BaseException):
    """The session's external-attempt budget is spent. Never swallowed."""


# ---------------------------------------------------------------------------
# Attempt ledger
# ---------------------------------------------------------------------------


class AttemptLedger:
    """Every external HTTP attempt this validation makes, persisted as it goes."""

    def __init__(self, path: Path) -> None:
        self._path = path
        if path.is_file():
            self._records: list[dict] = json.loads(path.read_text(encoding="utf-8"))
        else:
            self._records = []

    @property
    def count(self) -> int:
        return len(self._records)

    @property
    def records(self) -> list[dict]:
        return list(self._records)

    def record(self, **entry: object) -> None:
        self._records.append({"seq": self.count + 1, **entry})
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._records, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Instrumented provider
# ---------------------------------------------------------------------------


def _stage_from_metadata(metadata: dict) -> str:
    """Derive the pipeline stage from the request's own metadata.

    Every agent already labels its call: advocates send ``side``, rebuttals send
    ``role=rebuttal`` plus ``side``, the Judge sends ``role=judge``. Using that
    label means a retry is attributed to the stage that caused it, without
    inspecting or persisting any prompt text.
    """
    role = metadata.get("role")
    side = metadata.get("side")
    if role == "judge":
        return "JUDGE"
    if role == "rebuttal":
        return f"{side}_REBUTTAL"
    if side:
        return f"{side}_ADVOCATE"
    return "UNKNOWN"


class TaggingProvider:
    """Delegates to the real provider, tagging each call with its stage.

    The tag is read by the guarded HTTP client, which is where retries become
    visible. Calls are sequential by design for this validation, so a plain
    attribute is sufficient and a context variable would only add indirection.
    """

    def __init__(self, inner: OpenAiCompatibleProvider, tag: dict) -> None:
        self._inner = inner
        self._tag = tag
        self.name = inner.name

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    @property
    def base_url(self) -> str:
        return self._inner.base_url

    def complete(self, request):  # noqa: ANN001, ANN201 - mirrors the protocol
        self._tag["stage"] = _stage_from_metadata(request.metadata)
        return self._inner.complete(request)


class GuardedClient:
    """An httpx.Client that records every send and refuses to exceed the budget."""

    def __init__(self, inner: httpx.Client, ledger: AttemptLedger, tag: dict) -> None:
        self._inner = inner
        self._ledger = ledger
        self._tag = tag

    def __enter__(self) -> "GuardedClient":
        self._inner.__enter__()
        return self

    def __exit__(self, *exc_info: object) -> object:
        return self._inner.__exit__(*exc_info)

    def post(self, url: str, **kwargs: object) -> httpx.Response:
        return self._send("POST", url, kwargs)

    def get(self, url: str, **kwargs: object) -> httpx.Response:
        return self._send("GET", url, kwargs)

    def _send(self, method: str, url: str, kwargs: dict) -> httpx.Response:
        if self._ledger.count >= MAX_HTTP_ATTEMPTS:
            raise LiveBudgetExhausted(
                f"External-attempt budget of {MAX_HTTP_ATTEMPTS} is spent. "
                "Stopping before issuing another Gemini request."
            )
        stage = self._tag.get("stage") or "UNKNOWN"
        started = time.monotonic()
        try:
            response = self._inner.request(method, url, **kwargs)
        except BaseException as error:  # noqa: BLE001 - recorded then re-raised
            self._ledger.record(
                stage=stage,
                method=method,
                endpoint=_safe_endpoint(url),
                status=None,
                errorType=type(error).__name__,
                durationMs=int((time.monotonic() - started) * 1000),
                at=_now(),
            )
            raise
        self._ledger.record(
            stage=stage,
            method=method,
            endpoint=_safe_endpoint(url),
            status=response.status_code,
            errorType=None,
            durationMs=int((time.monotonic() - started) * 1000),
            retryAfter=response.headers.get("retry-after"),
            at=_now(),
        )
        return response


def _safe_endpoint(url: str) -> str:
    """The host and path only — never a query string, which can carry a key."""
    without_query = url.split("?", 1)[0]
    return without_query.replace("https://", "").replace("http://", "")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _tokens(execution) -> dict:  # noqa: ANN001
    if execution is None:
        return {
            "inputTokens": None,
            "outputTokens": None,
            "totalTokens": None,
            "unattributedTokens": None,
        }
    total, inp, out = execution.total_tokens, execution.input_tokens, execution.output_tokens
    unattributed = (
        max(total - (inp or 0) - (out or 0), 0)
        if total is not None and (inp is not None or out is not None)
        else None
    )
    return {
        "inputTokens": inp,
        "outputTokens": out,
        "totalTokens": total,
        "unattributedTokens": unattributed,
    }


def _advocate_metrics(side_result) -> dict:  # noqa: ANN001
    execution = side_result.execution
    warnings = list(side_result.warnings)
    generated = execution.generated_claim_count if execution else len(side_result.verified_claims)
    verified = len(side_result.verified_claims)
    rejected = len(side_result.rejected_claims)
    return {
        "status": side_result.status,
        "provider": execution.provider if execution else None,
        "model": execution.model if execution else None,
        "latencyMs": execution.duration_ms if execution else None,
        "attemptCount": None,  # filled from the ledger
        "retryCount": None,
        **_tokens(execution),
        "generatedClaimCount": generated,
        "verifiedClaimCount": verified,
        "rejectedClaimCount": rejected,
        "verificationRate": round(verified / generated, 4) if generated else None,
        "rejectionReasons": sorted({claim.reason for claim in side_result.rejected_claims}),
        "malformedOutput": execution.malformed_output if execution else None,
        "failureCode": execution.failure_code if execution else None,
        "failureReason": side_result.failure_reason,
        "structuredOutputValid": side_result.status == "COMPLETE",
        "requestedOutcome": side_result.requested_outcome,
        "contextAcknowledged": side_result.context_acknowledged,
        "invalidEvidenceRefs": _warn_count(warnings, "EVIDENCE_ID_NOT_FOUND", "EVIDENCE_NOT_IN_CASE", "EVIDENCE_NOT_VERIFIED"),
        "invalidPolicyRefs": _warn_count(warnings, "POLICY_REF_NOT_FOUND", "POLICY_NOT_APPLICABLE"),
        "factContradictions": _warn_count(warnings, "FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS"),
        "unknownFacts": _warn_count(warnings, "UNKNOWN_ASSERTED_FACT"),
        "warnings": [{"claimId": w.claim_id, "code": w.code} for w in warnings],
        "rejectedClaims": [
            {"claimId": claim.claim_id, "reason": claim.reason} for claim in side_result.rejected_claims
        ],
        "verifiedClaimIds": [claim.claim_id for claim in side_result.verified_claims],
    }


def _warn_count(warnings: list, *codes: str) -> int:
    return sum(1 for warning in warnings if warning.code in codes)


def _rebuttal_metrics(side_result) -> dict:  # noqa: ANN001
    execution = side_result.execution
    stances: dict[str, int] = {}
    for item in side_result.verified_rebuttals:
        stances[item.stance] = stances.get(item.stance, 0) + 1
    generated = len(side_result.verified_rebuttals) + len(side_result.rejected_rebuttals)
    return {
        "status": side_result.status,
        "provider": execution.provider if execution else None,
        "model": execution.model if execution else None,
        "latencyMs": execution.duration_ms if execution else None,
        "attemptCount": None,
        "retryCount": None,
        **_tokens(execution),
        "responsesGenerated": generated,
        "responsesVerified": len(side_result.verified_rebuttals),
        "responsesRejected": len(side_result.rejected_rebuttals),
        "verificationRate": (
            round(len(side_result.verified_rebuttals) / generated, 4) if generated else None
        ),
        "stances": stances,
        "targetClaimIds": [item.target_claim_id for item in side_result.verified_rebuttals],
        "verifiedRebuttalIds": [item.rebuttal_id for item in side_result.verified_rebuttals],
        "rejectionReasons": sorted({item.reason for item in side_result.rejected_rebuttals}),
        "rejected": [
            {"targetClaimId": item.target_claim_id, "reason": item.reason}
            for item in side_result.rejected_rebuttals
        ],
        "invalidTargetIds": _reject_count(side_result, "TARGET_CLAIM_NOT_FOUND"),
        "ownSideTargets": _reject_count(side_result, "TARGET_CLAIM_IS_OWN_SIDE"),
        "invalidEvidenceRefs": _reject_count(side_result, "EVIDENCE_ID_NOT_FOUND"),
        "invalidPolicyRefs": _reject_count(side_result, "POLICY_REF_NOT_FOUND"),
        "factContradictions": _reject_count(side_result, "FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS"),
        "unknownFacts": _reject_count(side_result, "UNKNOWN_ASSERTED_FACT"),
        "inventedMonetaryValues": _reject_count(side_result, "INVENTED_MONETARY_VALUE"),
        "concededTargetIds": list(side_result.conceded_target_ids),
        "failureReason": side_result.failure_reason,
        "structuredOutputValid": side_result.status == "COMPLETE",
    }


def _reject_count(side_result, code: str) -> int:  # noqa: ANN001
    return sum(1 for item in side_result.rejected_rebuttals if item.reason == code)


def _judge_metrics(judge, audit) -> dict:  # noqa: ANN001
    completed = next((e for e in audit if e.event == "JUDGE_COMPLETED"), None)
    rejected = next((e for e in audit if e.event == "JUDGE_OUTPUT_REJECTED"), None)
    failed = next((e for e in audit if e.event == "JUDGE_FAILED"), None)
    return {
        "status": judge.status,
        "executable": judge.executable,
        "skipReason": judge.skip_reason,
        "recommendedOutcome": judge.recommended_outcome,
        "provider": judge.execution.provider if judge.execution else None,
        "model": judge.execution.model if judge.execution else None,
        "latencyMs": judge.execution.duration_ms if judge.execution else None,
        "attemptCount": (completed.metadata.get("attempt_count") if completed else None),
        "retryCount": None,
        **_tokens(judge.execution),
        "acceptedRiderClaimIds": list(judge.accepted_rider_claim_ids),
        "acceptedDriverClaimIds": list(judge.accepted_driver_claim_ids),
        "rejectedRiderClaimIds": list(judge.rejected_rider_claim_ids),
        "rejectedDriverClaimIds": list(judge.rejected_driver_claim_ids),
        "consideredRiderRebuttalIds": list(judge.considered_rider_rebuttal_ids),
        "consideredDriverRebuttalIds": list(judge.considered_driver_rebuttal_ids),
        "evidenceIds": list(judge.evidence_ids),
        "policyRuleIds": list(judge.policy_rule_ids),
        "reasoningSummary": judge.reasoning_summary,
        "uncertainties": list(judge.uncertainties),
        "requiresHumanReview": judge.requires_human_review,
        "failureReason": judge.failure_reason,
        "validationResult": "REJECTED" if rejected else ("FAILED" if failed else "VALID"),
        "validationIssues": [
            {"code": issue.code, "detail": issue.detail} for issue in judge.validation_issues
        ],
        "failureCode": failed.metadata.get("failure_code") if failed else None,
        "failureCategory": failed.metadata.get("failure_category") if failed else None,
    }


def _deterministic_snapshot(analysis) -> dict:  # noqa: ANN001
    return {
        "facts": analysis.analysis.model_dump(by_alias=True, mode="json"),
        "policyEvaluation": analysis.policy_evaluation.model_dump(by_alias=True, mode="json"),
        "ruling": analysis.resolution_recommendation.ruling,
        "recommendedAction": analysis.resolution_recommendation.recommended_action,
        "refundAmount": analysis.resolution_recommendation.refund_amount,
        "currency": analysis.resolution_recommendation.currency,
        "confidence": analysis.confidence.overall_confidence,
        "resolutionMode": analysis.resolution_mode,
        "escalationReasons": list(analysis.escalation_reasons),
    }


# ---------------------------------------------------------------------------
# Trust-boundary verification
# ---------------------------------------------------------------------------


def _verify_trust_boundaries(result, case, analysis) -> dict:  # noqa: ANN001
    """Re-derive the Judge context and prove rejected material is absent.

    The context is rebuilt here with the same builder the orchestrator uses, so
    what is inspected is the projection the Judge actually saw — not a
    reconstruction of it.
    """
    advocate_context = AdvocateContextBuilder().build(case, analysis)
    context = JudgeContextBuilder().build(
        case, analysis, advocate_context, result.rider, result.driver, result.rebuttals
    )

    rejected_claim_ids = {
        claim.claim_id for claim in [*result.rider.rejected_claims, *result.driver.rejected_claims]
    }
    context_claim_ids = {
        claim.claim_id
        for claim in [
            *context.rider.verified_claims,
            *context.driver.verified_claims,
        ]
    }

    context_rebuttal_ids = {
        item.rebuttal_id
        for item in [*context.verified_rider_rebuttals, *context.verified_driver_rebuttals]
    }
    verified_rebuttal_ids = {
        item.rebuttal_id
        for item in [
            *result.rebuttals.rider.verified_rebuttals,
            *result.rebuttals.driver.verified_rebuttals,
        ]
    }

    # Claim IDs exist in two spaces on purpose. The Stage 4 response carries the
    # advocate-authored ids ("R1"); the Judge context namespaces them by side
    # ("RIDER-R1") so a rider and a driver claim cannot collide, and the Judge
    # and the explanation service both work in that namespaced space. Comparing
    # across the two would report a mismatch that does not exist, so the expected
    # set is derived by applying the same namespacing the builder applies.
    expected_context_claim_ids = {
        namespace_claim_id(side, claim.claim_id)
        for side, claims in (
            ("RIDER", result.rider.verified_claims),
            ("DRIVER", result.driver.verified_claims),
        )
        for claim in claims
    }

    evidence_ids = {item.id for item in case.evidence}
    policy_rule_ids = {rule.rule_id for rule in analysis.policy_evaluation.evaluated_rules}

    judge = result.judge
    cited_claims = {
        *judge.accepted_rider_claim_ids,
        *judge.accepted_driver_claim_ids,
        *judge.rejected_rider_claim_ids,
        *judge.rejected_driver_claim_ids,
    }
    cited_rebuttals = {
        *judge.considered_rider_rebuttal_ids,
        *judge.considered_driver_rebuttal_ids,
    }

    return {
        "rejectedClaimsExcludedFromContext": not (rejected_claim_ids & context_claim_ids),
        "rejectedClaimIds": sorted(rejected_claim_ids),
        "contextClaimIds": sorted(context_claim_ids),
        # The context must contain exactly the verified claims, namespaced — no
        # rejected claim added, and no verified claim silently dropped.
        "contextHoldsExactlyVerifiedClaims": context_claim_ids == expected_context_claim_ids,
        "contextUnexpectedClaimIds": sorted(context_claim_ids - expected_context_claim_ids),
        "contextMissingClaimIds": sorted(expected_context_claim_ids - context_claim_ids),
        "rejectedRebuttalsExcludedFromContext": not (
            {item.target_claim_id for item in [*result.rebuttals.rider.rejected_rebuttals, *result.rebuttals.driver.rejected_rebuttals]}
            & context_rebuttal_ids
        ),
        "contextHoldsExactlyVerifiedRebuttals": context_rebuttal_ids == verified_rebuttal_ids,
        "judgeCitedOnlyVerifiedClaims": cited_claims <= context_claim_ids,
        "judgeCitedUnknownClaimIds": sorted(cited_claims - context_claim_ids),
        "judgeCitedOnlyVerifiedRebuttals": cited_rebuttals <= verified_rebuttal_ids,
        "judgeCitedUnknownRebuttalIds": sorted(cited_rebuttals - verified_rebuttal_ids),
        "judgeCitedOnlyValidEvidence": set(judge.evidence_ids) <= evidence_ids,
        "judgeCitedUnknownEvidenceIds": sorted(set(judge.evidence_ids) - evidence_ids),
        "judgeCitedOnlyApplicablePolicy": set(judge.policy_rule_ids) <= policy_rule_ids,
        "judgeCitedUnknownPolicyIds": sorted(set(judge.policy_rule_ids) - policy_rule_ids),
        "contextRiderVerifiedCount": context.rider.verified_claim_count,
        "contextDriverVerifiedCount": context.driver.verified_claim_count,
        "contextRiderRebuttalCount": len(context.verified_rider_rebuttals),
        "contextDriverRebuttalCount": len(context.verified_driver_rebuttals),
        "judgeContextTrustedHash": trusted_context_hash(context),
    }


def _scan_artifacts(case_id: str) -> dict:
    """Re-check the written artefacts for anything that must never be persisted."""
    found: dict[str, object] = {}
    for stage, filename in (("ADVOCATES", "advocates.json"), ("REBUTTALS", "rebuttals.json"), ("JUDGE", "judge.json")):
        path = ARTIFACT_ROOT / case_id / filename
        if not path.is_file():
            found[stage] = {"written": False}
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        found[stage] = {
            "written": True,
            "bytes": path.stat().st_size,
            "forbiddenKeys": forbidden_keys_in(document),
            "forbiddenValues": forbidden_values_in(document),
        }
    return found


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def run_case(
    case_id: str,
    label: str,
    ledger: AttemptLedger,
    settings: AgentSettings,
    service: CaseService,
    *,
    offline: bool = False,
) -> dict:
    case = service._load_case(case_id)  # the canonical path the app itself uses
    baseline = service.get_analysis(case_id)
    baseline_case_dump = case.model_dump(by_alias=True, mode="json")
    advocate_context = AdvocateContextBuilder().build(case, baseline)
    baseline_hash = deterministic_analysis_hash(case, baseline)

    tag: dict = {"stage": None}

    if offline:
        # The identical code path with a provider that never opens a socket, so
        # the harness itself — ledger, metric extraction, trust verification,
        # artefact scanning — is proven before any quota is spent.
        from app.agents.providers.mock import MockLlmProvider

        provider = MockLlmProvider()
    else:
        def factory(**kwargs: object) -> GuardedClient:
            return GuardedClient(httpx.Client(**kwargs), ledger, tag)

        inner = OpenAiCompatibleProvider(
            base_url=settings.base_url or "",
            model=settings.model or "",
            api_key=settings.api_key or "",
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
            retry_backoff_seconds=settings.retry_backoff_seconds,
            retry_max_backoff_seconds=settings.retry_max_backoff_seconds,
            client_factory=factory,
        )
        provider = TaggingProvider(inner, tag)

    orchestrator = ResolutionOrchestratorService(settings=settings, provider=provider)

    attempts_before = ledger.count
    started = time.monotonic()
    error: str | None = None
    result = None
    try:
        result = orchestrator.run(
            case,
            baseline,
            capture_stages=frozenset({"ADVOCATES", "REBUTTALS", "JUDGE"}),
        )
    except LiveBudgetExhausted as budget_error:
        error = f"BUDGET_EXHAUSTED: {budget_error}"
    except Exception as failure:  # noqa: BLE001 - recorded, never hidden
        error = f"{type(failure).__name__}: {failure}"
    wall_ms = int((time.monotonic() - started) * 1000)

    stage_attempts: dict[str, int] = {}
    for record in ledger.records[attempts_before:]:
        stage_attempts[record["stage"]] = stage_attempts.get(record["stage"], 0) + 1

    report: dict = {
        "caseId": case_id,
        "label": label,
        "disputeType": case.dispute_type,
        "runAt": _now(),
        "wallClockMs": wall_ms,
        "httpAttemptsThisCase": ledger.count - attempts_before,
        "httpAttemptsByStage": stage_attempts,
        "error": error,
        "baselineHash": baseline_hash,
        "baselineContextHash": trusted_context_hash(advocate_context),
        "baselineDeterministic": _deterministic_snapshot(baseline),
        "stageAttempts": stage_attempts,
    }

    # Determinism: recompute everything from the untouched fixture.
    after = service.get_analysis(case_id)
    after_case = service._load_case(case_id)
    report["afterHash"] = deterministic_analysis_hash(after_case, after)
    report["afterDeterministic"] = _deterministic_snapshot(after)
    report["deterministicUnchanged"] = report["afterHash"] == baseline_hash
    report["caseFixtureUnchanged"] = (
        after_case.model_dump(by_alias=True, mode="json") == baseline_case_dump
    )
    report["deterministicChanges"] = {
        key: {"before": report["baselineDeterministic"][key], "after": report["afterDeterministic"][key]}
        for key in report["baselineDeterministic"]
        if report["baselineDeterministic"][key] != report["afterDeterministic"][key]
    }

    if result is None:
        report["stages"] = None
        report["trustBoundaries"] = None
        report["artifacts"] = _scan_artifacts(case_id)
        return report

    report["pipeline"] = [
        {"stage": item.stage, "status": item.status} for item in result.pipeline
    ]
    report["replayMetadata"] = result.replay_metadata.model_dump(by_alias=True, mode="json")
    report["auditEvents"] = [event.event for event in result.audit]

    report["stages"] = {
        "RIDER_ADVOCATE": _advocate_metrics(result.rider),
        "DRIVER_ADVOCATE": _advocate_metrics(result.driver),
        "RIDER_REBUTTAL": _rebuttal_metrics(result.rebuttals.rider),
        "DRIVER_REBUTTAL": _rebuttal_metrics(result.rebuttals.driver),
        "JUDGE": _judge_metrics(result.judge, result.audit),
    }
    for stage, count in stage_attempts.items():
        if stage in report["stages"]:
            report["stages"][stage]["attemptCount"] = count
            report["stages"][stage]["retryCount"] = max(count - 1, 0)

    report["deterministicResolution"] = result.deterministic_resolution.model_dump(
        by_alias=True, mode="json"
    )
    report["authoritativeMatchesBaseline"] = (
        result.deterministic_resolution.refund_amount
        == report["baselineDeterministic"]["refundAmount"]
        and result.deterministic_resolution.confidence
        == report["baselineDeterministic"]["confidence"]
        and result.deterministic_resolution.resolution_mode
        == report["baselineDeterministic"]["resolutionMode"]
        and list(result.deterministic_resolution.escalation_reasons)
        == report["baselineDeterministic"]["escalationReasons"]
    )
    report["trustBoundaries"] = _verify_trust_boundaries(result, case, baseline)
    report["artifacts"] = _scan_artifacts(case_id)
    return report


def _stop_reason(report: dict) -> str | None:
    """A provider failure that must end the session, or None to continue."""
    if report.get("error"):
        return f"RUN_ERROR: {report['error']}"
    stages = report.get("stages") or {}
    for stage, metrics in stages.items():
        code = metrics.get("failureCode")
        if code in STOP_CODES:
            return f"{stage} failed with {code}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="", help="Comma-separated case ids, in order")
    parser.add_argument("--labels", default="", help="Comma-separated labels matching --cases")
    parser.add_argument("--reset-budget", action="store_true", help="Clear the attempt ledger first")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Exercise the identical harness with the mock provider. Zero network.",
    )
    args = parser.parse_args()

    load_local_env()
    settings = AgentSettings.from_environment()
    settings.validate()

    # The brief allows at most one bounded retry for 503/timeout/network. This is
    # a property of the validation session, not a change to the repository.
    if settings.max_retries > SESSION_MAX_RETRIES:
        settings = dataclasses.replace(settings, max_retries=SESSION_MAX_RETRIES)

    if args.reset_budget and LEDGER_PATH.is_file():
        LEDGER_PATH.unlink()
    ledger = AttemptLedger(LEDGER_PATH)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    case_ids = [item.strip() for item in args.cases.split(",") if item.strip()]
    labels = [item.strip() for item in args.labels.split(",") if item.strip()]
    while len(labels) < len(case_ids):
        labels.append(case_ids[len(labels)])

    service = CaseService(MockCaseRepository())
    session: dict = {
        "startedAt": _now(),
        "offline": args.offline,
        "provider": "mock" if args.offline else settings.provider,
        "model": settings.model,
        "mode": "mock" if args.offline else settings.mode,
        "maxRetriesForSession": settings.max_retries,
        "budgetAtStart": ledger.count,
        "cases": [],
        "stoppedEarly": None,
        "logicalModelCalls": 0,
    }

    for case_id, label in zip(case_ids, labels):
        print(f"\n=== {label} · {case_id} — attempts so far {ledger.count}/{MAX_HTTP_ATTEMPTS} ===", flush=True)
        report = run_case(case_id, label, ledger, settings, service, offline=args.offline)
        (RESULTS_DIR / f"{case_id}.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        stages = report.get("stages") or {}
        # A logical call is a stage that actually reached the model. Counting the
        # ledger's distinct stage tags is exact for a live run; the status
        # fallback covers the offline rehearsal, where no HTTP happens.
        logical = len(report["httpAttemptsByStage"]) or sum(
            1 for metrics in stages.values() if metrics.get("status") in ("COMPLETE", "FAILED")
        )
        session["logicalModelCalls"] += logical
        session["cases"].append(
            {
                "caseId": case_id,
                "label": label,
                "error": report["error"],
                "logicalCalls": logical,
                "httpAttempts": report["httpAttemptsThisCase"],
                "byStage": report["httpAttemptsByStage"],
                "deterministicUnchanged": report["deterministicUnchanged"],
            }
        )
        print(
            f"  logical calls: {logical} | http attempts: {report['httpAttemptsThisCase']} "
            f"({report['httpAttemptsByStage']}) | total {ledger.count}",
            flush=True,
        )
        print(f"  error: {report['error']}", flush=True)

        reason = _stop_reason(report)
        if reason:
            session["stoppedEarly"] = reason
            print(f"  STOPPING: {reason}", flush=True)
            break

    session["finishedAt"] = _now()
    session["actualHttpAttempts"] = ledger.count - session["budgetAtStart"]
    session["retries"] = sum(
        max((metrics.get("attemptCount") or 1) - 1, 0)
        for case_id in case_ids
        if (RESULTS_DIR / f"{case_id}.json").is_file()
        for metrics in (json.loads((RESULTS_DIR / f"{case_id}.json").read_text(encoding="utf-8")).get("stages") or {}).values()
    )
    session["rateLimited"] = any(
        record.get("status") == 429 for record in ledger.records
    )
    session["serverErrors"] = sum(
        1 for record in ledger.records if (record.get("status") or 0) >= 500
    )
    (RESULTS_DIR / "session.json").write_text(json.dumps(session, indent=2), encoding="utf-8")
    print("\n" + json.dumps(session, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
