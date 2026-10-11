# Final secure release preparation

Date: 2026-10-12. Base commit: `8a561e51d067f53ba292a0a08452195dd3fe34c7`.
Status: proposed release reviewed locally; approval required before staging, committing or pushing.

## What is already included

The tracked base already contains Phase 2 intake integration and provenance, the Gemini Native provider and 4096-token live configuration, strict atomic proposition schemas, both Advocates/Rebuttals/Judge, deterministic resolution and human review, signed per-role recovery, replay/mock support, all eight fixtures, the four-section frontend, tests and documentation, Dockerfiles, Compose, pinned runtime dependencies, frontend lockfile and environment templates. They remain part of the release tree; no legacy or teammate files were discarded.

The pending release applies the final security corrections and the following reproduction repairs:

- Tracked tests previously loaded two source helpers from ignored `.live-validation`. Reviewed, credential-free copies now live under `backend/tests/support`; the published harness has no live CLI entry point and defaults its generated output paths to ignored test scratch storage. The private originals, credential probe and records are not included or changed.
- Three suites now import that distributed support. No test assertions were removed or disabled.
- The environment-path test checks the actual backend module location instead of requiring a particular checkout directory name.
- Setup/rehearsal documents use the current UI action, approved live token/retry settings and exact runtime dependency lockfile. Live configuration is documented only, not enabled by this preparation.

## Exact proposed inclusion list

Stage only these paths after the list is approved. M = tracked modification; A = new source/document. No broad `git add .` or `git add -A` is needed.

| Status | Path | Purpose |
| --- | --- | --- |
| M | README.md | Current architecture, secure setup and report links |
| M | RUNNING.md | Locked setup, 4096/one-retry live documentation, production boundary |
| M | backend/app/agents/config.py | Configuration error redaction |
| M | backend/app/api/cases.py | Safe standalone-analysis lock responses |
| M | backend/app/api/intake.py | Safe intake lock/storage responses |
| M | backend/app/deployment.py | Private permissions for new production files |
| M | backend/app/llm/hunyuan_client.py | Dormant client repr/error redaction |
| M | backend/app/llm/openai_client.py | Dormant client error redaction |
| M | backend/app/main.py | Request boundary, safe validation errors, production docs disabled |
| M | backend/app/models/intake.py | Bounded input and forged-field rejection |
| M | backend/app/replay/store.py | Embedded-secret checks, safe paths and artifact-size limit |
| A | backend/app/security.py | Body/Host boundary and validation redaction |
| M | backend/app/services/agent_checkpoints.py | Reusable OS-backed lock; signing identity retained |
| M | backend/app/services/case_service.py | Standalone/full-run exclusion |
| M | backend/app/services/conversation_service.py | Intake send/skip exclusion and credential-pattern rejection |
| A | backend/tests/security_gateway_smoke.py | Repeatable local synthetic HTTPS gateway regression |
| A | backend/tests/support/__init__.py | Distributed offline test-support package |
| A | backend/tests/support/checkpoints.py | Credential-free replay resume planner used by tests |
| A | backend/tests/support/harness.py | Guarded transport/capture test support, no live CLI |
| M | backend/tests/test_live_resume.py | Import distributed support |
| M | backend/tests/test_live_stop_rule.py | Import distributed support |
| M | backend/tests/test_local_env.py | Checkout-name-independent module-path assertion |
| M | backend/tests/test_replay_capture_safety.py | Import distributed support |
| A | backend/tests/test_security.py | 27 security regression cases |
| M | deploy/Caddyfile | Request limits/timeouts, CSP and no-store policy |
| M | docs/phase5a-deployment.md | Current saved-demo button label |
| M | docs/phase5b-demo-script.md | Current button label, results and security wording |
| A | docs/security-audit-20261012.md | Redacted findings, mitigations and evidence |
| A | docs/final-release-preparation.md | This inclusion/approval and reproduction guide |
| M | frontend/package-lock.json | source-map-js 1.2.1 -> 1.2.2 only |
| M | frontend/src/components/ErrorBoundary.tsx | Generic crash display/logging |
| M | frontend/test/simplifiedWorkflow.test.ts | XSS-safe rendering and crash-redaction tests |

