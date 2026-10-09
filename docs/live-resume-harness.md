# Resumable live validation — the simplified harness

**Scope.** This change is confined to the local validation harness. No production
trust boundary was simplified, weakened, or moved. `app/` is untouched by this
change except where noted below, and nothing here is reachable from the API.

## The problem this removes

A live session stops on a provider failure — that is what the brief requires. But
the *cost* of stopping was a restart: every attempt after a stop re-paid for the
stages that had already succeeded, against a provider that had just demonstrated
it was flaky. The 07:14 run is the case in point. The Rider advocate and both
rebuttal sides had completed; the session stopped; and none of that work was
resumable, so a second run would have re-bought all of it.

Three separate sources of waste were in the way:

| Source | Cost |
| --- | --- |
| A separate availability probe before the run | 2 requests to learn what the Rider advocate's first call answers anyway |
| A mandatory 60 s wait between calls and between cases | minutes of wall-clock per run, and it still did not honour `Retry-After` |
| No resume | every stage after a stop re-bought |

## The design decision that made this small

**The replay artefact already is a checkpoint.**

`ReplayArtifactBase` already carries the case id, the format and schema versions,
the deterministic analysis hash, the trusted context hash, the provider/model/mode
provenance, the raw model output, the execution metadata and the verification
fingerprint — which is the whole list a checkpoint is supposed to contain. It is
already written by the orchestrator on every successful stage, and already
revalidated on every load by the *current* code.

So no checkpoint format was introduced. A second format would have meant two
things to keep in step, and two places for a stale artefact to hide. The store
directory *is* the checkpoint store:

```
backend/replay_artifacts/<CASE>/advocates.json   ← ADVOCATES checkpoint
backend/replay_artifacts/<CASE>/rebuttals.json   ← REBUTTALS checkpoint
backend/replay_artifacts/<CASE>/judge.json       ← JUDGE checkpoint
```

One consequence is worth stating explicitly, because it is a constraint rather
than a feature: **validation status is not stored.** An artefact records what the
model said and nothing about whether it is trustworthy. The verified/rejected
split, and therefore the checkpoint's validity, is recomputed on every load. A
stored "VALID" flag would have been the one field a stale checkpoint could hide
behind — and it would have defeated the requirement that a checkpoint must
revalidate under *current* code.

## Which of the five resume conditions were already enforced

The brief required reuse only when five conditions hold. Four of them already had
exactly one implementation, in the replay layer, and re-implementing them in the
harness would have created a second copy that could drift:

| Condition | Where it is enforced | Status |
| --- | --- | --- |
| Case ID matches | `ReplayValidationService` → `CASE_ID_MISMATCH` | already there |
| Deterministic analysis hash matches | → `ANALYSIS_HASH_MISMATCH` | already there |
| Schema / version matches | → `SCHEMA_VERSION_MISMATCH`, `REPLAY_VERSION_UNSUPPORTED` | already there |
| Output revalidates under current code | `replay_advocates`/`replay_rebuttals` re-run verification and compare the fingerprint; `replay_judge` re-runs `JudgeOutputValidationService` | already there |
| Required preceding checkpoints remain valid | `ReplayMode` is a prefix chain | **structural** |
| Provider/model provenance is correct | *nothing* — the replay layer never asks this | **added, harness-side** |

The planner does not call the validators. It calls `ReplayService` — the same
object the orchestrator is about to call — and treats a `ReplayRefused` as the
answer. A checkpoint the planner accepts is therefore one the orchestrator will
accept, by construction rather than by agreement.

**Dependency invalidation needs no policy.** `ReplayMode` is
`NONE ⊂ ADVOCATES ⊂ ADVOCATES_AND_REBUTTALS ⊂ FULL_AI`. There is no mode that
replays the rebuttals without the advocates, and none that replays the Judge
without both. A stage whose predecessor must run live is therefore not merely
forbidden — it is unrepresentable. `plan_resume` returns a mode, and asserts that
its reuse set is a prefix of the chain before doing so.

## The one check that had to be added

