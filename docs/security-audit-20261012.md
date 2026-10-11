# RydeResolve pre-submission security audit

Audit date: 2026-10-12. Starting HEAD: `8a561e51d067f53ba292a0a08452195dd3fe34c7`. Starting working tree was clean. This report contains no credential values or interview transcripts.

## Executive assessment

**SAFE WITH REQUIRED MITIGATIONS**, for an authenticated, shared, synthetic hackathon demonstration only. The shared gateway credential authorizes access to the whole demo workspace, including both party histories. This is not a private Rider/Driver account service and is not suitable for unrestricted public or real-person disputes.

Confirmed local vulnerabilities were corrected without changing policy thresholds, refund formulas, strict model-output validation, the four-step UI, checkpoint identities or existing data. No active credential was found in current source, build output or branch-reachable history. However, the historical exposed credential remains in an ignored local probe and in reflog-retained Git history. The user reports revocation/replacement; this audit did not contact Google to test either key.

Remaining Python advisories require a separately approved framework/test-tool migration if a fully patched dependency baseline is required. Their affected functionality is unused or mitigated in the reviewed demo paths; this is not the same as fixing those packages. Public DNS/TLS, host firewall, actual Linux container images and host/backup ACLs still require deployment-time verification.

## Threat model

| Actor or failure | Boundary and consequence |
| --- | --- |
| Anonymous visitor | Caddy authenticates UI/API before dispatch; HTTPS protects credentials. Never publish backend port 8000. |
| Authenticated demo viewer | Can view and act on all synthetic disputes and both histories. Shared password is not party/tenant authorization. Restrict distribution to trusted judges/operators. |
| Malicious API request | Typed JSON requests, 32 KiB body ceiling, Host syntax guard, parameterized SQL, known-case lookup, lifecycle checks, request locks and replay gate. No uploads/admin mutation endpoints. |
| Misleading interview | Untrusted allegations only; server-owned attribution/corrections; restricted party context; deterministic evidence/claim checks. No executable model-generated commands. |
| Compromised frontend JS | Same-origin authenticated JS has workspace authority. CSP and escaped text reduce injection risk, but cannot make an already compromised application safe. |
| Git/archive/deployment accident | Git ignores private files; Docker context allows only approved build inputs. A whole-directory ZIP or `.git` copy can expose ignored files/reflogs. |
| Provider failure or malicious response | Strict schemas, atomic verification, verified Judge projection, bounded retries/repairs, safe diagnostics and compatible signed recovery. Code retains financial authority. |

## Findings and fixes

No CRITICAL active deployment exploit was confirmed within the audit scope. Severity describes the project finding, rather than blindly copying a dependency advisory's CVSS.

