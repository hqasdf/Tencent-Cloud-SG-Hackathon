# RydeResolve — LIVE Gemini validation report

**Run date:** 2026-10-05 (local), 07:14–07:19 UTC+8
**Provider:** `gemini-3.8-flash` via `openai_compatible`
**Endpoint:** `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`
**Session shape:** fresh ledger, ceiling 40, **no health probes**, 60 s harness-side pacing
**Outcome:** **SESSION STOPPED** on the Judge — `429 RATE_LIMITED` (brief §9: stop immediately)

---

## 1. Verdict table

| Item | Result |
|---|---|
| **Stage 4 — Live Advocates** | **PASS** ✅ |
| **Stage 5 — Live Judge** | **FAIL** (provider: 503 then 429) |
| **Stage 6 — Live Route Pipeline** | **FAIL** (Rider rebuttal 503 ×2; Judge failed) |
| Stage 6 — Live No-Show (DISP-002) | **NOT ATTEMPTED** (instructed to stop after DISP-005) |
| Human-review gate (DISP-003) | **NOT ATTEMPTED** (instructed to stop after DISP-005) |

**Stage 4 genuinely passed**: both advocates returned real, validated Gemini responses
(§3). Stages 5 and 6 did **not** pass — the failures are provider-side, so this is
**not** evidence about model output quality and **not** a hallucination.

---

## 2. Run configuration

Per instruction, **no separate health probes were run** — the Rider Advocate was the
availability test. Pacing was a uniform **60 s** between model calls, which satisfies
every gap in the spec ("45–60 s" and "60 s") at once, applied by the harness-side
`PacingProvider` that wraps the provider. No product code, prompt, model or provider was
changed.

| Setting | Value |
|---|---|
| Ledger at start | **0** (fresh) |
| Ledger at end | **7** |
| Remaining | **33** |
| Pacing | 60 s before every model call after the first |

---

## 3. Stage 4 — LIVE ADVOCATES — **PASS**

Both sides completed on the **first attempt** — no retries, no provider errors.

| | Rider | Driver |
|---|---|---|
| status | **COMPLETE** | **COMPLETE** |
| provider / model | `openai_compatible` / `gemini-3.8-flash` | same |
| attempts / retries | 1 / 0 | 1 / 0 |
| latency | 7,281 ms (HTTP 7,083 ms) | 6,541 ms (HTTP 6,479 ms) |
| input tokens | 4,462 | 4,532 |
| output tokens | 744 | 904 |
| total tokens | 6,431 | 6,524 |
| **unattributed** | **1,225** | **1,088** |
| claims generated | **2** | **3** |
| claims verified | **2** | **3** |
| claims rejected | **0** | **0** |
| verification rate | **1.0** | **1.0** |
| verified claim IDs | `R1`, `R2` | `D1`, `D2`, `D3` |
| requested outcome | `PARTIAL_REFUND` | `NO_REFUND` |
| invalid evidence refs | 0 | 0 |
| invalid policy refs | 0 | 0 |
| fact contradictions | 0 | 0 |
| unknown asserted facts | 0 | 0 |
| malformed output | false | false |

**Independence:** the two sides reached *different* requested outcomes
(`PARTIAL_REFUND` vs `NO_REFUND`) from the same facts, which is what independent
reasoning looks like. Neither is a copy of the other.

**Non-interference:** `deterministicChanges = {}` and the deterministic hash is identical
before and after (§7). The AI did not move a single calculated value.

**Unattributed tokens** (1,225 and 1,088) are thinking tokens: billed, counted in
`total_tokens`, never returned as `content`. `4,462 + 744 = 5,206` against a total of
6,431 — a gap of 1,225. This is the same effect that made the old 16-token probe look
like a failure (§8).

---

## 4. Stage 6 — Rebuttals (partial)

| | Rider rebuttal | Driver rebuttal |
|---|---|---|
| status | **FAILED** | **COMPLETE** |
| attempts / retries | 2 / 1 | 1 / 0 |
| HTTP statuses | **503, 503** | **200** |
| latency | 68,875 ms | 15,266 ms |
| tokens | none | 5,419 / 686 / 9,889 (**3,784 unattributed**) |
| responses generated | 0 | **2** |
| responses verified | 0 | **2** |
| responses rejected | 0 | **0** |
| stances | — | `CHALLENGE` ×1, `CONCEDE` ×1 |
| targets | — | `RIDER-R1`, `RIDER-R2` |
| verified rebuttal IDs | — | `DRIVER-RB1`, `DRIVER-RB2` |
| own-side targets | 0 | **0** |
| invalid target IDs | 0 | 0 |
| invented monetary values | 0 | 0 |

The Driver's rebuttals targeted **both** Rider claims and used two *different* stances
(one challenge, one concession). Code verified that every target belongs to the opposing
side, that the stances are valid, and that no monetary value was invented — all clean.

