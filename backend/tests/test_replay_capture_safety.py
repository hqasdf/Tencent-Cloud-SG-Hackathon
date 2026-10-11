"""Capture-safety rules: a stage that never ran must leave no artefact.

Both rules here come from a real failed live run (DISP-005, 2026-10-05). The Rider
advocate returned 503 twice and the session stopped. The rebuttal stage was
therefore ``NOT_RUN`` — and the orchestrator wrote ``rebuttals.json`` anyway,
carrying ``provider: ""`` and ``model: null``. A file like that names a stage that
never executed, and it can later be mistaken for a capture of real model output.

Two things are locked down:

1. **The availability probe's token budget.** ``gemini-3.8-flash`` spends thinking
   tokens that are billed but never returned as ``content``. At ``max_tokens: 16``
   the thinking consumed the whole allowance, so a healthy provider answered 200
   with an *empty* body and the gate scored it as a failure.
2. **``NOT_RUN`` writes nothing**, and a capture may only be called live when it
   records the mode, the provider and the model that produced it.

Nothing here touches the network: the artefact store is a ``tmp_path``, and the
providers are a mock or an object that fails on purpose.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.agents.config import AgentSettings
from app.agents.context_builder import AdvocateContextBuilder
from app.agents.providers.mock import MockLlmProvider
from app.models.rebuttal import (
    MAX_REBUTTAL_ROUNDS,
    RebuttalRunResponse,
    RebuttalSideResult,
    RebuttalVerificationSummary,
)
from app.replay.artifacts import AdvocateArtifact, RebuttalArtifact
from app.replay.service import ReplayService
from app.replay.store import ReplayArtifactStore
from app.repositories.case_repository import MockCaseRepository
from app.services.audit import AuditTrail
from app.services.case_service import CaseService
from app.services.dispute_analysis import DisputeAnalysisService
from app.services.resolution_orchestrator import ResolutionOrchestratorService

ROUTE_CASE = "DISP-005"

MOCK_SETTINGS = AgentSettings(mode="mock", provider="mock")

_BACKEND = Path(__file__).resolve().parent.parent
_HARNESS = _BACKEND / "tests" / "support" / "harness.py"


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class _FailRiderAdvocate:
    """Fails the Rider advocate's call and leaves the Driver's intact.

    This is the shape of the live failure that motivated the rule: one side
    fails, so the rebuttal stage is never reached and is reported ``NOT_RUN``.
    """

    name = "failing-rider"

    def __init__(self) -> None:
        self._mock = MockLlmProvider()

    def complete(self, request):  # noqa: ANN001, ANN201 - mirrors the protocol
        metadata = request.metadata or {}
        is_rebuttal = metadata.get("role") == "rebuttal"
        if metadata.get("side") == "RIDER" and not is_rebuttal:
            raise RuntimeError("simulated provider outage on the Rider advocate")
        return self._mock.complete(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(case_id: str):
    case = CaseService(MockCaseRepository())._load_case(case_id)
    analysis = DisputeAnalysisService().analyze(case)
    context = AdvocateContextBuilder().build(case, analysis)
    return case, analysis, context


def _not_run_rebuttals(case) -> RebuttalRunResponse:
    """The rebuttal response the pipeline produces when neither side ran."""
    reason = (
        "Rebuttal did not run: RIDER did not produce an initial argument, so "
        "there is no verified case to cross-examine."
    )
    return RebuttalRunResponse(
        case_id=case.id,
        dispute_type=case.dispute_type,
        round=MAX_REBUTTAL_ROUNDS,
        max_rounds=MAX_REBUTTAL_ROUNDS,
        rider=RebuttalSideResult(side="RIDER", status="NOT_RUN", failure_reason=reason),
        driver=RebuttalSideResult(side="DRIVER", status="NOT_RUN", failure_reason=reason),
        verification_summary=RebuttalVerificationSummary(
            verified_count=0, rejected_count=0, rejection_reasons=[]
        ),
    )


@pytest.fixture
def store(tmp_path: Path) -> ReplayArtifactStore:
    """An artefact store inside tmp_path, never the repository directory."""
    return ReplayArtifactStore(
        root=tmp_path / "replay_artifacts", curated_root=tmp_path / "demo_replays"
    )


@pytest.fixture(scope="module")
def harness():
    """The live-validation harness, imported by path.

    The test-support directory is file-loaded, so the module is loaded from its file.
    Importing it runs only module-level definitions — there is no CLI entry point — so this costs no network.
    """
    spec = importlib.util.spec_from_file_location("live_run_under_test", _HARNESS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Fix 2 — NOT_RUN writes no artefact
# ---------------------------------------------------------------------------


def test_capture_rebuttals_refuses_when_no_side_ran(store) -> None:
    case, analysis, context = _load(ROUTE_CASE)
    service = ReplayService(store=store, settings=MOCK_SETTINGS)

    written = service.capture_rebuttals(
        case, analysis, context, _not_run_rebuttals(case), {}
    )

    assert written is None
    assert not store.path_for(case.id, "REBUTTALS").exists()


def test_capture_rebuttals_records_no_audit_event_when_no_side_ran(store) -> None:
    """Silence matters: a write event for a file that does not exist would lie."""
    case, analysis, context = _load(ROUTE_CASE)
    service = ReplayService(store=store, settings=MOCK_SETTINGS)
    audit = AuditTrail()

    service.capture_rebuttals(case, analysis, context, _not_run_rebuttals(case), {}, audit)

    assert [event.event for event in audit.events] == []


def test_capture_rebuttals_still_stores_a_side_that_ran_and_failed(store) -> None:
    """A side that ran and failed is a real outcome, and stays storable.

    The rule is "did not run writes nothing", not "any failure writes nothing" —
    a failed rebuttal does not block the Judge, so reproducing it faithfully is
    the honest thing to do.
    """
    case, analysis, context = _load(ROUTE_CASE)
    service = ReplayService(store=store, settings=MOCK_SETTINGS)
    rebuttals = RebuttalRunResponse(
        case_id=case.id,
        dispute_type=case.dispute_type,
        round=MAX_REBUTTAL_ROUNDS,
        max_rounds=MAX_REBUTTAL_ROUNDS,
        rider=RebuttalSideResult(
            side="RIDER", status="FAILED", failure_reason="provider unavailable"
        ),
        driver=RebuttalSideResult(side="DRIVER", status="NOT_RUN"),
        verification_summary=RebuttalVerificationSummary(
            verified_count=0, rejected_count=0, rejection_reasons=[]
        ),
    )

    written = service.capture_rebuttals(case, analysis, context, rebuttals, {})

    assert written is not None
    assert store.path_for(case.id, "REBUTTALS").exists()


def test_capture_advocates_refuses_a_side_that_is_not_complete(store) -> None:
    """Raw output alone is not enough — the side must have completed.

    A side can return raw output and still not be ``COMPLETE``; capturing that
    would store an argument the pipeline itself refused to use.
    """
    case, analysis, context = _load(ROUTE_CASE)
    service = ReplayService(store=store, settings=MOCK_SETTINGS)

    class _Run:
        class agent_run:  # noqa: N801 - stand-in for the run metadata
            provider = "openai_compatible"
            model = "gemini-3.8-flash"

        class rider:  # noqa: N801
            status = "FAILED"

        class driver:  # noqa: N801
            status = "COMPLETE"

    written = service.capture_advocates(
        case, analysis, context, _Run(), {"RIDER": object(), "DRIVER": object()}
    )

    assert written is None
    assert not store.path_for(case.id, "ADVOCATES").exists()


def test_a_failed_rider_advocate_writes_no_rebuttals_artifact(store) -> None:
    """End-to-end reproduction of the live failure.

    One advocate fails, so the rebuttal stage is never reached. Nothing may be
    captured for a stage that did not execute.
    """
    case, analysis, _ = _load(ROUTE_CASE)
    orchestrator = ResolutionOrchestratorService(
        settings=MOCK_SETTINGS,
        provider=_FailRiderAdvocate(),
        replay_service=ReplayService(store=store, settings=MOCK_SETTINGS),
    )

    result = orchestrator.run(
        case,
        analysis,
        capture_stages=frozenset({"ADVOCATES", "REBUTTALS", "JUDGE"}),
    )

    assert result.rider.status == "FAILED"
    assert result.rebuttals.rider.status == "NOT_RUN"
    assert result.rebuttals.driver.status == "NOT_RUN"

    assert not store.path_for(case.id, "REBUTTALS").exists(), (
        "REBUTTALS_NOT_RUN must never create rebuttals.json"
    )
    assert not store.path_for(case.id, "ADVOCATES").exists()
    assert not store.path_for(case.id, "JUDGE").exists()

    events = [event.event for event in result.audit]
    assert "REBUTTALS_NOT_RUN" in events
    assert "REPLAY_ARTIFACT_WRITTEN" not in events


# ---------------------------------------------------------------------------
# Fix 2 — live eligibility requires mode, provider and model
# ---------------------------------------------------------------------------


def _captured_document(store: ReplayArtifactStore) -> dict:
    """A real capture, read back from disk."""
    case, analysis, _ = _load(ROUTE_CASE)
    orchestrator = ResolutionOrchestratorService(
        settings=MOCK_SETTINGS,
        provider=MockLlmProvider(),
        replay_service=ReplayService(store=store, settings=MOCK_SETTINGS),
    )
    orchestrator.run(case, analysis, capture_stages=frozenset({"ADVOCATES"}))
    return json.loads(store.path_for(case.id, "ADVOCATES").read_text(encoding="utf-8"))


def test_a_mock_capture_is_never_live_eligible(store) -> None:
    """The mock capture is a valid artefact, but it is not live evidence."""
    document = _captured_document(store)
    artifact = AdvocateArtifact.model_validate(document)

    assert artifact.captured_mode == "mock"
    assert artifact.is_live_eligible is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"capturedMode": "mock", "capturedProvider": "openai_compatible", "capturedModel": "gemini-3.8-flash"},
        {"capturedMode": "real", "capturedProvider": "", "capturedModel": "gemini-3.8-flash"},
        {"capturedMode": "real", "capturedProvider": "openai_compatible", "capturedModel": None},
        {"capturedMode": None, "capturedProvider": "openai_compatible", "capturedModel": "gemini-3.8-flash"},
    ],
)
def test_live_eligibility_requires_all_three_fields(store, overrides) -> None:
    document = {**_captured_document(store), **overrides}

    assert AdvocateArtifact.model_validate(document).is_live_eligible is False


def test_a_complete_live_capture_is_live_eligible(store) -> None:
    document = {
        **_captured_document(store),
        "capturedMode": "real",
        "capturedProvider": "openai_compatible",
        "capturedModel": "gemini-3.8-flash",
    }

    assert AdvocateArtifact.model_validate(document).is_live_eligible is True


def test_an_artefact_written_before_the_field_rename_still_loads(store) -> None:
    """Old artefacts carry ``provider``/``model``; they must keep working.

    Refusing to load them would strand every capture already on disk, and the
    replay feature would appear broken for a purely cosmetic reason.
    """
    document = _captured_document(store)
    legacy = {
        key: value
        for key, value in document.items()
        if key not in {"capturedMode", "capturedProvider", "capturedModel"}
    }
    legacy["provider"] = "openai_compatible"
    legacy["model"] = "gemini-3.8-flash"

    artifact = AdvocateArtifact.model_validate(legacy)

    assert artifact.captured_provider == "openai_compatible"
    assert artifact.captured_model == "gemini-3.8-flash"
    # No mode was recorded, so it cannot be called live — which is the point.
    assert artifact.captured_mode is None
    assert artifact.is_live_eligible is False


def test_provider_and_model_properties_mirror_the_captured_fields(store) -> None:
    """The pre-rename attribute names stay readable for existing callers."""
    document = {
        **_captured_document(store),
        "capturedMode": "real",
        "capturedProvider": "openai_compatible",
        "capturedModel": "gemini-3.8-flash",
    }
    artifact = AdvocateArtifact.model_validate(document)

    assert artifact.provider == artifact.captured_provider
    assert artifact.model == artifact.captured_model


def _rebuttal_document(store: ReplayArtifactStore) -> dict:
    """A complete mock rebuttal capture under the test's isolated store."""
    case, analysis, _ = _load(ROUTE_CASE)
    ResolutionOrchestratorService(
        settings=MOCK_SETTINGS,
        provider=MockLlmProvider(),
        replay_service=ReplayService(store=store, settings=MOCK_SETTINGS),
    ).run(case, analysis, capture_stages=frozenset({"REBUTTALS"}))
    return json.loads(store.path_for(case.id, "REBUTTALS").read_text(encoding="utf-8"))


