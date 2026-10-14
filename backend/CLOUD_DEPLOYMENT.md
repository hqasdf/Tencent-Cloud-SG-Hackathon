# Protected Vercel + Turso synthetic demo

This release keeps SQLite as the default. Setting `STORAGE_BACKEND=turso`
selects the official `libsql==0.1.11` adapter. It does not import local records.
All policy, claim verification, Judge validation and refund decisions retain
their existing code authority. Cloud live Gemini is disabled even if a caller
tries to select a real provider.
Intake table and index identifiers are also scoped by `STORAGE_NAMESPACE`, so
preview and production cannot read each other's intake records. Selecting a new
namespace creates additive tables; it never imports existing intake data.

## Configuration

Create a separate Vercel backend project with root directory `backend`, Python
3.13 and the FastAPI entry point `app/main.py`. Use the committed pinned runtime
dependencies. The function duration ceiling is 300 seconds; application workers
have a smaller 180-second hard deadline. Do not change the existing frontend
production project until its preview works with this backend.

Use `cloud.env.example` as a list of server-only variable names. Obtain the
Turso URL/token from private configuration. Generate independent, cryptographically
random checkpoint and session secrets with at least 32 characters, and retain
the same checkpoint secret across deployments. A changed signing secret makes
existing checkpoints incompatible. Never copy secrets into source or Vite variables.

`DEMO_PASSWORD_HASH` is the PBKDF2-SHA256 representation produced by
`app.demo_auth.make_password_hash`, with 300,000 iterations. Generate it privately
from a strong demo password; share that password only with approved reviewers.
Do not print secrets to build logs. This is a shared synthetic-demo account,
not a multi-user identity or authorization system.

Set `CORS_ALLOWED_ORIGINS` to exact approved HTTPS frontend origins separated by
commas, including a preview origin only while testing. Empty origins, wildcards
and production loopback origins are rejected. CORS is not authentication.
The existing project's verified production domain is `https://ryderesolve.vercel.app`;
the example uses that exact origin. A preview's exact origin must be added before
testing it, then removed when it is no longer authorized.
Private APIs require an expiring HMAC-signed bearer session, independently of
the former Caddy gateway. Browser sessions stay in memory; refresh signs out.

## Explicit initialization

Load private configuration into the local process without printing it, then run:

```text
python -m app.cloud_bootstrap --smoke
python -m app.cloud_bootstrap --seed-demo DISP-005
```

Repeat the explicit seed command for the other supported synthetic cases
DISP-002 through DISP-009. The CLI always runs remote operations inside a
bounded subprocess. Schema initialization is additive. Smoke records are uniquely
named and retained; this command never migrates or deletes local data or the six
existing smoke-test tables. Existing replay artifacts are not silently replaced.

The first cloud release shows **MOCK_DEMO**. It does not fabricate live intake
or Gemini responses. The existing frontend's four steps remain available, with
live intake clearly unavailable in this release. Full analysis uses the saved,
validated five-agent mock replay; the final decision remains calculated by code.

## Preview acceptance gate

After approval to publish a preview:

1. Verify `/api/health` reports authentication required and live AI disabled.
2. Verify unauthenticated private API requests receive 401.
3. Sign in privately. Authenticated `/api/runtime-check` must report
   `isolatedWorker: true`. This specifically verifies subprocess/native-client
   support in the actual Vercel runtime; local Linux tests alone are insufficient.
4. Set only `VITE_API_BASE_URL` in an existing frontend preview to the HTTPS
   backend URL. Keep the strict public-variable validation enabled.
5. Check sign-in, all eight cases, the four-step workflow and DISP-005 full
   analysis. Verify five roles, MOCK_DEMO provenance and code-controlled decisions.
6. Check CORS, expired sessions, rejected duplicate Idempotency-Key requests,
   recovery and safe deadline responses. Do not call Gemini.
7. Promote only after those checks pass and production deployment is approved.

No public deployment or current frontend production configuration has been
changed by this implementation.
Vercel application startup fails closed unless the platform target is `preview`
and authenticated production-mode Turso configuration is present. The backend
cannot fall back to local SQLite defaults on Vercel. This release intentionally
rejects a production target until a separate production approval and release.

## Bounds and remaining risks

Each cloud request executes in an isolated subprocess. Native stderr is captured
and never forwarded. The SDK's two-second SQLite busy timeout is **not** an HTTP
timeout; the enclosing worker deadline bounds network calls. A killed write has
an unknown outcome. The backend does not automatically retry it. Mutation action
keys are reserved durably and duplicate keys are rejected rather than replaying
an unsafe operation. Check saved state before initiating a new action after a
timeout. Leases expire after 330 seconds and checkpoint writes verify the owner
and expiry atomically, fencing stale workers.

The global synthetic-demo caps are 60 login attempts and 600 authenticated
requests per hour per storage namespace. The live AI budget is zero. These
are application safety caps, not guesses about Google or Turso quotas. They do
not prevent all unauthenticated traffic costs or deliberate denial of service.
Use platform spending controls available on the free plan, monitor quotas and
do not enable automatic paid upgrades. Login caps can temporarily block a demo
after hostile traffic. Turso and Vercel free-tier availability is not an uptime
guarantee. Request reservations and synthetic test records are retained; monitor
growth and approve any future retention cleanup separately.

The application does not depend on writable backend runtime files for cloud
intake, checkpoints, replay artifacts or leases. Prompts and synthetic fixtures
remain bundled Python/package resources. Actual Vercel preview validation and
stable private production configuration are mandatory before promotion.
