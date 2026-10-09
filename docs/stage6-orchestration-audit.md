# Orchestration audit — making the pipeline flow as one workflow

**Scope.** The Rider Advocate → Driver Advocate → cross-examination → Judge chain
was audited end to end, from the frontend button to the serialised response, and
fixed so it behaves as one dependency-driven workflow with a single source of
truth. This document is the report. It records what the code actually did, what
was wrong, what changed, and what was observed in a real browser.

**Out of scope, deliberately.** No CSS was touched before the orchestration was
fixed. The provider was **not** changed — §3 reports a mismatch rather than
resolving it.

---

## 1. The flow as it actually was

Traced, not assumed. The chain, with what each hop receives and returns:

| # | Hop | In | Out | Failure behaviour |
|---|-----|----|-----|-------------------|
| 1 | `CaseDetail.tsx` `.wf-run` `onClick` | user gesture | calls `onRunResolution` | none — pure event |
| 2 | `App.runResolution()` | case id | sets `resolutionRun` | `catch` cleared the previous result (see P4) |
| 3 | `caseService.runResolution(id)` | id | `POST /api/cases/{id}/resolution/run` | throws `CaseApiError` with status |
| 4 | `api/cases.run_case_resolution` | id, optional `replayMode` | `CaseResolutionResponse` | 403 if replay unset on server; 409 `REPLAY_INVALID` |
| 5 | `CaseService.get_resolution_run` | id | case + analysis, then orchestrator | `CaseNotFoundError` → 404 |
| 6 | `ResolutionOrchestratorService.run` | case, analysis | full response | — |
| 6a | ↳ `_advocate_context_builder.build` | case, analysis | one shared `AgentCaseContext` | — |
| 6b | ↳ `AdvocateOrchestratorService.run_with_raw` | context | `AdvocateRunResponse` + raw outputs | per-side `FAILED`; **does not raise** |
| 6c | ↳ `RebuttalOrchestratorService.run_with_raw` | context, advocate run | `RebuttalRunResponse` + raw | per-side `NOT_RUN` / `FAILED` |
| 6d | ↳ `_run_judge` | context, runs | `JudgeResult` | `NOT_RUN` when a side is missing; `FAILED` on provider/validation error |
| 6e | ↳ `_deterministic(analysis)` | analysis | `DeterministicResolution` | cannot fail — pure projection |
| 6f | ↳ `_explanations.build` | all of the above | `DecisionExplanation` | cannot fail |

**Key structural fact.** Every AI stage is *contained*: a provider failure
returns a result object with `status="FAILED"` rather than propagating an
exception. So an HTTP 200 always comes back, and **HTTP 200 tells you nothing
about whether any AI stage worked.** That is what made the frontend's inference
(§2, P1) possible in the first place.

## 2. What was wrong

**P1 — The frontend inferred status from the existence of an object.**
`stateFor()` returned `"done"` for every step from 4 onward whenever
`resolutionRun !== null`. A run in which both advocates failed therefore rendered
as **"8 of 8 steps complete"**. The UI claimed DONE while the backend state was
FAILED / BLOCKED / NOT_RUN — the exact prohibition in the brief.

**P2 — The backend had no word for "not attempted because a dependency failed".**
The vocabulary was `COMPLETE` / `FAILED` / `NOT_RUN`. `NOT_RUN` covered both
*never started* and *could not start*. A blocked stage and an untouched stage
rendered identically, so the UI could not tell the operator which it was looking
at, and neither could a reader of the API.

**P3 — No single source of truth for workflow status.** `pipeline` was a flat
list of ten stage rows: no side pairing (the two advocates were two unrelated
rows), no dependency semantics, and no statement that a later stage was blocked
by an earlier failure. The frontend had to reconstruct meaning from it, and
reconstructed it wrongly.

**P4 — A failed run destroyed the previous successful result.** `App.tsx`'s catch
blocks called `setResolutionRun(null)` and `setAdvocateRun(null)`, so a transient
provider outage erased a good analysis from the screen and left the operator with
nothing.

**P5 — A pill label overrode the state.** Step 6 was rendered with
`stateLabel={resolutionRun ? "Advisory" : undefined}`, which forced the pill to
read "Advisory" for *any* run — including one where the Judge was blocked. The
pill then contradicted the rail chip beside it and the body beneath it. Found in
the browser, not by reading; fixed (§12).