@pytest.mark.parametrize("failed_side", ["rider", "driver"])
def test_failed_rebuttal_capture_is_audit_only_not_live_eligible(store, failed_side) -> None:
    """Provider/model/mode alone cannot mark an incomplete rebuttal live-ready."""
    document = _rebuttal_document(store)
    document.update(
        capturedMode="real",
        capturedProvider="openai_compatible",
        capturedModel="gemini-3.8-flash",
    )
    document["payload"][failed_side]["status"] = "FAILED"
    document["payload"][failed_side]["output"] = None
    artifact = RebuttalArtifact.model_validate(document)

    assert artifact.captured_mode == "real"
    assert artifact.captured_provider == "openai_compatible"
    assert artifact.captured_model == "gemini-3.8-flash"
    assert artifact.is_live_eligible is False


def test_both_complete_rebuttals_with_raw_output_are_live_eligible(store) -> None:
    document = _rebuttal_document(store)
    document.update(
        capturedMode="real",
        capturedProvider="openai_compatible",
        capturedModel="gemini-3.8-flash",
    )
    artifact = RebuttalArtifact.model_validate(document)

    assert artifact.payload.rider.status == "COMPLETE"
    assert artifact.payload.driver.status == "COMPLETE"
    assert artifact.is_live_eligible is True


