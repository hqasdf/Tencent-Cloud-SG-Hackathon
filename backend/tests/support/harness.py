"""Offline test support for bounded transport, capture safety and resume.

No CLI entry point, credential probe, private records or ledger are distributed.
Tests inject synthetic providers/transports and temporary stores.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
# File-loaded test support locates its sibling module without private diagnostics.
# ``sys.path[0]`` is this directory when the file is run as a script, but it is
# not when a test loads the module by file location -- hence the explicit insert.
sys.path.insert(0, str(HERE))

from app.agents.config import AgentSettings  # noqa: E402
from app.agents.context_builder import AdvocateContextBuilder  # noqa: E402
from app.agents.judge_claims import namespace_claim_id  # noqa: E402
from app.agents.judge_context_builder import JudgeContextBuilder  # noqa: E402
from app.agents.providers.openai_compatible import OpenAiCompatibleProvider  # noqa: E402
from app.models.agent import AgentCaseContext  # noqa: E402
from app.models.analysis import CaseAnalysisResponse  # noqa: E402
from app.models.case import DisputeCase  # noqa: E402
from app.replay.artifacts import artifact_class_for_stage  # noqa: E402
from app.replay.hashing import deterministic_analysis_hash, trusted_context_hash  # noqa: E402
from app.replay.service import ReplayService  # noqa: E402
from app.replay.store import (  # noqa: E402
    ReplayArtifactStore,
    forbidden_keys_in,
    forbidden_values_in,
)
from app.repositories.case_repository import MockCaseRepository  # noqa: E402
from app.services.case_service import CaseService  # noqa: E402
from app.services.resolution_orchestrator import ResolutionOrchestratorService  # noqa: E402

from checkpoints import Provenance, plan_resume  # noqa: E402

LEDGER_PATH = BACKEND / ".pytest-scratch" / "unit-support" / "http_attempts.json"
RESULTS_DIR = BACKEND / ".pytest-scratch" / "unit-support" / "results"
ARTIFACT_ROOT = BACKEND / ".pytest-scratch" / "unit-support" / "replay"

# The case a bare invocation validates. DISP-005 is the route-deviation case
# that exercises every stage, so it is the one worth defaulting to.
DEFAULT_CASE = "DISP-005"

# FRESH FULL-PROJECT SESSION (2026-10-02). The ledger was archived to
# ledger-archive/http_attempts-through-20261002.json and reset to zero, and the
# ceiling raised to 40 to cover this session's shape: a 2-probe stability gate
# plus three full cases at 5 logical calls each (15), plus one bounded retry on
# every logical call (15) = 32 worst case, leaving 8 attempts of headroom. The
# ceiling is a hard stop, never a target. The STOP rules below are unchanged and
# still end the whole session on a second 503 / timeout / network failure, or on
# any 429. The probe gate has since been removed, so the same ceiling now leaves
# more headroom for cases.
MAX_HTTP_ATTEMPTS = 40
SESSION_MAX_RETRIES = 1

# The stages a run captures. Passed explicitly rather than implied, because
# capture writes to the repository's *real* artefact root. Capture is the write
# half of the checkpoint mechanism: it is how a stage that just succeeded becomes
# resumable. An offline rehearsal must therefore never capture, or it would
# replace a live checkpoint with a mock one -- which is exactly what happened
# twice on 2026-10-05, the second time destroying the only live DISP-005
# artefacts that existed. ``--offline --capture`` is now refused outright rather
# than merely discouraged.
CAPTURE_STAGES = frozenset({"ADVOCATES", "REBUTTALS", "JUDGE"})


def resolve_capture(flag: bool | None, *, offline: bool) -> bool:
    """Decide whether a run writes replay artefacts.

    An explicit flag always wins. Otherwise capture is on for a live run and
    **off for an offline one**, because a mock capture is never live evidence and
    writing it would destroy a real capture in place.
    """
    if flag is not None:
        return flag
    return not offline


def capture_refusal(*, offline: bool, capture: bool) -> str | None:
    """Why this combination must not run, or ``None`` if it may.

    A pure predicate so the rule can be tested without a subprocess. A mock
    capture and a live capture write to the same path, so the two cannot coexist
    and the newer silently wins -- which destroyed the only live DISP-005
    artefacts that existed, twice on 2026-10-05. ``resolve_capture`` makes the
    safe choice by default, but an explicit ``--capture`` used to be enough to
    re-arm the trap. It is now refused rather than merely discouraged.
    """
    if offline and capture:
        return (
            "a mock capture overwrites a live one in place; "
            "re-run without --capture to rehearse read-only"
        )
    return None

# Failure codes that mean "stop the whole session", per the brief's provider
# failure rules. A 503 gets one bounded retry; a second one is capacity gone.
# A 429 without Retry-After is never retried and stops at once. A timeout or a
# network error gets one bounded retry, and stops the session if it recurs --
# otherwise the orchestrator would keep walking the pipeline and spending
# attempts against a transport that is not working.
STOP_CODES = frozenset({"RATE_LIMITED", "SERVER_ERROR", "AUTHENTICATION_FAILED",
                        "ACCESS_DENIED", "MODEL_NOT_FOUND", "NOT_CONFIGURED",
                        "TIMEOUT", "NETWORK_ERROR"})


class LiveBudgetExhausted(BaseException):
    """The session's external-attempt budget is spent. Never swallowed."""


