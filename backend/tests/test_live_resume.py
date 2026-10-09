"""Resume from validated checkpoints, instead of re-running every AI stage.

A live validation session stops on a provider failure, and the brief that governs
it says to stop rather than to push on. That makes the cost of a *restart* the
thing that matters: without resume, every attempt after a stop re-pays for the
stages that already succeeded, against a provider that is demonstrably flaky.

These tests prove the resume decision, and they prove it the only way that is
worth anything here: by running the *real* capture path to produce the
checkpoints, and the *real* replay layer to judge them. Nothing is stubbed except
the provider, which never opens a socket.

The provider is the mock -- the same deterministic provider the offline suite
already uses -- wearing the live model's provenance. That is not a trick to make
the tests pass: capture stamps an artefact with whatever the provider reports, so
a mock capture is *by construction* not live evidence. These tests are about the
harness's decision, so the artefacts have to look like live ones for the REUSED
branch to be reachable at all. The NOT_ELIGIBLE tests then remove that provenance
and assert the checkpoint is refused.

Nothing here touches the network. ``-p netguard`` blocks all non-loopback egress
for the whole suite, so "zero external calls" is enforced rather than asserted.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.agents.config import AgentSettings
from app.agents.context_builder import AdvocateContextBuilder
from app.agents.providers.mock import MockLlmProvider
from app.models.replay import NO_REPLAY
from app.replay.plan import build_plan
from app.replay.service import ReplayService
from app.replay.store import ReplayArtifactStore
from app.repositories.case_repository import MockCaseRepository
from app.services.case_service import CaseService
from app.services.resolution_orchestrator import ResolutionOrchestratorService

MODE = "real"
PROVIDER = "openai_compatible"
MODEL = "gemini-3.8-flash"

# The session's own settings. ``mode="real"`` is what makes the advocates service
# stamp ``captured_model`` at all -- in mock mode it records ``None``, which is
# why a mock capture can never be live evidence.
LIVE_SETTINGS = AgentSettings(mode=MODE, provider=PROVIDER, model=MODEL)

ROUTE_CASE = "DISP-005"

_BACKEND = Path(__file__).resolve().parent.parent
_HARNESS = _BACKEND / ".live-validation" / "live_run.py"
_CHECKPOINTS = _BACKEND / ".live-validation" / "checkpoints.py"


def _load(name: str, path: Path):
    """Import a module from a file path, as the other harness tests do.

    The module is registered in ``sys.modules`` *before* it is executed, and that
    is load-bearing rather than tidy: ``@dataclass`` resolves a field's
    annotations by looking up ``sys.modules[cls.__module__]``, so a module that
    is executed without being registered raises ``AttributeError: 'NoneType'
    object has no attribute '__dict__'`` from inside the dataclasses machinery.
    ``live_run.py`` gets away without this because it defines no dataclasses;
    ``checkpoints.py`` defines three.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checkpoints():
    """The resume planner, imported by path (``.live-validation`` is not a package)."""
    return _load("checkpoints_under_test", _CHECKPOINTS)


@pytest.fixture(scope="module")
def harness():
    return _load("live_run_resume_under_test", _HARNESS)


class _LiveLookingMock:
    """The deterministic mock provider, reporting the live model's provenance.

    Delegates every completion to ``MockLlmProvider``, so the output is derived
    from the real context and the real validators accept it. Only the reported
    ``provider_name``/``model_name`` differ, because those are what capture
    writes into the artefact's provenance fields.
    """

    name = PROVIDER

    def __init__(self) -> None:
        self._inner = MockLlmProvider()
        self.calls = 0

    def complete(self, request):  # noqa: ANN001, ANN201
        self.calls += 1
        completion = self._inner.complete(request)
        return dataclasses.replace(
            completion, provider_name=PROVIDER, model_name=MODEL
        )


