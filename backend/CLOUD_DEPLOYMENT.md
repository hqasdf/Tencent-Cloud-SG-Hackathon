# Protected Vercel + Turso synthetic demo

This release keeps SQLite as the default. Setting `STORAGE_BACKEND=turso`
selects the official `libsql==0.1.11` adapter. It does not import local records.
All policy, claim verification, Judge validation and refund decisions retain
their existing code authority. Cloud live Gemini is disabled by default. An explicitly approved live-preview
configuration is prepared behind the gates described below.
The mock release uses its existing 1024-token configuration ceiling. Gemini
Native retains its approved 4096-token configuration when separately enabled
and validated in a separately approved preview.
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
an unknown outcome. The backend never automatically retries such writes.
Workers inherit the parent's bundled dependency paths,
including Vercel's `_vendor` directory, rather than assuming `PYTHONPATH` contains
them. Mutation action
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

## Live Gemini preview preparation (not enabled by default)

The mock configuration above remains the safe default. Live preparation uses
the same Native provider, interview service, verification and Judge code.
Before accessing a new private key, publishing Git changes or making even a
Gemini model-list request, obtain the corresponding user approval.

For an approved **Preview only** release, use backend-only configuration:

- `CLOUD_LIVE_AI_ENABLED=1`
- `GEMINI_FREE_TIER_CONFIRMED=1` only after checking the key's project tier,
  disabled billing and actual model quota in Google AI Studio. This flag records
  an operator confirmation; the application cannot inspect billing from a key.
- `AGENT_MODE=real`, `AGENT_PROVIDER=gemini_native`
- `AGENT_MODEL=gemini-3.5-flash-lite`, `AGENT_MAX_TOKENS=4096`
- `AGENT_TIMEOUT_SECONDS=60`, `AGENT_MAX_RETRIES=0`
- `AGENT_RETRY_MAX_BACKOFF_SECONDS=20`
- `AGENT_API_KEY` from the approved private file; never a Vite variable
- Explicit `CLOUD_GEMINI_DAILY_ATTEMPTS` and
  `CLOUD_GEMINI_DAILY_TOKEN_BUDGET`; no unlimited or inferred free quota
- `CLOUD_GEMINI_CUMULATIVE_ATTEMPTS=20` for the approved validation; the
  existing namespace ledger is retained and counted across UTC day boundaries

Use a new logical validation namespace rather than enabling live calls on the
working mock preview. Keep its current URL, configuration, data and deployment
available as the rollback and demo fallback. Bootstrap the new namespace with
the existing explicit synthetic-only command. `SqlState.initialize()` adds
`cloud_ai_usage` without changing existing records or smoke tables.

Each live full-resolution request runs at most one new role. Existing compatible
signed checkpoints are parsed and verified again. Between roles the API returns
202 with saved role names, and the frontend performs at most five progressively
advancing requests. Provider failures, invalid output, non-progress, session
expiry and uncertain deadlines stop continuation; the operator must review
them before a new action. Leaving a case or signing out stops new role requests;
the in-flight request retains its existing deadline so it can save its checkpoint. No task runs after the HTTP response.

The native SDK translates its millisecond timeout to both HTTPX seconds and
`X-Server-Timeout`, so the previous 30-second setting also asked Google to stop
generation at 30 seconds. The approved native opt-in is 60 seconds with zero
transport retries. One allowed schema repair therefore permits at most two
60-second calls (120 seconds), leaving 60 seconds of the existing worker envelope
for startup, storage and checkpoint persistence. Other transports and the default
remain 30 seconds; native settings above 60 seconds or long native cloud calls
with transport retries are rejected.

The 180-second process deadline is retained below the configured 300-second
function duration. A role has at most one structured-output repair; each
completion has at most one transport retry. The normal configured worst-case
transport allowance is 4 x 30 seconds plus 2 x 20 seconds of retry waits.
Storage or transport stalls can still hit the hard deadline. A killed call or
write has an unknown outcome: no automatic replay of that action is permitted.
Abandoned leases expire and signed completed roles remain reusable. A native
SDK timeout is not a guarantee of exactly-once execution by Google.

Every transport attempt, including retries and repairs, first reserves an
atomic namespace/day attempt and conservative token allowance in Turso.
Reservations are never refunded after uncertain calls. Admission storage writes
are outside the Gemini transport retry handler: an uncertain reservation is
never automatically repeated and cannot start a provider call. The allowance includes
UTF-8 prompt/schema bytes, request overhead and the maximum configured output;
it is an application safety cap, not an assertion about Google's quota or
billing. Provider-supplied 429 cooldowns are persisted and cannot be shortened
by a later concurrent failure. Missing retry timing is never guessed. SDK
automatic retries remain disabled and no paid-provider fallback exists.

Private AI interview prompts retain only the current party's bounded history
and allegations. Facts remain `ALLEGED`; attribution, contradiction checks and
the verified-only Judge projection are unchanged. Authentication is still a
synthetic-demo **reviewer** session, not separate rider/driver user identities.
This release must not be advertised as a public real-person dispute service.

`FULL_AI` remains an explicit replay action with truthful `MOCK_DEMO` provenance
for seeded synthetic artifacts. A changed intake/context can make an old replay
incompatible: refusal is required, never a silent live replacement. Keep at
least one untouched, validated synthetic case for the fallback demonstration.

### Proposed bounded live validation, requiring separate approval

Confirm free-tier eligibility first. Then publish the reviewed changes to the
preview branch only and configure a separately isolated live-preview deployment.
Propose a hard cap of 12 generate attempts and 1,000,000 conservatively reserved
tokens for the validation namespace/day. Permit one tiny connectivity check,
at most three synthetic intake turns, and one five-role run across up to five
requests. Existing repair/retry attempts count toward the same 12-attempt cap.
Stop on insufficient quota, lifecycle incompletion, rejected output or timeout;
do not repeatedly retry. This cap may prevent completion when repairs are
needed, which is preferable to silently exceeding approval.

### Reversible production promotion gate

1. Obtain separate approval after real live-preview and manual Opera validation.
2. Prepare a production namespace and independent stable server signing/session
   secrets, authentication hash and Gemini allowance. Never copy local records.
3. Review a minimal change to the current `VERCEL_ENV=preview` guard; production
   remains deliberately rejected by the present implementation.
4. Configure only the backend production environment with exact CORS origin
   `https://ryderesolve.vercel.app`; verify public health and authenticated APIs
   against a staged production backend before switching frontend configuration.
5. After approval, set the frontend production `VITE_API_BASE_URL` to that
   verified backend. Review branch settings before merging/promoting so Git
   automation cannot accidentally expose an unconfigured backend.
6. Check sign-in, case selection, private synthetic intake, evidence, five-role
   live analysis, fallback provenance, deterministic final outcome and sign-out.
7. Keep the last working mock deployments and environment fingerprints for
   rollback. Reverting the frontend URL/deployment and disabling the live gate
   must not delete or rewrite data or signed checkpoints.

Reference documentation: https://vercel.com/docs/functions/limitations,
https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash,
https://ai.google.dev/gemini-api/docs/pricing,
https://ai.google.dev/gemini-api/docs/rate-limits.
