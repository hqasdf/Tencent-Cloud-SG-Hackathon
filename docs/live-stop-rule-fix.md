# Harness fix: the live session's stop rule fired one stage late

**Date:** 2026-10-05
**Scope:** `backend/.live-validation/live_run.py` and tests. **No production code,
no prompts, no model or provider change. Zero network used by the work below.**
**Trigger:** the DISP-005 live run of 2026-10-05 07:14, where the Rider rebuttal
returned 503 twice and the session did not stop until the Judge's 429.

---

## 1. What was actually wrong

The previous report described this as "the stop fired one stage late". That was
accurate but incomplete: there were **two independent defects**, and fixing either
one alone changes nothing.

### Defect 1 — classification: the rebuttal stage never reported a failure code

`_stop_reason` decides whether to end the session by reading `failureCode` from
each stage's metrics. `_advocate_metrics` and `_judge_metrics` both emit that key.
**`_rebuttal_metrics` did not emit it at all**, so the failed Rider rebuttal read
as `failureCode: None` and was never recognised as a stop condition.

The code was always available. `RebuttalOrchestratorService._failure_metadata`
sets `failure_code=code` on the execution metadata (`rebuttal_orchestrator.py:417`),
and the harness simply never read it.

### Defect 2 — timing: the rule was only consulted between cases

```
report = run_case(...)          # runs ALL FIVE stages inside this call
...
reason = _stop_reason(report)   # consulted only after the case has finished
```

The orchestrator owns the stage sequence and never asks the harness whether to
continue. So even correctly classified, the stop could only ever prevent the
*next* case from starting. Within a case, a 503 pair at stage 3 still let stages
4 and 5 run — three wasted attempts.

---

## 2. What changed

### 2.1 `StopRule` + `LiveStopCondition` — the rule now runs per HTTP attempt

The rule is enforced inside `GuardedClient._send`, on every attempt:

| condition | behaviour |
|---|---|
| **429** | stop immediately; the response is never handed back, so the provider cannot retry it either |
| **5xx, timeout, network error** | one bounded retry for that stage; a **second** failure for the *same* stage stops the session |
| any other status | never a stop condition |

`LiveStopCondition` derives from **`BaseException`**, not `Exception`. This is
load-bearing. Every stage boundary in the product isolates failure with
`except Exception` — correct for a product, wrong for a session told to stop. An
`Exception`-derived stop would be caught at the boundary, the stage marked
`FAILED`, and the orchestrator would walk on. This is the same technique
`LiveBudgetExhausted` already used.

The counter is keyed by stage (a rebuttal outage must not end a session whose
advocates were healthy) and lives on the shared `StopRule`, **not** on the client
— because `OpenAiCompatibleProvider` builds a **fresh client for every attempt**,
so a client-owned counter would reset and could never reach two.

### 2.2 The rebuttal stage now reports its failure code

One line, mirroring `_advocate_metrics`:

```python
"failureCode": execution.failure_code if execution else None,
```

### 2.3 A stopped run degrades honestly

An abort means `orchestrator.run` never returns, so there is no result object and
no `stages` block. The report now says so rather than silently emptying:

- `stop` — the reason, the stage that triggered it, and the per-stage attempt counts
- `recoveredFromArtifacts` — per-side `AgentCallMetadata` and the verification
  fingerprint, read from **this run's own** advocates artefact (gated on
  `createdAt >= run start`), and labelled as artefact-sourced rather than presented
  as pipeline output
- `error` is `"STOPPED: …"`, never an error: a provider outage is not a product fault

### 2.4 The probe gate can no longer report OPEN on an abandoned run

A 429 on a probe now abandons the remaining probes, and the gate requires
`not stopped and len(results) == count`. Without that, `all()` over a shortened
list would be vacuously true and would report a rate-limiting provider as
available.

---

## 3. Tests — 37 new, all offline, egress blocked

`backend/tests/test_live_stop_rule.py`. Run with the non-vacuous egress guard:

```
PYTHONPATH="<guard>;." python -m pytest -q -p netguard tests/test_live_stop_rule.py
```

The guard was proven to fire before being trusted (`getaddrinfo` and a raw
`connect` both blocked; loopback still allowed).

