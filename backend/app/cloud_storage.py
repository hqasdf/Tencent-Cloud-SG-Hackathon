"""Durable SQL storage for the protected cloud demo; no policy decisions."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import PurePosixPath, Path
import re
import secrets
import time
from urllib.parse import urlsplit



class CloudStorageError(RuntimeError):
    pass


def storage_backend():
    value = os.getenv('STORAGE_BACKEND', 'sqlite')
    if value not in {'sqlite', 'turso'}:
        raise CloudStorageError('Unsupported STORAGE_BACKEND')
    return value


def validate_cloud_config():
    from app.deployment import DeploymentConfigurationError
    url = os.getenv('TURSO_DATABASE_URL', '')
    parsed = urlsplit(url)
    if (parsed.scheme not in {'https', 'libsql', 'turso'} or not parsed.hostname
            or not parsed.hostname.endswith('.turso.io') or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {'', '/'}):
        raise DeploymentConfigurationError('TURSO_DATABASE_URL must be a Turso HTTPS database origin')
    for name in ('TURSO_AUTH_TOKEN', 'CHECKPOINT_SIGNING_SECRET', 'DEMO_SESSION_SECRET', 'DEMO_PASSWORD_HASH'):
        value = os.getenv(name, '')
        if not value or (name.endswith('SECRET') and len(value) < 32):
            raise DeploymentConfigurationError(f'Missing or invalid {name}')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', os.getenv('STORAGE_NAMESPACE', 'ryderesolve')):
        raise DeploymentConfigurationError('Invalid STORAGE_NAMESPACE')
    from app.demo_auth import password_hash_valid
    if not password_hash_valid(os.environ['DEMO_PASSWORD_HASH']):
        raise DeploymentConfigurationError('Invalid DEMO_PASSWORD_HASH')


def connect(autocommit=False):
    """Native network calls must execute within the bounded cloud worker.

    The SDK timeout is a database busy timeout, NOT a remote HTTP deadline.
    Native output is captured by the worker boundary; errors never echo it.
    """
    if os.getenv('_RYDE_CLOUD_WORKER') != '1':
        raise CloudStorageError('Remote operations require the bounded worker')
    import libsql
    try:
        return NamedConnection(libsql.connect(database=os.environ['TURSO_DATABASE_URL'],
            auth_token=os.environ['TURSO_AUTH_TOKEN'], timeout=2.0,
            isolation_level=None if autocommit else 'DEFERRED'))
    except Exception:
        raise CloudStorageError('REMOTE_STORAGE_UNAVAILABLE') from None


class NamedCursor:
    def __init__(self, cursor):
        self.cursor = cursor
        self.names = [column[0] for column in cursor.description or ()]
    def fetchone(self):
        try:
            row = self.cursor.fetchone()
            return dict(zip(self.names, row)) if row is not None else None
        except Exception:
            raise CloudStorageError('REMOTE_STORAGE_UNAVAILABLE') from None
    def fetchall(self):
        try:
            return [dict(zip(self.names, row)) for row in self.cursor.fetchall()]
        except Exception:
            raise CloudStorageError('REMOTE_STORAGE_UNAVAILABLE') from None
    def __iter__(self):
        return iter(self.fetchall())
    @property
    def rowcount(self):
        return self.cursor.rowcount


class NamedConnection:
    def __init__(self, conn):
        self.conn = conn
    @property
    def isolation_level(self):
        return self.conn.isolation_level
    def execute(self, sql, parameters=()):
        try:
            return NamedCursor(self.conn.execute(sql, parameters))
        except Exception:
            raise CloudStorageError('REMOTE_STORAGE_UNAVAILABLE') from None
    def executescript(self, sql):
        try:
            return self.conn.executescript(sql)
        except Exception:
            raise CloudStorageError('REMOTE_STORAGE_UNAVAILABLE') from None
    def commit(self):
        try:
            self.conn.commit()
        except Exception:
            raise CloudStorageError('REMOTE_STORAGE_UNAVAILABLE') from None
    def rollback(self):
        try:
            self.conn.rollback()
        except Exception:
            raise CloudStorageError('REMOTE_STORAGE_UNAVAILABLE') from None
    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.commit()
        else:
            try:
                self.rollback()
            except CloudStorageError:
                pass  # Preserve the original failure; never retry an unknown write.
        return False


@contextmanager
def connection(autocommit=False):
    conn = None
    try:
        conn = connect(autocommit=autocommit)
        with conn:
            yield conn
    finally:
        if conn is not None:
            conn.close()


class SqlState:
    def __init__(self):
        self.namespace = os.getenv('STORAGE_NAMESPACE', 'ryderesolve')
    def initialize(self):
        with connection() as conn:
            conn.executescript('''
            CREATE TABLE IF NOT EXISTS cloud_documents(namespace TEXT NOT NULL, case_id TEXT NOT NULL, slot TEXT NOT NULL, document TEXT NOT NULL, PRIMARY KEY(namespace,case_id,slot));
            CREATE TABLE IF NOT EXISTS cloud_leases(namespace TEXT NOT NULL, case_id TEXT NOT NULL, owner TEXT NOT NULL, expires INTEGER NOT NULL, PRIMARY KEY(namespace,case_id));
            CREATE TABLE IF NOT EXISTS cloud_usage(namespace TEXT NOT NULL, bucket TEXT NOT NULL, count INTEGER NOT NULL, PRIMARY KEY(namespace,bucket));
            CREATE TABLE IF NOT EXISTS cloud_requests(namespace TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, created INTEGER NOT NULL, PRIMARY KEY(namespace,request_id));
            ''')

    def read(self, case_id, slot):
        with connection() as conn:
            row = conn.execute('SELECT document FROM cloud_documents WHERE namespace=? AND case_id=? AND slot=?', (self.namespace, case_id, slot)).fetchone()
        if row is None:
            return None
        text = row['document']
        if len(text.encode()) > 2 * 1024 * 1024:
            raise CloudStorageError('Stored document is invalid')
        return json.loads(text)

    def write(self, case_id, slot, document, owner=None):
        from app.replay.hashing import canonical_json
        from app.replay.store import forbidden_keys_in, forbidden_values_in
        text = canonical_json(document)
        if len(text.encode()) > 2 * 1024 * 1024 or forbidden_keys_in(document) or forbidden_values_in(document):
            raise CloudStorageError('Unsafe stored document rejected')
        with connection(autocommit=True) as conn:
            if owner is None:
                cursor = conn.execute('INSERT INTO cloud_documents VALUES(?,?,?,?) ON CONFLICT(namespace,case_id,slot) DO UPDATE SET document=excluded.document', (self.namespace, case_id, slot, text))
            else:
                cursor = conn.execute("INSERT INTO cloud_documents SELECT ?,?,?,? WHERE EXISTS(SELECT 1 FROM cloud_leases WHERE namespace=? AND case_id=? AND owner=? AND expires>CAST(strftime('%s','now') AS INTEGER)) ON CONFLICT(namespace,case_id,slot) DO UPDATE SET document=excluded.document",
                    (self.namespace, case_id, slot, text, self.namespace, case_id, owner))
            if cursor.rowcount != 1:
                raise CloudStorageError('Request lease expired')

    @contextmanager
    def lease(self, case_id):
        from app.services.agent_checkpoints import CheckpointBusy, CheckpointStorageError
        owner = secrets.token_hex(24)
        try:
            with connection(autocommit=True) as conn:
                cursor = conn.execute("INSERT INTO cloud_leases VALUES(?,?,?,CAST(strftime('%s','now') AS INTEGER)+330) ON CONFLICT(namespace,case_id) DO UPDATE SET owner=excluded.owner,expires=excluded.expires WHERE cloud_leases.expires<=CAST(strftime('%s','now') AS INTEGER)", (self.namespace, case_id, owner))
                if cursor.rowcount != 1:
                    raise CheckpointBusy('Analysis for this case is already running.')
        except CloudStorageError:
            raise CheckpointStorageError('Request storage is unavailable.') from None
        try:
            yield owner
        finally:
            try:
                with connection(autocommit=True) as conn:
                    conn.execute('DELETE FROM cloud_leases WHERE namespace=? AND case_id=? AND owner=?', (self.namespace, case_id, owner))
            except CloudStorageError:
                pass  # Expiry releases abandoned leases; never mask the original error.

    def consume(self, bucket, limit):
        with connection(autocommit=True) as conn:
            cursor = conn.execute('INSERT INTO cloud_usage VALUES(?,?,1) ON CONFLICT(namespace,bucket) DO UPDATE SET count=count+1 WHERE count<?', (self.namespace, bucket, limit))
            return cursor.rowcount == 1

    def reserve_request(self, request_id, fingerprint):
        with connection(autocommit=True) as conn:
            cursor = conn.execute("INSERT OR IGNORE INTO cloud_requests VALUES(?,?,?,CAST(strftime('%s','now') AS INTEGER))",
                                  (self.namespace, request_id, fingerprint))
            return cursor.rowcount == 1


class CloudDirectory:
    def __init__(self, state, case_id, owner):
        self.state, self.case_id, self.owner = state, case_id, owner
    def __truediv__(self, name):
        return CloudSlot(self.state, self.case_id, self.owner, name)


class CloudSlot:
    def __init__(self, state, case_id, owner, name):
        self.state, self.case_id, self.owner, self.name = state, case_id, owner, name
    def exists(self):
        return self._read() is not None
    def _read(self):
        from app.services.agent_checkpoints import CheckpointStorageError
        try:
            return self.state.read(self.case_id, self.name)
        except (CloudStorageError, json.JSONDecodeError):
            raise CheckpointStorageError('Checkpoint storage is unavailable.') from None
    def read_text(self, **_kwargs):
        from app.replay.hashing import canonical_json
        return canonical_json(self._read())
    def atomic_write(self, data):
        from app.services.agent_checkpoints import CheckpointStorageError
        try:
            self.state.write(self.case_id, self.name, json.loads(data), self.owner)
        except CloudStorageError:
            raise CheckpointStorageError('Checkpoint storage is unavailable.') from None


class CloudCheckpointStore:
    def __init__(self):
        self.state = SqlState()
    @contextmanager
    def request_lock(self, case_id):
        with self.state.lease(case_id) as owner:
            yield CloudDirectory(self.state, case_id, owner)
    @contextmanager
    def session(self, case_id, settings, provider, analysis_hash):
        from app.services.agent_checkpoints import CheckpointSession
        secret = os.getenv('CHECKPOINT_SIGNING_SECRET', '')
        if len(secret) < 32:
            raise CloudStorageError('Checkpoint signing is not configured')
        key = hmac.new(secret.encode(), (self.state.namespace + ':' + case_id).encode(), hashlib.sha256).digest()
        with self.request_lock(case_id) as directory:
            yield CheckpointSession(directory, key, settings, provider, analysis_hash)


class CloudReplayStore:
    def __init__(self):
        self.state = SqlState()
    def write(self, artifact):
        from app.replay.store import ReplayArtifactStore
        ReplayArtifactStore._safe_path(Path('/replay'), artifact.case_id, artifact.stage)
        self.state.write(artifact.case_id, 'replay:' + artifact.stage, artifact.model_dump(by_alias=True, mode='json'))
        return PurePosixPath(artifact.case_id) / (artifact.stage.lower() + '.json')
    def read(self, case_id, stage):
        from app.replay.store import ReplayArtifactStore, ReplayArtifactError
        from app.models.replay import ARTIFACT_NOT_FOUND, MALFORMED_ARTIFACT
        try:
            ReplayArtifactStore._safe_path(Path('/replay'), case_id, stage)
            document = self.state.read(case_id, 'replay:' + stage)
            return ReplayArtifactStore._parse(document, stage) if document is not None else (None, [ARTIFACT_NOT_FOUND])
        except (json.JSONDecodeError, ReplayArtifactError, ValueError):
            return None, [MALFORMED_ARTIFACT]
    def exists(self, case_id, stage):
        return self.read(case_id, stage)[0] is not None


def intake_repository():
    from app.repositories.intake_repository import IntakeCaseRepository
    if storage_backend() == 'sqlite':
        return IntakeCaseRepository()
    class TursoIntakeRepository(IntakeCaseRepository):
        def __init__(self):
            pass  # No remote I/O during FastAPI import. Bootstrap schema explicitly.
        def initialize(self):
            self._init_schema()
            with connection() as conn:
                conn.executescript('CREATE INDEX IF NOT EXISTS intake_source_updated ON intake_cases(source_case_id,updated_at); CREATE INDEX IF NOT EXISTS intake_messages_case_time ON intake_messages(intake_case_id,timestamp);')
        def _conn(self):
            return connection()
    return TursoIntakeRepository()
