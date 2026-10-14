"""Authentication for a restricted synthetic demo, independent of Caddy."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from starlette.responses import JSONResponse

router = APIRouter(prefix='/api/auth', tags=['demo authentication'])


def password_hash_valid(value):
    try:
        algorithm, rounds, salt, digest = value.split('$')
        return algorithm == 'pbkdf2_sha256' and 200000 <= int(rounds) <= 1000000 and len(bytes.fromhex(salt)) >= 16 and len(bytes.fromhex(digest)) == 32
    except (ValueError, TypeError):
        return False


def make_password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 300000)
    return f'pbkdf2_sha256$300000${salt.hex()}${digest.hex()}'


def verify_password(password):
    value = os.getenv('DEMO_PASSWORD_HASH', '')
    if not password_hash_valid(value):
        return False
    _, rounds, salt, digest = value.split('$')
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds))
    return hmac.compare_digest(actual, bytes.fromhex(digest))


def _sign(payload):
    secret = os.getenv('DEMO_SESSION_SECRET', '')
    if len(secret) < 32:
        raise HTTPException(503, 'Demo authentication is not configured')
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_session():
    payload = base64.urlsafe_b64encode(json.dumps({'sub': 'demo-operator', 'exp': int(time.time()) + 3600, 'nonce': secrets.token_hex(16)}, separators=(',', ':')).encode()).decode()
    return payload + '.' + _sign(payload)


def valid_session(token):
    try:
        if len(token) > 1024:
            return False
        payload, signature = token.split('.')
        if not hmac.compare_digest(signature, _sign(payload)):
            return False
        document = json.loads(base64.urlsafe_b64decode(payload))
        return document['sub'] == 'demo-operator' and int(time.time()) < document['exp'] <= int(time.time()) + 3600
    except (ValueError, KeyError, TypeError, HTTPException):
        return False


class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    password: str = Field(min_length=1, max_length=256)


@router.post('/login')
def login(request: Login):
    from app.cloud_storage import SqlState, storage_backend
    if storage_backend() != 'turso':
        raise HTTPException(404, 'Cloud demo authentication is unavailable')
    # Global cap bounds password hashing and brute force across serverless instances.
    if not SqlState().consume('login:' + str(int(time.time()) // 3600), 60):
        raise HTTPException(429, 'Demo login limit reached. Try later.')
    if not verify_password(request.password):
        raise HTTPException(401, 'Invalid demo credentials')
    return {'accessToken': issue_session(), 'tokenType': 'Bearer', 'expiresIn': 3600}


class DemoAuthentication:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        from app.cloud_storage import storage_backend
        path = scope.get('path', '')
        if (scope['type'] != 'http' or storage_backend() != 'turso' or scope.get('method') == 'OPTIONS'
                or path in {'/api/health', '/api/auth/login'} or not path.startswith('/api/')):
            return await self.app(scope, receive, send)
        value = dict(scope.get('headers', [])).get(b'authorization', b'').decode('latin-1')
        if not value.startswith('Bearer ') or not valid_session(value[7:]):
            return await JSONResponse({'detail': 'Demo authentication required'}, 401, headers={'Cache-Control': 'no-store'})(scope, receive, send)
        if os.getenv('_RYDE_CLOUD_WORKER') == '1':
            from app.cloud_storage import SqlState
            state = SqlState()
            try:
                if not state.consume('requests:' + str(int(time.time()) // 3600), 600):
                    return await JSONResponse({'detail': 'Demo usage budget reached. Try later.'}, 429)(scope, receive, send)
                if scope.get('method') == 'POST':
                    key = dict(scope.get('headers', [])).get(b'idempotency-key', b'').decode('latin-1')
                    import re
                    if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', key):
                        return await JSONResponse({'detail': 'A valid Idempotency-Key is required'}, 400)(scope, receive, send)
                    fingerprint = hashlib.sha256(('demo-operator:' + path).encode()).hexdigest()
                    if not state.reserve_request(key, fingerprint):
                        return await JSONResponse({'detail': {'code': 'REQUEST_ALREADY_RESERVED', 'message': 'This action was already accepted. No duplicate operation was started.'}}, 409)(scope, receive, send)
            except Exception:
                return await JSONResponse({'detail': {'code': 'STORAGE_UNAVAILABLE', 'message': 'Request storage is unavailable. No operation was started.'}}, 503)(scope, receive, send)
        await self.app(scope, receive, send)