class _ProviderThatMustNotBeCalled:
    """Fails the test the moment a model is asked for anything.

    The call is counted rather than merely raised, because every stage boundary
    in the orchestrator isolates a failure with ``except Exception`` -- so a
    raised error would be swallowed, the stage marked ``FAILED``, and the test
    would have to infer what happened from the wreckage.
    """

    name = PROVIDER

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request):  # noqa: ANN001, ANN201
        self.calls += 1
        raise AssertionError("a reused checkpoint reached the provider")


@pytest.fixture
def scratch(tmp_path):
    """A private artefact root, so no test can touch the repository's captures."""
    return ReplayArtifactStore(root=tmp_path / "artifacts")


@pytest.fixture
def service():
    return CaseService(MockCaseRepository())


def _capture(service, store, *, capture_stages=("ADVOCATES", "REBUTTALS", "JUDGE")):
    """Run the real pipeline once, capturing into ``store``.

    Returns the case, its analysis and the advocate context, because the planner
    needs all three to validate an artefact against the current case state.
    """
    case = service._load_case(ROUTE_CASE)
    analysis = service.get_analysis(ROUTE_CASE)
    provider = _LiveLookingMock()
    orchestrator = ResolutionOrchestratorService(
        settings=LIVE_SETTINGS,
        provider=provider,
        replay_service=ReplayService(settings=LIVE_SETTINGS, store=store),
    )
    orchestrator.run(
        case, analysis, capture_stages=frozenset(capture_stages)
    )
    return case, analysis


def _plan(checkpoints, harness, service, store, case, analysis):
    return checkpoints.plan_resume(
        case=case,
        analysis=analysis,
        advocate_context=AdvocateContextBuilder().build(case, analysis),
        store=store,
        replays=ReplayService(settings=LIVE_SETTINGS, store=store),
        expected=harness._expected_provenance(LIVE_SETTINGS),
    )


def _status(plan, stage: str) -> str:
    return next(item.status for item in plan.verdicts if item.stage == stage)


def _edit(store, stage: str, mutate) -> None:  # noqa: ANN001
    """Rewrite a stored artefact, the way a stale or foreign one would look."""
    path = store.path_for(ROUTE_CASE, stage)
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# The happy path: a complete capture resumes everything
# ---------------------------------------------------------------------------


def test_a_complete_capture_resumes_every_stage(checkpoints, harness, service, scratch):
    case, analysis = _capture(service, scratch)

    plan = _plan(checkpoints, harness, service, scratch, case, analysis)

    assert plan.mode == "FULL_AI", "all three stages are on disk and valid"
    assert plan.reused == ["ADVOCATES", "REBUTTALS", "JUDGE"]
    assert plan.live == []
    assert all(item.status == checkpoints.REUSED for item in plan.verdicts)


def test_an_empty_store_resumes_nothing(checkpoints, harness, service, scratch):
    """Nothing on disk: the advocates are missing and everything after them is skipped.

    The distinction between ``ABSENT`` and ``SKIPPED`` is the useful part. Only
    the first stage is reported missing, because only the first stage *is*
    missing -- the rest were never examined, and saying otherwise would imply the
    planner had looked at files that do not exist.
    """
    case = service._load_case(ROUTE_CASE)
    analysis = service.get_analysis(ROUTE_CASE)

    plan = _plan(checkpoints, harness, service, scratch, case, analysis)

    assert plan.mode == NO_REPLAY
    assert plan.reused == []
    assert plan.live == ["ADVOCATES", "REBUTTALS", "JUDGE"]
    assert _status(plan, "ADVOCATES") == checkpoints.ABSENT
    assert _status(plan, "REBUTTALS") == checkpoints.SKIPPED
    assert _status(plan, "JUDGE") == checkpoints.SKIPPED
    assert "advocates" in plan.verdicts[1].reason


def test_a_reused_checkpoint_never_reaches_the_provider(service, scratch):
    """The point of the whole mechanism, asserted where it costs something.

    The pipeline is run again with a provider that raises if it is called at all.
    If reuse is real, nothing calls it.
    """
    case, analysis = _capture(service, scratch)
    provider = _ProviderThatMustNotBeCalled()
    orchestrator = ResolutionOrchestratorService(
        settings=LIVE_SETTINGS,
        provider=provider,
        replay_service=ReplayService(settings=LIVE_SETTINGS, store=scratch),
    )

    result = orchestrator.run(case, analysis, replay_mode="FULL_AI")

    assert provider.calls == 0, "FULL_AI made a model call"
    assert result.rider.status == "COMPLETE"
    assert result.judge.status in ("COMPLETE", "PENDING_HUMAN_REVIEW")