A failed rebuttal is a non-blocking outcome by design: the Judge may still run with the
verified material that exists.

---

## 5. Stage 5 — LIVE JUDGE — **FAIL**

| field | value |
|---|---|
| status | **FAILED** |
| failureCode | **`RATE_LIMITED`** |
| failureCategory | `PROVIDER_FAILURE` |
| attempts / retries | 2 / 1 |
| HTTP statuses | **503, then 429** |
| latency | 64,555 ms |
| recommended outcome | `null` |
| accepted Rider / Driver claim IDs | `[]` / `[]` |
| evidence IDs / policy IDs | `[]` / `[]` |
| executable | `false` |
| validationResult | **`FAILED`** |
| validation issues | `[]` |

The Judge context **was** built (`JUDGE_CONTEXT_BUILT` in the audit trail) and the call
**was** attempted twice. The first attempt returned 503; the retry returned **429**, which
is the instruction to stop immediately.

**There are no Judge validation issues to report — not zero issues found, but zero Judge
output to validate.** Those are different statements and only the second is true. No
outcome, claim IDs, evidence IDs or policy IDs exist because no Judge response was ever
received.

The stage-level `validationResult` is **`FAILED`** (the stage did not produce usable
output), while `validationIssues` is empty (there was nothing to raise an issue about).
A reader who conflated the two would wrongly conclude the validator found the Judge
output clean. It found no output at all.

### Protocol deviation — the stop fired one stage late

The instruction was: *"503 … allow one retry for that logical stage. If the retry also
fails: STOP the session."* The Rider rebuttal returned **503, then 503** — that is a
second failure, so **the session should have stopped there.**

It did not. The harness continued to the Driver rebuttal (1 more attempt) and the Judge
(2 more attempts) before stopping on the 429.

Cause: the harness's stop rule reads each stage's `failureCode` and checks it against the
stop set. The rebuttal stage reports `failureCode = None` — the outage is described in
`failureReason` instead. So the rule never fired for the rebuttal, and the effective stop
came one stage later, from the Judge's 429.

**Not fixed — flagged.** It is a harness defect, not a product one, and it will recur on
the next run. Proposed fix: have the rebuttal failure carry a `failureCode`, or have the
harness treat a `FAILED` stage whose `failureReason` names a provider outage as a stop
condition.

---

## 6. Counts

| metric | value |
|---|---|
| Logical model calls | **5** (both advocates, both rebuttals, Judge) |
| **Total HTTP attempts** | **7** |
| Retries | 2 |
| HTTP 200 | **3** (Rider advocate, Driver advocate, Driver rebuttal) |
| HTTP 429 | **1** (Judge retry) |
| HTTP 503 | **3** (Rider rebuttal ×2, Judge ×1) |
| Timeouts | 0 |
| Network errors | 0 |
| Budget ceiling / spent / remaining | 40 / 7 / **33** |
| Stages completed live | 3 of 5 (Rider advocate, Driver advocate, Driver rebuttal) |
| Cases completed live | 0 of 1 |

---

## 7. Deterministic invariants — UNCHANGED

| item | before | after |
|---|---|---|
| `deterministic_analysis_hash` | `33899cc4…e407` | **identical** |
| recommended action | `NO_REFUND` | `NO_REFUND` |
| refund | 0.0 SGD | 0.0 SGD |
| confidence | 0.95 | 0.95 |
| resolutionMode | `AUTO_RESOLVE` | `AUTO_RESOLVE` |
| escalation reasons | `[]` | `[]` |
| ruling | "No refund recommended" | same |

- `deterministicUnchanged` = **true**
- `deterministicChanges` = **{}**
- `caseFixtureUnchanged` = **true**
- `authoritativeMatchesBaseline` = **true**

Two provider failures, a failed rebuttal and a failed Judge left the deterministic layer
**bit-identical**. The central claim held under a second adverse run.

---

## 8. Trusted-context validation

| check | result |
|---|---|
| `contextHoldsExactlyVerifiedClaims` | **true** |
| `contextClaimIds` | `RIDER-R1`, `RIDER-R2`, `DRIVER-D1`, `DRIVER-D2`, `DRIVER-D3` |
| `contextUnexpectedClaimIds` / `contextMissingClaimIds` | `[]` / `[]` |
| `contextHoldsExactlyVerifiedRebuttals` | **true** |
| `contextRiderRebuttalCount` / `contextDriverRebuttalCount` | **0** / **2** |
| `rejectedClaimsExcludedFromContext` | **true** |
| `rejectedRebuttalsExcludedFromContext` | **true** |
| `judgeContextTrustedHash` | `a0d83e8d8f74dc8a02c3c341629c56bd4fedfeb898d5def009369aa37ec3d9dd` |

The context holds exactly the 5 verified claims and exactly the 2 verified rebuttals —
nothing extra, nothing missing. Claim IDs are namespaced by side (`R1` → `RIDER-R1`),
so the Driver's rebuttal targets and the Judge context agree on one ID space.