class LiveStopCondition(BaseException):
    """A provider failure the brief says must end the session at once.

    Deriving from ``BaseException`` is load-bearing here, not stylistic, and it is
    the same reason ``LiveBudgetExhausted`` does. Every stage boundary in the
    product isolates a failure with ``except Exception`` so that one bad stage
    cannot take down the rest of the pipeline -- correct for a product, wrong for
    a validation session that has been told to stop. An ``Exception``-derived stop
    would be caught at the boundary, the stage marked ``FAILED``, and the
    orchestrator would walk on to the next stage and spend more attempts against a
    provider the brief says to abandon.
    """

    def __init__(self, message: str, *, stage: str | None = None) -> None:
        super().__init__(message)
        # Carried on the exception rather than read back off the rule, so the
        # stage that stopped the session travels with the stop itself and cannot
        # be lost between raising and reporting.
        self.stage = stage


class StopRule:
    """The brief's provider-failure rules, evaluated on every HTTP attempt.

    Enforced per attempt rather than per case, because the orchestrator runs all
    five stages inside a single call and never asks the harness whether to
    continue. A stop that is only consulted once the case has finished is a stop
    that fires one stage late -- which is exactly what happened on the 2026-10-05
    run: the Rider rebuttal returned 503 twice, and the Driver rebuttal and the
    Judge still ran.

    Two rules, taken from the brief:

    * **429** -- stop immediately. The response is never handed back to the
      provider, so the provider cannot retry it either.
    * **5xx, timeout, network error** -- one bounded retry for that logical stage.
      A second failure for the *same* stage stops the session.

    The counter is keyed by stage, so a 503 on a rebuttal does not end a session
    whose advocates were fine. It lives here rather than on the client because the
    provider builds a **fresh client for every attempt** -- a counter on the
    client would reset each time and could never reach two.
    """

    def __init__(self) -> None:
        self._failures: dict[str, int] = {}
        self.triggered: str | None = None

    def check(self, stage: str, status: int | None) -> None:
        """Raise ``LiveStopCondition`` when this attempt ends the session."""
        if status == 429:
            self.triggered = (
                f"{stage}: HTTP 429 (rate limited). Stopping immediately, without a retry."
            )
            raise LiveStopCondition(self.triggered, stage=stage)

        if status is not None and status < 500:
            # Any other 4xx, and every 2xx/3xx, is not a stop condition.
            return

        # A 5xx, or a transport failure (``status`` is None for a raised request).
        self._failures[stage] = self._failures.get(stage, 0) + 1
        if self._failures[stage] < 2:
            return

        described = f"HTTP {status}" if status is not None else "a transport failure"
        self.triggered = (
            f"{stage}: second {described}. The one bounded retry is spent, so the "
            "session stops here."
        )
        raise LiveStopCondition(self.triggered, stage=stage)


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
    """An httpx.Client that records every send and refuses to exceed the budget.

    It also enforces the session's stop rule on every attempt, which is the only
    place the rule *can* be enforced: the product owns the stage sequence, and it
    has no reason to ask a validation harness whether to carry on.
    """

    def __init__(
        self,
        inner: httpx.Client,
        ledger: AttemptLedger,
        tag: dict,
        stop: StopRule | None = None,
    ) -> None:
        self._inner = inner
        self._ledger = ledger
        self._tag = tag
        # Passed in, never defaulted per client: the provider builds a new client
        # for each attempt, so a counter owned by the client would reset before it
        # could ever reach two.
        self._stop = stop if stop is not None else StopRule()

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
            # A transport failure counts toward the stop rule like a 5xx does.
            # If this is the second one for the stage, this raises and replaces
            # the original error -- which is intended: the session is over.
            self._stop.check(stage, None)
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
        # Checked *after* recording, so the ledger always shows the attempt that
        # ended the session. A 429 raises here, which means the provider never
        # receives the response and cannot retry it.
        self._stop.check(stage, response.status_code)
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
        "verifiedClaimReferences": [
            {
                "claimId": claim.claim_id,
                "evidenceIds": list(claim.evidence_ids),
                "policyRefs": list(claim.policy_refs),
                "assertedFacts": [
                    {"fact": fact.fact, "value": fact.value}
                    for fact in claim.asserted_facts
                ],
            }
            for claim in side_result.verified_claims
        ],
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
        "verifiedRebuttalReferences": [
            {
                "rebuttalId": item.rebuttal_id,
                "targetClaimId": item.target_claim_id,
                "stance": item.stance,
                "evidenceIds": list(item.evidence_ids),
                "policyRuleIds": list(item.policy_rule_ids),
                "assertedFacts": [
                    {"fact": fact.fact, "value": fact.value}
                    for fact in item.asserted_facts
                ],
            }
            for item in side_result.verified_rebuttals
        ],
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
        # Read for the same reason ``_advocate_metrics`` reads it: the stop rule
        # keys off the structured code, and without it a rebuttal that failed on a
        # 503 reported ``failureCode: None`` and was silently never a stop
        # condition. The product has always set it (``_failure_metadata``); only
        # the harness was dropping it.
        "failureCode": execution.failure_code if execution else None,
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


