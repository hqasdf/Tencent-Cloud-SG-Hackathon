import pytest
from app.cloud_worker import run_worker, WorkerDeadline


def test_native_client_imports_in_real_subprocess():
    result = run_worker({'op': 'capability'}, 10)
    assert result['status'] == 200
    assert result['body'] == {'worker': True, 'nativeClientImported': True}


def test_deadline_terminates_worker_and_never_retries():
    import time
    start = time.monotonic()
    with pytest.raises(WorkerDeadline, match='unknown'):
        run_worker({'op': 'fault'}, .5)
    assert time.monotonic() - start < 3


def test_worker_failure_payload_is_sanitized():
    result = run_worker({'op': 'unsupported', 'private': 'synthetic-sensitive-value'}, 10)
    assert result['status'] == 503
    assert 'synthetic-sensitive-value' not in str(result)


def test_remote_connections_cannot_escape_worker(monkeypatch):
    from app.cloud_storage import connect, CloudStorageError
    monkeypatch.delenv('_RYDE_CLOUD_WORKER', raising=False)
    with pytest.raises(CloudStorageError, match='bounded worker'):
        connect()


def boundary_client(monkeypatch, worker):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import cloud_boundary
    monkeypatch.setattr(cloud_boundary, 'storage_backend', lambda: 'turso')
    monkeypatch.setattr(cloud_boundary, 'run_worker', worker)
    monkeypatch.delenv('_RYDE_CLOUD_WORKER', raising=False)
    app = FastAPI()
    app.add_middleware(cloud_boundary.CloudRequestDeadline)
    return TestClient(app)


def test_http_deadline_reports_unknown_write_and_does_not_retry(monkeypatch):
    calls = []
    def worker(payload, seconds):
        calls.append((payload, seconds))
        raise WorkerDeadline('synthetic private upstream details')
    with boundary_client(monkeypatch, worker) as client:
        response = client.post('/api/test', json={'synthetic': True})
    assert response.status_code == 504
    assert response.json()['detail']['code'] == 'REQUEST_DEADLINE'
    assert 'do not automatically retry' in response.text
    assert 'private upstream' not in response.text
    assert len(calls) == 1 and calls[0][1] == 180


def test_worker_receives_only_allowed_headers_and_preserves_query(monkeypatch):
    calls = []
    def worker(payload, seconds):
        calls.append(payload)
        return {'status': 200, 'body': {'synthetic': True}}
    with boundary_client(monkeypatch, worker) as client:
        response = client.get('/api/test?party=rider', headers={'Authorization': 'Bearer synthetic', 'X-Private': 'synthetic-value'})
    assert response.status_code == 200
    assert calls[0]['path'] == '/api/test?party=rider'
    assert 'x-private' not in calls[0]['headers']
    assert response.headers['cache-control'] == 'no-store'


def test_large_request_never_starts_cloud_worker(monkeypatch):
    def worker(*args):
        pytest.fail('Rejected body must not start remote operations')
    with boundary_client(monkeypatch, worker) as client:
        assert client.post('/api/test', content=b'x' * (32 * 1024 + 1)).status_code == 413
