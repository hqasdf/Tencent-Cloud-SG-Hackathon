# RydeResolve — full-project validation report

**Run date:** 2026-10-04 / 2026-10-05 (single paced session)
**Scope:** Stages 1–6C, per the "fresh, slow, full validation" brief.
**Provider under test:** `gemini-3.8-flash` via `openai_compatible`.
**Status:** offline stages complete and green. Live stages **held pending key rotation**
(see §6) — not failed, not blocked by the provider.

---

## 1. Verdict table

| Stage | What it covers | Mode | Verdict |
|---|---|---|---|
| **1** | Frontend build + workflow shell + real-browser walkthrough | offline | **PASS** |
| **2** | Backend API contract | offline | **PASS** |
| **3** | Deterministic engine | offline | **PASS** |
| — | Regression suite | offline, egress-guarded | **PASS** (79 known failures, unchanged) |
| — | Credential / rotation safety | offline | **CONFIRMED NOT ROTATED** — user elected to rotate before live |
| **Gate** | Gemini stability gate (2 probes) | **live** | **NOT YET RUN THIS SESSION** (held for rotation) |
| **4** | Advocates (Rider, Driver) | live | **HELD** — behind gate |
| **5** | Judge | live | **HELD** — behind gate |
| **6** | Rebuttals | live | **HELD** — behind gate |
| **6B** | Replay machinery | offline | **PASS** |
| **6B-live** | Replay from a *real* capture | live | **HELD** — behind gate |
| **6C** | Final frontend workflow (+ replay badge) | offline + browser | **PASS** |

Stages 4, 5 and 6 are **HELD**, not failed and not passed. No AI stage ran against the
provider in this session, so nothing in this report should be read as evidence about
model output quality.

---

## 2. Stage 1 — Frontend (offline) — PASS

`npm run build` (`tsc -b && vite build`):

```
✓ 43 modules transformed.
dist/index.html                   0.55 kB │ gzip:  0.34 kB
dist/assets/index-1eXRfefJ.css   37.79 kB │ gzip:  7.23 kB
dist/assets/index-o_M_5hl_.js   269.45 kB │ gzip: 81.21 kB
✓ built in 6.49s
```

- Dev server on `127.0.0.1:5173` (PID 12264), serving **fresh** transformed source
  (verified by fetching `/src/components/CaseDetail.tsx` through Vite). No zombie on
  5174 — the documented stale-serving hazard was checked explicitly.
- Eight numbered workflow steps present in order.

### Render harness

`frontend/node_modules/.tmp-verify/workflow.tsx` renders the real `CaseDetail` and its
panels against live API payloads and asserts on the resulting markup.

- **36 checks × 3 cases** (DISP-002 no-show, DISP-005 route, DISP-003 human-review) —
  **108/108 passed**.

## 3. Stage 2 — Backend / API (offline) — PASS

| Route | Status |
|---|---|
| `GET /api/health` | 200 |
| `GET /api/cases` | 200 |
| `GET /api/cases/DISP-005` | 200 |
| `GET /api/cases/{DISP-005,DISP-002,DISP-003}/analysis` | 200 |

`/api/health` reports:
`{"mode":"real","provider":"openai_compatible","model":"gemini-3.8-flash","configured":true,"credential_configured":true}`

8 DISP cases load (002–009). `OPTIONS /api/cases/{id}/advocates/run` returns **405**
(route exists — a missing route would be 404), and both `/advocates/run` and
`/resolution/run` are registered in `app/api/cases.py`.

## 4. Stage 3 — Deterministic engine (offline) — PASS

All eight services construct and are callable: `EvidenceValidationService`,
`RouteDeviationService`, `NoShowAnalysisService`, `PolicyEvaluationService`,
`ResolutionEngine`, `ConfidenceEngine`, `EscalationEngine`, `DisputeAnalysisService`.

Two properties were proven rather than assumed:

1. **Composition is faithful.** For all 8 cases, the facts and policy evaluation
   produced by `DisputeAnalysisService` are byte-identical to those produced by the
   individual services called directly. A mis-wired argument (wrong policy, wrong
   currency, swapped service) would answer the wrong question while still "working";
   this rules that out.
2. **The result is a pure function of the fixture.** Every case hashes identically
   across two in-process runs.

The whole stage ran under a **socket-level egress guard** (`egress_guard.install()`),
which was first proven **non-vacuous**: it blocks non-loopback hosts and raw socket
connects while allowing loopback. So "zero Gemini calls" in Stage 3 is demonstrated,
not asserted.

### Deterministic baselines

| case | type | mode | action | refund | conf | hash (first 12) |
|---|---|---|---|---|---|---|
| DISP-002 | no_show_charge | AUTO_RESOLVE | UPHOLD_CANCELLATION_CHARGE | 0.0 | 0.95 | `1e4b88382660` |
| DISP-003 | route_deviation | **HUMAN_REVIEW** | HUMAN_REVIEW | 0.0 | 0.29 | `7fa746d5a531` |
| DISP-004 | no_show_charge | AUTO_RESOLVE | UPHOLD_CANCELLATION_CHARGE | 0.0 | 0.95 | `66569c8f97da` |
| DISP-005 | route_deviation | AUTO_RESOLVE | NO_REFUND | 0.0 | 0.95 | `33899cc47872` |
| DISP-006 | no_show_charge | HUMAN_REVIEW | HUMAN_REVIEW | 0.0 | 0.29 | `b5fb03caf573` |
| DISP-007 | route_deviation | AUTO_RESOLVE | FULL_FARE_DIFFERENCE_REFUND | 8.5 | 0.95 | `d0f555e9d88c` |
| DISP-008 | route_deviation | AUTO_RESOLVE | NO_REFUND | 0.0 | 0.95 | `73bfd06cd08c` |
| DISP-009 | no_show_charge | HUMAN_REVIEW | HUMAN_REVIEW | 0.0 | 0.29 | `7f49baf180e9` |

DISP-005's full hash is
`33899cc478724950b44333590819eaa69245e58ba9572783a9511464dc39e407` — **byte-identical to
the value recorded in earlier sessions**, so determinism holds across sessions and not
merely within one process. (This is also the value embedded in the Stage 6B artefacts,
which is why the replay hash check passes.)

### Semantic case selection (derived, not assumed)

The brief's three cases are usable only if the current engine still classifies them as
required. It does:

| slot | case | observed |
|---|---|---|
| ROUTE_DEVIATION | DISP-005 | `route_deviation` / AUTO_RESOLVE |
| NO_SHOW | DISP-002 | `no_show_charge` / AUTO_RESOLVE |
| HUMAN_REVIEW | DISP-003 | `route_deviation` / **HUMAN_REVIEW** / conf 0.29 |

Selection is **not unique**, and that is recorded rather than hidden:

- ROUTE_DEVIATION candidates: `DISP-003, DISP-005, DISP-007, DISP-008`
- NO_SHOW candidates: `DISP-002, DISP-004, DISP-006, DISP-009`
- HUMAN_REVIEW candidates: `DISP-003, DISP-006, DISP-009`

The harness picks the lowest id deterministically and records the tie.

## 5. Regression (offline, egress-guarded) — PASS

```
79 failed, 441 passed, 1 warning
```

- The 79 failures are exactly the known **Bucket B** set (`StopIteration` on
  `CASE-2026-1041`, a fixture id replaced by `DISP-*` in an earlier stage). The count
  is unchanged and none were touched.
- Passing count rose from the Stage 6 baseline of **367 → 441** (+74, the Stage 6B
  replay tests).
- The suite ran under the socket-level egress guard, proven non-vacuous. **Zero
  outbound calls were attempted** — a blocked call would have errored the suite.

## 6. Credential / rotation safety — NOT ROTATED (live stages held)

- Key present in `backend/.env`; hygiene clean (no whitespace, quotes or newline).
- Fingerprint `sha256[:12] = 7f7932b484a2` — **identical to the value exposed on
  2026-09-30**. **The value itself was never printed.**