def _scan_artifacts(case_id: str, *, since: str) -> dict:
    """Re-check the artefacts for anything that must never be persisted.

    An artefact file on disk is not necessarily *this* run's output. The store is
    keyed by (case, stage) and a failed stage writes nothing, so a previous
    capture survives untouched -- including one captured from the mock provider
    whose hashes still match an unchanged fixture. Reporting such a file as
    "written" would overstate what this run produced, so freshness is recorded
    explicitly. ``writtenByThisRun`` is the field to trust, not ``written``.
    """
    found: dict[str, object] = {}
    for stage, filename in (("ADVOCATES", "advocates.json"), ("REBUTTALS", "rebuttals.json"), ("JUDGE", "judge.json")):
        path = ARTIFACT_ROOT / case_id / filename
        if not path.is_file():
            found[stage] = {"written": False, "writtenByThisRun": False}
            continue
        # Scanning is diagnostics. It must never be able to destroy the run's
        # report: a truncated or malformed artefact is recorded as unreadable
        # rather than allowed to raise out of here.
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            created_at = document.get("createdAt")
            # Read the captured* names, falling back to the pre-rename ones so an
            # artefact written before the field set changed still reports its
            # provenance instead of appearing to have none.
            mode = document.get("capturedMode", document.get("mode"))
            provider = document.get("capturedProvider", document.get("provider"))
            model = document.get("capturedModel", document.get("model"))
            # Ask the stage's schema whether it is live-eligible. A rebuttal
            # artefact can deliberately store a failed side for audit, but that
            # does not mean the rebuttal stage completed successfully. A generic
            # three-field check would falsely advertise it as live-ready.
            artifact_cls = artifact_class_for_stage(stage)
            artifact = artifact_cls.model_validate(document)
            found[stage] = {
                "written": True,
                "writtenByThisRun": bool(created_at) and created_at >= since,
                "artifactCreatedAt": created_at,
                "capturedMode": mode,
                "capturedProvider": provider,
                "capturedModel": model,
                # The one field that answers "may this be reported as live?".
                "liveEligible": artifact.is_live_eligible,
                "fileMtime": datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
                "bytes": path.stat().st_size,
                "forbiddenKeys": forbidden_keys_in(document),
                "forbiddenValues": forbidden_values_in(document),
            }
        except Exception as error:  # noqa: BLE001 - diagnostics must not be fatal
            found[stage] = {
                "written": True,
                "writtenByThisRun": False,
                "scanError": f"{type(error).__name__}: {error}",
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
    capture: bool = True,
) -> dict:
    case = service._load_case(case_id)  # the canonical path the app itself uses
    baseline = service.get_analysis(case_id)
    baseline_case_dump = case.model_dump(by_alias=True, mode="json")
    advocate_context = AdvocateContextBuilder().build(case, baseline)
    baseline_hash = deterministic_analysis_hash(case, baseline)

    tag: dict = {"stage": None}
    # One rule for the whole case, shared by every client the provider builds.
    stop_rule = StopRule()

    if offline:
        # The identical code path with a provider that never opens a socket, so
        # the harness itself — ledger, metric extraction, trust verification,
        # artefact scanning — is proven before any quota is spent.
        from app.agents.providers.mock import MockLlmProvider

        provider = MockLlmProvider()
    else:
        def factory(**kwargs: object) -> GuardedClient:
            return GuardedClient(httpx.Client(**kwargs), ledger, tag, stop_rule)

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

    # One store, shared by the resume planner and the orchestrator, so the two
    # cannot disagree about what is on disk.
    store = ReplayArtifactStore()
    replays = ReplayService(settings=settings, store=store)
    orchestrator = ResolutionOrchestratorService(
        settings=settings, provider=provider, replay_service=replays
    )

    # Decide how far the stored checkpoints reach BEFORE spending anything. The
    # planner asks the replay layer the same question the orchestrator is about
    # to ask it, so a checkpoint it accepts is one the orchestrator will accept.
    resume = plan_resume(
        case=case,
        analysis=baseline,
        advocate_context=advocate_context,
        store=store,
        replays=replays,
        expected=_expected_provenance(settings),
    )
    for line in resume.lines():
        print(f"  {line}", flush=True)

    attempts_before = ledger.count
    started = time.monotonic()
    run_started_at = _now()
    error: str | None = None
    stop_reason: str | None = None
    stop_stage: str | None = None
    result = None
    try:
        result = orchestrator.run(
            case,
            baseline,
            replay_mode=resume.mode,
            capture_stages=CAPTURE_STAGES if capture else frozenset(),
        )
    except LiveStopCondition as stopped:
        # The stop rule fired mid-case. The orchestrator never returned, so there
        # is no result object -- recorded as a stop rather than an error, because
        # a provider outage is not a product fault.
        stop_reason = str(stopped)
        stop_stage = stopped.stage
        error = f"STOPPED: {stop_reason}"
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
        "provider": "mock" if offline else settings.provider,
        "model": settings.model,
        "mode": "mock" if offline else settings.mode,
        "httpAttemptsThisCase": ledger.count - attempts_before,
        "httpAttemptsByStage": stage_attempts,
        "error": error,
        "baselineHash": baseline_hash,
        "baselineContextHash": trusted_context_hash(advocate_context),
        "baselineDeterministic": _deterministic_snapshot(baseline),
        "stageAttempts": stage_attempts,
        "resume": resume.report(),
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
        report["artifacts"] = _scan_artifacts(case_id, since=run_started_at)
        report["checkpoints"] = _checkpoint_state(
            case=case,
            analysis=baseline,
            advocate_context=advocate_context,
            store=store,
            replays=replays,
            settings=settings,
        )
        if stop_reason is not None:
            # A stop is not an error and must not be reported as one. What it does
            # cost is the pipeline's own metrics: ``run`` never returned, so there
            # is no ``stages`` block to read them from. That is stated rather than
            # papered over, and whatever this run did capture is surfaced below.
            report["stop"] = {
                "reason": stop_reason,
                "stage": stop_stage,
                "httpAttemptsByStage": stage_attempts,
                # The attempt that ended the session, straight off the ledger. The
                # failure category is read from this rather than guessed from the
                # stop message, so "what kind of failure was it?" has one answer.
                "lastAttempt": (
                    dict(ledger.records[-1], attemptSeq=ledger.count)
                    if ledger.count > attempts_before
                    else None
                ),
                "note": (
                    "The session stopped mid-case, so the orchestrator never returned a "
                    "result object and there are no pipeline stage metrics for this case. "
                    "The ledger breakdown, the checkpoint state and the deterministic "
                    "comparison below are still this run's own."
                ),
            }
            report["recoveredFromArtifacts"] = _recover_advocate_metadata(
                case_id, since=run_started_at
            )
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
    report["artifacts"] = _scan_artifacts(case_id, since=run_started_at)
    report["checkpoints"] = _checkpoint_state(
        case=case,
        analysis=baseline,
        advocate_context=advocate_context,
        store=store,
        replays=replays,
        settings=settings,
    )
    return report


