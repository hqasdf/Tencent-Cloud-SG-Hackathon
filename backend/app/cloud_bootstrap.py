"""Explicit synthetic-only cloud smoke and replay initialization. No migration."""
import argparse
import os
import secrets

from app.agents.config import AgentSettings
from app.agents.providers.mock import MockLlmProvider
from app.cloud_storage import SqlState, connection, intake_repository, storage_backend, CloudReplayStore
from app.repositories.case_repository import MockCaseRepository
from app.services.case_service import CaseService
from app.services.resolution_orchestrator import ResolutionOrchestratorService
from app.replay.service import ReplayService


def seed_demo(case_id):
    if storage_backend() != 'turso':
        raise ValueError('Explicit Turso configuration is required')
    settings = AgentSettings(mode='mock', provider='mock', max_tokens=1024)
    service = CaseService(MockCaseRepository())
    case, analysis = service._load_case(case_id), service.get_analysis(case_id)
    store = CloudReplayStore()
    replay = ReplayService(settings=settings, store=store)
    runner = ResolutionOrchestratorService(settings=settings, provider=MockLlmProvider(), replay_service=replay)
    # Existing valid captures are preserved; invalid existing data is never replaced.
    if not any(store.exists(case_id, stage) for stage in ('ADVOCATES', 'REBUTTALS', 'JUDGE')):
        with SqlState().lease('seed:' + case_id):
            if not any(store.exists(case_id, stage) for stage in ('ADVOCATES', 'REBUTTALS', 'JUDGE')):
                runner.run(case, analysis, capture_stages=frozenset({'ADVOCATES', 'REBUTTALS', 'JUDGE'}))
    result = runner.run(case, analysis, replay_mode='FULL_AI')
    if result.deterministic_resolution != runner.run(case, analysis).deterministic_resolution:
        raise ValueError('Deterministic replay mismatch')
    return result


def smoke():
    """Create additive, uniquely identified synthetic records. Never delete data."""
    if storage_backend() != 'turso':
        raise ValueError('Explicit Turso configuration is required')
    run_id = 'SMOKE-' + secrets.token_hex(12)
    repo = intake_repository()
    repo.initialize()
    SqlState().initialize()
    created = repo.create_case(source_case_id=run_id, trip_id=run_id)
    repo.add_message(intake_case_id=created.id, party='rider', sender='user', content='Synthetic deployment smoke record; not an interview.')
    restarted = intake_repository()
    if restarted.get_case(created.id).source_case_id != run_id:
        raise ValueError('Persistence smoke failed')
    try:
        with connection() as conn:
            conn.execute('UPDATE intake_cases SET trip_id=? WHERE id=?', ('SYNTHETIC-ROLLBACK', created.id))
            raise RuntimeError('synthetic rollback')
    except RuntimeError:
        pass
    if restarted.get_case(created.id).trip_id != run_id:
        raise ValueError('Rollback smoke failed')
    state = SqlState()
    with state.lease(run_id) as owner:
        state.write(run_id, 'smoke', {'synthetic': True}, owner)
        if state.read(run_id, 'smoke') != {'synthetic': True}:
            raise ValueError('Document smoke failed')
    return True


def main():
    parser = argparse.ArgumentParser(description='Add synthetic cloud smoke or mock replay records; never import local data')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--seed-demo', choices=[case.id for case in MockCaseRepository().list_cases()])
    args = parser.parse_args()
    try:
        if not args.smoke and not args.seed_demo:
            parser.error('Choose --smoke or --seed-demo')
        from app.cloud_worker import run_worker
        result = run_worker({'op': 'bootstrap', 'cases': [args.seed_demo] if args.seed_demo else []}, 180)
        if result['status'] != 200:
            raise ValueError('Cloud bootstrap failed')
        print('Cloud synthetic storage smoke: PASS')
        if args.seed_demo:
            print('MOCK_DEMO replay validated; zero live calls; deterministic decision preserved.')
    except Exception:
        print('Cloud verification: FAIL. Credentials and upstream details suppressed.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