All other already-tracked files stay in the tree. The inclusion proposal does not alter the original Git index.

## Explicit exclusions

- Actual `.env` files, `deploy/backend.env`, `deploy/compose.env`, credentials and gateway password/hash values. Blank/commented `.env.example` templates remain included.
- Entire `backend/.live-validation`, including `credential_check.py`, ledgers, request dumps, logs and private diagnostics.
- Developer SQLite files and journals; interview records; private checkpoints/signing keys; private replay/benchmark artifacts; deployment data and backups.
- Browser profiles/caches, authentication sessions, screenshots and generated test/build/dependency directories.
- Private TLS keys/certificates and generated gateway diagnostics.
- `.git`, reflogs, old Git objects and any sensitive backup of the original checkout.

Never submit a whole-working-directory ZIP. Build from reviewed tracked source plus the explicitly approved new source paths, then generate new synthetic demo state on the target. A validation checkout develops its own ignored database/keys/caches while testing; those generated files are not release source.

## Fresh-checkout offline setup (Windows PowerShell)

Use a new checkout with Python 3.13 and Node 24 available. Do not run initialization over your existing development database or overwrite a configured environment.

From the root:

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.lock -r backend/requirements.txt
backend/.venv/Scripts/python.exe -m pip check
cd frontend
npm ci
npm test
$env:VITE_API_BASE_URL=''
npm run build
cd ..
```

The runtime closure is pinned by `requirements.lock`; pytest and local Uvicorn development extras come from `requirements.txt`. Development extras can resolve newer compatible versions. The offline demo needs no provider credential. No real interview is bundled or fabricated.

In the backend terminal, on the fresh checkout only:

```powershell
cd backend
$env:APP_ENV='development'
$env:AGENT_MODE='mock'
$env:AGENT_PROVIDER='mock'
$env:AGENT_API_KEY=''
$env:AGENT_MAX_TOKENS='1024'
$env:AGENT_MAX_RETRIES='1'
$env:INTAKE_DB_PATH=Join-Path (Get-Location).Path 'intake.db'
$env:AGENT_CHECKPOINT_DIR=Join-Path (Get-Location).Path 'agent_checkpoints'
$env:REPLAY_API_ENABLED='1'
$env:REPLAY_ARTIFACT_DIR=Join-Path (Get-Location).Path 'replay_artifacts/submission-v1'
.venv/Scripts/python.exe -m app.replay.cli mock-demo --case DISP-005 --output $env:REPLAY_ARTIFACT_DIR --initialize-empty-intake
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The initializer refuses an existing artifact directory or populated/unrecognized database. If either exists, stop and inspect; do not delete or reset it to make this command pass.

In the frontend terminal:

```powershell
cd frontend
$env:VITE_API_BASE_URL='http://127.0.0.1:8000'
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

On a fresh machine these ports are the defaults. If occupied, choose unused ports for both services, set the frontend API URL accordingly, and set `CORS_ALLOWED_ORIGINS` to the exact local frontend origin in the backend terminal. Do not stop unrelated services.

Manual UI: select DISP-005, choose **More options -> View saved demo**, and verify **MOCK DEMO**, five completed roles, NO_REFUND and SGD 0.00. Intake is empty/incomplete, not a completed AI interview. In this explicit mock-only server, **Run analysis** uses simulated outputs for all eight cases without Gemini; it is not a genuine live model test. Mock mode does not implement conversational AI intake, so do not present its unconfigured interview as a successful interview.

Linux/macOS use `backend/.venv/bin/python`. Public demonstration must use the authenticated HTTPS gateway, private backend and production configuration from [deployment preparation](phase5a-deployment.md); these local development commands are not public deployment instructions.

## Reproduction verification

The proposed source tree was exported without private data or original Git metadata. A new virtual environment without system packages installed the pinned runtime plus documented test/development dependencies; pip check passed. A separate frontend installed with npm ci and built from its lockfile. Only empty temporary Git metadata was initialized to test ignore rules; process-only trust was scoped to that known temporary path, not added to global Git configuration.

Fresh isolated servers used distinct loopback ports, blank provider keys, mock mode, a new SQLite database/checkpoint namespace and newly generated synthetic replay. The frontend's served configuration was verified to point to the fresh backend, with matching CORS. All eight fixture HTTP workflows completed with MOCK_DEMO provenance and unchanged outcomes:

| Case | Code-owned outcome | SGD | Judge |
| --- | --- | ---: | --- |
| DISP-002 | UPHOLD_CANCELLATION_CHARGE | 0.00 | COMPLETE |
| DISP-003 | HUMAN_REVIEW | 0.00 | PENDING_HUMAN_REVIEW |
| DISP-004 | UPHOLD_CANCELLATION_CHARGE | 0.00 | COMPLETE |
| DISP-005 | NO_REFUND | 0.00 | COMPLETE |
| DISP-006 | HUMAN_REVIEW | 0.00 | PENDING_HUMAN_REVIEW |
| DISP-007 | FULL_FARE_DIFFERENCE_REFUND | 8.50 | COMPLETE |
| DISP-008 | NO_REFUND | 0.00 | COMPLETE |
| DISP-009 | HUMAN_REVIEW | 0.00 | PENDING_HUMAN_REVIEW |

Both Advocates and both Rebuttals completed for every case. The newly generated DISP-005 FULL_AI replay completed all five roles without fallback calls, and empty intake remained correctly incomplete. The temporary servers were stopped afterward; original services and private records were not replaced.

This checks HTTP contracts and frontend configuration, not browser visual behavior. Docker image/public-domain execution is still a separate deployment verification.

Final isolated results: **922 backend tests passed with zero failures/skips**, **26 frontend tests passed**, TypeScript compilation and the production Vite build passed, and pip check reported no broken requirements. One existing Starlette/AnyIO deprecation warning remains. Earlier failed exports exposed the missing test support and an overfitted checkout-name assertion; those were corrected without skipping tests. Temporary-path length and Git ownership issues were handled in the test environment rather than by weakening application checks.

The reviewed proposed tree contains 3,375 files and 32 explicit pending paths. A redacted scan of every proposed source file and the complete proposed diff found zero active/historical credential matches or forbidden data paths. The security audit document itself was included in that review. This is a scoped detection result, not a guarantee of absolute absence. The original index remains unchanged; exact staged blobs must be checked again after the inclusion list is approved.

## Historical cleanup recommendation (separate approval)

The old credential is still in the ignored local probe and reflog-retained objects, but absent from current branch/ref-reachable history. Current refs do not require rewriting to publish the reviewed source. Normal main publication must not include reflog objects, ignored diagnostics or a `.git` archive.

Safest immediate route: publish only the reviewed source tree/current branch and provision a new synthetic demo from it. Preserve the original sensitive checkout separately. If local forensic cleanup is desired, first approve an outside-repository sensitive preservation copy, removal/redaction of the obsolete literal, and narrowly scoped reflog/object cleanup. Do not run blanket garbage collection, expire all recovery material, delete checkpoints or force-push. No cleanup action was performed here. Revocation/replacement is user-reported; old credentials were not tested against Google.

## Remaining conditions and approval gate

The release remains **SAFE WITH REQUIRED MITIGATIONS** for an authenticated shared synthetic demo, not a private multi-user service. Keep the backend private, protect UI/API over HTTPS, use trusted viewers and synthetic data only, monitor sequential request spending/disk use, and verify host/image/storage/backup protections before deployment. Starlette and pytest advisories documented in the [security audit](security-audit-20261012.md) remain pending separately approved compatible upgrades.

Review the explicit inclusion list and the complete proposed diff, including new files. After approval, stage exactly that list, recheck the actual staged blobs and privacy exclusions, and present the final staged diff for a second review. Do not commit or push until explicitly approved. No live Gemini, browser automation, credential changes, database reset, checkpoint deletion, history rewriting, commit, push or public deployment occurred during this preparation.
