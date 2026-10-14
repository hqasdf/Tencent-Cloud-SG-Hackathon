"""Explicit preview-only live gate and durable budgets; no policy decisions."""
import json
import math
import os
import time

from app.agents.config import AgentConfigurationError
from app.agents.provider import LlmProviderError, ProviderErrorCode


class StageDeferred(Exception):
    def __init__(self, role, saved_roles):
        super().__init__('Verified stage saved; continue in another request')
        self.role, self.saved_roles = role, saved_roles


class StageBudget:
    def __init__(self, session):
        self.session, self.attempted = session, None

    def before_invoke(self, role):
        if self.attempted is not None:
            saved = [key for key, event in self.session.events.items() if event in {'SAVED', 'REUSED'}]
            # Never automatically buy a replacement for an unverified stage.
            if self.session.events.get(self.attempted) != 'SAVED':
                raise LlmProviderError('An earlier role did not produce a verified checkpoint. Continue manually after reviewing its failure.', provider=self.session.provider, code=ProviderErrorCode.REQUEST_REJECTED, attempts=0)
            raise StageDeferred(role, saved)
        self.attempted = role


def live_enabled():
    from app.cloud_storage import storage_backend
    return (storage_backend() == 'turso' and os.getenv('CLOUD_LIVE_AI_ENABLED') == '1'
            and os.getenv('GEMINI_FREE_TIER_CONFIRMED') == '1'
            and os.getenv('AGENT_MODE') == 'real' and os.getenv('AGENT_PROVIDER') == 'gemini_native')


def _limit(name, maximum):
    try:
        value = int(os.getenv(name, '0'))
    except ValueError:
        value = 0
    if not 1 <= value <= maximum:
        raise AgentConfigurationError(f'{name} must be explicitly bounded before cloud live AI')
    return value


def validate_live_settings(settings):
    if (not live_enabled() or settings.mode != 'real' or settings.provider != 'gemini_native'
            or settings.model != 'gemini-3.8-flash' or settings.max_tokens != 4096
            or settings.max_retries > 1 or settings.timeout_seconds > 60
            or (settings.timeout_seconds > 30 and settings.max_retries != 0)
            or settings.retry_max_backoff_seconds > 20):
        raise AgentConfigurationError('Cloud live Gemini is disabled without explicit approval, confirmed free tier and bounded approved configuration')
    return (_limit('CLOUD_GEMINI_DAILY_ATTEMPTS', 100),
            _limit('CLOUD_GEMINI_DAILY_TOKEN_BUDGET', 2000000))


def reserve_attempt(request, model):
    from app.agents.config import AgentSettings
    from app.cloud_storage import SqlState, connection
    attempts, tokens = validate_live_settings(AgentSettings.from_environment())
    if model != 'gemini-3.8-flash' or request.max_tokens != 4096:
        raise LlmProviderError('Unapproved cloud model or token setting', provider='gemini_native', code=ProviderErrorCode.REQUEST_REJECTED, attempts=0)
    # Reserve conservatively by UTF-8 bytes plus maximum output, including repairs
    # and transport retries. This is an application allowance, not Google's quota.
    prompt_bytes = len((request.system_prompt + request.user_prompt).encode()) + len(json.dumps(request.response_schema or {}).encode()) + 1024
    if prompt_bytes > 65536:
        raise LlmProviderError('Cloud AI input exceeds its permitted budget', provider='gemini_native', code=ProviderErrorCode.REQUEST_REJECTED, attempts=0)
    state = SqlState()
    cooldown = state.read('cloud-gemini', 'cooldown')
    remaining = (cooldown or {}).get('until', 0) - time.time()
    if remaining > 0:
        raise LlmProviderError('Gemini provider cooldown is still active', provider='gemini_native', code=ProviderErrorCode.RATE_LIMITED, attempts=0, retry_after_seconds=remaining)
    day = str(int(time.time()) // 86400)
    reserved = prompt_bytes + request.max_tokens
    with connection(autocommit=True) as conn:
        # One statement atomically caps both counters across independent workers.
        cursor = conn.execute('INSERT INTO cloud_ai_usage(namespace,day,attempts,tokens) SELECT ?,?,1,? WHERE ?<=? ON CONFLICT(namespace,day) DO UPDATE SET attempts=attempts+1,tokens=tokens+excluded.tokens WHERE attempts<? AND tokens+excluded.tokens<=?',
                              (state.namespace, day, reserved, reserved, tokens, attempts, tokens))
        if cursor.rowcount != 1:
            raise LlmProviderError('Cloud Gemini validation budget exhausted', provider='gemini_native', code=ProviderErrorCode.RATE_LIMITED, attempts=0)


def remember_cooldown(failure):
    if failure.code != ProviderErrorCode.RATE_LIMITED:
        return
    delay = failure.retry_after_seconds
    if delay is None or not math.isfinite(delay) or delay < 0:
        return  # Never guess a quota/reset time.
    from app.cloud_storage import SqlState, connection
    state = SqlState()
    document = json.dumps({'until': time.time() + delay})
    with connection(autocommit=True) as conn:
        conn.execute("INSERT INTO cloud_documents VALUES(?,?,?,?) ON CONFLICT(namespace,case_id,slot) DO UPDATE SET document=excluded.document WHERE json_extract(excluded.document,'$.until')>json_extract(cloud_documents.document,'$.until')", (state.namespace, 'cloud-gemini', 'cooldown', document))