`ReplayValidationService` asks *"does this still describe this case?"*. It never
asks *"did this come from the model under test?"*. A mock capture validates
perfectly: same schema, same case, same hash. The only thing wrong with it is its
provenance — which is exactly the shape of the incident that destroyed the live
DISP-005 artefacts on 2026-10-05.

That distinction belongs to a validation session, not to the product, so it lives
in the harness. `provenance_problem` compares `capturedMode`/`capturedProvider`/
`capturedModel` against the session's declared target and then consults
`is_live_eligible` (which additionally requires that every side of the stage
produced output). A mismatch yields `NOT_ELIGIBLE`, not `REFUSED`: the artefact
may be flawless, and calling it invalid would misdescribe it.

## What changed

**Removed**

- The availability probe — `probe_payload`, `probe`, `run_probes`, `--probe`,
  `--probe-gap`, and the probe gate. The Rider advocate's first call is the
  availability test, and it fails identically. The probe's one real finding
  (thinking tokens consume a small completion budget, so a healthy provider can
  return an empty body) is recorded in `docs/live-stage-report.md`.
- `PacingProvider` and `--pace-seconds`. Pacing is now the provider's own: a
  bounded exponential backoff for 5xx, and `Retry-After` honoured on 429.
- `--case-gap` (default 60 s), `--cases`, `--labels`.

**Added**

- `--offline --capture` is **refused** (exit 2), not merely discouraged.
  `resolve_capture` already defaulted it off; an explicit flag used to be enough
  to re-arm the data-loss trap, and now cannot be.
- A positional case argument, so `python .live-validation/live_run.py DISP-005`
  works, and a bare invocation defaults to DISP-005.
- `.live-validation/checkpoints.py` — the resume planner.
- Per-stage outcome lines and a failure block (below).

**Kept, unchanged**

Every trust check the brief listed: `AdvocateClaimVerificationService`, the
rebuttal validation service, `JudgeOutputValidationService`, evidence/policy/
deterministic-fact validation, monetary-value protection, HUMAN_REVIEW protection,
rejected-material exclusion, and the deterministic before/after hash comparison.
`StopRule` — 429 stops immediately, one bounded retry, second failure stops — is
untouched, and still enforced per HTTP attempt inside the guarded client.

### Honest note on size

`live_run.py` went from 1,331 to 1,387 lines, and a new 370-line module appeared.
The complexity removed is the kind that spends quota; the complexity added is the
kind that saves it. This is not a net line reduction and should not be described
as one. What it is: the probe, the fixed waits and the restart-after-stop are
gone, and a stage that succeeded once is not paid for twice.

## Output

A run now says, per stage:

```
=== DISP-005 — attempts so far 0/40 ===
  ADVOCATES CHECKPOINT REUSED   (gemini-3.8-flash @ 2026-10-05T02:46:20Z)
  REBUTTALS no checkpoint — will run live
  JUDGE     not attempted (the rebuttals must run live) — will run live
  RIDER_ADVOCATE   CHECKPOINT REUSED
  DRIVER_ADVOCATE  CHECKPOINT REUSED
  RIDER_REBUTTAL   LIVE → COMPLETE → VERIFIED → CHECKPOINT SAVED
  DRIVER_REBUTTAL  LIVE → COMPLETE → VERIFIED → CHECKPOINT SAVED
  JUDGE            LIVE → COMPLETE → VERIFIED → CHECKPOINT SAVED
```

And a failure names what a resume needs, on one screen:

```
  FAILED
    stage               RIDER_REBUTTAL
    provider            openai_compatible
    model               gemini-3.8-flash
    category            SERVER_ERROR
    attempts            2
    latency             4120 ms
    valid checkpoints   ADVOCATES
    resume from         REBUTTALS
    reason              STOPPED: RIDER_REBUTTAL: second HTTP 503. ...
```

## Offline evidence

**Full suite: 79 failed / 528 passed.** The 79 are the unchanged Bucket B
(`StopIteration` on the `CASE-2026-1041` fixture, replaced by `DISP-*` in an
earlier stage) and are untouched. Passing moved 502 → 528: +30 new tests, −4
tests that covered the deleted probe.