def test_rebuttal_with_missing_raw_output_is_not_live_eligible(store) -> None:
    document = _rebuttal_document(store)
    document.update(
        capturedMode="real",
        capturedProvider="openai_compatible",
        capturedModel="gemini-3.8-flash",
    )
    document["payload"]["rider"]["output"] = None

    assert RebuttalArtifact.model_validate(document).is_live_eligible is False


def test_harness_scanner_uses_stage_specific_eligibility(store, harness, monkeypatch) -> None:
    """The JSON report must not reimplement the weaker provenance-only check."""
    document = _rebuttal_document(store)
    document.update(
        capturedMode="real",
        capturedProvider="openai_compatible",
        capturedModel="gemini-3.8-flash",
    )
    document["payload"]["rider"]["status"] = "FAILED"
    document["payload"]["rider"]["output"] = None
    store.path_for(ROUTE_CASE, "REBUTTALS").write_text(
        json.dumps(document), encoding="utf-8"
    )
    monkeypatch.setattr(harness, "ARTIFACT_ROOT", store.root)

    found = harness._scan_artifacts(ROUTE_CASE, since="2000-01-01T00:00:00+00:00")

    assert found["REBUTTALS"]["writtenByThisRun"] is True
    assert found["REBUTTALS"]["liveEligible"] is False
