# Phase 5A: deployment preparation

Local preparation is complete; no public deployment, purchase, billing change or
live Gemini request has been made. This is an **authenticated synthetic hackathon
demo**, not a multi-tenant service for private customer disputes.

## Recommended architecture and costs

| Approach | Components | Tradeoff |
|---|---|---|
| **Recommended: Singapore Lighthouse** | One Linux instance; Caddy serves built React files and proxies FastAPI; SQLite, signed checkpoints and replay use persistent Docker volumes | Smallest operational setup and bundled pricing; one host is a failure point, so backups are required |
| Alternative: Singapore CVM + CBS | Same two-container deployment; persistent CBS-backed host disk | More sizing/storage flexibility; compute, disks and bandwidth are separate cost items |

Do not place the current SQLite file on an ephemeral application filesystem or
scale this deployment across multiple hosts. There is one API worker and one
database file. No managed database, queue, Redis, Kubernetes or extra API service
is needed for this MVP.

Tencent's published Lighthouse table currently lists Singapore-region-group
Starter Linux 2-core/4-GB bundles at **USD 6/month** (60-GB SSD, 1,536-GB transfer)
and **USD 7.50/month** (70-GB SSD, 2,048-GB transfer). Budget roughly USD 6-10/month
for the instance, subject to actual account availability/quote. Domain purchase,
snapshots/backups, taxes and Gemini usage are additional. The table lists
Singapore traffic overage at USD 0.081/GB for applicable capped bundles. No free
VM or competition credit is assumed. See [official Lighthouse pricing](https://intl.cloud.tencent.com/document/product/1103/47794?lang=en)
and [CVM pricing](https://www.tencentcloud.com/pricing/cvm/overview).

Required services: Tencent Cloud account/CAM access, one approved Linux instance,
persistent disk, a DNS hostname/domain, and Docker Engine with Compose v2. Caddy
provides HTTPS and the demo gateway on that host. A registry is optional; images
can be built on the host. [Caddy's automatic HTTPS requirements](https://caddyserver.com/docs/automatic-https)
include DNS pointing at the host, reachable TCP ports 80/443 and durable writable
certificate storage.

## Challenge requirements requiring confirmation

The [official event homepage](https://tch.tencentcloud.com/) lists multiple
Singapore 2026 events. It describes the AI CAN DO IT event using CodeBuddy,
WorkBuddy and other relevant technologies. The [contest 44 page](https://tch.tencentcloud.com/contest/44)
does not expose its detailed challenge statement in the retrieved content.
The repository does not contain an authoritative track brief.

Before submission, confirm the exact event and industry track, required Tencent
products, whether Google Gemini is permitted, whether Tencent hosting alone
qualifies, public URL/authentication rules, required submission artifacts and
any credit conditions. Do not assume a Tencent model, TRTC integration or managed
database is mandatory. The current Gemini Native configuration remains intact.

## Production boundaries

- `APP_ENV=production` skips project `.env` loading and validates storage and AI
  configuration before route imports can initialize SQLite.
- `INTAKE_DB_PATH`, `AGENT_CHECKPOINT_DIR` and `REPLAY_ARTIFACT_DIR` must be
  absolute writable paths. The Compose manifest mounts `/data` on a persistent
  named volume; this does not survive deleting the volume or host disk.
- Production CORS defaults to no cross-origin grants because UI/API share an
  origin. Explicit origins must be exact HTTPS origins; wildcards and localhost
  are refused. CORS is not authentication.
- Caddy protects UI and API with a hashed-password demo gateway, blocks
  foreign-origin browser POSTs before proxying, and strips the gateway
  Authorization header before forwarding to FastAPI. The API port is not
  published. Do not bypass this gateway by exposing port 8000.
- Provider keys remain in the server runtime environment. The frontend build
  accepts only the public `VITE_API_BASE_URL` variable and refuses credentials,
  insecure URLs and localhost in production. An empty URL uses same-origin `/api`.
- No access/request-body logging is enabled in the prepared backend command or
  Caddy config. Do not add verbose request dumps or print populated Compose config.
- The backend image runs as UID/GID 10001. Preserve volume ownership and the
  signed-checkpoint key alongside checkpoint files; deleting/replacing that key
  invalidates recovery. Keep backups sensitive and outside the source checkout.

Development remains on localhost/127.0.0.1:5173 and backend port 8000. It retains
the existing local database default and local `.env` behavior.

## Environment variables (names only)

| Variable | Purpose/location |
|---|---|
| `RELEASE_TAG` | Reviewed image release identity in server-only compose.env |
| `DOMAIN` | DNS hostname only, without scheme/path/port, in compose.env |
| `DEMO_USER`, `DEMO_PASSWORD_HASH` | Approved gateway access; hash is server-side |
| `APP_ENV` | Deployment mode; Compose sets it for the backend |
| `INTAKE_DB_PATH`, `AGENT_CHECKPOINT_DIR` | Durable paths; Compose sets them |
| `REPLAY_ARTIFACT_DIR`, `REPLAY_API_ENABLED` | Compatible saved demo location and explicit replay gate |
| `CORS_ALLOWED_ORIGINS` | Exact allowed origins; empty for this same-origin setup |
| `AGENT_MODE`, `AGENT_PROVIDER` | Offline mock or separately approved live Native configuration |
| `AGENT_MODEL`, `AGENT_MAX_TOKENS` | Approved live Gemini model and 4096-token envelope |
| `AGENT_TIMEOUT_SECONDS`, `AGENT_MAX_RETRIES` | Existing bounded transport configuration |
| `AGENT_RETRY_BACKOFF_SECONDS`, `AGENT_RETRY_MAX_BACKOFF_SECONDS`, `AGENT_REASONING_EFFORT` | Existing retry/reasoning settings |
| `AGENT_API_KEY` | Provider credential, backend runtime only |
| `VITE_API_BASE_URL` | Public frontend build URL only; empty in prepared Compose build |

The shipped runtime template defaults to **explicit mock mode**, with the mock
transport's existing 1024 ceiling. Its commented live alternative remains
Gemini Native / gemini-3.8-flash / 4096 tokens / at most one transport retry.
This does not change the developer's configured live provider or token budget.
Mock mode does not simulate AI Intake conversations; sending a message reports
unconfigured intake. Saved empty intake and the full resolution demo are honest
offline demonstrations.

## Exact deployment sequence — only after approval

1. Confirm the challenge brief, domain, host choice, quote and gateway access
   policy. Obtain approval before purchasing, provisioning publicly, changing
   billing or migrating existing data. No credentials have been created here.
2. Provision the approved Linux host in Singapore and configure DNS. Allow TCP
   80/443; restrict SSH to the operator's address. Do not expose 8000 or 5173.
3. Install Docker Engine/Compose v2 using its official installation guide. Copy
   reviewed source/manifests only. Do not copy developer `.env`, SQLite files,
   checkpoints, browser profiles, private interview data or local replay files.
4. Copy `deploy/compose.env.example` to the ignored `deploy/compose.env`, and
   `deploy/backend.env.example` to the ignored `deploy/backend.env`. Set the
   release tag/domain and approved gateway username/hash on the host. Restrict
   file permissions to the operator. Use Caddy's interactive `hash-password`
   command after credential setup is authorized; never place plaintext passwords
   in command-line arguments or committed files. Single-quote the hash in the
   Compose env file to preserve dollar characters.
5. Keep backend mock mode for the first public demo. No Gemini credential is
   necessary for that mode. Live mode requires separate approval and the existing
   Gemini Native/4096 configuration; never put its key in `VITE_` variables.
6. From the repository root, validate and build:

   ```bash
   docker compose --env-file deploy/compose.env -f deploy/compose.yaml config --quiet
   docker compose --env-file deploy/compose.env -f deploy/compose.yaml build
   ```

   Run the image pull/build on a Docker-capable host before public launch. The
   base images use explicit release tags, Python runtime dependencies are pinned
   in `requirements.lock`, and frontend installation uses `npm ci`. Record the
   resulting image IDs/digests for rollback. All three base-image tags were confirmed in Docker Hub metadata. Phase 5B subsequently built both exact images on the local Docker Linux engine
   and smoke-tested them offline. Public host execution remains unverified.
7. Before starting the app, initialize a **new synthetic empty intake** and its
   compatible replay in the persistent volume:

   ```bash
   docker compose --env-file deploy/compose.env -f deploy/compose.yaml run --rm --no-deps api \
     python -m app.replay.cli mock-demo --case DISP-005 \
     --output /data/replay/submission-v1 --initialize-empty-intake
   ```

   This never calls Gemini. It requires an explicitly configured database path,
   preserves existing records, refuses any populated or unrecognized database and refuses an existing
   artifact directory. It captures/revalidates only mock output. No private
   database is imported. Future regeneration uses a fresh versioned directory;
   update the runtime replay path after validation, never delete checkpoints.
8. Start both services:

   ```bash
   docker compose --env-file deploy/compose.env -f deploy/compose.yaml up -d --no-build
   docker compose --env-file deploy/compose.env -f deploy/compose.yaml ps
   ```

   Caddy waits for backend health and provisions public TLS. Verify authentication
   is required for both UI and API and that the domain/certificate are correct.
9. Check the protected health endpoint with `curl --user "$DEMO_USER"` and the
   domain's `/api/health` URL; curl prompts for the password instead of storing it
   in the command. Do not use verbose header logging. Open the website manually,
   select DISP-005 and click **Show saved demo (replay)** once. Expect MOCK_DEMO,
   five completed roles, partial/empty intake provenance and NO_REFUND/SGD 0.00.
   This does not claim completed live interviews. Do not click live analysis or
   Send without separate approval.
10. Restart/recreate the API and confirm intake ID, checkpoints and replay survive.
    This validates the mounted volume, not just container startup. Complete the
    existing manual Opera visual checklist before submission.

## Backup and rollback

Keep reviewed previous image tags/digests. Before an upgrade, briefly stop the
API and make a consistent protected backup of the entire `ryderesolve_app_data`
volume (intake DB, signing key, checkpoints and replay) outside the checkout;
also preserve Caddy certificate storage and server configuration securely. A
stopped-volume backup avoids copying a database while writes are in flight.
Cloud snapshots/backups incur their own charges; approve them first.

To roll back application code, select the previous `RELEASE_TAG` and run Compose
`up -d --no-build --pull never`, keeping the same named volumes. Check health and
the saved demo again. Do not run `down -v`, remove volumes, or replace the signing
key. A data restore or migration affecting existing records needs approval; it
is not part of this preparation. Incompatible replay safely refuses rather than
calling Gemini, so prepare a fresh demo if source/schema identity changed.

## Validation and remaining limits

- Complete backend suite: **887 passed / 0 failed** (Phase 4 baseline: 864).
- Frontend: **17 passed**; TypeScript and Vite same-origin production build passed.
- Production-style startup, health, fresh demo and persistence after restart:
  passed on isolated loopback ports with a fresh SQLite file.
- Native Caddy 2.11.7 was checked against its official SHA512 release checksum.
  Configuration validation and local HTTPS integration passed: trusted test TLS,
  unauthenticated UI/API 401, foreign-origin POST 403, authenticated static UI/API
  and same-origin five-agent replay. No browser automation, public listener,
  trust-store installation, credential change or live Gemini call occurred.
- Docker Compose YAML parses and declares persistent volumes with no published
  backend port. Docker Engine is unavailable here, so image build/runtime and
  actual Compose execution remain **unverified**. Local HTTPS used an isolated
  test CA, not a public ACME certificate.
- A credential/pattern scan covered 3,347 tracked files and 139 proposed build
  inputs, plus the compiled frontend: zero detected credential findings, no
  tracked local database/config files, and no localhost:8000 dependency in the
  production bundle. The final tracked-plus-proposed scan covered 3,361 files with zero detected credential matches. This is a bounded scan, not proof of every possible secret.
- `.dockerignore` allowlists the active build inputs; private files, Git history,
  generated caches and diagnostics are excluded. Runtime env files are ignored.
- No known application regression remains in the tested scope. Live quota,
  target-host Docker execution, public DNS/TLS, official track compliance and
  manual browser sign-off remain external/readiness checks.

The shared gateway is sufficient for a judge-only synthetic demo. It does not
provide individual accounts, per-case tenant authorization or unrestricted
public access to real interview data. That scope would need additional security
work and approval; do not represent this MVP deployment as providing it.

## Exact changed files

| Purpose | Files |
|---|---|
| Production configuration and persistence | `backend/app/deployment.py`, `backend/app/main.py`, `backend/app/env.py`, `backend/app/repositories/intake_repository.py`, `backend/.env.example` |
| Portable demo | `backend/app/replay/cli.py` |
| Dependency installation | `backend/requirements.txt`, `backend/requirements.lock` |
| Backend image | `backend/Dockerfile` |
| Public frontend URL and build guard | `frontend/src/utils/apiConfig.ts`, `frontend/src/services/caseService.ts`, `frontend/src/services/intakeService.ts`, `frontend/vite.config.ts`, `frontend/.env.example` |
| Frontend/gateway image | `frontend/Dockerfile` |
| Host orchestration and gateway | `deploy/compose.yaml`, `deploy/Caddyfile`, `deploy/compose.env.example`, `deploy/backend.env.example` |
| Private artifact exclusion | `.dockerignore`, `.gitignore` |
| Offline regression coverage | `backend/tests/conftest.py`, `backend/tests/test_deployment.py`, `frontend/test/apiConfig.test.ts` |
| Documentation | `README.md`, `backend/README.md`, `docs/phase5a-deployment.md` |

All changes are local and uncommitted. No deterministic policy, claim/Judge
validation, signed-checkpoint identity or existing database schema changed.

## Phase 5B validation update - 11 October 2026

Both Dockerfiles built successfully on Docker Desktop's Linux engine. The backend
ran as UID/GID 10001, reported healthy mock configuration with no credential, and
served a valid five-stage MOCK_DEMO replay. Fresh synthetic DISP-005 and DISP-002
initialization/revalidation passed with network disabled. DISP-005 replay remained
valid after API restart. Two empty-intake rows and zero interview messages existed
only in a newly created isolated volume. The frontend image served HTTP successfully
inside its container; the production Caddy configuration validated. Compose syntax
validated quietly using a temporary copy resolving paths and placeholder variables.

This was not a new public TLS or full original-Compose gateway integration test;
the Phase 5A HTTPS evidence remains separate. QA containers were stopped/removed;
the isolated volume was retained without DB/checkpoint deletion. Existing application
storage was never mounted. Backend 887 tests and frontend 17 tests passed again;
frontend TypeScript/production build and git diff --check passed. No public deployment.

Submission requirements and remaining mandatory assets take priority over optional
hosting: see [Phase 5B readiness](phase5b-submission-readiness.md).