def test_planning_does_not_write_anything(checkpoints, harness, service, scratch):
    """The planner is an oracle, not a dry run with side effects."""
    case, analysis = _capture(service, scratch)
    before = {
        path: path.read_text(encoding="utf-8")
        for path in sorted(scratch.root.rglob("*.json"))
    }

    _plan(checkpoints, harness, service, scratch, case, analysis)

    after = {
        path: path.read_text(encoding="utf-8")
        for path in sorted(scratch.root.rglob("*.json"))
    }
    assert after == before, "planning must not modify the checkpoints it judges"


# ---------------------------------------------------------------------------
# Refusal: a checkpoint the current code will not accept
# ---------------------------------------------------------------------------


def test_a_corrupted_artefact_is_refused_not_reported_absent(
    checkpoints, harness, service, scratch
):
    """A file that exists but cannot be parsed is not the same as no file.

    "Nothing to lose, re-run it" and "something is there and it is wrong" call
    for different responses from whoever is reading the output, so they are
    different verdicts.
    """
    case, analysis = _capture(service, scratch)
    scratch.path_for(ROUTE_CASE, "ADVOCATES").write_text("{not json", encoding="utf-8")

    plan = _plan(checkpoints, harness, service, scratch, case, analysis)

    assert _status(plan, "ADVOCATES") == checkpoints.REFUSED
    assert plan.verdicts[0].reason_codes == ["MALFORMED_ARTIFACT"]
    assert plan.mode == NO_REPLAY


def test_a_changed_deterministic_hash_refuses_the_checkpoint(
    checkpoints, harness, service, scratch
):
    """A checkpoint describes one deterministic state, and no other."""
    case, analysis = _capture(service, scratch)
    _edit(
        scratch,
        "ADVOCATES",
        lambda document: document.update(deterministicAnalysisHash="0" * 64),
    )

    plan = _plan(checkpoints, harness, service, scratch, case, analysis)

    assert _status(plan, "ADVOCATES") == checkpoints.REFUSED
    assert "ANALYSIS_HASH_MISMATCH" in plan.verdicts[0].reason_codes
    assert plan.mode == NO_REPLAY


def test_a_mismatched_schema_version_refuses_the_checkpoint(
    checkpoints, harness, service, scratch
):
    case, analysis = _capture(service, scratch)
    _edit(scratch, "ADVOCATES", lambda document: document.update(schemaVersion="stage4-v0"))

    plan = _plan(checkpoints, harness, service, scratch, case, analysis)

    assert _status(plan, "ADVOCATES") == checkpoints.REFUSED
    assert "SCHEMA_VERSION_MISMATCH" in plan.verdicts[0].reason_codes


def test_a_different_dispute_type_refuses_the_checkpoint(
    checkpoints, harness, service, scratch
):
    case, analysis = _capture(service, scratch)
    _edit(scratch, "ADVOCATES", lambda document: document.update(disputeType="no_show"))

    plan = _plan(checkpoints, harness, service, scratch, case, analysis)

    assert _status(plan, "ADVOCATES") == checkpoints.REFUSED


# ---------------------------------------------------------------------------
# Provenance: a valid artefact is not automatically live evidence
# ---------------------------------------------------------------------------


def test_a_mock_capture_is_never_live_evidence(checkpoints, harness, service, scratch):
    """The guard for the incident that destroyed the live DISP-005 captures.

    A mock capture validates perfectly -- same schema, same case, same hash. The
    only thing wrong with it is that it came from the wrong model, and that is
    exactly what the replay layer does not check.
    """
    case, analysis = _capture(service, scratch)
    _edit(scratch, "ADVOCATES", lambda document: document.update(capturedMode="mock"))

    plan = _plan(checkpoints, harness, service, scratch, case, analysis)

    assert _status(plan, "ADVOCATES") == checkpoints.NOT_ELIGIBLE
    assert "capturedMode" in plan.verdicts[0].reason
    assert plan.mode == NO_REPLAY, "a mock artefact must not resume a live session"