**P6 — A stale server hid all of the above.** The backend on port 8000 had been
started without `--reload` before the state machine existed. It served code that
did not serialise `workflow`, so the frontend fell back to the legacy path and
the first fix appeared not to work. Diagnosed by diffing `/openapi.json` against
the source: the served `CaseResolutionResponse` was missing `workflow` entirely.
Restarting with `--reload` fixed it.

## 3. Which provider the website actually uses

Verified by resolving the real `.env` through the real registry, not by reading
intent:

```
env source: backend/.env (loaded=True)
  mode             = 'real'
  provider         = 'openai_compatible'
  model            = 'gemini-3.8-flash'
  base_url         = https://generativelanguage.googleapis.com/v1beta/openai/
  timeout_seconds  = 30.0
  temperature      = 0.0
  max_tokens       = 1024
  max_retries      = 1
  reasoning_effort = 'low'
```

**All five roles resolve `OpenAiCompatibleProvider`** — `RIDER_ADVOCATE`,
`DRIVER_ADVOCATE`, `RIDER_REBUTTAL`, `DRIVER_REBUTTAL`, `JUDGE`. Each stage calls
`app/agents/providers/registry.py:get_provider` against the same `AgentSettings`,
so the class is identical by construction.

**Runtime ≠ intended, and this is reported rather than fixed** (the brief said not
to change the provider silently). `gemini_native` is implemented and reachable —
it exists because the OpenAI-compatible endpoint could not serve a structured
output request for this model — but it is **not the provider in use**. The
configured `AGENT_PROVIDER` is `openai_compatible`, so the native transport is
dead code in the current configuration.

## 4. The dependency graph after the change

```
DETERMINISTIC_ANALYSIS            (code, cannot fail)
        │
        ├──────────────┬─────────────────────┐
        ▼              ▼                     │
  RIDER_ADVOCATE  DRIVER_ADVOCATE           │   both run independently
        │              │                     │
        └──────┬───────┘                     │
               ▼                             │
        ┌──────────────┐                     │
        │  THE GATE    │  any side not settled OK  →  BLOCKED downstream
        └──────┬───────┘                     │
               ▼                             │
        ├──────────────┬─────────────┐       │
        ▼              ▼             │       │
  RIDER_REBUTTAL  DRIVER_REBUTTAL    │       │   both run independently
        │              │             │       │
        └──────┬───────┘             │       │
               ▼                     │       │
             JUDGE  ◄────────────────┼───────┘
               ▼                     │
        FINAL_RESOLUTION  ◄──────────┘   computed WITHOUT reference to any AI stage
               ▼
          EXPLANATION
```

Two asymmetries are deliberate and load-bearing:

* **An initial advocate failing blocks the rebuttals and the Judge.** Deciding a
  dispute with one side absent would misrepresent the record.
* **A rebuttal failing blocks nothing.** Rebuttals are supplementary; the Judge
  still runs, and a missing rebuttal is never fabricated.
* **Nothing blocks FINAL_RESOLUTION.** It is computed from the deterministic
  analysis alone.

## 5. The stage-state model

`StageStatus` — seven values, in `backend/app/models/workflow.py`:

| Status | Meaning |
|--------|---------|
| `NOT_STARTED` | no reason to have run yet |
| `RUNNING` | in flight |
| `COMPLETE` | attempted and settled successfully |
| `FAILED` | attempted and went wrong |
| `BLOCKED` | **not attempted, because a dependency failed.** Retrying it alone cannot help |
| `SKIPPED` | not attempted, and no dependency fault — a legitimate omission |
| `PENDING_HUMAN_REVIEW` | the stage ran; code decided a human must sign off |

`SETTLED_OK = {COMPLETE, PENDING_HUMAN_REVIEW}` and
`SETTLED_NOT_OK = {FAILED, BLOCKED, SKIPPED}`.

**`BLOCKED` is the addition that makes the UI honest.** It is the difference
between "this went wrong" and "this never happened because something upstream
went wrong" — two states that need different words, different colours, and
different retry advice. `BLOCKED` renders amber (`--rvde-warn`), `FAILED` red
(`--rvde-danger`); the rail marks them `–` and `!` respectively.

The object graph:

```
WorkflowState
├── deterministic      StageState
├── advocates          SideStages { rider, driver }
├── rebuttals          SideStages { rider, driver }
├── judge              StageState
├── finalResolution    StageState   (alias finalResolution)
└── explanation        StageState
```

Every state is produced by `build_workflow_state(...)` in
`backend/app/services/workflow_state.py`, from **actual execution** — the
advocate run, the rebuttal run, the judge result, and the gate's answer. Nothing
is inferred from the shape of the response.

