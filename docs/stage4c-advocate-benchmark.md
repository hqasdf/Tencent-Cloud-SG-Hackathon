# Stage 4C — Gemini Characterization Benchmark

**Model under test:** `gemini-3.8-flash` (project-selected advocate model)
**Status:** `STAGE 4C INFRASTRUCTURE COMPLETE — FINAL SMOKE RUN PENDING GEMINI QUOTA`

---

## 1. Project decision

Ollama/Qwen is **no longer a benchmarking target**. `gemini-3.8-flash` is the selected
advocate model for RydeResolve.

Consequences:

- Ollama is not run, started, configured, or benchmarked.
- Ollama/provider-compatibility code is **not deleted**. It is retained as an unused
  fallback.
- Stage 4C is a **characterization** benchmark, not a model-selection competition.

## 2. What Stage 4C is, and what it is not

**Is:** a measurement of how the selected model behaves on real cases through the real
pipeline, producing standardized metrics.

**Is not:** a ranking, a bake-off, or a vendor evaluation. The CLI computes no aggregate
quality score and ranks nothing. There is no winner to report.

Gemini is **not declared "best"**. It is the model the project selected. Those are
different statements, and only the second one is supported by the data.

## 3. Infrastructure retained

All existing benchmark infrastructure stays, because it remains useful for:

- regression testing
- provider reliability measurement
- latency measurement
- token measurement
- hallucination measurement
- claim verification measurement

The multi-model capability in `--models` and `MODEL_CATALOGUE` is retained so the
transport stays demonstrably vendor-agnostic. It is not a Stage 4C goal.

## 4. Recorded result — the failed run stands

Run `bench-20260928-075134-3db79f`, smoke profile, runs=1:

| Case | Side | Code | Category | Latency | Attempts | Retries |
|---|---|---|---|---|---|---|
| DISP-005 | RIDER | `SERVER_ERROR` | PROVIDER_FAILURE | 32494 ms | 3 | 2 |
| DISP-005 | DRIVER | `SERVER_ERROR` | PROVIDER_FAILURE | 31475 ms | 3 | 2 |
| DISP-002 | RIDER | `RATE_LIMITED` | PROVIDER_FAILURE | 441 ms | 1 | 0 |
| DISP-002 | DRIVER | `RATE_LIMITED` | PROVIDER_FAILURE | 500 ms | 1 | 0 |

**0 complete, 4 provider failures, 0 model-output failures, 0 internal errors.**

This record is kept as-is. It is **not** a statement about model quality:

- The model never produced an output to judge. No claim was generated, so there is
  nothing to call a hallucination and nothing to call a correct citation.
- `verificationRate` is `null`, not `0.0`. Zero would assert that every claim was
  rejected, which is a different and false claim.
- `SERVER_ERROR` (HTTP 503) and `RATE_LIMITED` (HTTP 429) are both recorded under
  `PROVIDER_FAILURE`. A 503 is never counted as a hallucination, and a hallucination is
  never counted as downtime.

Two behaviours were confirmed in production by this run: the 503 path made 3 attempts
with backoff, and the 429 path with no `Retry-After` made exactly 1 attempt and stopped
rather than firing repeatedly at a rate limiter.

## 5. The one final smoke run

When Gemini quota is available, run exactly this — once:

```bash
cd backend
python -m app.benchmarks.advocate_benchmark --profile smoke --models gemini --execute
```

| Parameter | Value |
|---|---|
| profile | `smoke` |
| runs | `1` |
| model | `gemini` |
| expected external API calls | **4** |
| cases | 1 `route_deviation`, 1 `no_show_charge` |
| advocates per case | `RIDER`, `DRIVER` |

Constraints:

- **No connectivity or model-list probes immediately before the benchmark.** The
  benchmark calls are themselves sufficient.
- **No automatic additional repetitions.**

## 6. Success data to capture

For each of the four advocate calls:

| Group | Fields |
|---|---|
| Outcome | `status`, provider error if any |
| Timing | `latencyMs` |
| Tokens | `inputTokens`, `outputTokens`, `totalTokens`, `unattributedTokens` |
| Claims | `generatedClaimCount`, `verifiedClaimCount`, `rejectedClaimCount`, `verificationRate` |
| Rejections | `rejectionReasons`, `invalidEvidenceReferenceCount`, `invalidPolicyReferenceCount`, `factContradictionCount`, `unknownFactCount` |
| Output shape | `structuredOutputValid`, `malformedOutput` |
| Outcome | `recommendedOutcome` |
| Retry | `retryCount`, `attemptCount` |

All of these are already emitted per call by `CallMetrics.to_dict()`; no change is
needed to capture them.

## 7. Exit condition

**If all four advocate calls COMPLETE:**

1. Generate the benchmark JSON + Markdown report.
2. Mark: **`STAGE 4C GEMINI CHARACTERIZATION COMPLETE`**.

No repeated trials are required before Stage 5.

**If one or more calls fail with `429`, `503`, `TIMEOUT`, or `NETWORK_ERROR`:**

- Record them as **PROVIDER** failures.
- Do not change models.
- Do not switch to Ollama.
- Do not repeatedly retry the entire benchmark.