| ID | Severity | Evidence before correction | Disposition |
| --- | --- | --- | --- |
| S01 | HIGH, local exposure | `backend/.live-validation/credential_check.py:24` still holds the historical literal. The old commit is retained by local reflogs. | Not modified: credential deletion/reflog pruning requires separate approval. Excluded from Git/build. Do not distribute this working directory or `.git`. |
| S02 | HIGH if used for private/public service | Intake GET/message responses return both histories; no tenant/party identity exists. Backend itself relies on private networking and Caddy authentication. | Accepted only for shared synthetic demo. Not a claim of private user authorization; direct public backend access is prohibited. |
| S03 | MEDIUM | Bodies were parsed before the service's 4,000-character message check; Caddy had no body ceiling. | 32 KiB limits in ASGI and gateway, including chunked/no-length bodies; intake field bounds; gateway read timeouts. |
| S04 | MEDIUM | Default FastAPI validation included request `input`/`ctx`; UI ErrorBoundary printed raw errors/stacks to console and page; configuration errors echoed malformed environment values. | Redacted validation locations/messages; generic UI crash output; environment parsing errors no longer echo arbitrary string values. |
| S05 | MEDIUM | Concurrent intake requests could pass the same lifecycle/turn count before either provider call completed. Standalone advocates could overlap a full run. | Reused existing OS-backed SQLite request locks. Intake send/skip share a lock; standalone advocates share the full-resolution case lock; safe 409/503 responses. Signed checkpoint format unchanged. |
| S06 | LOW in current API, meaningful local risk | Replay storage joined arbitrary case IDs directly; API known-case lookup already prevented HTTP exploitation. | Reject traversal/absolute/invalid IDs and stages, enforce resolved-root containment, reject artifacts over 2 MiB. |
| S07 | MEDIUM | Credential persistence check only matched string prefixes, allowing credentials embedded in otherwise ordinary output text. | Credential patterns detected anywhere in output strings; replay/checkpoint persistence refuses them. Matching interview credentials rejected before saving/sending. Pattern detection is not comprehensive PII/secrets detection. |
| S08 | LOW, dormant shipped code | Legacy Hunyuan/OpenAI clients could expose API keys through dataclass reprs and upstream bodies through error messages. They are not used by current intake/native provider. | API-key repr hidden; network/body errors redacted; synthetic regression tests. No provider/model changes. |
| S09 | LOW hardening | Missing CSP/no-store; production FastAPI schema routes remained enabled; new storage relied on default umask. | Production docs/OpenAPI disabled, CSP/Permissions-Policy/no-store headers, private POSIX umask for new files, owner-only new case directories. Existing files untouched. |
| S10 | LOW effective demo exposure; HIGH package advisory | `source-map-js` 1.2.1 is a Vite/PostCSS development dependency with a source-map DoS advisory. | Lockfile and actual installed package updated to 1.2.2; no other package version changes. Fresh tests/build and npm audit pass. |

The CSP permits the existing Google Fonts stylesheet/font hosts. Fonts can disclose ordinary connection metadata to Google; interview contents are not placed in those requests. Referrer policy remains no-referrer. No inline error styling or HTML injection is needed.

## Secrets and Git history

Read-only `git ls-remote origin` returned only HEAD, main and feature/intake-party-statements. Remote SHAs match local remote-tracking refs; no tags or pull refs were advertised. Branch/ref-reachable history contains 19 commits. All local refs, including Codex capture refs, were covered.

- Initial broad local scan included 18,364 files, including browser caches. Browser/extension Google-key patterns were found only in ignored local caches; ownership/validity was not checked.
- Final combined ref/reflog scan examined 2,576 unique blobs. Ref-reachable blobs produced only intentional synthetic URL-password test literals, not confirmed credentials. Reflog-only blobs include the historical credential probe and old browser caches.
- `72c4515` is **not reachable from current branch/tag/Codex refs**, but **is retained in local reflogs**. Its probe literal was privately extracted and compared exactly, independent of vendor-key regex format. It is also still present in the ignored current local probe. The configured current backend credential differs.
- Final source/build scan: 3,375 tracked, proposed or compiled files; zero vendor-key/configured-key matches, zero high-entropy candidates under secret-related labels, zero production `.map` files.
- Expected sensitive local files: `backend/.env:39`, ignored credential probe, browser profiles, checkpoint integrity keys, local Caddy test password hashes and TLS test keys. Do not upload these as diagnostics.
- Synthetic URL-password fixtures at `backend/tests/test_deployment.py:33` and `frontend/test/apiConfig.test.ts:13` are intentional invalid-input tests. They are not operational credentials.

No standard Gitleaks executable was available on PATH. Redacted Python scans covered Google/Tencent/GitHub/OpenAI/AWS patterns, private keys, password-bearing URLs/hashes, exact currently configured and historical literals, and final high-entropy labelled literals. Scans emitted paths/lines/categories only. Node modules, virtual environments and tooling caches were excluded; seven final local paths were skipped due size/access restrictions. Initial browser scanning also excluded large binary/cache objects. These limits prevent any assertion of absolute absence.