def _expected_provenance(settings: AgentSettings) -> Provenance:
    """The model under test, as this session declares it.

    Read from the session's own settings rather than hardcoded, so a checkpoint
    is reused only when it was captured by the exact mode, provider and model
    this run is configured for. A checkpoint captured against a different model
    is not evidence about this one.
    """
    return Provenance(
        mode=settings.mode, provider=settings.provider, model=settings.model or ""
    )


def _checkpoint_state(
    *,
    case: DisputeCase,
    analysis: CaseAnalysisResponse,
    advocate_context: AgentCaseContext,
    store: ReplayArtifactStore,
    replays: ReplayService,
    settings: AgentSettings,
) -> dict:
    """What is reusable *now*.

    Called after the run, so a stage that just completed shows up as a saved
    checkpoint -- which is the evidence that resuming would skip it. Deliberately
    the same function as the pre-run decision: two implementations of "is this
    checkpoint usable?" would eventually disagree, and the one that mattered
    would be whichever ran second.
    """
    return plan_resume(
        case=case,
        analysis=analysis,
        advocate_context=advocate_context,
        store=store,
        replays=replays,
        expected=_expected_provenance(settings),
    ).report()


# The pipeline's stages in the order they run, and the checkpoint group each
# belongs to. Two names for one thing because the report is read by a human
# triaging a stage, while reuse is decided per group.
_STAGE_ORDER: tuple[str, ...] = (
    "RIDER_ADVOCATE",
    "DRIVER_ADVOCATE",
    "RIDER_REBUTTAL",
    "DRIVER_REBUTTAL",
    "JUDGE",
)