- `backend/.env` mtime is `2026-09-27 21:48`, which *predates* the exposure event, and
  the file is unchanged. So the key still in use is the exposed one.
- `rotatedSinceExposure: false`.

**Decision (this session):** the user elected to **rotate the key first** rather than
spend quota on the exposed credential. The live stages are therefore *held*, not
blocked — the harness, ledger and gate are ready and waiting.

### To resume live validation

1. Rotate the key in Google AI Studio.
2. Put the new value in `backend/.env` as `AGENT_API_KEY`, with
   `AGENT_PROVIDER=gemini_native` and the intended `AGENT_MODEL`. This supersedes
   the separate intake credential used when this historical report was written.
3. **Restart the backend on port 8000** — the running process holds the old value in
   memory and will not pick up the new one.
4. Tell me it is done; I will re-run the credential check (expect
   `rotatedSinceExposure: true`), then the two-probe gate, then Stages 4–6.

## 7. Gemini stability gate — previously CLOSED; not yet re-run this session

The gate was last executed on 2026-10-04 with two minimal probes (`"Reply with OK"`, no
`response_format`, no schema, no retries), 60 s apart, each counted against the budget.

| probe | status | latency | valid content | Retry-After | error category | Server |
|---|---|---|---|---|---|---|
| 1 | **503** | 1787 ms | no | absent | `UNAVAILABLE` | `scaffolding on HTTPServer2` |
| 2 | **503** | 1250 ms | no | absent | `UNAVAILABLE` | `scaffolding on HTTPServer2` |

The gate requires **200 + 200**. It did not open, so Stages 4–6 were not run.

### Provenance of the 503 — checked, and my first guess was wrong

The `Server: scaffolding on HTTPServer2` header initially looked like a sandbox
interception artefact, because this environment sets
`http_proxy`/`https_proxy=http://127.0.0.1:59624` and `httpx` honours env proxies by
default. That hypothesis was **tested and refuted**:

- `CONNECT` through the local proxy to `generativelanguage.googleapis.com:443` returns
  **`200 Connection Established`** — the tunnel works.
- A deliberately malformed request returns a well-formed Google **400**, carrying
  Google's own headers: `Vary: X-Origin`, `Vary: Referer`, `Alt-Svc: h3=":443"`,
  `Server-Timing: gfet4t7`.
- Direct and proxied calls return **byte-identical** headers.

**Conclusion: `scaffolding on HTTPServer2` is Google's own server banner.** The proxy
is a transparent tunnel, the provider is genuinely reachable, and the 503s were **real
Google responses**, not local artefacts. This is consistent with the previously
established root cause (`docs/gemini-failure-root-cause.md`): the failures are
**provider-side** and flapping — a 126-byte minimal request fails identically to the
17.4 KB production request.

**A 503 is a provider failure, never a hallucination and never a model-output failure.**

## 8. Stage 6B — Replay (offline) — PASS

Capture used the **mock** provider into an isolated artefact root, so
`backend/replay_artifacts/` was never touched. Every artefact it writes records
`provider: "mock"`, so nothing captured this way can be mistaken for live output.

| property | result |
|---|---|
| Three artefacts written (ADVOCATES, REBUTTALS, JUDGE) | yes |
| Replay of `FULL_AI` made **zero** provider calls | **yes** — proven with a tripwire provider that raises on `complete` |
| Replay reproduced the captured advocate claims | yes |
| Deterministic half recomputed live, not replayed | yes |
| Tampered analysis hash → `REPLAY_INVALID` / `ANALYSIS_HASH_MISMATCH` | yes, **refused** |
| …and did **not** fall back to a live call | yes (0 calls after refusal) |
| No stored verdict flag in any artefact | yes |
| Store refuses forbidden keys / credential-shaped values | yes |

### API replay, end-to-end

Against a mock-mode server with `REPLAY_API_ENABLED=1`:

```
HTTP 200
  replayMetadata   : mode=FULL_AI  replayed=true
                     advocatesReplayed=true rebuttalsReplayed=true judgeReplayed=true
                     valid=true  reasonCodes=[]
  currentAnalysisHash == artifactAnalysisHash (33899cc4…e407)
  artifactVersion  : "1"
  capturedProvider : "mock" | capturedModel: null
  resolutionMode   : AUTO_RESOLVE   (recomputed, not replayed)
```

With the flag off (real-mode server), the endpoint returns **403** and names the
variable — the refusal happens *before* any provider call:

> `Replay mode FULL_AI is a developer facility and is disabled on this server. Set REPLAY_API_ENABLED=1 to enable it, or use `python -m app.replay.cli replay`.`

### Finding: capture must use the canonical case projection

The first API replay of the capture was **refused** with
`TRUSTED_CONTEXT_HASH_MISMATCH`. Cause: the harness had captured from
`repository.get_case()` directly, bypassing `CaseService._validated_case()`, which
canonicalises the timeline against the evidence. The API uses the canonical
projection, so the trusted-context hash differed.

This is the system **failing closed** and working as designed. It is recorded because
it is easy to hit: an artefact is only valid for the canonical case projection, and a
capture made outside `CaseService` will be refused rather than silently used. Fixed in
the harness by capturing through `CaseService`.

### Stage 6B-live is HELD

The brief requires a replay whose `capturedModel = gemini-3.8-flash`. No real capture
exists, because no live stage has ever completed. This stays **HELD** behind the gate.

## 9. Stage 6C — Final frontend workflow — PASS

### Render harness

- **36 checks × 3 cases** (DISP-002, DISP-005, DISP-003) — **108/108 passed**.
- **+6 replay checks** on DISP-005 against a replay-enabled server — **42/42 passed**.

It POSTs to `/resolution/run`, so it was pointed at a **mock-mode** server via a
`CHECK_API` override; running it against the real-mode server would have made a live
model call, which a render check must never do.

What the checks establish:

- Eight steps render; the rail shows eight chips; step 1 is open before any run.
- **One primary action** — "Run full analysis" appears exactly once.
- Step state transitions correctly (`done/ready/waiting`, and the Judge step marked
  **Advisory**).
- Step 7 renders the authoritative outcome and the refund the code calculated; step 6
  renders an advisory recommendation that explicitly "cannot move any value here".
- A mock run is labelled `MOCK` and never claims a `REAL MODEL`.
- **A live run never shows the replay badge; a replayed run is labelled `REPLAYED` and
  names the provider that produced the stored output.**
- No developer vocabulary leaks (`Stage 1`…`Stage 6`, "panel above", "this milestone").
- The old duplicates are gone (no second decision panel, no fixture confidence
  breakdown, no static activity pipeline, no raw escalation-code brief).

### Real-browser walkthrough

A second Vite instance was started on `127.0.0.1:5175` with
`VITE_API_BASE_URL` pointed at the **mock+replay** server, because the default build
targets `localhost:8000` (real mode) and clicking "Run full analysis" there would have
made a live call.

Verified in a real Chromium session:

| check | result |
|---|---|
| Dashboard renders; 8 DISP cases listed and selectable | yes |
| Eight-step workflow renders with correct headings and order | yes |
| Steps 1–3 `DONE`, step 4 `START HERE`, steps 5–8 `NOT RUN` before a run | yes |
| "Run full analysis" count | **exactly 1** |
| After a run: all 8 `done`, step 6 pill = `ADVISORY` | yes |
| Step 6 copy: "A recommendation only. It cannot change the outcome." | yes |
| Step 7 copy: "What code will actually do — the refund, and whether a human is needed." | yes |
| Mock run shows `MOCK`, never `REAL MODEL`, never `REPLAYED` | yes |
| Replayed run shows `REPLAYED · advocates + rebuttals + judge`; `MOCK` suppressed; never `REAL MODEL` | yes |
| Developer vocabulary leaks | **none** |
| Console warnings/errors | **none** |
| Page errors | **none** |