def test_a_different_model_is_not_the_model_under_test(
    checkpoints, harness, service, scratch
):
    case, analysis = _capture(service, scratch)
    _edit(
        scratch,
        "ADVOCATES",
        lambda document: document.update(capturedModel="some-other-model"),
    )

    plan = _plan(checkpoints, harness, service, scratch, case, analysis)

    assert _status(plan, "ADVOCATES") == checkpoints.NOT_ELIGIBLE
    assert "capturedModel" in plan.verdicts[0].reason


def test_a_different_provider_is_not_the_model_under_test(
    checkpoints, harness, service, scratch
):
    case, analysis = _capture(service, scratch)
    _edit(
        scratch,
        "ADVOCATES",
        lambda document: document.update(capturedProvider="some-other-provider"),
    )

    plan = _plan(checkpoints, harness, service, scratch, case, analysis)

    assert _status(plan, "ADVOCATES") == checkpoints.NOT_ELIGIBLE
    assert "capturedProvider" in plan.verdicts[0].reason


def test_provenance_is_checked_before_the_replay_layer_is_consulted(
    checkpoints, harness, service, scratch
):
    """A mock artefact is rejected on provenance, not on validation.

    Both would reject it here, so the test pins *which* reason wins: the
    distinction a developer needs is "wrong model", not "invalid artefact".
    """
    case, analysis = _capture(service, scratch)
    _edit(scratch, "ADVOCATES", lambda document: document.update(capturedMode="mock"))

    plan = _plan(checkpoints, harness, service, scratch, case, analysis)

    verdict = plan.verdicts[0]
    assert verdict.status == checkpoints.NOT_ELIGIBLE
    assert verdict.reason_codes == [], "provenance is decided without loading"


# ---------------------------------------------------------------------------
# Dependency invalidation: the chain, enforced structurally
# ---------------------------------------------------------------------------


def test_a_refused_earlier_stage_invalidates_every_stage_after_it(
    checkpoints, harness, service, scratch
):
    """The rebuttals cross-examine the advocates, so they cannot outlive them.

    The rebuttal and Judge files on disk are still perfectly valid. They are
    skipped anyway, because a checkpoint is only meaningful relative to the
    pipeline that produced it.
    """
    case, analysis = _capture(service, scratch)
    _edit(scratch, "ADVOCATES", lambda document: document.update(capturedMode="mock"))

    plan = _plan(checkpoints, harness, service, scratch, case, analysis)

    assert _status(plan, "ADVOCATES") == checkpoints.NOT_ELIGIBLE
    assert _status(plan, "REBUTTALS") == checkpoints.SKIPPED
    assert _status(plan, "JUDGE") == checkpoints.SKIPPED
    assert plan.mode == NO_REPLAY
    assert scratch.exists(ROUTE_CASE, "REBUTTALS"), (
        "the downstream artefact is untouched; it is skipped, not deleted"
    )


def test_resume_picks_up_at_the_first_unreused_stage(
    checkpoints, harness, service, scratch
):
    """A refused rebuttal keeps the advocates and costs only the remaining work."""
    case, analysis = _capture(service, scratch)
    _edit(
        scratch,
        "REBUTTALS",
        lambda document: document.update(deterministicAnalysisHash="0" * 64),
    )

    plan = _plan(checkpoints, harness, service, scratch, case, analysis)

    assert plan.mode == "ADVOCATES", "the advocates are reusable, the rest are not"
    assert plan.reused == ["ADVOCATES"]
    assert plan.live == ["REBUTTALS", "JUDGE"]