## 6. Advocate success and failure rules

**`COMPLETE` means all four of these succeeded**, not one of them:

1. the provider call returned;
2. the output parsed as JSON;
3. it validated against the stage schema;
4. claim verification ran to completion.

**HTTP 200 does not mean `COMPLETE`.** A 200 with malformed JSON is a failure; a
200 whose every claim is rejected is *not* — the side still completes and the
rejections are recorded. Those are different events and are kept apart.

Each side runs independently, so rider-success + driver-failure is representable
and is what the gate keys on: **if either side is not settled OK, the rebuttals
and the Judge are `BLOCKED` with reason `ADVOCATE_INPUT_INCOMPLETE`.**

**Repair is bounded and selective.** `REPAIRABLE_OUTPUT_CODES =
{MALFORMED_JSON, SCHEMA_VALIDATION_FAILED}` with `MAX_REPAIR_ATTEMPTS = 1`.
`OUTPUT_TRUNCATED` and `WRONG_SIDE` are deliberately **not** repairable. A
trust-boundary rejection — a claim citing evidence that does not exist — is
**not** a parse failure: the side completes and the claim is recorded rejected.
The pipeline never "repairs" a hallucination into existence.

## 7. Cross-examination rules

* **Verified claims only.** The rebuttal context is built from
  `verified_claims`; rejected claims are not passed in and there is no parameter
  through which they could be.
* **Exactly one round, and a second round is unrepresentable.**
  `RebuttalCaseContext` has no field that could name a rebuttal as a target, so
  round 2 cannot be expressed — not merely disabled. `MAX_REBUTTAL_ROUNDS = 1`
  is a constant, not a config knob.
* **Targets are namespaced** (`DRIVER-D1`, not `D1`), so a target is unambiguous
  across sides.
* **Never sent to the rebuttal stage:** rejected claims, Judge output, the final
  resolution, the refund amount, confidence, or hidden history.
* **Rebuttal failure does not destroy the pipeline.** The Judge still runs. The
  successful side is never re-run, and a missing rebuttal is never fabricated.

## 8. The Judge's context, exactly

Built by `JudgeContextBuilder`, and built by **allow-list** — it never reads
`rejected_claims` or `rejected_rebuttals` at all. This is structural, not
filtered: there is nothing to forget to filter, and no "NOT TRUSTED" label that
could shape the model's reasoning while staying out of its citations.

**Included:** verified claims from both sides, verified rebuttals, the
deterministic facts, policy rule text, evidence identifiers.

**Excluded, and verified excluded offline:** rejected claims, rejected
rebuttals, raw unverified prose, the deterministic recommendation/ruling, the
refund amount, confidence, escalation reasons, and historical guilt.

An assertion that `"NO_REFUND" not in serialised_context` was **wrong and was
corrected** — `NO_REFUND` is a member of the allowed-outcome vocabulary the
schema legitimately offers the model, so its presence proves nothing. The real
invariant is that nothing reveals *which* outcome the code chose. The test now
asserts `ruling not in`, `refundAmount not in`, `confidence not in`,
`escalation not in`, `recommendedAction not in`.

## 9. Judge execution semantics and blocking

**`COMPLETE` requires all four:** the provider call returned, the JSON parsed,
the schema validated, and `JudgeOutputValidationService` accepted it.

**The Judge is `BLOCKED`** — not failed — when the gate reports a missing side.
The gate is evaluated **once**, in `run()`, and the same answer is handed to both
the Judge and the workflow object. Computing it twice would let the Judge block
while the UI reported the case as decidable, or the reverse.

**On Judge failure, FINAL_RESOLUTION is still `COMPLETE`.** Deterministic output
is never erased because an AI stage failed. This is the single most important
guarantee in the response contract, and it is asserted in
`test_scenario_f_judge_failure_preserves_everything_before_it`.

## 10. Deterministic-resolution preservation

`_deterministic(analysis)` takes the analysis and nothing else. It has no
parameter through which an AI result could reach it, so the refund, the
confidence, the escalation state and the resolution mode cannot be influenced by
the Judge — not because they are filtered, but because there is no path. The same
holds for `explanation` and `counterfactual`.

This is why the failure path in §16 can honestly tell the operator: *"The
decision and the explanation below are calculated by code and are unaffected."*

## 11. Checkpoint and resume behaviour

There is **no checkpoint layer inside the app**. The distinction matters and the
two layers are easy to conflate:

**Layer 1 — replay (in `app/`).** Substitutes the *source* of an AI stage's
output and nothing else. Only the model's own output is stored; never a refund,
a confidence score, an escalation reason or a final action. There is **no stored
trust flag** — verification is re-run against current code on every load, so
"verified" is never a stale assertion. Revalidation covers: replay format
version, stage schema version, case id, dispute type, deterministic-analysis
hash, and trusted-context hash. A refusal is `REPLAY_INVALID` and **never falls
back to a live call**.

*Provider and model are recorded and surfaced (`capturedProvider`,
`capturedModel`) but do not invalidate.* That is deliberate: the artefact is
model output that is re-verified against current code, so a provider change does
not make the stored output untrue — and the provenance is reported rather than
silently trusted.

**Layer 2 — resume-from-checkpoint (in the live-validation harness).** The
harness gates reuse through `provenance_problem`, which **does** check
`capturedMode`, `capturedProvider`, `capturedModel`, and `is_live_eligible`
(which additionally requires that both sides actually produced output, so a
capture where one side failed is not a checkpoint of a stage that succeeded).
Layer 1's hash and schema revalidation then applies underneath.

So the answer to "are checkpoints revalidated against mode / provider / model /
schema?" is: **mode, provider and model at the harness gate; schema and content
hashes at the replay validator.** Both layers are exercised by
`tests/test_live_resume.py` and `tests/test_replay.py`, which pass.

## 12. Files changed

**New**

| File | Purpose |
|------|---------|
| `backend/app/models/workflow.py` | the status vocabulary and `WorkflowState` |
| `backend/app/services/workflow_state.py` | `build_workflow_state(...)` — states from actual execution |
| `backend/tests/test_workflow_state.py` | 24 tests: scenarios A–H, handoffs, agreement |
| `frontend/src/types/workflow.ts` | the frontend mirror of the same vocabulary |

**Modified**

| File | Change |
|------|--------|
| `backend/app/models/judge.py` | `CaseResolutionResponse.workflow: WorkflowState \| None` |
| `backend/app/services/resolution_orchestrator.py` | gate computed once; `blocked_sides` passed to `_run_judge`; `workflow=` added to the response |
| `frontend/src/services/caseService.ts` | `workflow: WorkflowState \| null` on `ResolutionRunResult` |
| `frontend/src/components/WorkflowStep.tsx` | `failed` state; rail marks `✓` / `!` / `–` |
| `frontend/src/components/CaseDetail.tsx` | `stateFor` / `fromStage` / `fromPair` derive every step from `workflow`; done/failed/blocked counts; the amber notice; the P5 label fix |
| `frontend/src/App.tsx` | a failed run no longer clears the previous result (P4) |
| `frontend/src/styles.css` | blocked/failed tones; `.workflow-notice` |

`backend/app/models/judge.py` is the only response-model change: **one new
optional field.** Nothing existing was removed or renamed, so an old stored
response deserialises with `workflow = null` and the frontend falls back to
legacy behaviour rather than crashing.

## 13. Targeted tests

`backend/tests/test_workflow_state.py` — **24 tests, all passing**, run offline
against a scripted provider (no socket):

| Scenario | Tests |
|----------|-------|
| A — all success | `test_scenario_a_all_success`, `..._every_ai_role_used_the_same_provider` |
| B / C — advocate fails | `..._blocks_downstream[RIDER-DRIVER]`, `[DRIVER-RIDER]`, `..._no_rebuttal_or_judge_call_is_spent` |
| D — one rebuttal fails, Judge still runs | `..._one_rebuttal_fails_and_the_judge_still_runs`, `..._missing_rebuttal_is_never_fabricated` |
| E — both rebuttals fail | `..._both_rebuttals_fail_and_the_judge_still_runs` |
| F — Judge fails | `..._judge_failure_preserves_everything_before_it` |
| G — human review | `..._human_review_cannot_be_overridden_by_ai`, `..._judge_still_analyses_when_inputs_are_complete` |
| H — malformed output | `..._malformed_output_is_repaired_exactly_once`, `..._wrong_side_is_never_repaired`, `..._a_trust_boundary_rejection_is_not_a_parse_failure` |
| Handoffs | verification split, rebuttal context, Judge context exclusion, Judge validation, explanation |
| Cross-check | `test_workflow_state_agrees_with_the_flat_pipeline[4 variants]`, `test_a_blocked_stage_is_never_reported_as_complete_anywhere` |

The last two matter most: they pin the new state machine to the legacy flat
pipeline so the two cannot drift, and they assert that **no blocked stage is
reported as complete anywhere in the serialised response.**