Because the UI has no control that requests replay mode (replay is a developer
facility), the badge was exercised by an init-script that rewrites the UI's own run
request to ask for `FULL_AI`; the server was the mock+replay instance, so this cost
nothing. This is the one presentation error the feature must make impossible — showing
stored AI output as if the model had just produced it — and the badge held.

### Rejected material is structurally excluded from the Judge

Grepping the page text finds `CLAIMS REJECTED BY CODE` exactly once, in **step 4** (the
operator-facing arguments view). The guarantee that matters is at the code level, and
it is structural rather than a filter:

- `JudgeContextBuilder._side()` reads only `result.verified_claims`.
- `JudgeContextBuilder._rebuttals()` reads only `result.verified_rebuttals`.
- `rejected_claims` / `rejected_rebuttals` appear in the context builders **only in
  comments saying they are not read**.

There is no code path by which rejected material could reach the Judge prompt — it is
*unrepresentable*, not merely suppressed.

## 10. Incidental finding — a hazard I created, recorded honestly

While testing the replay gate I issued a second POST to the **real-mode** server on
8000 with a 2 s client timeout, expecting the refusal path. It was a live pipeline
request (`{}` body, `replayMode` absent), not a replay.

- The server **did** dispatch it. Proven by control: an identical `-m 2` request to a
  nonexistent case returned 404 and *was* logged, so the timeout does not prevent
  dispatch.
- The request then ran longer than 2 s, curl disconnected, and uvicorn emitted **no
  access-log line** for it.
- The server's `AGENT_MAX_RETRIES=2` allows up to 3 attempts per logical call.

**So an uncounted number of live attempts — on the order of 2–6, all expected 503 —
was spent outside the validation ledger, and the result was discarded.** The exact
count is unknowable.

The structural lesson is the important part: **the harness's budget guard covers only
calls made through its guarded client. The API server has its own provider and is not
counted.** Any HTTP-driven run is therefore invisible to the ledger.

## 11. Counts (this session)

| metric | value |
|---|---|
| External HTTP attempts recorded in the ledger | **0** (fresh ledger; live stages held) |
| Budget ceiling / remaining | 40 / 40 |
| Live pipeline attempts from the harness | **0** |
| 429 responses | 0 |
| 5xx responses | 0 |
| AI stages completed live | 0 |
| Deterministic baselines captured | 8 |
| Frontend render checks | 36 × 3 cases + 6 replay = **114** |
| Replay provider calls | **0** |

*From the 2026-10-04 run, for reference:* 2 ledger probes (both 503), plus an uncounted
~2–6 API-driven attempts (§10).

## 12. Outstanding

1. **Rotate the exposed API key**, then restart the backend on 8000 and resume (§6).
   This is the only thing holding the live stages.
2. **Re-run the stability gate** after rotation, then Stages 4–6, then Stage 6B-live.
   The harness, ledger and gate are ready.
3. **Count API-driven calls.** The ledger cannot see them (§10). Until that is closed,
   no validation session can claim a complete attempt count.
4. **Bucket B** — 79 stale tests, unapproved and untouched.
5. A stale artefact, `replay_artifacts/DISP-005/rebuttals.json`
   (`createdAt 2026-10-01T13:51:20Z`, `provider: ""`, `model: null`), remains on disk.
   It is a faithful record of a run in which both rebuttal sides failed — `""` is the
   documented encoding — but it is **not** this run's output and must never be
   presented as live.
6. **Minor, pre-existing UI inconsistency (not a regression):** the dashboard sidebar
   shows each case's *fixture* status, which can disagree with the computed outcome —
   e.g. DISP-004 is listed "HUMAN REVIEW" while its deterministic analysis is
   `AUTO_RESOLVE` / `UPHOLD_CANCELLATION_CHARGE` at 0.95, and DISP-003 is listed
   "INVESTIGATING" while it computes `HUMAN_REVIEW` at 0.29. The case page itself is
   correct (it renders only computed values). Worth a look, but it is fixture data and
   predates this session.