Another run can be collected later.

## 8. Relationship to Stage 5

Stage 5 must not be blocked indefinitely waiting for Gemini quota.

The Stage 4C infrastructure is complete and tested, and prior real Gemini evidence
already exists from the migration validation:

| Call | Status | Latency | Tokens (in/out/total) | Claims (gen/verified/rejected) |
|---|---|---|---|---|
| Route — Rider | COMPLETE | 8500 ms | 4462 / 650 / 6352 | 2 / 2 / 0 |
| Route — Driver | COMPLETE | 18508 ms | 4532 / 815 / 6633 | 3 / 3 / 0 |
| No-show — Driver | COMPLETE | — | — | 3 / 3 / 0 |

Route claims verified 5/5; no-show claims verified 3/3. Both advocates produced
well-formed, fully grounded output. The note that tokens exceed input + output is the
provider's own accounting, recorded as `unattributedTokens` rather than guessed at.

A standardized smoke-run result is **desirable** for the metrics table, but Stage 5
architecture work may proceed without it.

**Stage 5 is not implemented in this task.**

---

## 9. Implementation inventory

`backend/app/benchmarks/` — six modules:

| File | Purpose |
|---|---|
| `models.py` | `BenchmarkModelConfig`, `MODEL_CATALOGUE`, `FailureCategory`, `RunStatus`, `SkipReason`, `CallMetrics`, `LatencyStats`, `ModelAggregate`. |
| `cases.py` | Semantic case selection by criterion. |
| `metrics.py` | Metric derivation and aggregation. |
| `runner.py` | Planning and execution through the real pipeline. |
| `reporting.py` | JSON + Markdown output with a secret-leak write guard. |
| `advocate_benchmark.py` | CLI. |

`backend/tests/test_benchmark.py` — 87 offline tests.

### Design properties that still hold

- **Real pipeline.** `AdvocateContextBuilder` → `RiderAdvocateAgent` /
  `DriverAdvocateAgent` → `AdvocateClaimVerificationService`. Prompts, parsing,
  validation and verification are the production classes.
  `AdvocateOrchestratorService` is deliberately unused: it collapses failures into a
  human-readable side result and discards the raw failure code and attempt count.
- **Provider-agnostic.** No provider-specific logic in the benchmark or the agents.
- **No secret in configuration.** The catalogue stores the env var *name*, never a
  value. `write_results` refuses to write if a live credential appears in either
  artefact.
- **Failure separation.** Provider failures, model-output failures, and internal errors
  are counted in three distinct buckets.
- **Unknown is not zero.** Missing tokens, an undefined verification rate, and absent
  pricing all stay `null`.
- **Dry-run gate.** `--execute` is required for any live call, and `execute_plan`
  refuses live execution without `allow_live=True`, so no test can reach a live model.
- **Semantic case selection.** Both criteria currently match two equivalent fixtures, so
  the lowest case id is chosen deterministically and the tie is recorded in the plan and
  both artefacts. `--require-unique-cases` turns that ambiguity into a hard failure.

## 10. Validation results

| Step | Result |
|---|---|
| Benchmark unit tests | **87 passed** |
| Benchmark + provider suites | **193 passed** |
| Existing backend suite | 263 passed, **79 failed** |
| Frontend build | clean — `tsc -b && vite build`, 37 modules |
| Gemini dry-run | **4 expected calls**, zero-network proven |

The 79 failures are pre-existing and unchanged: every one is a `StopIteration` on
`CASE-2026-1041`, a fixture id replaced by `DISP-*` in an earlier stage. They are the
known Bucket B set and were not touched.

Nothing shared with the frontend was touched: `app/benchmarks/` has no importer in
`app/main.py` or `app/api/`, and `frontend/src` contains no benchmark reference.

The dry-run was run with **all non-loopback egress blocked** and still printed its plan
and exited 0. The guard was separately proven non-vacuous: it blocks
`generativelanguage.googleapis.com` and a raw `socket.socket().connect(("8.8.8.8", 53))`
while still allowing loopback.

## 11. Current status

```
STAGE 4C INFRASTRUCTURE  : COMPLETE AND TESTED
GEMINI CHARACTERIZATION  : PENDING — QUOTA EXHAUSTED
RECORDED FAILED RUN      : RETAINED (4 provider failures, not quality failures)
OLLAMA / QWEN            : UNUSED FALLBACK — NOT RUN, NOT BENCHMARKED
STAGE 5                  : NOT STARTED (NOT BLOCKED)
```

As of the last recorded run, Gemini returned `SERVER_ERROR` × 2 and `RATE_LIMITED` × 2.
Quota was not re-probed, per the no-repeated-retry constraint. The final smoke run should
be executed when quota is available, using the exact command in §5.

## 12. Out of scope for this task

- Implementing Stage 5.
- Any change to `PolicyTwin`, refund calculations, advocate prompts, the Judge,
  cross-examination, fixture semantics, deterministic analysis, or the intake/database
  architecture.
- Deleting Ollama/provider-compatibility code.
- Declaring a "best" model.