def test_a_refused_judge_preserves_the_earlier_checkpoints(
    checkpoints, harness, service, scratch
):
    """A failure in the last stage costs the last stage, and nothing more."""
    case, analysis = _capture(service, scratch)
    _edit(
        scratch,
        "JUDGE",
        lambda document: document.update(capturedModel="some-other-model"),
    )

    plan = _plan(checkpoints, harness, service, scratch, case, analysis)

    assert plan.mode == "ADVOCATES_AND_REBUTTALS"
    assert _status(plan, "ADVOCATES") == checkpoints.REUSED
    assert _status(plan, "REBUTTALS") == checkpoints.REUSED
    assert _status(plan, "JUDGE") == checkpoints.NOT_ELIGIBLE
    assert plan.live == ["JUDGE"], "only the Judge is re-run"


def test_an_incomplete_rebuttal_is_not_a_checkpoint(checkpoints, harness, service, scratch):
    """Provenance alone would accept this; completeness is the second half.

    A rebuttal artefact is written whenever the stage ran, including when one
    side failed -- that is honest storage. It is not a checkpoint of a stage that
    succeeded.
    """
    case, analysis = _capture(service, scratch)

    def cripple(document: dict) -> None:
        document["payload"]["rider"]["status"] = "FAILED"
        document["payload"]["rider"]["output"] = None

    _edit(scratch, "REBUTTALS", cripple)

    plan = _plan(checkpoints, harness, service, scratch, case, analysis)

    assert _status(plan, "REBUTTALS") == checkpoints.NOT_ELIGIBLE
    assert plan.mode == "ADVOCATES"


def test_every_verdict_names_the_stage_it_judged(checkpoints, harness, service, scratch):
    case, analysis = _capture(service, scratch)

    plan = _plan(checkpoints, harness, service, scratch, case, analysis)

    assert [item.stage for item in plan.verdicts] == list(checkpoints.CHAIN)
    assert plan.report()["checkpoints"][0]["reusable"] is True


# ---------------------------------------------------------------------------
# The plan is always something the orchestrator can execute
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        (0, "NONE"),
        (1, "ADVOCATES"),
        (2, "ADVOCATES_AND_REBUTTALS"),
        (3, "FULL_AI"),
    ],
)
def test_reuse_maps_onto_a_real_replay_mode(checkpoints, prefix, expected):
    """The four modes are the four prefixes, and there is no fifth.

    This is why dependency invalidation needs no policy of its own: a mode that
    replayed the rebuttals without the advocates is not representable, so the
    mixed plan cannot be built even by mistake.
    """
    assert checkpoints.MODE_FOR_PREFIX[prefix] == expected
    assert len(checkpoints.CHAIN) == 3, "one mode per prefix, plus NO_REPLAY"


def test_every_mode_the_planner_returns_is_one_the_orchestrator_accepts(checkpoints):
    """Pin the vocabulary against the layer that has to consume it.

    ``NO_REPLAY`` is the *name* of a constant whose value is ``"NONE"``, so
    writing the mode as the string ``"NO_REPLAY"`` produces something the
    orchestrator rejects with a pydantic ``literal_error`` -- and a test that
    compares the planner's output against the planner's own constant agrees with
    it and passes. This test compares against the real replay plan instead, which
    is the only comparison that can fail.

    Found by the offline rehearsal, not by these unit tests: the rehearsal runs
    the real CLI, so it hit the real validator.
    """
    for prefix, mode in checkpoints.MODE_FOR_PREFIX.items():
        plan = build_plan(mode)
        assert plan.mode == mode
        assert set(plan.replayed_stages) == set(checkpoints.CHAIN[:prefix]), (
            f"{mode!r} does not serve exactly the first {prefix} stages"
        )


def test_the_chain_covers_every_capture_stage(checkpoints, harness):
    assert set(checkpoints.CHAIN) == set(harness.CAPTURE_STAGES)


# ---------------------------------------------------------------------------
# An offline rehearsal cannot capture
# ---------------------------------------------------------------------------


def test_an_offline_rehearsal_defaults_to_no_capture(harness):
    assert harness.resolve_capture(None, offline=True) is False


