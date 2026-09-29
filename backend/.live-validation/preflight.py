"""Preflight for the final live Gemini validation. MAKES ZERO EXTERNAL CALLS.

Run from ``backend/``:

    python .live-validation/preflight.py

Everything here is a local read: the ``.env`` file, the case fixtures, and the
deterministic analysis service. There is deliberately no ``/models`` listing, no
hello request and no connectivity probe — the first real production call is
intended to be the connectivity test, and burning a call on a probe would spend
budget without validating anything.

Case selection is *semantic*: it is derived from the deterministic analysis the
pipeline actually computes, not from the display status on the fixture. The two
can disagree, and only the first one matters to the pipeline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.config import AgentSettings  # noqa: E402
from app.env import describe_env_source, load_local_env  # noqa: E402
from app.repositories.case_repository import MockCaseRepository  # noqa: E402
from app.services.case_service import CaseService  # noqa: E402

EXPECTED_MODE = "real"
EXPECTED_PROVIDER = "openai_compatible"
EXPECTED_MODEL = "gemini-3.8-flash"


def main() -> int:
    loaded = load_local_env()
    settings = AgentSettings.from_environment()

    # `validate()` raises when real mode is not usable. It is the same check the
    # provider registry runs before handing back a live provider.
    configuration_ok = True
    configuration_error = None
    try:
        settings.validate()
    except Exception as error:  # noqa: BLE001 - reported, not raised
        configuration_ok = False
        configuration_error = str(error)

    described = settings.describe()

    # No mock fallback, and nothing about the credential beyond "present".
    real_mode_confirmed = (
        settings.mode == EXPECTED_MODE
        and settings.provider == EXPECTED_PROVIDER
        and settings.model == EXPECTED_MODEL
        and configuration_ok
    )

    service = CaseService(MockCaseRepository())
    cases: list[dict] = []
    for summary in service.list_case_summaries():
        case = service._load_case(summary.id)  # canonical timeline, as the app uses
        analysis = service.get_analysis(summary.id)
        facts = analysis.analysis
        cases.append(
            {
                "caseId": case.id,
                "disputeType": case.dispute_type,
                "fixtureStatus": case.status,
                "resolutionMode": analysis.resolution_mode,
                "recommendedAction": analysis.resolution_recommendation.recommended_action,
                "refundAmount": analysis.resolution_recommendation.refund_amount,
                "currency": analysis.resolution_recommendation.currency,
                "confidence": analysis.confidence.overall_confidence,
                "escalationReasons": list(analysis.escalation_reasons),
                "missingEvidenceIds": list(facts.missing_evidence_ids),
                "contradictoryEvidence": bool(facts.contradictory_evidence),
                "failedRules": list(analysis.policy_evaluation.failed_rules),
            }
        )

    def clean(candidate: dict) -> bool:
        """Analysis available, required evidence present, and auto-resolvable."""
        return (
            not candidate["missingEvidenceIds"]
            and candidate["resolutionMode"] != "HUMAN_REVIEW"
        )

    routes = [c for c in cases if c["disputeType"] == "route_deviation" and clean(c)]
    no_shows = [c for c in cases if c["disputeType"] == "no_show_charge" and clean(c)]
    human_reviews = [c for c in cases if c["resolutionMode"] == "HUMAN_REVIEW"]

    def prefer(candidates: list[dict], preferred_id: str) -> dict | None:
        """Prefer the named case only if it still satisfies the conditions."""
        for candidate in candidates:
            if candidate["caseId"] == preferred_id:
                return candidate
        return candidates[0] if candidates else None

    selection = {
        "route": prefer(routes, "DISP-005"),
        "noShow": prefer(no_shows, "DISP-002"),
        "humanReview": human_reviews[0] if human_reviews else None,
        "humanReviewAvailable": bool(human_reviews),
        "routeCandidates": [c["caseId"] for c in routes],
        "noShowCandidates": [c["caseId"] for c in no_shows],
        "humanReviewCandidates": [c["caseId"] for c in human_reviews],
    }

    report = {
        "envSource": describe_env_source(),
        "envFileLoaded": loaded,
        "configuration": {
            "mode": settings.mode,
            "provider": settings.provider,
            "model": settings.model,
            "baseUrl": settings.base_url,
            "timeoutSeconds": settings.timeout_seconds,
            "temperature": settings.temperature,
            "maxTokens": settings.max_tokens,
            "maxRetries": settings.max_retries,
            "credentialConfigured": described["credential_configured"],
            "realModeConfirmed": real_mode_confirmed,
            "configurationError": configuration_error,
        },
        "cases": cases,
        "selection": selection,
    }
    print(json.dumps(report, indent=2))
    return 0 if real_mode_confirmed else 1


if __name__ == "__main__":
    raise SystemExit(main())