_GROUP_OF_STAGE: dict[str, str] = {
    "RIDER_ADVOCATE": "ADVOCATES",
    "DRIVER_ADVOCATE": "ADVOCATES",
    "RIDER_REBUTTAL": "REBUTTALS",
    "DRIVER_REBUTTAL": "REBUTTALS",
    "JUDGE": "JUDGE",
}


def _print_outcome(report: dict) -> None:
    """One line per stage: what happened, and whether it is now resumable.

    Written to be read at a glance after a run that may have taken minutes, so
    the shape is fixed and the vocabulary is small. "VERIFIED" means the stage's
    own check ran -- claim verification for an advocate, rebuttal validation for
    a rebuttal, output validation for the Judge.
    """
    reused = set(report.get("resume", {}).get("reused", []))
    saved = set(report.get("checkpoints", {}).get("reused", []))
    stages = report.get("stages") or {}

    for stage in _STAGE_ORDER:
        group = _GROUP_OF_STAGE[stage]
        if group in reused:
            print(f"  {stage:<16} CHECKPOINT REUSED", flush=True)
            continue
        metrics = stages.get(stage)
        if metrics is None:
            # The run stopped before this stage was reached. Said plainly rather
            # than omitted, so a reader can tell "did not run" from "not shown".
            if report.get("error"):
                print(f"  {stage:<16} not reached", flush=True)
            continue
        if metrics.get("status") == "COMPLETE":
            tail = "CHECKPOINT SAVED" if group in saved else "NOT CAPTURED"
            print(f"  {stage:<16} LIVE → COMPLETE → VERIFIED → {tail}", flush=True)
        else:
            code = metrics.get("failureCode") or metrics.get("status")
            print(f"  {stage:<16} LIVE → FAILED ({code})", flush=True)

    if report.get("error"):
        _print_failure(report)