**`tests/test_live_resume.py` — 30 tests, all passing.** They run the *real*
capture path to produce the checkpoints and the *real* replay layer to judge
them. The only stub is the provider, which never opens a socket. The mock wears
the live model's provenance, because capture stamps an artefact with whatever the
provider reports — without that, the artefacts would be correctly rejected as
not-live-evidence and the REUSED branch would be unreachable.

Coverage: a complete capture resumes everything; an empty store resumes nothing;
a reused checkpoint never reaches the provider (asserted with a provider that
counts calls, because a raised error would be swallowed by the orchestrator's
stage boundary); planning writes nothing; corrupted, wrong-hash, wrong-schema,
wrong-dispute-type, wrong-mode, wrong-provider and wrong-model checkpoints are
each refused; a refused earlier stage invalidates every later one *without
deleting it*; a refused Judge preserves the advocates and rebuttals; an
incomplete rebuttal is not a checkpoint; the four modes are the four prefixes; and
the offline-capture refusal.

**Sensitivity.** Each load-bearing rule was neutralised in turn and the failures
confirmed, because a test that cannot fail is not evidence:

| Neutralised | Result |
| --- | --- |
| `provenance_problem` → always `None` | 7 tests fail |
| dependency skip → `if False` | 12 tests fail |
| `MODE_FOR_PREFIX[0]` → `"NO_REPLAY"` | 7 tests fail |

**Egress guard: 87 passed** across the three harness test files under
`-p netguard`, which blocks all non-loopback `connect`, `getaddrinfo` and
`create_connection`. `guard_selftest.py` confirms the guard is not vacuous:
`generativelanguage.googleapis.com` and a raw `connect(("8.8.8.8", 53))` are both
blocked, loopback is allowed.

**Hermetic rehearsal**, three phases through the real CLI with the egress guard
installed, into a redirected store:

| Phase | Result |
| --- | --- |
| No checkpoints | `replayMode=NONE`, all 5 stages live, nothing captured |
| Checkpoints seeded | `replayMode=FULL_AI`, all 5 stages `CHECKPOINT REUSED`, **zero model calls** |
| `--offline --capture` | refused, exit 2 |

### The rehearsal found a bug the unit tests could not

`NO_REPLAY` is the *name* of a constant whose **value is `"NONE"`**. The planner
originally wrote the mode as the string `"NO_REPLAY"`, which the orchestrator
rejects at runtime with a pydantic `literal_error`. The unit tests passed anyway,
because they compared the planner's output against the planner's own constant —
self-consistent, and wrong.

The rehearsal hit it on the first run, because it drives the real CLI and so
reaches the real validator. Fixed by importing `NO_REPLAY` and typing the mapping
as `ReplayMode`; and pinned by a new test that asserts each mode against
`build_plan`, the layer that actually consumes it, rather than against the
planner's own vocabulary.

The lesson is worth keeping: **compare against the consumer, not against your own
constant.** A test written from the implementation can only confirm that the
implementation agrees with itself.

## What was not done

- **Per-side resume is not implemented, and is not implementable today.** The
  brief asked to checkpoint `RIDER_ADVOCATE`, `DRIVER_ADVOCATE`,
  `RIDER_REBUTTAL`, `DRIVER_REBUTTAL` and `JUDGE` individually. The report names
  all five, but the *reuse unit* is the group, because that is the unit the store
  and the pipeline can express: `capture_advocates` refuses unless **both** sides
  completed, and `replay_advocates` returns a whole `AdvocateRunResponse`. A
  half-reused advocates stage — Rider from storage, Driver live — has no
  `ReplayMode` and no orchestrator path. Adding one would be a product change,
  which this brief forbids. The cost of the limit is bounded: an advocates stage
  where one side failed is re-run whole, which is one extra call, and it is the
  same invariant that makes a stored advocate argument replayable at all.
- **No live call was made.** This change is unvalidated against the real provider
  by design; that is the next step and it is the user's to authorise.
