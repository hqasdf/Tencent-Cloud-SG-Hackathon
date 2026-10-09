"""Production per-role recovery: real parsers, verification, API and disk; no network."""
from collections import Counter
from dataclasses import replace
import hashlib
import hmac
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agents.config import AgentSettings
from app.agents.provider import LlmProviderError
from app.agents.providers.mock import MockLlmProvider
from app.benchmarks.cases import case_by_id
from app.replay.hashing import canonical_json, deterministic_analysis_hash
from app.replay.service import ReplayService
from app.replay.store import ReplayArtifactStore
from app.services.agent_checkpoints import AgentCheckpointStore, CheckpointBusy
from app.services.dispute_analysis import DisputeAnalysisService
from app.services.resolution_orchestrator import ResolutionOrchestratorService

SETTINGS = AgentSettings(mode="real", provider="controlled", model="controlled-v1")


def role_of(request):
    role = request.metadata.get("role", "advocate")
    return "JUDGE" if role == "judge" else request.metadata["side"] + "_" + role.upper()


class ControlledProvider:
    """Explicit test transport; call recording does not affect production state."""
    name = "controlled"

    def __init__(self, fail=(), rejected=False):
        self.fail = set(fail)
        self.rejected = rejected
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        role = role_of(request)
        if role in self.fail:
            raise LlmProviderError("Controlled offline failure", provider=self.name, code="SERVER_ERROR")
        result = MockLlmProvider().complete(request)
        if self.rejected and role == "RIDER_ADVOCATE":
            payload = json.loads(result.raw_text)
            bad = dict(payload["claims"][0], claimId="REJECTED_SENTINEL", evidenceIds=["NONEXISTENT"])
            payload["claims"].append(bad)
            result = replace(result, raw_text=json.dumps(payload))
        return replace(result, provider_name=self.name, model_name="controlled-v1")

    @property
    def counts(self):
        return Counter(map(role_of, self.requests))


def execute(root, provider, settings=SETTINGS, case=None):
    case = case or case_by_id("DISP-005")
    analysis = DisputeAnalysisService().analyze(case)
    return ResolutionOrchestratorService(settings=settings, provider=provider).run(
        case, analysis, resume=True, checkpoint_store=AgentCheckpointStore(root)
    )


def assert_complete(result):
    for state in (result.workflow.advocates.rider, result.workflow.advocates.driver,
                  result.workflow.rebuttals.rider, result.workflow.rebuttals.driver):
        assert state.status == "COMPLETE"
    assert result.workflow.judge.status in {"COMPLETE", "PENDING_HUMAN_REVIEW"}
    assert result.workflow.final_resolution.status == "COMPLETE"


def test_all_five_then_restart_reuses_every_role(tmp_path):
    provider = ControlledProvider()
    first = execute(tmp_path, provider)
    assert_complete(first)
    assert provider.counts == Counter({role: 1 for role in (
        "RIDER_ADVOCATE", "DRIVER_ADVOCATE", "RIDER_REBUTTAL", "DRIVER_REBUTTAL", "JUDGE")})
    restarted = ControlledProvider()
    second = execute(tmp_path, restarted)  # new store, service and provider
    assert_complete(second)
    assert restarted.requests == []
    assert len(second.replay_metadata.reused_roles) == 5
    assert second.agent_run.total_tokens == 0
    assert second.deterministic_resolution == first.deterministic_resolution


@pytest.mark.parametrize("failed,good", [("DRIVER", "RIDER"), ("RIDER", "DRIVER")])
def test_one_advocate_failure_then_only_missing_advocate_is_called(tmp_path, failed, good):
    provider = ControlledProvider([failed + "_ADVOCATE"])
    first = execute(tmp_path, provider)
    assert getattr(first, good.lower()).status == "COMPLETE"
    assert len(getattr(first, good.lower()).verified_claims) > 0
    assert first.workflow.judge.status == "BLOCKED"
    assert first.workflow.rebuttals.rider.status == "BLOCKED"
    assert provider.counts == {"RIDER_ADVOCATE": 1, "DRIVER_ADVOCATE": 1}
    assert len(list(tmp_path.rglob("*.json"))) == 1
    recovered = ControlledProvider()
    second = execute(tmp_path, recovered)
    assert_complete(second)
    assert recovered.counts == {failed + "_ADVOCATE": 1, "RIDER_REBUTTAL": 1, "DRIVER_REBUTTAL": 1, "JUDGE": 1}
    assert getattr(second, good.lower()).verified_claims == getattr(first, good.lower()).verified_claims
    assert getattr(second.workflow.advocates, good.lower()).reused
    assert second.deterministic_resolution == first.deterministic_resolution
    judge_input = json.loads(next(r for r in recovered.requests if role_of(r) == "JUDGE").metadata["context_json"])
    assert judge_input["rider"]["verifiedClaims"]
    assert judge_input["driver"]["verifiedClaims"]
    assert judge_input["verifiedRiderRebuttals"]
    assert judge_input["verifiedDriverRebuttals"]