**Honest caveat:** zero claims and zero rebuttals were rejected, so the two *exclusion*
checks are **vacuously true**. They prove nothing was wrongly included; they do not
exercise the exclusion path. The structural guarantee is the code: `_side()` reads only
`verified_claims`, `_rebuttals()` only `verified_rebuttals`. The `judgeCited*` checks are
likewise vacuous — the Judge produced no output to cite anything.

---

## 9. Live replay artefacts — **LIVE ELIGIBLE** ✅

| stage | written | writtenByThisRun | capturedMode | capturedProvider | capturedModel | liveEligible |
|---|---|---|---|---|---|---|
| ADVOCATES | **yes** | **yes** | `real` | `openai_compatible` | `gemini-3.8-flash` | **true** |
| REBUTTALS | **yes** | **yes** | `real` | `openai_compatible` | `gemini-3.8-flash` | **true** |
| JUDGE | **no** | no | — | — | — | — |

- `replay_artifacts/DISP-005/advocates.json` — 8,830 bytes, `schemaVersion stage4-v1`
- `replay_artifacts/DISP-005/rebuttals.json` — 4,883 bytes, `schemaVersion stage6-v1`
- Both carry the new `capturedMode`/`capturedProvider`/`capturedModel` keys and **no**
  legacy `provider`/`model` keys.
- Neither contains forbidden keys or credential-shaped values.

The **JUDGE artefact was not written**, because that stage did not produce validated
output — which is the new rule working. Fresh artefacts from a previous failed run were
moved aside beforehand, so nothing here is stale.

---

## 10. Fixes applied before this run, with tests

### 10.1 The availability probe's token budget

`max_tokens` **16 → 512** (`PROBE_MAX_TOKENS`). The previous run's probes returned HTTP 200
with *empty* content, which the gate scored as failure; the cause was thinking tokens
consuming the entire 16-token allowance. The payload is now a pure function
(`probe_payload`) so the budget can be asserted offline. The prompt is still
`"Reply with OK"`, with no `response_format` and no schema. No production provider code
changed.

### 10.2 A stage that did not run writes no artefact

- `capture_rebuttals` now **returns `None` and writes nothing** when no side ran
  (`NOT_RUN`), and records **no audit event**. A side that *ran and failed* is still
  stored — that is a real outcome, and the rule is "did not run", not "any failure".
- `capture_advocates` now requires both sides to be `COMPLETE`, not merely to have
  returned raw output.
- New fields `capturedMode` / `capturedProvider` / `capturedModel` on every artefact, with
  `is_live_eligible` requiring **all three** (`mode == "real"`, non-empty provider,
  non-null model). Old artefacts carrying `provider`/`model` still load via alias, and are
  correctly **not** live-eligible because no mode was recorded.
- The harness reports `liveEligible` per artefact.

### 10.3 Tests — 16 new, all offline, under a non-vacuous egress guard

`backend/tests/test_replay_capture_safety.py` — **16 passed**. Covers the probe budget and
the "no `response_format`" property; `NOT_RUN` writing nothing (unit *and* end-to-end);
the audit trail staying silent; a run-and-failed side still being stored; advocate status
gating; live eligibility across all four missing-field combinations; legacy-artefact
loading; and the compatibility properties.

**The tests were proven able to fail.** With the `NOT_RUN` guard temporarily neutralised,
3 tests failed with the exact production symptom:

```
AssertionError: REBUTTALS_NOT_RUN must never create rebuttals.json
assert not True
 where True = .../replay_artifacts/DISP-005/rebuttals.json).exists()
```

The guard was then restored and re-run green.

### 10.4 Regression

Full backend suite under the egress guard: **79 failed / 457 passed**. The 79 are the
known Bucket B set, unchanged and untouched; passing rose **441 → 457** (+16 new tests).
**No regression.**

---

## 11. Outstanding

1. **Harness stop rule fires one stage late (§5).** A second 503 on a rebuttal does not
   stop the session; the effective stop came from the Judge's 429. Fix before the next run.
2. **Stage 5 and Stage 6 did not pass.** Both were attempted against a real provider and
   failed on capacity (503/429). Re-run when the provider settles.
3. **DISP-002 and DISP-003 not attempted**, per instruction.
4. **The exposed API key is still not rotated.** Accepted for this run; the exposure is
   unchanged.
5. **The provider is flapping.** Across two runs: 200, 200, 200, 503, 503, 503, 200, then
   200, 200, 503, 503, 200, 503, 429 — including 503 → 200 on a retry of the same call.
   Provider-side capacity, never a product fault.
6. **Judge validation remains unexercised live.** `JudgeOutputValidationService` has only
   ever run offline against stored or mock output. It cannot be reported as validated
   against live model output until a Judge call succeeds.