No history was rewritten, refs changed, credentials rotated, backups deleted or Git garbage collection performed. Normal main pushes do not publish reflog-only objects. Safest submission packaging is a clean tracked-source export/fresh clone plus separately provisioned server secrets; never a ZIP of this working directory. Removing the obsolete local literal and expiring/pruning old recovery objects is a separate destructive/credential cleanup requiring approval and a preservation plan.

## API inventory and privacy flow

Reviewed endpoints: GET cases/list, case detail, deterministic analysis and health; POST advocates/full resolution; GET intake trips/by-source/by-ID; POST intake create/by-source/messages/skip-rebuttal/analyse. Replay is an opt-in mode on full resolution, doubly gated by server configuration; capture remains CLI-only. No HTTP file/checkpoint/database read or credential management route exists.

SQLite uses parameterized queries. Paths come from server configuration, not requests. Known source-case/trip association is checked before attaching intake. Fact IDs, statedBy/sourceMessageId, verification status and correction history are server-owned. Superseded allegations are excluded from active projections. New body models forbid forged extra fields. Wrong lifecycle/party messages are rejected before calls. Cases cannot introduce trusted evidence or policy rules through JSON.

Precisely what goes to the native provider:

1. **Intake:** system interview instructions, current party/stage/coarse topic, up to the last 20 messages for that party and up to 20 active prior allegations. Actual current-party interview text is transmitted. Opposing private interviews are not included, including optional rider clarification.
2. **Advocates:** case/dispute identifiers, deterministic facts, permitted timeline/evidence and policy evaluation, plus attributed extracted intake allegations. Opposing allegations are withheld during restricted/partial intake; completed comparison can include both parties. They remain ALLEGED, even when compared with trusted evidence.
3. **Rebuttals:** deterministic facts/evidence/policy and code-projected own/opposing accepted claims, not raw private intake transcripts.
4. **Judge:** allowlisted facts/evidence/policy, resolution mode, and verified claim/rebuttal projections. Rejected claims, raw interview context, final refund/ruling and deterministic recommendation are excluded.
5. **Storage/UI:** raw interviews persist in private SQLite. Checkpoints/replays store outputs/execution metadata, not full request prompts or credentials; model paraphrases can still contain synthetic party information. React renders strings as text. Browser local/session storage is not used for interviews or credentials.

Provider-context separation is verified offline. It does **not** establish Rider/Driver access privacy in the shared operator API: an authenticated viewer can request both histories. Real private data requires separately designed identities, authorization and retention controls, outside this demo scope.

## AI, recovery and replay checks

Synthetic spoofed SYSTEM instructions, invented evidence and refund demands remain allegations. They cannot add trusted evidence, disable strict claim checks, reach the Judge as raw intake instructions, change refunds or bypass human review. Tests prove these code boundaries with controlled outputs; no claim is made that Gemini will never produce an injected response.

Checkpoint HMAC/context/provider/model/schema/prompt identity, atomic writes and revalidation remain intact. The same request-lock file/layout protects full and standalone runs; intake locks use a separate hashed namespace. Successful compatible roles remain reusable. HMAC does not protect against an attacker who owns the storage and can also read its signing key.

Replay checks case/stage/hash/schema/verification compatibility and refuses incompatible input without silently falling back to paid calls. LIVE, REPLAYED LIVE and MOCK DEMO labels remain distinct. Storage root containment and size checks supplement the existing case identity checks. Completed data/checkpoints/artifacts were not deleted or overwritten.

## Deployment and native HTTPS regression

Compose publishes only 80/443; API port 8000 has no host mapping. Backend runs UID/GID 10001 with no-new-privileges and private persistent data. Caddy uses its image's default root user to bind privileged ports; it has no-new-privileges but not a non-root user/capability-drop profile. This is a residual hardening item requiring actual image/volume verification, not a claim that both containers run non-root.