def test_failed_rebuttal_does_not_block_judge_and_changed_judge_input_invalidates_cache(tmp_path):
    first_provider = ControlledProvider(["RIDER_REBUTTAL"])
    first = execute(tmp_path, first_provider)
    assert first.workflow.rebuttals.rider.status == "FAILED"
    assert first.workflow.judge.status == "COMPLETE"
    old_input = json.loads(first_provider.requests[-1].metadata["context_json"])
    assert old_input["verifiedRiderRebuttals"] == []
    assert old_input["verifiedDriverRebuttals"]
    provider = ControlledProvider()
    second = execute(tmp_path, provider)
    assert_complete(second)
    assert provider.counts == {"RIDER_REBUTTAL": 1, "JUDGE": 1}
    assert second.workflow.rebuttals.driver.reused
    assert second.deterministic_resolution == first.deterministic_resolution


def test_only_failed_judge_is_retried(tmp_path):
    first = execute(tmp_path, ControlledProvider(["JUDGE"]))
    assert first.workflow.judge.status == "FAILED"
    assert first.workflow.judge.failure_code == "SERVER_ERROR"
    provider = ControlledProvider()
    second = execute(tmp_path, provider)
    assert_complete(second)
    assert provider.counts == {"JUDGE": 1}
    assert second.deterministic_resolution == first.deterministic_resolution


@pytest.mark.parametrize("mutation", ["malformed", "signature", "signed_schema", "signed_rejected"])
def test_invalid_checkpoint_never_becomes_trusted_output(tmp_path, mutation):
    execute(tmp_path, ControlledProvider(["DRIVER_ADVOCATE"]))
    path = next(tmp_path.rglob("rider_advocate.json"))
    data = json.loads(path.read_text())
    if mutation == "malformed":
        path.write_text("not json")
    else:
        if mutation == "signed_schema":
            data["document"]["output"] = {}
        else:
            data["document"]["output"]["claims"][0]["evidenceIds"] = ["NONEXISTENT"]
        if mutation.startswith("signed_"):
            key = (path.parent / "integrity.key").read_bytes()
            data["signature"] = hmac.new(key, canonical_json(data["document"]).encode(), hashlib.sha256).hexdigest()
        path.write_text(json.dumps(data))
    provider = ControlledProvider()
    result = execute(tmp_path, provider)
    assert_complete(result)
    assert provider.counts["RIDER_ADVOCATE"] == 1
    assert not result.workflow.advocates.rider.reused
    assert "NONEXISTENT" not in provider.requests[-1].metadata["context_json"]


@pytest.mark.parametrize("change", ["model", "provider", "mode", "context", "temperature", "prompt_version"])
def test_configuration_and_context_changes_invalidate_live_resume(tmp_path, change):
    execute(tmp_path, ControlledProvider(["DRIVER_ADVOCATE"]))
    provider = ControlledProvider()
    settings = SETTINGS
    case = case_by_id("DISP-005")
    if change == "context":
        case = case.model_copy(update={"rider_complaint": case.rider_complaint + " Additional rider statement."})
    elif change == "provider":
        provider.name = "another-controlled"
        settings = replace(settings, provider=provider.name)
    elif change == "mode":
        settings = replace(settings, mode="mock")
    else:
        settings = replace(settings, **{change: 0.3 if change == "temperature" else "changed"})
    result = execute(tmp_path, provider, settings, case)
    assert provider.counts["RIDER_ADVOCATE"] == 1
    assert not result.workflow.advocates.rider.reused