def _print_failure(report: dict) -> None:
    """Everything needed to decide whether to resume, on one screen.

    Names the stage that failed, the model it failed against, the *category* of
    failure, what it cost, which checkpoints survived and where a resume would
    pick up. A provider outage and a refused claim are different facts and are
    never conflated here: the category comes from the failure code the pipeline
    assigned, not from the fact that something went wrong.
    """
    stop = report.get("stop") or {}
    failing = stop.get("stage")
    checkpoints = report.get("checkpoints", {})
    saved = checkpoints.get("reused", [])
    stages = report.get("stages") or {}
    last_attempt = stop.get("lastAttempt") or {}

    category = None
    if failing and failing in stages:
        category = stages[failing].get("failureCode")
    if category is None:
        category = last_attempt.get("errorType") or last_attempt.get("status")

    # Where a resume would pick up: the first stage whose checkpoint did not
    # survive. That is the whole point of the block.
    live = checkpoints.get("live", [])
    print("\n  FAILED", flush=True)
    print(f"    stage               {failing or 'UNKNOWN'}", flush=True)
    print(f"    provider            {report.get('provider') or 'openai_compatible'}", flush=True)
    print(f"    model               {report.get('model') or 'gemini-3.8-flash'}", flush=True)
    print(f"    category            {category}", flush=True)
    print(f"    attempts            {last_attempt.get('attemptSeq') or report.get('httpAttemptsThisCase')}", flush=True)
    print(f"    latency             {last_attempt.get('durationMs')} ms", flush=True)
    print(f"    valid checkpoints   {', '.join(saved) if saved else 'none'}", flush=True)
    print(f"    resume from         {live[0] if live else 'nothing left to run'}", flush=True)
    print(f"    reason              {report.get('error')}", flush=True)