Docker context is deny-by-default and permits app/build inputs only; it excludes `.git`, environment files, databases, checkpoints, replay artifacts, browser profiles and logs. Secrets are not Docker arguments or frontend VITE variables. Production local dotenv loading is disabled; CORS is empty by default and exact HTTPS origins only if configured. Debug/access-body logging is not enabled. Caddy admin is off, Basic auth applies to UI/API, and Authorization is stripped before proxying. TLS/ACME depends on a valid deployment domain and connectivity.

The repeatable `backend/tests/security_gateway_smoke.py --caddy PATH` uses temporary loopback ports, synthetic credentials/certificate and a synthetic HTTP upstream. It validates the current production Caddyfile with only local domain/certificate/static/upstream substitutions. Final results:

| Check | Result |
| --- | --- |
| Unauthenticated UI/API/paid-action route | 401 |
| Authenticated static UI/API | 200 |
| Foreign-origin POST | 403, upstream not invoked |
| Same-origin POST | 200 |
| Oversized body | 413 |
| Gateway Authorization at upstream | Absent |
| CSP, no-store and other security headers | Present |
| Request for private database path | Frontend HTML fallback, not database bytes |
| Provider calls | Zero |

No root CA was installed and no public listener was created. The smoke validates native Caddy and proxy policy; it does not validate actual Linux images, public ACME, firewall rules or real deployment credentials. Docker was unavailable on PATH. Production storage/backup ACLs, encrypted access-controlled backups, disk/quota monitoring and image/OS vulnerability scanning are deployment obligations.

## Dependency advisories

Queried current OSV records for 30 pinned runtime packages and eight installed development packages; duplicate GHSA/PYSEC aliases below are counted once. Frontend npm audit covers its lockfile. The legacy root Node project has no third-party dependencies and is excluded from active images.

| Package/version | Advisory/severity | Used vulnerable feature? | Patched version / decision |
| --- | --- | --- | --- |
| source-map-js 1.2.1 | GHSA-68fv-2mgg-jv7q / HIGH | Build-time source maps; no untrusted upload path or deployed Node build server | Updated to 1.2.2; actual installed bytes/version verified |
| Starlette 0.48.0 | GHSA-7f5h-v6xp-fcq8 / HIGH | No Starlette FileResponse; Caddy serves static assets | 0.49.1; deferred incompatible framework constraint |
| Starlette 0.48.0 | GHSA-82w8-qh3p-5jfq / HIGH | No request.form(), multipart/form endpoints; body ceiling added | 1.3.1; major migration deferred |
| Starlette 0.48.0 | GHSA-86qp-5c8j-p5mr / MODERATE | No security decisions based on reconstructed request.url; malformed Host rejected before FastAPI | 1.0.1; major migration deferred |
| Starlette 0.48.0 | GHSA-jp82-jpqv-5vv3 / LOW | No authorization/redirect logic using request.url.hostname | 1.3.0; major migration deferred |
| Starlette 0.48.0 | GHSA-wqp7-x3pw-xc5r / HIGH | No Starlette StaticFiles; production Linux, not Windows static serving | 1.1.0; major migration deferred |
| Starlette 0.48.0 | GHSA-x746-7m8f-x49c / MODERATE | No HTTPEndpoint attribute dispatch | 1.1.0; major migration deferred |
| pytest 8.4.2 | GHSA-6w46-j5rx-g56g / MODERATE | UNIX shared /tmp tmpdir issue; this audit uses Windows and explicit private workspace basetemp; pytest not in runtime image | 9.0.3; major test-tool migration deferred |

