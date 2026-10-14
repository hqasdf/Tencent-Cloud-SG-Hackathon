"""Bound native libSQL work by process lifetime, including request handling.

Credentials stay in the server environment. Native stderr is captured, never
forwarded. A killed write has unknown outcome and must not be auto-retried.
"""
import json
import os
from pathlib import Path
import subprocess
import sys


class WorkerDeadline(RuntimeError):
    pass


def run_worker(payload, seconds=180):
    env = dict(os.environ, _RYDE_CLOUD_WORKER='1', PYTHONDONTWRITEBYTECODE='1')
    root = str(Path(__file__).resolve().parent.parent)
    env['PYTHONPATH'] = root + os.pathsep + env.get('PYTHONPATH', '')
    try:
        result = subprocess.run([sys.executable, '-B', '-m', 'app.cloud_worker'],
            input=json.dumps(payload), capture_output=True, text=True, timeout=seconds, env=env,
            cwd=root)
    except subprocess.TimeoutExpired:
        raise WorkerDeadline('Request deadline exceeded; write outcome may be unknown') from None
    if result.returncode or len(result.stdout.encode()) > 4 * 1024 * 1024:
        raise WorkerDeadline('Cloud worker unavailable; write outcome may be unknown')
    try:
        reply = json.loads(result.stdout)
        if not isinstance(reply, dict) or not isinstance(reply.get('status'), int):
            raise ValueError()
        return reply
    except ValueError:
        raise WorkerDeadline('Cloud worker returned an invalid response') from None


def main():
    payload = json.loads(sys.stdin.read(64 * 1024))
    if payload.get('op') == 'capability':
        import libsql
        print(json.dumps({'status': 200, 'body': {'worker': True, 'nativeClientImported': True}}))
        return
    if payload.get('op') == 'fault':
        import time
        time.sleep(10)
        return
    if payload.get('op') == 'bootstrap':
        from app.cloud_bootstrap import smoke, seed_demo
        smoke()
        cases = payload.get('cases', [])
        from app.repositories.case_repository import MockCaseRepository
        permitted = {case.id for case in MockCaseRepository().list_cases()}
        if not isinstance(cases, list) or not set(cases).issubset(permitted):
            raise ValueError('Unsupported synthetic case')
        for case_id in cases:
            seed_demo(case_id)
        print(json.dumps({'status': 200, 'body': {'smoke': True, 'seededCases': cases, 'provenance': 'MOCK_DEMO'}}))
        return
    if payload.get('op') != 'http':
        raise ValueError('Unsupported worker operation')
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.request(payload['method'], payload['path'],
            headers=payload['headers'], content=payload['body'])
        body = response.json() if response.content else None
        print(json.dumps({'status': response.status_code, 'body': body}))


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        print(json.dumps({'status': 503, 'body': {'detail': {'code': 'STORAGE_UNAVAILABLE',
              'message': 'Cloud request storage is unavailable. No live Gemini call was made.'}}}))
