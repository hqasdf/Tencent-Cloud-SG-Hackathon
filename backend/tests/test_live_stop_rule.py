"""The live session's stop rule: it must fire at the stage that caused it.

Both defects here were found by the DISP-005 live run on 2026-10-05. The Rider
rebuttal returned **503, then 503** -- one bounded retry, both failed. The brief
says that stops the session. It did not: the Driver rebuttal and the Judge still
ran, and the session only ended on the Judge's 429. Three attempts were spent
against a provider the brief had already told the harness to abandon.

Two independent causes, and fixing only one fixes nothing:

1. **Classification.** ``_rebuttal_metrics`` never emitted a ``failureCode`` key,
   so the stop rule read ``None`` for the failed rebuttal and never recognised it
   as a stop condition. The product has always set the code
   (``RebuttalOrchestratorService._failure_metadata``); only the harness dropped
   it on the floor.
2. **Timing.** ``_stop_reason`` is consulted *between cases*, but the orchestrator
   runs all five stages inside one ``run`` call. Even correctly classified, a stop
   decided after the case has finished is a stop that fires one stage late.

So the rule is now enforced **per HTTP attempt**, inside the guarded client, and
``LiveStopCondition`` derives from ``BaseException`` so no stage boundary's
``except Exception`` can swallow it.

Nothing here touches the network. The "client" is a stub that returns canned
responses or raises, and the ledger is a ``tmp_path`` file.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents.config import AgentSettings

MOCK_SETTINGS = AgentSettings(mode="mock", provider="mock")

_BACKEND = Path(__file__).resolve().parent.parent
_HARNESS = _BACKEND / ".live-validation" / "live_run.py"


# ---------------------------------------------------------------------------
# Harness + transport stubs
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def harness():
    """The live-validation harness, imported by path.

    ``.live-validation`` is not a package, so the module is loaded from its file.
    Importing runs only module-level definitions -- ``main()`` is behind an
    ``if __name__ == "__main__"`` guard -- so this costs no network.
    """
    spec = importlib.util.spec_from_file_location("live_run_stop_under_test", _HARNESS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StubResponse:
    def __init__(self, status_code: int, headers: dict | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


class _StubClient:
    """Stands in for ``httpx.Client``. Never opens a socket."""

    def __init__(self, outcomes: list) -> None:
        self._outcomes = list(outcomes)
        self.requests = 0

    def request(self, method: str, url: str, **kwargs: object):
        self.requests += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def __enter__(self) -> "_StubClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def _ledger(harness, tmp_path: Path):
    return harness.AttemptLedger(tmp_path / "http_attempts.json")


def _guarded(harness, tmp_path: Path, outcomes: list, stage: str, stop):
    """A GuardedClient wired to a stub transport and a real ledger file."""
    ledger = _ledger(harness, tmp_path)
    client = harness.GuardedClient(
        _StubClient(outcomes), ledger, {"stage": stage}, stop
    )
    return client, ledger


# ---------------------------------------------------------------------------
# The load-bearing property: a stop cannot be swallowed
# ---------------------------------------------------------------------------


def test_a_stop_condition_is_not_an_exception(harness) -> None:
    """This is why the stop works at all, so it is asserted directly.

    Every stage boundary in the product isolates failure with ``except
    Exception``. A stop that was an ``Exception`` would be caught there, the stage
    marked ``FAILED``, and the orchestrator would carry on to the next stage --
    spending exactly the attempts the stop exists to prevent.
    """
    assert issubclass(harness.LiveStopCondition, BaseException)
    assert not issubclass(harness.LiveStopCondition, Exception)

    try:
        raise harness.LiveStopCondition("stop")
    except Exception:  # noqa: BLE001 - the point is that this must NOT catch it
        pytest.fail("LiveStopCondition must not be catchable as an Exception")
    except harness.LiveStopCondition:
        pass


# ---------------------------------------------------------------------------
# StopRule semantics
# ---------------------------------------------------------------------------


def test_the_first_5xx_alone_does_not_stop_the_session(harness) -> None:
    """One 503 buys a bounded retry. Stopping here would waste the retry."""
    rule = harness.StopRule()

    rule.check("RIDER_REBUTTAL", 503)  # no raise

    assert rule.triggered is None


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_the_second_5xx_for_a_stage_stops_the_session(harness, status) -> None:
    rule = harness.StopRule()

    rule.check("RIDER_REBUTTAL", status)
    with pytest.raises(harness.LiveStopCondition) as raised:
        rule.check("RIDER_REBUTTAL", status)

    assert str(status) in str(raised.value)
    assert raised.value.stage == "RIDER_REBUTTAL"
    assert rule.triggered is not None


def test_stages_are_counted_independently(harness) -> None:
    """A rebuttal outage must not end a session whose advocates were healthy.

    The counter is per stage on purpose: grouping them would make any two
    unrelated transient failures anywhere in a case look like one dead stage.
    """
    rule = harness.StopRule()

    rule.check("RIDER_ADVOCATE", 503)
    rule.check("RIDER_REBUTTAL", 503)  # a different stage, first failure

    assert rule.triggered is None


def test_a_429_stops_immediately_without_spending_a_retry(harness) -> None:
    rule = harness.StopRule()

    with pytest.raises(harness.LiveStopCondition) as raised:
        rule.check("JUDGE", 429)

    assert "429" in str(raised.value)
    assert raised.value.stage == "JUDGE"


def test_transport_failures_count_like_a_5xx(harness) -> None:
    """``status is None`` means the request itself raised.

    A timeout and a 503 are the same fact to the brief -- "the provider is not
    answering" -- so they share a counter.
    """
    rule = harness.StopRule()

    rule.check("JUDGE", None)
    with pytest.raises(harness.LiveStopCondition):
        rule.check("JUDGE", None)


def test_a_5xx_then_a_transport_failure_is_a_second_failure(harness) -> None:
    """The retry failing differently is still the retry failing."""
    rule = harness.StopRule()

    rule.check("DRIVER_ADVOCATE", 503)
    with pytest.raises(harness.LiveStopCondition):
        rule.check("DRIVER_ADVOCATE", None)


@pytest.mark.parametrize("status", [200, 201, 204, 301, 400, 401, 403, 404, 422])
def test_success_and_client_errors_never_stop_the_session(harness, status) -> None:
    """A rejected request is a product fact, not an outage.

    Only 429 among the 4xx family is a capacity signal; everything else means the
    request was wrong, and stopping the session for that would hide a bug.
    """
    rule = harness.StopRule()

    for _ in range(5):
        rule.check("JUDGE", status)

    assert rule.triggered is None


# ---------------------------------------------------------------------------
# The rule is actually wired into the transport
# ---------------------------------------------------------------------------


def test_the_guarded_client_stops_on_the_second_attempt(harness, tmp_path) -> None:
    """Integration: the rule must be enforced in ``_send``, not merely defined."""
    stop = harness.StopRule()
    client, ledger = _guarded(
        harness, tmp_path, [_StubResponse(503), _StubResponse(503)], "RIDER_REBUTTAL", stop
    )

    client.post("https://example.invalid/chat/completions")  # first attempt passes through

    with pytest.raises(harness.LiveStopCondition):
        client.post("https://example.invalid/chat/completions")

    # The attempt that ended the session is still on the ledger -- otherwise the
    # session's cost would be understated by exactly one request.
    assert [record["status"] for record in ledger.records] == [503, 503]


def test_the_guarded_client_stops_on_a_429_without_returning_the_response(
    harness, tmp_path
) -> None:
    """The provider must never see a 429, or it could retry it itself."""
    stop = harness.StopRule()
    client, ledger = _guarded(
        harness, tmp_path, [_StubResponse(429, {"retry-after": "30"})], "JUDGE", stop
    )

    with pytest.raises(harness.LiveStopCondition):
        client.post("https://example.invalid/chat/completions")

    assert [record["status"] for record in ledger.records] == [429]


def test_the_guarded_client_counts_a_raised_request(harness, tmp_path) -> None:
    stop = harness.StopRule()
    client, ledger = _guarded(
        harness,
        tmp_path,
        [ConnectionError("boom"), ConnectionError("boom")],
        "DRIVER_REBUTTAL",
        stop,
    )

    with pytest.raises(ConnectionError):
        client.post("https://example.invalid/chat/completions")
    with pytest.raises(harness.LiveStopCondition):
        client.post("https://example.invalid/chat/completions")

    assert [record["status"] for record in ledger.records] == [None, None]


def test_the_counter_survives_a_fresh_client_per_attempt(harness, tmp_path) -> None:
    """The provider builds a new client for every attempt.

    A counter owned by the client would reset each time and could never reach two,
    so the rule would silently never fire -- which is the shape of the original
    bug. The counter lives on the shared ``StopRule`` for this reason.
    """
    stop = harness.StopRule()
    first, _ = _guarded(harness, tmp_path, [_StubResponse(503)], "JUDGE", stop)
    second, _ = _guarded(harness, tmp_path, [_StubResponse(503)], "JUDGE", stop)

    first.post("https://example.invalid/chat/completions")  # no raise
    with pytest.raises(harness.LiveStopCondition):
        second.post("https://example.invalid/chat/completions")


def test_a_successful_attempt_passes_the_response_through(harness, tmp_path) -> None:
    stop = harness.StopRule()
    client, ledger = _guarded(
        harness, tmp_path, [_StubResponse(200)], "RIDER_ADVOCATE", stop
    )

    response = client.post("https://example.invalid/chat/completions")

    assert response.status_code == 200
    assert len(ledger.records) == 1


# ---------------------------------------------------------------------------
# Fix 1 -- the rebuttal stage reports a structured failure code
# ---------------------------------------------------------------------------


def _rebuttal_side(status: str = "FAILED", failure_code: str | None = "SERVER_ERROR"):
    """A duck-typed stand-in for ``RebuttalSideResult``.

    Duck-typed rather than a real model instance so this test fails for exactly
    one reason: the harness dropping the field.
    """
    execution = None
    if failure_code is not None or status == "COMPLETE":
        execution = SimpleNamespace(
            provider="openai_compatible",
            model="gemini-3.8-flash",
            duration_ms=68875,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            failure_code=failure_code,
        )
    return SimpleNamespace(
        status=status,
        execution=execution,
        verified_rebuttals=[],
        rejected_rebuttals=[],
        conceded_target_ids=[],
        failure_reason="Rebuttal provider unavailable (openai_compatible: SERVER_ERROR).",
    )


def test_the_rebuttal_metrics_carry_the_provider_failure_code(harness) -> None:
    """The direct cause of the missed stop: the key was absent, so it read None."""
    metrics = harness._rebuttal_metrics(_rebuttal_side())

    assert metrics["failureCode"] == "SERVER_ERROR", (
        "a rebuttal that failed on a provider error must expose its failureCode, "
        "or the stop rule can never recognise it"
    )


def test_the_stop_reason_names_the_failed_rebuttal(harness) -> None:
    """End of the causal chain: the reason must name the stage that failed first.

    Before the fix this reported the *Judge*, because the Judge was the last stage
    to fail and the rebuttal was invisible to the rule.
    """
    report = {
        "error": None,
        "stages": {
            "RIDER_ADVOCATE": {"status": "COMPLETE", "failureCode": None},
            "RIDER_REBUTTAL": harness._rebuttal_metrics(_rebuttal_side()),
            "JUDGE": {"status": "FAILED", "failureCode": "RATE_LIMITED"},
        },
    }

    assert harness._stop_reason(report) == "RIDER_REBUTTAL failed with SERVER_ERROR"


def test_a_rebuttal_failure_is_not_stoppable_without_a_code(harness) -> None:
    """The guard that keeps the fix honest.

    If the code were absent the stage would still be ``FAILED``, and a rule that
    stopped on the status alone would fire for a model-output failure too -- which
    the pipeline deliberately tolerates.
    """
    report = {
        "error": None,
        "stages": {"RIDER_REBUTTAL": harness._rebuttal_metrics(_rebuttal_side(failure_code=None))},
    }

    assert harness._stop_reason(report) is None


def test_every_stage_metric_builder_emits_a_failure_code(harness) -> None:
    """Structural parity, so this class of omission cannot recur unnoticed.

    The original bug was one builder quietly not having a key the stop rule reads.
    Three builders, three chances to repeat it -- so the contract is asserted for
    all of them at once rather than for the one that broke.
    """
    advocate = harness._advocate_metrics(
        SimpleNamespace(
            status="COMPLETE",
            execution=SimpleNamespace(
                provider="openai_compatible",
                model="gemini-3.8-flash",
                duration_ms=1,
                generated_claim_count=0,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                malformed_output=False,
                failure_code=None,
            ),
            warnings=[],
            verified_claims=[],
            rejected_claims=[],
            requested_outcome="NO_REFUND",
            context_acknowledged=True,
            failure_reason=None,
        )
    )
    rebuttal = harness._rebuttal_metrics(_rebuttal_side())
    judge = harness._judge_metrics(
        SimpleNamespace(
            status="FAILED",
            executable=False,
            skip_reason=None,
            recommended_outcome=None,
            execution=None,
            accepted_rider_claim_ids=[],
            accepted_driver_claim_ids=[],
            rejected_rider_claim_ids=[],
            rejected_driver_claim_ids=[],
            considered_rider_rebuttal_ids=[],
            considered_driver_rebuttal_ids=[],
            evidence_ids=[],
            policy_rule_ids=[],
            reasoning_summary="",
            uncertainties=[],
            requires_human_review=False,
            failure_reason="x",
            validation_issues=[],
        ),
        [],
    )

    for stage, metrics in (
        ("RIDER_ADVOCATE", advocate),
        ("RIDER_REBUTTAL", rebuttal),
        ("JUDGE", judge),
    ):
        assert "failureCode" in metrics, f"{stage} metrics must expose failureCode"


# ---------------------------------------------------------------------------
# A stopped run reports a stop, not an error, and does not invent stage metrics
# ---------------------------------------------------------------------------


def test_a_stopped_case_reports_a_stop_and_no_stage_metrics(harness, monkeypatch, tmp_path) -> None:
    stop_message = "RIDER_REBUTTAL: second HTTP 503. The one bounded retry is spent."

    class _StoppingOrchestrator:
        def __init__(self, **kwargs: object) -> None:
            pass

        def run(self, *args: object, **kwargs: object):
            raise harness.LiveStopCondition(stop_message, stage="RIDER_REBUTTAL")

    monkeypatch.setattr(harness, "ResolutionOrchestratorService", _StoppingOrchestrator)
    monkeypatch.setattr(harness, "ARTIFACT_ROOT", tmp_path / "replay_artifacts")

    from app.repositories.case_repository import MockCaseRepository
    from app.services.case_service import CaseService

    report = harness.run_case(
        "DISP-005",
        "TEST",
        _ledger(harness, tmp_path),
        MOCK_SETTINGS,
        CaseService(MockCaseRepository()),
        offline=True,
    )

    assert report["stop"]["reason"] == stop_message
    assert report["stop"]["stage"] == "RIDER_REBUTTAL"
    # No pipeline metrics exist, and none are fabricated to fill the gap.
    assert report["stages"] is None
    assert report["trustBoundaries"] is None
    # A stop is a provider fact, not a product fault.
    assert report["error"].startswith("STOPPED:")
    assert harness._stop_reason(report) == stop_message
    # The deterministic layer is still verified independently of the aborted run.
    assert report["deterministicUnchanged"] is True


def test_an_incomplete_route_case_blocks_later_cases(harness) -> None:
    """Non-provider validation failure must not silently start DISP-002."""
    report = {
        "caseId": "DISP-005",
        "error": None,
        "baselineDeterministic": {"resolutionMode": "AUTO_RESOLVE"},
        "stages": {
            stage: {"status": "COMPLETE", "failureCode": None}
            for stage in ("RIDER_ADVOCATE", "DRIVER_ADVOCATE", "RIDER_REBUTTAL", "DRIVER_REBUTTAL")
        },
    }
    report["stages"]["JUDGE"] = {
        "status": "FAILED",
        "failureCode": "SCHEMA_VALIDATION_FAILED",
        "validationResult": "REJECTED",
    }

    assert "JUDGE did not reach COMPLETE" in harness._stop_reason(report)


def test_successful_human_review_is_not_misclassified_as_failure(harness) -> None:
    report = {
        "caseId": "DISP-003",
        "error": None,
        "baselineDeterministic": {"resolutionMode": "HUMAN_REVIEW"},
        "stages": {
            stage: {"status": "COMPLETE", "failureCode": None}
            for stage in ("RIDER_ADVOCATE", "DRIVER_ADVOCATE", "RIDER_REBUTTAL", "DRIVER_REBUTTAL")
        },
        "deterministicUnchanged": True,
        "authoritativeMatchesBaseline": True,
        "artifacts": {
            stage: {"writtenByThisRun": True, "liveEligible": True}
            for stage in ("ADVOCATES", "REBUTTALS", "JUDGE")
        },
    }
    report["stages"]["JUDGE"] = {
        "status": "PENDING_HUMAN_REVIEW",
        "executable": False,
        "failureCode": None,
        "validationResult": "VALID",
    }

    assert harness._stop_reason(report) is None
    report["stages"]["JUDGE"]["executable"] = True
    assert "HUMAN_REVIEW became executable" in harness._stop_reason(report)


def test_complete_case_without_successful_rebuttals_capture_stops(harness) -> None:
    report = {
        "caseId": "DISP-005",
        "error": None,
        "baselineDeterministic": {"resolutionMode": "AUTO_RESOLVE"},
        "stages": {
            stage: {"status": "COMPLETE", "failureCode": None}
            for stage in ("RIDER_ADVOCATE", "DRIVER_ADVOCATE", "RIDER_REBUTTAL", "DRIVER_REBUTTAL")
        },
        "deterministicUnchanged": True,
        "authoritativeMatchesBaseline": True,
        "artifacts": {
            stage: {"writtenByThisRun": True, "liveEligible": True}
            for stage in ("ADVOCATES", "REBUTTALS", "JUDGE")
        },
    }
    report["stages"]["JUDGE"] = {
        "status": "COMPLETE",
        "failureCode": None,
        "validationResult": "VALID",
    }
    assert harness._stop_reason(report) is None
    report["artifacts"]["REBUTTALS"]["liveEligible"] = False
    assert harness._stop_reason(report) == "REBUTTALS has no fresh, successful live capture"


def test_the_enforced_stop_outranks_a_later_stage_failure(harness) -> None:
    """The reason must name the stage that stopped the run, not the last to fail."""
    report = {
        "error": "STOPPED: RIDER_REBUTTAL: second HTTP 503.",
        "stop": {"reason": "RIDER_REBUTTAL: second HTTP 503.", "stage": "RIDER_REBUTTAL"},
        "stages": {"JUDGE": {"status": "FAILED", "failureCode": "RATE_LIMITED"}},
    }

    assert harness._stop_reason(report) == "RIDER_REBUTTAL: second HTTP 503."


# ---------------------------------------------------------------------------
# Recovery from this run's own artefact
# ---------------------------------------------------------------------------


def _write_advocates_artifact(root: Path, created_at: str) -> None:
    case_dir = root / "DISP-005"
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "advocates.json").write_text(
        json.dumps(
            {
                "createdAt": created_at,
                "payload": {
                    "rider": {
                        "execution": {"inputTokens": 4462, "totalTokens": 6431},
                        "capturedVerification": {
                            "verifiedSignatures": ["R1", "R2"],
                            "rejected": [],
                        },
                    },
                    "driver": {
                        "execution": {"inputTokens": 4532, "totalTokens": 6524},
                        "capturedVerification": {
                            "verifiedSignatures": ["D1", "D2", "D3"],
                            "rejected": [],
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def test_recovery_surfaces_this_runs_own_capture_metadata(harness, monkeypatch, tmp_path) -> None:
    root = tmp_path / "replay_artifacts"
    _write_advocates_artifact(root, "2026-10-05T07:15:00+00:00")
    monkeypatch.setattr(harness, "ARTIFACT_ROOT", root)

    recovered = harness._recover_advocate_metadata(
        "DISP-005", since="2026-10-05T07:00:00+00:00"
    )

    assert recovered["sides"]["rider"]["execution"]["inputTokens"] == 4462
    assert recovered["sides"]["driver"]["verifiedSignatures"] == ["D1", "D2", "D3"]


def test_recovery_ignores_a_previous_runs_artefact(harness, monkeypatch, tmp_path) -> None:
    """Stale evidence is worse than no evidence, so freshness is enforced here."""
    root = tmp_path / "replay_artifacts"
    _write_advocates_artifact(root, "2026-10-04T23:15:00+00:00")
    monkeypatch.setattr(harness, "ARTIFACT_ROOT", root)

    assert (
        harness._recover_advocate_metadata("DISP-005", since="2026-10-05T07:00:00+00:00")
        is None
    )


def test_recovery_is_absent_when_nothing_was_captured(harness, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(harness, "ARTIFACT_ROOT", tmp_path / "nothing_here")

    assert (
        harness._recover_advocate_metadata("DISP-005", since="2026-10-05T07:00:00+00:00")
        is None
    )


# ---------------------------------------------------------------------------
# An offline rehearsal must not overwrite a live capture
# ---------------------------------------------------------------------------


def test_an_offline_rehearsal_defaults_to_no_capture(harness) -> None:
    """Capture writes to the repository's real artefact root.

    ``ReplayArtifactStore`` derives that root from its own module path and offers
    no override, so redirecting ``ARTIFACT_ROOT`` in the harness changes only what
    it *reads*. An offline rehearsal that captures therefore replaces a live
    capture with a mock one -- which happened twice on 2026-10-05, the second time
    destroying the only live advocates/rebuttals artefacts that existed.
    """
    assert harness.resolve_capture(None, offline=True) is False
    assert harness.resolve_capture(None, offline=False) is True
    # An explicit flag always wins, in both directions.
    assert harness.resolve_capture(True, offline=True) is True
    assert harness.resolve_capture(False, offline=False) is False


def test_capture_can_be_disabled_for_a_case(harness, monkeypatch, tmp_path) -> None:
    """``capture=False`` must reach the orchestrator, not just the CLI."""
    seen: dict = {}

    class _RecordingOrchestrator:
        def __init__(self, **kwargs: object) -> None:
            pass

        def run(self, *args: object, capture_stages=None, **kwargs: object):
            seen["capture_stages"] = capture_stages
            raise harness.LiveStopCondition("stop", stage="JUDGE")

    monkeypatch.setattr(harness, "ResolutionOrchestratorService", _RecordingOrchestrator)
    monkeypatch.setattr(harness, "ARTIFACT_ROOT", tmp_path / "replay_artifacts")

    from app.repositories.case_repository import MockCaseRepository
    from app.services.case_service import CaseService

    harness.run_case(
        "DISP-005",
        "TEST",
        _ledger(harness, tmp_path),
        MOCK_SETTINGS,
        CaseService(MockCaseRepository()),
        offline=True,
        capture=False,
    )

    assert seen["capture_stages"] == frozenset()


def test_capture_is_requested_when_not_disabled(harness, monkeypatch, tmp_path) -> None:
    """The live path must still capture, or the run would produce no evidence.

    ``offline=True`` is used only because a non-offline run constructs a real
    provider, which needs credentials; the flag under test is ``capture``, and it
    is passed explicitly here.
    """
    seen: dict = {}

    class _RecordingOrchestrator:
        def __init__(self, **kwargs: object) -> None:
            pass

        def run(self, *args: object, capture_stages=None, **kwargs: object):
            seen["capture_stages"] = capture_stages
            raise harness.LiveStopCondition("stop", stage="JUDGE")

    monkeypatch.setattr(harness, "ResolutionOrchestratorService", _RecordingOrchestrator)
    monkeypatch.setattr(harness, "ARTIFACT_ROOT", tmp_path / "replay_artifacts")

    from app.repositories.case_repository import MockCaseRepository
    from app.services.case_service import CaseService

    harness.run_case(
        "DISP-005",
        "TEST",
        _ledger(harness, tmp_path),
        MOCK_SETTINGS,
        CaseService(MockCaseRepository()),
        offline=True,
        capture=True,
    )

    assert seen["capture_stages"] == harness.CAPTURE_STAGES
