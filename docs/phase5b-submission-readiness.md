# Phase 5B - submission readiness

Reviewed 11 October 2026 (Singapore). No public deployment, live Gemini calls, credential changes, database resets, checkpoint deletion, commit or push.

## Official requirements - verified handbook

Source: [official event](https://tch.tencentcloud.com/contest/44), its Developer Guides download, [handbook PDF](https://heikesong-global-1256915710.cos.ap-hongkong.myqcloud.com/online_video/3af2d08d-d3f0-4998-98a5-a820bd245910.pdf), retrieved through the event's public download endpoint. Relevant PDF pages: 21-26, 37-40.

| Item | Handbook requirement | Readiness/action |
| --- | --- | --- |
| Deadline | 16 October 2026 | Finish package by 15 October; confirm submission-form cutoff/timezone |
| Title | Required | RydeResolve |
| Short blurb | Required, fewer than 10 words | Evidence-driven AI dispute resolution with auditable, code-controlled outcomes. (8 words) |
| Description | Required: users/scenarios/value, pain points, architecture/prompt design, impact | Draft below and README |
| Tencent usage | Original project built using at least CodeBuddy or WorkBuddy; proof is mandatory | Provide at least 3 development chat screenshots or screen recordings, with secrets redacted |
| Cover | Required, 16:9; recommended 380 x 216 px | Still needs preparing; handbook's recommended dimensions are approximately 16:9 |
| Video | Optional, 5-8 minutes: overview, core features, build/tool reflection | Three-minute rehearsal script prepared separately; expand before submitting optional video |
| Project URL | Optional, earns bonus points | Public hosting is not a submission prerequisite |
| Ryde source | Complete GitHub source | Commit/push approval and judge-access decision pending |
| Ryde prototype | End-to-end flow for at least 2 categories | Route deviation and no-show implemented; offline checks pass |
| Ryde diagram | Architecture diagram | README Mermaid diagram; check judge rendering or export later |
| Ryde walkthrough | Live demonstration of processing/resolution | Offline replay smoke-tested; manual Opera walkthrough pending |

The handbook permits any LLM/framework and mock external APIs/data. It does not require Lighthouse, ADP, TRTC, or a Tencent-hosted LLM. CodeBuddy/WorkBuddy development-use proof is distinct from a runtime Tencent Cloud integration. Tool configuration files alone are not adequate evidence.

The submission Google Form redirected to a sign-in/access-protected response (HTTP 401). Its exact fields, repository public/private policy, upload limits, URL access rules, and deadline timezone remain unverified. Open the form manually and provide screenshots of its requirements if they differ from the handbook. The handbook says detailed preliminary track judging criteria are supplied after registration; obtain those from the Ryde group. It lists ten equal 10-point general dimensions: impact, human-centred design, AI interaction, technical execution, feasibility, demo/storytelling, innovation, UX/accessibility, responsible AI, and overall quality. Some surrounding final-round prose appears reused from another event; confirm inconsistencies with organizers rather than inventing rules.

## Submission description draft

### Problem and users
Ride-hailing support teams must reconcile competing rider/driver accounts with GPS, timestamps, chat and fare evidence. Manual reviews can be slow and inconsistent. RydeResolve serves support reviewers and demonstrates a transparent dispute workflow for route deviation and no-show cancellation. This prototype does not claim production access to Ryde or real company policy.

### Solution and innovation
Two AI intake roles capture party statements; the deterministic engine validates structured evidence and flags contradictions. Rider and Driver Advocates argue from an allow-listed factual context. Code verifies claims before bounded Rebuttals and the advisory Judge. The final refund, confidence and escalation remain code-controlled. Rejected claims remain visible. Replay revalidates saved output against the current trusted context, labels provenance, and never silently spends provider quota after invalidation.

### Architecture and stack
React 19/TypeScript/Vite UI -> FastAPI/Pydantic API -> synthetic fixture/intake SQLite repositories -> deterministic evidence/policy calculations -> restricted AI inputs -> Rider/Driver Advocates -> claim verification -> Rebuttals -> advisory Judge -> deterministic resolution/human review. Durable signed checkpoints preserve valid work; replay is separately identified as LIVE, REPLAYED_LIVE, MOCK_DEMO or UNVERIFIED_REPLAY. Python 3.13, google-genai, pytest and Node tests; production images use Docker and a same-origin authenticated Caddy HTTPS gateway. Lighthouse configuration is prepared, not deployed.

### Value and evidence
The proposed value is auditable decisions, fair party representation and fewer unnecessary model calls. Current automated evidence: 887 backend and 17 frontend tests, production frontend build, container builds, and isolated offline replay smoke checks. These are engineering validation, not measured resolution-time savings, fairness outcomes, cost reduction or production scalability. A future controlled evaluation should measure review time, policy agreement, escalation precision and calls per dispute.

### Limitations
Synthetic text/structured fixtures and prototype policies; no real Ryde integration or financial execution. No verified photo fraud analysis, live policy RAG, feedback learning or broad dispute coverage. SQLite/single-worker deployment is a prototype. Mock mode does not provide AI Intake conversation replies. Existing interviews must not be packaged. Live AI Intake/production provider QA is outside this offline pass. Tool-use evidence, cover, actual form review and manual UI QA remain outstanding.

## Manual Opera acceptance checklist

Use the existing Opera browser manually. No browser automation was used.

1. Confirm `/api/health` reports mock mode before any AI action. Do not click Send, Run full analysis, Retry/reuse or Advocates only against the developer's real-provider server.
2. Select DISP-005 (route deviation/road closure): verify parties/evidence/timeline and explained fare difference; play **Show saved demo (replay)** only with compatible artifacts. Expect MOCK_DEMO, Rider and Driver Advocates, verified/rejected claims, both Rebuttals, advisory Judge, and NO_REFUND / SGD 0.00 controlled by code.
3. Select DISP-002 (no-show): inspect pickup radius, wait threshold and cancellation policy. Expected deterministic outcome: cancellation charge upheld, refund SGD 0.00. A separate compatible no-show replay is required for its five-role demo; changing case does not make DISP-005 artifacts transferable.
4. Select DISP-003 (route-deviation contradictions): inspect evidence conflicts and HUMAN_REVIEW; do not mistake static activity labels for an executed AI run. This case shows why uncertainty escalates rather than fabricates a ruling.
5. Inspect Rider/Driver intake panels and statement attribution. In an offline empty-intake demo, explain the intake lifecycle, but do not claim a completed conversation. Existing automated tests cover intake extraction and contradictions; they are not a recorded UI conversation.
6. Check case switching/rapid switching, expansion, scrolling, keyboard focus, narrow Opera window, and refresh. Confirm late responses never replace another case and saved replay still works.
7. Confirm replay badges are readable, unavailable/invalid replay produces a refusal with no live fallback, and human-review status is distinct from automatic resolution.
8. Record observed results manually before filming. Do not expose developer interviews, keys, filesystem paths or browser profiles in capture.

Pitch cases: lead with DISP-005 for the full safeguarded pipeline, use DISP-002 to prove the second category, then DISP-003 briefly to show responsible escalation.

## Git review and proposed commits - not executed

All pending Phase 5A diffs were reviewed: production env validation/CORS, configured intake path, guarded synthetic initialization, test isolation, frontend same-origin API selection, pinned runtime closure, Docker/Caddy/Compose and documentation. No tracked file deletion; Phase 2 intake/statements and Phase 4 checkpoints/replay/orchestration remain. Full backend regression suite passes.

Tracked plus non-ignored candidate scan: no matching Gemini/GitHub/OpenAI credential patterns or private-key headers; no candidate `.env`, database, private-key or checkpoint JSON files. This is a limited pattern/path scan, not proof against every possible credential. Developer env, intake DB, checkpoints, replay artifacts and deployment runtime env are ignored; Docker context uses an allow-list. Do not use `git add .` or copy private artifacts into source.

Proposed commits, each only after approval:
1. `Prepare isolated production configuration and reproducible deployment`: deployment code/tests, backend env example/requirements/lock, configured intake storage, guarded replay CLI, frontend API helper/service/Vite changes/tests/env example, Dockerfiles, .dockerignore, deploy templates/Caddy/Compose and .gitignore.
2. `Document submission package and offline demonstration`: README, backend README, Phase 5A deployment notes, this readiness document and three-minute script.

Before approved staging, repeat the redacted scan, review the explicit file list and staged diff, and confirm no local env/DB/interview/checkpoint artifacts. No secrets are printed here. Historical Git credential rotation remains a separate prior incident; this pass neither changes credentials nor claims rotation was performed.

## Validation and deployment boundary

Docker Desktop 29.8.2/Linux engine available. Both exact Dockerfiles built successfully. Backend image: Python 3.13.12, locked dependencies, runtime UID/GID 10001. Frontend: npm ci, production same-origin bundle, Caddy 2.11.7. Process-local Docker PATH corrected; no Docker credential configuration changed.

Offline containers use `--network none`, a fresh uniquely named data volume and no provider credential. No existing application DB/checkpoint directory is mounted. DISP-005 and DISP-002 MOCK_DEMO initialization and five-stage revalidation pass; API health and frontend static HTTP serving pass. DISP-005 full replay stays valid after API restart. Two synthetic intake rows and zero interview messages were verified in isolated storage. Production Caddy configuration validation passes. This checks real images but is not a new end-to-end public HTTPS/Compose deployment test. Phase 5A HTTPS integration remains separately reported evidence.

The production Compose manifest binds public 80/443, hides API host port, requires a gateway username/hash/domain, waits for API health and names persistent volumes. `-p` alone does NOT isolate its explicitly named volumes. Never run it unchanged against local production-like data for QA. Validate with `docker compose --env-file deploy/compose.env -f deploy/compose.yaml config --quiet` after approved host credential setup; never print rendered secret-bearing configuration. Build with `docker compose --env-file deploy/compose.env -f deploy/compose.yaml build`. Public start remains a later approval decision. No `down -v`, DB reset or checkpoint deletion.

Remaining deployment: approved host/domain/DNS, operator setup of ignored runtime env and gateway credentials, production volume/backup permissions, protected TLS/UI/API checks, restart/rollback and manual acceptance. Only do this before submission if required by an updated official instruction or worthwhile after mandatory materials are complete.

## Before 16 October - priority order

- 11-12 Oct: review actual submission form and registered Ryde rubric; gather at least 3 sanitized CodeBuddy/WorkBuddy development screenshots; confirm team registration and repository judge access.
- 12-13 Oct: manual Opera QA; finalize description and architecture; prepare required 16:9 cover. Decide whether to record optional 5-8 minute video.
- 13-14 Oct: approve scoped commits/push separately; check clean-clone reproduction on another environment and accessible GitHub source; rehearse 3-minute narrative and expand if making video.
- 15 Oct: submit ahead of cutoff, verify required links/uploads and save submission receipt. Do not wait for optional Lighthouse deployment.
- 16 Oct: contingency for organizer-requested corrections only; confirm exact cutoff/timezone with the form/organizers.

### Final Phase 5B result

887 backend tests passed (one upstream AnyIO/Starlette deprecation warning); 17 frontend tests passed; TypeScript/Vite production build passed; both Docker image builds passed; isolated mock replay, restart persistence, frontend container HTTP and production Caddy validation passed; Compose quiet syntax validation passed; git diff --check passed. No application source changes were made in Phase 5B. Files edited: README.md and docs/phase5a-deployment.md; files added: this document and phase5b-demo-script.md. Existing Phase 5A files remain pending and uncommitted. No broad staging/commit/push occurred.