def test_rejected_claim_never_reaches_judge_or_checkpoint(tmp_path):
    provider = ControlledProvider(rejected=True)
    result = execute(tmp_path, provider)
    assert result.rider.rejected_claims
    assert not list(tmp_path.rglob("rider_advocate.json"))
    judge_payload = provider.requests[-1].metadata["context_json"]
    assert "REJECTED_SENTINEL" not in judge_payload
    assert "NONEXISTENT" not in judge_payload
    assert result.workflow.final_resolution.status == "COMPLETE"


def test_human_review_cannot_be_overridden_on_resume(tmp_path):
    case = case_by_id("DISP-003")
    first = execute(tmp_path, ControlledProvider(), case=case)
    provider = ControlledProvider()
    second = execute(tmp_path, provider, case=case)
    assert second.deterministic_resolution == first.deterministic_resolution
    assert second.deterministic_resolution.resolution_mode == "HUMAN_REVIEW"
    assert second.workflow.judge.status == "PENDING_HUMAN_REVIEW"
    assert not provider.requests


def test_overlapping_request_cannot_call_provider(tmp_path):
    store = AgentCheckpointStore(tmp_path)
    case = case_by_id("DISP-005")
    analysis = DisputeAnalysisService().analyze(case)
    provider = ControlledProvider()
    with store.session(case.id, SETTINGS, provider.name, deterministic_analysis_hash(case, analysis)):
        with pytest.raises(CheckpointBusy):
            execute(tmp_path, provider)
    assert provider.requests == []


@pytest.mark.parametrize("mock", [True, False])
def test_historical_replay_keeps_original_provider_under_gemini_runtime(tmp_path, mock):
    case = case_by_id("DISP-005")
    analysis = DisputeAnalysisService().analyze(case)
    store = ReplayArtifactStore(tmp_path)
    capture_settings = AgentSettings() if mock else SETTINGS
    provider = MockLlmProvider() if mock else ControlledProvider()
    replay = ReplayService(store=store, settings=capture_settings)
    ResolutionOrchestratorService(settings=capture_settings, provider=provider, replay_service=replay).run(
        case, analysis, capture_stages=frozenset({"ADVOCATES", "REBUTTALS", "JUDGE"}))
    gemini_settings = replace(SETTINGS, provider="gemini_native", model="different-runtime-model")
    forbidden = ControlledProvider(["RIDER_ADVOCATE", "DRIVER_ADVOCATE", "RIDER_REBUTTAL", "DRIVER_REBUTTAL", "JUDGE"])
    result = ResolutionOrchestratorService(settings=gemini_settings, provider=forbidden,
        replay_service=ReplayService(store=store, settings=gemini_settings)).run(case, analysis, replay_mode="FULL_AI")
    assert not forbidden.requests
    assert result.replay_metadata.source == ("MOCK_DEMO" if mock else "REPLAYED_LIVE")
    assert result.agent_run.mode == ("mock" if mock else "real")
    assert result.replay_metadata.captured_provider == provider.name


def test_api_retry_uses_production_recovery(tmp_path, monkeypatch):
    from app.main import app
    monkeypatch.setenv("AGENT_CHECKPOINT_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_MODE", "real")
    monkeypatch.setenv("AGENT_PROVIDER", "controlled")
    monkeypatch.setenv("AGENT_MODEL", "controlled-v1")
    provider = ControlledProvider(["DRIVER_ADVOCATE"])
    for module in ("advocate_orchestrator", "rebuttal_orchestrator", "resolution_orchestrator"):
        monkeypatch.setattr(f"app.services.{module}.get_provider", lambda _: provider)
    with TestClient(app) as client:
        first = client.post("/api/cases/DISP-005/resolution/run")
        assert first.status_code == 200
        assert first.json()["workflow"]["judge"]["status"] == "BLOCKED"
        provider.fail.clear()
        second = client.post("/api/cases/DISP-005/resolution/run")
    assert second.status_code == 200
    assert second.json()["workflow"]["advocates"]["rider"]["reused"]
    assert second.json()["workflow"]["judge"]["status"] == "COMPLETE"
    assert provider.counts == {"RIDER_ADVOCATE": 1, "DRIVER_ADVOCATE": 2,
                               "RIDER_REBUTTAL": 1, "DRIVER_REBUTTAL": 1, "JUDGE": 1}