## 14. Full regression result

```
79 failed, 651 passed, 1 warning in 29.52s
```

**No regression.** The 79 failures are the pre-existing "Bucket B", unchanged in
count and in file:

| File | Failures |
|------|----------|
| `test_advocate_orchestrator.py` | 20 |
| `test_claim_verification.py` | 19 |
| `test_advocate_agents.py` | 13 |
| `test_advocate_context.py` | 11 |
| `test_advocate_api.py` | 10 |
| `test_provider_mock.py` | 6 |

Every one is a `StopIteration` on the fixture id `CASE-2026-1041`, which was
replaced by `DISP-*` in an earlier stage. None of those files was touched. The
passing count rose from 627 to **651**, which is exactly the 24 new tests.

Every orchestrator-dependent suite is green:

```
tests/test_workflow_state.py tests/test_judge.py tests/test_rebuttal.py
tests/test_replay.py tests/test_live_resume.py tests/test_live_stop_rule.py
tests/test_api.py tests/test_replay_capture_safety.py tests/test_case_replay.py
→ 298 passed, 0 failed
```

`tsc -b` on the frontend: clean, exit 0.

## 15. Browser result — the success path

Opera, headless, driven over CDP, viewport 1600×900, case **DISP-005**.

The success path was produced with a **`FULL_AI` replay** — every AI stage served
from a stored artefact, revalidated against the current case state, **zero
provider calls** (0.10 s end to end). The browser test injects a page-level
`fetch` wrapper that adds `replayMode: "FULL_AI"` to the resolution POST; no
source file was modified to make this work, and the wrapper is removed at the end
of the run.

**Before the run:** `3 of 8 steps complete`; step 4 `START HERE`; steps 5–8
`NOT RUN`.

**After the run:**

```
8 of 8 steps complete
1=DONE  2=DONE  3=DONE  4=DONE  5=DONE  6=ADVISORY  7=DONE  8=DONE
rail: ✓ The case  ✓ Evidence  ✓ The facts  ✓ Arguments  ✓ Cross-exam
      ✓ Judge  ✓ Decision  ✓ Why
notice: none
```

The UI also reports its own provenance honestly: **`REPLAYED · advocates +
rebuttals + judge`** — a replayed run says so rather than presenting stored
output as a live call.

## 16. Browser result — the provider-failure path

Same browser, same case, **live run against the real provider** — no replay
header. The provider genuinely failed:

```
RIDER ADVOCATE   FAILED   openai_compatible / gemini-3.8-flash   20241 ms
  Agent provider unavailable (openai_compatible: SERVER_ERROR).
  The case analysis is unaffected.

DRIVER ADVOCATE  FAILED   openai_compatible / gemini-3.8-flash   30505 ms
  Agent provider unavailable (openai_compatible: SERVER_ERROR).
  The case analysis is unaffected.
```

A provider-side `SERVER_ERROR` — a 5xx. Not a hallucination, not a bad model
answer: the model was never reached. The UI keeps those apart, which is the whole
point of the failure taxonomy.

**What the operator sees:**

```
5 of 8 steps complete   1 failed   2 blocked

1=DONE  2=DONE  3=DONE  4=FAILED  5=BLOCKED  6=BLOCKED  7=DONE  8=DONE
rail: ✓ The case  ✓ Evidence  ✓ The facts  ! Arguments  – Cross-exam
      – Judge  ✓ Decision  ✓ Why

amber notice: "The AI stages did not complete for this case. Cross-examination
and the Judge's view could not run, so there is no AI recommendation. The
decision and the explanation below are calculated by code and are unaffected."
```

Step 5 says: *"Rebuttal did not run: RIDER and DRIVER did not produce an initial
argument, so there is no verified case to cross-examine."*

Step 6 says: *"Judge did not run: RIDER and DRIVER did not produce an argument.
Deciding a dispute with one side absent would misrepresent the record.
(ADVOCATE INPUT INCOMPLETE)"*

Step 7 — **`DONE`** — still shows the full deterministic outcome: `AUTO RESOLVE`,
`NO REFUND`, `$0.00`, `95%`, `Human review: Not required`.

So the four properties the brief asked for hold exactly: **facts complete /
advocate failure / dependent stages blocked / deterministic decision preserved.**

The same run before the fix read `8 of 8 steps complete` with no failure shown at
all. That was P1.

---

## ONE COMPLETE SUCCESSFUL TRACE