Installed FastAPI 0.118.0 explicitly requires `starlette>=0.40.0,<0.49.0`. Overriding that constraint or moving to Starlette 1.x without a compatible FastAPI regression is not a safe patch. No such upgrade or shared WorkBuddy Python environment mutation was performed. These are open package advisories, not zero-findings Python results. Primary advisories: [source-map-js](https://github.com/advisories/GHSA-68fv-2mgg-jv7q), [Starlette Host](https://github.com/Kludex/starlette/security/advisories/GHSA-86qp-5c8j-p5mr), [Starlette form](https://github.com/Kludex/starlette/security/advisories/GHSA-82w8-qh3p-5jfq), [Starlette Range](https://github.com/Kludex/starlette/security/advisories/GHSA-7f5h-v6xp-fcq8), [Starlette path](https://github.com/Kludex/starlette/security/advisories/GHSA-jp82-jpqv-5vv3), [Starlette StaticFiles](https://github.com/Kludex/starlette/security/advisories/GHSA-wqp7-x3pw-xc5r), [Starlette HTTPEndpoint](https://github.com/Kludex/starlette/security/advisories/GHSA-x746-7m8f-x49c), [pytest](https://github.com/advisories/GHSA-6w46-j5rx-g56g).

## Final regression results

- Backend: **922 passed, zero failures/skips**, 34.13 seconds; baseline 895 plus 27 targeted security cases. Existing Starlette/AnyIO deprecation warning remains.
- Frontend: **26 passed, zero failures/skips**; baseline 24 plus XSS and crash-redaction tests.
- TypeScript and Vite production build passed against actual source-map-js 1.2.2. npm audit: zero findings.
- HTTPS gateway smoke: passed after final Caddy changes.
- All eight fixture/service/HTTP workflows passed with unchanged outcomes: DISP-002/004 charge upheld (0); DISP-003/006/009 human review (0); DISP-005/008 no refund (0); DISP-007 fare-difference refund (8.50). Human-review Judge status remains intentional PENDING_HUMAN_REVIEW.
- Existing privacy, wrong-case intake, unsupported evidence, atomic proposition, tampered checkpoint, incompatible replay, recovery and duplicate full-run tests all passed as part of the complete suite.
- Final source/build and ref/reflog scans completed with redacted classifications above; Git diff whitespace/staging review passed. Nothing was staged, committed or pushed.

Windows prevented an npm clean refresh from deleting an in-use esbuild binary. Generated dependencies were restored via npm install without stopping services; package version, tests and build were rechecked. A locked generated cleanup directory remains ignored. Frontend 5173 and backend 8000 both returned 200 after restoration; no live analysis/interview action was sent.

## Required conditions before submission/deployment

1. Use synthetic fixtures only. Treat the gateway account as shared operator access, never private Rider/Driver identity.
2. Build/export tracked source only; exclude ignored files, `.git`, local database/checkpoint/replay contents and all credential/TLS diagnostics. Provision demo artifacts separately from approved synthetic captures.
3. Keep 8000 private, authenticate UI/API over HTTPS, retain exact origin/body controls, and distribute a strong demo password only to trusted viewers. Prefer approved mock/replay demonstration when live provider availability/cost is unnecessary.
4. Rebuild actual images from reviewed source and verify their OS/dependency scan, TLS/domain, security-group/firewall and persistent-data/backup access controls before any public deployment. No public deployment was performed here.
5. Shared-auth viewers can still make repeated sequential requests/create intakes. Per-request retries, concurrent locks and party-turn limits are bounded; there is no global spending cap, account-level quota or unlimited-service retention control. Use trusted viewers, a monitored provider budget, limited demo duration and disk monitoring. External provider/billing settings require separate authorization.
6. Obtain separate approval if removing the historical credential probe/reflog objects or upgrading FastAPI/Starlette/pytest. The former changes sensitive recovery material; the latter needs a compatible isolated environment and regression, not a forced dependency override.

No live Gemini calls, browser automation, attacks on public services, credential changes, database reset, checkpoint deletion, Git rewriting, commits, pushes or deployment occurred. Local HTTP and HTTPS tests used only synthetic fixtures/ephemeral resources. This audit reduces identified risks; it does not guarantee complete security.