Coverage: `BaseException`-not-`Exception`; first-5xx-does-not-stop; second-5xx per
status code; per-stage independence; 429 immediate; transport failures counted like
a 5xx; 5xx-then-timeout as a second failure; success and client errors never
stopping; the rule actually wired into `_send`; the stopping attempt still on the
ledger; a fresh client per attempt sharing one counter; rebuttal `failureCode`;
`_stop_reason` naming the right stage; all three metric builders emitting
`failureCode`; the stopped-run report; artefact recovery including freshness
rejection; the probe gate; and the capture guard.

### 3.1 The tests were proven able to fail

Three separate neutralisations, each reverted afterwards:

| neutralised | result |
|---|---|
| `_rebuttal_metrics` failure code | 2 failed — reproducing the **exact production symptom**: `assert 'JUDGE failed with RATE_LIMITED' == 'RIDER_REBUTTAL failed with SERVER_ERROR'` |
| the `_stop.check` calls in `_send` | 4 failed — `Failed: DID NOT RAISE <LiveStopCondition>` |
| `resolve_capture` default | 1 failed — `assert True is False` |

---

## 4. Regression

Full backend suite under the egress guard: **79 failed / 494 passed**.

The 79 are the known Bucket B set, unchanged and untouched. Passing rose
**457 → 494** (+37 new tests). **No regression.**

Offline rehearsal of the harness (`--offline`): 5 logical calls, **0 HTTP
attempts**, `deterministicUnchanged: true`, no error.

---

## 5. Incident — the offline rehearsal destroyed the live artefacts

**This is a real loss and it is reported rather than papered over.**

The first offline rehearsal was run with `RESULTS_DIR`, `ARTIFACT_ROOT` and
`LEDGER_PATH` redirected to a scratch directory, intending to leave the live
artefacts untouched. **It did not work.** `ReplayArtifactStore` derives its root
from its own module path (`store.py:41`) and offers **no override**, so
`live_run.ARTIFACT_ROOT` changes only what the harness *reads*. The capture path
still wrote to `backend/replay_artifacts/DISP-005/`.

Consequently the rehearsal replaced:

| file | was (live) | became (mock) |
|---|---|---|
| `advocates.json` | 8,830 B, `capturedMode: real` | 4,489 B, `capturedMode: mock` |
| `rebuttals.json` | 4,883 B, `capturedMode: real` | 4,487 B, `capturedMode: mock` |
| `judge.json` | did not exist | 1,853 B, `capturedMode: mock` (created) |

`replay_artifacts/` is gitignored, and no backup contained run 2's captures, so
**the live raw model output is unrecoverable.** The mock files have been moved to
`.live-validation/stale-artifacts-backup/rehearsal-clobber-20261005/` and
`replay_artifacts/` is now empty, so nothing on disk can be mistaken for live
output.

**What survived:** `.live-validation/results/DISP-005.json` (the live run's full
metrics) and `session.json` are intact. They still record that the live run wrote
an 8,830 B advocates artefact and a 4,883 B rebuttals artefact, both
`liveEligible: true`.

**What was lost:** the live-captured replay artefacts themselves — the raw
`AdvocateOutput` / `RebuttalOutput` the models produced. Every metric derived from
them survives; the artefacts needed to *replay* that run do not.

**Root cause fixed.** `--no-capture` now exists, and `resolve_capture` defaults
capture to **off for `--offline`** and on for a live run. The rehearsal wrapper
passes `--no-capture`. Re-running the rehearsal now writes **0 files** to
`replay_artifacts/`. Three tests lock this down, and the guard was proven able to
fail.

This trap had already been hit once before, on the same day; the earlier response
moved the clobbered files aside without fixing the cause. It is now fixed.

---

## 6. Outstanding

1. **The live replay artefacts are gone (§5).** Re-capturing them requires a live
   run, which has not been authorised.
2. **Stage 5 / Stage 6 live validation is still unachieved.** The Judge has never
   completed against live model output, so `JudgeOutputValidationService` remains
   unexercised live.
3. **The stop rule has never fired against a real provider.** Its correctness
   rests on the tests and the offline rehearsal; the next live run is the first
   real exercise of it.
4. **DISP-002 and DISP-003 were not attempted**, per instruction.
5. **The exposed API key is still not rotated.**