Case **DISP-005**, route deviation, six evidence items. Produced offline through
the real `ResolutionOrchestratorService` with `replay_mode="FULL_AI"`; every
deterministic stage recomputed live. Zero provider calls.

```
CASE DISP-005  (route_deviation)

replayMetadata:
  mode                 = FULL_AI
  replayed             = true   (advocates, rebuttals, judge)
  valid                = true   reasonCodes = []
  currentAnalysisHash  = 33899cc4…39e407
  artifactAnalysisHash = 33899cc4…39e407      ← hashes agree
  capturedProvider     = mock                 ← provenance reported, not hidden
  capturedModel        = gemini-3.8-flash

WORKFLOW STATE MACHINE
  deterministic    COMPLETE  "Calculated by code from the case record.
                              No AI stage can change it."
  advocates.rider  COMPLETE  "2 claim(s) passed verification; 0 rejected by code."
  advocates.driver COMPLETE  "1 claim(s) passed verification; 0 rejected by code."
  rebuttals.rider  COMPLETE  "1 response(s) passed verification; 0 rejected."
  rebuttals.driver COMPLETE  "2 response(s) passed verification; 0 rejected."
  judge            COMPLETE  "The Judge's advice passed deterministic validation."
  finalResolution  COMPLETE  "The authoritative outcome. Preserved in full even
                              when an AI stage fails."
  explanation      COMPLETE  "Assembled by code from the decisive facts and the
                              policy thresholds."

FLAT PIPELINE
  CASE_RECEIVED COMPLETE · EVIDENCE_VALIDATED COMPLETE
  DETERMINISTIC_ANALYSIS COMPLETE · RIDER_ADVOCATE COMPLETE
  DRIVER_ADVOCATE COMPLETE · CLAIM_VERIFICATION COMPLETE
  RIDER_REBUTTAL COMPLETE · DRIVER_REBUTTAL COMPLETE
  REBUTTAL_VERIFICATION COMPLETE · JUDGE COMPLETE

HANDOFF: advocates → verification
  rider  2 verified, 0 rejected      driver  1 verified, 0 rejected

HANDOFF: verified → rebuttals
  rider  1 verified, 0 rejected      driver  2 verified, 0 rejected
  (targets namespaced: RIDER-RB1 responds to DRIVER-D1)

HANDOFF: verified → Judge
  judge status = COMPLETE, skipReason = null
  context contained verified claims and verified rebuttals only

HANDOFF: Judge → validation → deterministic
  ruling           = "No refund recommended"
  recommendedAction= NO_REFUND
  refundAmount     = 0.0 SGD
  resolutionMode   = AUTO_RESOLVE
  confidence       = 0.95
  escalationReasons= []

EXPLANATION (assembled without reference to the Judge)
  accepted claims:
    RIDER-R1  "The actual route was 14.7 km against an expected 11.5 km,
               a 27.83% deviation."
    RIDER-R3  "3.2 km of the deviation is explained by verified conditions,
               which weakens part of the rider's position."
    DRIVER-D1 "Verified route conditions explain 3.2 km of the 3.2 km deviation."
  relevant rebuttals:
    RIDER-RB1 → DRIVER-D1, CHALLENGE
      "This states a measured value accurately, but a measurement on its own
       does not establish the outcome that RIDER is asking the decision to reach."

INVARIANTS
  [PASS] workflow object present
  [PASS] deterministic COMPLETE      [PASS] finalResolution COMPLETE
  [PASS] explanation COMPLETE        [PASS] judge COMPLETE
  [PASS] advocates both COMPLETE     [PASS] rebuttals both COMPLETE
  [PASS] no stage is BLOCKED         [PASS] no stage is FAILED
  [PASS] no stage is NOT_STARTED

FULL_AI_SUCCESS_TRACE_OK = True
```

The same trace as the operator sees it in the browser (`8 of 8 steps complete`,
all rail marks `✓`) is in `.ui-test/shots/91-success-after-run.png` and
`92`–`96`.

---

## Stopping here

The audit is complete and the fixes are verified. The provider mismatch reported
in §3 is **unresolved on purpose** — the brief said not to change the provider,
only to report it. That is the open item.

**Evidence on disk:** `.ui-test/run21b.json` (success), `.ui-test/run22.json`,
`.ui-test/run22-steps.json` (failure), `.ui-test/shots/90`–`96` (success),
`100`–`110` (failure). Offline probes: `.live-validation/probe_full_ai_trace.py`,
`probe_provider_identity.py`, `probe_replay_validity.py`.
