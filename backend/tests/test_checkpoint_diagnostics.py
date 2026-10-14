"""Sanitized checkpoint rejection receipts, never real provider calls."""
import hashlib
import hmac
import json
from dataclasses import replace
import pytest
from app.replay.hashing import canonical_json
from app.services import agent_checkpoints as checkpoint
from tests.test_agent_recovery import ControlledProvider, execute
from tests.test_cloud_storage import remote

def receipt(root, role='rider_advocate'):
    path=next(root.rglob(role+'.checkpoint-diagnostic'))
    envelope=json.loads(path.read_text())
    key=(path.parent/'integrity.key').read_bytes()
    assert hmac.compare_digest(envelope['signature'],hmac.new(key,canonical_json(envelope['document']).encode(),hashlib.sha256).hexdigest())
    return envelope['document']

def test_rejected_claim_retains_reason_without_retaining_output(tmp_path):
    result=execute(tmp_path,ControlledProvider(rejected=True))
    assert result.rider.rejected_claims
    assert not list(tmp_path.rglob('rider_advocate.json'))
    saved=receipt(tmp_path)
    assert saved['outcome']=='REJECTED_OUTPUT'
    assert saved['stage']=='VERIFICATION'
    assert saved['rejectedCount']>0 and saved['reasonCodes']
    assert 'REJECTED_SENTINEL' not in canonical_json(saved)
    assert 'NONEXISTENT' not in canonical_json(saved)

def test_accepted_checkpoint_and_recovery_remain_signed(tmp_path):
    execute(tmp_path,ControlledProvider())
    assert receipt(tmp_path)['outcome']=='SAVED'
    provider=ControlledProvider()
    result=execute(tmp_path,provider)
    assert not provider.requests
    assert result.rider.execution.reused
    assert receipt(tmp_path)['outcome']=='REUSED'

def test_privacy_rejection_records_no_sensitive_text(tmp_path):
    class SensitiveSummary(ControlledProvider):
        def complete(self,request):
            response=super().complete(request)
            if request.metadata.get('side')=='RIDER' and request.metadata.get('role','advocate')=='advocate':
                payload=json.loads(response.raw_text)
                payload['summary']='Bearer PRIVATE_OFFLINE_SENSITIVE_VALUE'
                response=replace(response,raw_text=json.dumps(payload))
            return response
    execute(tmp_path,SensitiveSummary())
    assert not list(tmp_path.rglob('rider_advocate.json'))
    saved=receipt(tmp_path)
    assert saved['outcome']=='CHECKPOINT_UNSAFE' and saved['stage']=='PRIVACY'
    assert 'PRIVATE_OFFLINE_SENSITIVE_VALUE' not in canonical_json(saved)

def test_write_failure_is_distinct_from_verification(tmp_path,monkeypatch):
    write=checkpoint._atomic_write
    def fail(path,data):
        if path.name=='rider_advocate.json': raise OSError('PRIVATE_OS_ERROR')
        return write(path,data)
    monkeypatch.setattr(checkpoint,'_atomic_write',fail)
    execute(tmp_path,ControlledProvider())
    saved=receipt(tmp_path)
    assert saved['outcome']=='CHECKPOINT_WRITE_FAILED' and saved['stage']=='PERSISTENCE'
    assert 'PRIVATE_OS_ERROR' not in canonical_json(saved)


def test_remote_diagnostic_receipt_is_signed_and_fenced(remote, monkeypatch):
    from app.cloud_storage import CloudCheckpointStore, SqlState
    from app.cloud_live import StageDeferred
    from app.benchmarks.cases import case_by_id
    from app.services.dispute_analysis import DisputeAnalysisService
    from app.services.resolution_orchestrator import ResolutionOrchestratorService
    from tests.test_agent_recovery import SETTINGS
    case=case_by_id('DISP-005')
    analysis=DisputeAnalysisService().analyze(case)
    with pytest.raises(StageDeferred):
        ResolutionOrchestratorService(settings=SETTINGS,provider=ControlledProvider()).run(case,analysis,resume=True)
    state=SqlState()
    envelope=state.read(case.id,'rider_advocate.checkpoint-diagnostic')
    key=hmac.new(__import__('os').environ['CHECKPOINT_SIGNING_SECRET'].encode(),(state.namespace+':'+case.id).encode(),hashlib.sha256).digest()
    assert hmac.compare_digest(envelope['signature'],hmac.new(key,canonical_json(envelope['document']).encode(),hashlib.sha256).hexdigest())
    assert envelope['document']['outcome']=='SAVED'
    assert state.read(case.id,'rider_advocate.json') is not None