def _stop_reason(report: dict) -> str | None:
    """A provider failure that must end the session, or None to continue."""
    # A stop already enforced mid-case by ``StopRule``. Reported first so the
    # reason names the stage that actually triggered it, rather than whichever
    # stage happened to fail last.
    stop = report.get("stop")
    if stop is not None:
        return stop["reason"]

    if report.get("error"):
        return f"RUN_ERROR: {report['error']}"
    stages = report.get("stages") or {}
    for stage, metrics in stages.items():
        code = metrics.get("failureCode")
        if code in STOP_CODES:
            return f"{stage} failed with {code}"
    if report.get("caseId"):
        # A non-provider validation failure does not trigger StopRule, but this
        # brief still says to stop after an incomplete case. Do not start the
        # next case just because a model-output failure is not a provider outage.
        expected_judge = (
            "PENDING_HUMAN_REVIEW"
            if report.get("baselineDeterministic", {}).get("resolutionMode") == "HUMAN_REVIEW"
            else "COMPLETE"
        )
        for stage in ("RIDER_ADVOCATE", "DRIVER_ADVOCATE", "RIDER_REBUTTAL", "DRIVER_REBUTTAL"):
            if stages.get(stage, {}).get("status") != "COMPLETE":
                return f"{stage} did not COMPLETE; stopping before the next case"
        if stages.get("JUDGE", {}).get("status") != expected_judge:
            return f"JUDGE did not reach {expected_judge}; stopping before the next case"
        if stages["JUDGE"].get("validationResult") != "VALID":
            return "JUDGE output did not pass deterministic validation"
        if not report.get("deterministicUnchanged") or not report.get("authoritativeMatchesBaseline"):
            return "Deterministic analysis changed; stopping before the next case"
        if expected_judge == "PENDING_HUMAN_REVIEW" and stages["JUDGE"].get("executable") is not False:
            return "HUMAN_REVIEW became executable; stopping before the next case"
        for stage in ("ADVOCATES", "REBUTTALS", "JUDGE"):
            capture = report.get("artifacts", {}).get(stage, {})
            if not capture.get("writtenByThisRun") or not capture.get("liveEligible"):
                return f"{stage} has no fresh, successful live capture"
    return None


def _recover_advocate_metadata(case_id: str, *, since: str) -> dict | None:
    """Per-side metadata for a case whose run was stopped before it returned.

    A mid-case stop aborts ``ResolutionOrchestratorService.run``, so there is no
    response object to read metrics from. The advocates artefact, though, was
    written by this same run and stores each side's ``AgentCallMetadata`` along
    with the verification fingerprint taken at capture time.

    Those figures are surfaced **as what they are** — stored capture metadata —
    and not reshaped into the pipeline's stage schema, which would imply the
    orchestrator had reported them. ``since`` gates on ``createdAt`` so a previous
    run's artefact can never be presented as this run's evidence.
    """
    path = ARTIFACT_ROOT / case_id / "advocates.json"
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - diagnostics must never be fatal
        return None
    created_at = document.get("createdAt")
    if not created_at or created_at < since:
        return None

    payload = document.get("payload") or {}
    sides: dict[str, object] = {}
    for side in ("rider", "driver"):
        entry = payload.get(side) or {}
        verification = entry.get("capturedVerification") or {}
        verified = set(verification.get("verifiedSignatures", []))
        output = entry.get("output") or {}
        sides[side] = {
            "execution": entry.get("execution"),
            "requestedOutcome": output.get("requestedOutcome"),
            "verifiedSignatures": verification.get("verifiedSignatures", []),
            "rejected": verification.get("rejected", []),
            # References from the model's raw output, limited to claim IDs the
            # verifier recorded at capture. This is diagnostics, not a newly
            # re-verified pipeline response (that object was never returned).
            "capturedVerifiedClaimReferences": [
                {
                    "claimId": claim.get("claimId"),
                    "evidenceIds": claim.get("evidenceIds", []),
                    "policyRefs": claim.get("policyRefs", []),
                    "assertedFacts": claim.get("assertedFacts", []),
                }
                for claim in output.get("claims", [])
                if claim.get("claimId") in verified
            ],
        }
    return {
        "note": (
            "Read from the advocates artefact this run wrote, not from a pipeline "
            "response -- the run stopped before one was returned."
        ),
        "sides": sides,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
#
# There is deliberately no availability probe here. A probe spends quota to ask
# a question the run is about to ask anyway -- the Rider Advocate's own first
# call is the availability test, and it fails in exactly the same way (a 503 is a
# 503). The probe existed when the concern was "is the provider up at all?", and
# that concern is now answered by the first stage of the first case, at no extra
# cost. Its one real finding -- that ``gemini-3.8-flash`` spends thinking tokens
# which are billed but never returned as ``content``, so a small completion
# budget yields an empty body on a healthy provider -- is recorded in
# ``docs/live-stage-report.md`` and stays relevant to reading token counts.