def test_an_explicit_capture_flag_is_still_refused_offline(harness):
    """``resolve_capture`` alone is not enough: an explicit flag overrides it."""
    assert harness.resolve_capture(True, offline=True) is True
    refusal = harness.capture_refusal(offline=True, capture=True)
    assert refusal is not None and "overwrites" in refusal


def test_a_live_run_captures_by_default(harness):
    assert harness.resolve_capture(None, offline=False) is True
    assert harness.capture_refusal(offline=False, capture=True) is None


# ---------------------------------------------------------------------------
# The failure output names what a resume needs
# ---------------------------------------------------------------------------


def test_the_failure_block_names_the_next_resumable_stage(harness, capsys):
    """A developer reading a failure needs to know where to pick up, not just that it failed."""
    report = {
        "caseId": ROUTE_CASE,
        "provider": PROVIDER,
        "model": MODEL,
        "error": "STOPPED: RIDER_REBUTTAL: second HTTP 503.",
        "httpAttemptsThisCase": 2,
        "stages": None,
        "stop": {
            "stage": "RIDER_REBUTTAL",
            "lastAttempt": {"status": 503, "durationMs": 4120, "attemptSeq": 2},
        },
        "checkpoints": {"reused": ["ADVOCATES"], "live": ["REBUTTALS", "JUDGE"]},
    }

    harness._print_failure(report)

    printed = capsys.readouterr().out
    assert "RIDER_REBUTTAL" in printed
    assert PROVIDER in printed and MODEL in printed
    assert "503" in printed
    assert "4120" in printed
    assert "ADVOCATES" in printed
    assert "resume from         REBUTTALS" in printed


def test_the_outcome_lines_distinguish_saved_from_reused(harness, capsys):
    report = {
        "resume": {"reused": ["ADVOCATES"]},
        "checkpoints": {"reused": ["ADVOCATES", "REBUTTALS"]},
        "stages": {
            "RIDER_ADVOCATE": {"status": "COMPLETE"},
            "DRIVER_ADVOCATE": {"status": "COMPLETE"},
            "RIDER_REBUTTAL": {"status": "COMPLETE"},
            "DRIVER_REBUTTAL": {"status": "COMPLETE"},
        },
        "error": None,
    }

    harness._print_outcome(report)
    lines = _lines_by_stage(capsys.readouterr().out)

    assert "CHECKPOINT REUSED" in lines["RIDER_ADVOCATE"]
    assert "CHECKPOINT SAVED" in lines["RIDER_REBUTTAL"]
    assert "JUDGE" not in lines, "a stage that was not reached is not claimed"


def test_a_judge_stage_that_completed_but_was_not_captured_says_so(harness, capsys):
    report = {
        "resume": {"reused": []},
        "checkpoints": {"reused": ["ADVOCATES"]},
        "stages": {"JUDGE": {"status": "COMPLETE"}},
        "error": None,
    }

    harness._print_outcome(report)
    lines = _lines_by_stage(capsys.readouterr().out)

    assert "NOT CAPTURED" in lines["JUDGE"]
    assert "CHECKPOINT SAVED" not in lines["JUDGE"]


def test_a_failed_stage_reports_its_failure_code(harness, capsys):
    report = {
        "resume": {"reused": []},
        "checkpoints": {"reused": []},
        "stages": {"RIDER_REBUTTAL": {"status": "FAILED", "failureCode": "SERVER_ERROR"}},
        "error": None,
    }

    harness._print_outcome(report)
    lines = _lines_by_stage(capsys.readouterr().out)

    assert "SERVER_ERROR" in lines["RIDER_REBUTTAL"]
    assert "CHECKPOINT SAVED" not in lines["RIDER_REBUTTAL"]


def _lines_by_stage(printed: str) -> dict[str, str]:
    """Map each stage name to its output line, independent of column padding."""
    lines = {}
    for line in printed.splitlines():
        if not line.strip():
            continue
        stage = line.split()[0]
        if stage.isupper() and "_" in stage or stage == "JUDGE":
            lines[stage] = line
    return lines
