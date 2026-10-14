"""Cloud request isolation; CORS/body checks apply before delegation."""
import os

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from app.cloud_storage import storage_backend
from app.cloud_worker import run_worker, WorkerDeadline


class CloudRequestDeadline:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope['type'] != 'http' or storage_backend() != 'turso'
                or os.getenv('_RYDE_CLOUD_WORKER') == '1'
                or scope.get('method') == 'OPTIONS' or scope.get('path') == '/api/health'):
            return await self.app(scope, receive, send)
        # Only APIs enter the worker; no new frontend or demo page is provided.
        if not scope.get('path', '').startswith('/api/'):
            return await self.app(scope, receive, send)
        chunks, length = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body = message.get('body', b'')
            length += len(body)
            if length > 32 * 1024:
                return await JSONResponse({'detail': 'Request body too large'}, 413)(scope, receive, send)
            chunks.append(body)
            if not message.get('more_body', False):
                break
        try:
            body = b''.join(chunks).decode('utf-8')
            headers = {key.decode('latin-1'): value.decode('latin-1') for key, value in scope.get('headers', [])
                       if key.lower() in {b'authorization', b'content-type', b'idempotency-key', b'host'}}
            reply = await run_in_threadpool(run_worker, {'op': 'http', 'method': scope['method'],
                'path': scope['path'] + ('?' + scope['query_string'].decode('ascii') if scope.get('query_string') else ''), 'headers': headers, 'body': body}, 180)
            response = JSONResponse(reply['body'], status_code=reply['status'], headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        except UnicodeDecodeError:
            response = JSONResponse({'detail': 'Invalid request body'}, 400)
        except WorkerDeadline:
            response = JSONResponse({'detail': {'code': 'REQUEST_DEADLINE',
                'message': 'The request stopped at its deadline. Saved stages remain available. A write may have completed; do not automatically retry.'}}, 504, headers={'Cache-Control': 'no-store'})
        await response(scope, receive, send)
