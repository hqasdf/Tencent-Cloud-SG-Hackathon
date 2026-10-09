# RydeResolve — fresh LIVE Gemini validation, 2026-10-05

**Session:** 2026-10-05 10:05:43–10:05:44 (GMT+8; ledger timestamps UTC)
**Model under test:** `gemini-3.8-flash`, `AGENT_MODE=real`, `AGENT_PROVIDER=openai_compatible`
**Result:** **STOPPED at the first Rider Advocate HTTP attempt: 429 RATE LIMITED.** No retry, no later stage, no later case, and no separate health probes.

## Verdicts for this fresh session

| Validation stage | Fresh-session result | Interpretation |
|---|---|---|
| Stage 4 Live Advocates | **BLOCKED** | Rider Advocate returned 429 with no model output; Driver was not attempted. The previous session had Stage 4 **PASS**, but this session did not re-establish it or restore its lost artefact. |
| Stage 5 Live Judge | **BLOCKED** | Judge was never called; JudgeOutputValidationService did not receive live output. |
| Stage 6 Live Route Pipeline, DISP-005 | **BLOCKED** | Stopped on the first call; no rebuttals, Judge, or deterministic execution inside the orchestrator. |
| Stage 6 Live No-show Pipeline, DISP-002 | **BLOCKED / NOT ATTEMPTED** | Required previous case to succeed completely. |
| Live HUMAN_REVIEW Gate, DISP-003 | **BLOCKED / NOT ATTEMPTED** | Required previous cases to succeed completely. |
| FULL_AI live replay | **BLOCKED / NOT ATTEMPTED** | No validated live artefacts from this session; replay would have no complete set. |

The 429 is **provider-side rate limiting**, not a hallucination, a claim-verification failure, a prompt/schema failure, or evidence that the model answered incorrectly. No fresh stage is marked PASS without a real, validated model answer.

## Offline preflight and bounded execution

Preflight confirmed `mode=real`, `provider=openai_compatible`, `model=gemini-3.8-flash`, credential present (never displayed). Configured `AGENT_MAX_RETRIES=2` was **capped to one** by the validation harness. No provider/model/prompt/schema was switched to GPT or anything else. No separate health probe or model-list request ran. The ledger from the previous session was archived together with its DISP-005 and session results; the new ledger started at **0/40**. The replay store and harness scanner were confirmed to point to the same default artefact directory, which started empty.

The run requested `--pace-seconds 60` and `--capture` with **DISP-005 only**. Pacing never became relevant: the first call returned 429. The live StopRule was enforced **in the guarded HTTP client, on that attempt**, raised its uncatchable stop condition, and prevented the provider from retrying the 429 or the orchestrator from advancing to Driver Advocate. The stored report has `stop.stage=RIDER_ADVOCATE`; the ledger contains only `RIDER_ADVOCATE`.

| Metric | Observed |
|---|---:|
| Logical Gemini calls | **1** (Rider Advocate) |
| External HTTP attempts | **1**, of maximum 40 |
| Remaining attempt headroom | **39** (not a target for further calls) |
| Retries | **0** |
| HTTP 200 / 429 / 503 | **0 / 1 / 0** |
| Other 5xx / timeout / network errors | **0 / 0 / 0** |
| Rider HTTP latency | **585 ms** |
| DISP-005 harness wall time | **809 ms** |
| Input / output / total / unattributed tokens | **unknown / unknown / unknown / unknown** — 429 supplied no validated model response; unknown is not zero |

No claims or rebuttals were generated, verified, or rejected **by this session**. Those counts reflect that no model response was accepted, not a zero-quality judgement on Gemini. No Rider recommended outcome exists. No Rider/Driver evidence references, policy references, or asserted facts were returned for this session. Judge recommended outcome, cited claim/rebuttal/evidence/policy IDs, and Judge validation issues are **not applicable** (Judge never ran, so zero reported issues must not be read as successful validation).

## Deterministic integrity

The harness independently recomputed the canonical DISP-005 fixture before and after the aborted run. The orchestrator itself was interrupted before constructing its `deterministicResolution`, so there is **no completed pipeline execution** to compare; the following checks are the harness's before/after recomputation:

| Check | Before | After |
|---|---|---|
| `deterministic_analysis_hash` | `33899cc478724950b44333590819eaa69245e58ba9572783a9511464dc39e407` | **identical** |
| Facts and policy evaluation | canonical snapshot | **identical** |
| Recommended action | `NO_REFUND` | `NO_REFUND` |
| Refund | 0.0 SGD | 0.0 SGD |
| Confidence | 0.95 | 0.95 |
| Resolution mode | `AUTO_RESOLVE` | `AUTO_RESOLVE` |
| Escalation reasons | `[]` | `[]` |

`deterministicUnchanged=true`, `caseFixtureUnchanged=true`, `deterministicChanges={}`. `authoritativeMatchesBaseline` is **not available** because there was no completed pipeline result; do not infer it from the harness-only comparison. There was no Judge context to inspect, and thus no *live* accepted/rejected-material exclusion check this session.

## Artefacts and replay

| Expected file | Written by this session | Live eligible |
|---|---|---|
| `advocates.json` | **No** | Not applicable |
| `rebuttals.json` | **No** | Not applicable |
| `judge.json` | **No** | Not applicable |

The `backend/replay_artifacts/` directory is empty. The previous run's live artefacts were lost in the offline rehearsal incident recorded in `docs/live-stop-rule-fix.md`; this attempt did **not** recover them. The previous run's **metrics/report** were preserved and archived, but they are not replayable raw model output. No FULL_AI replay was attempted without a complete live capture.

## Safety checks before the live call

This brief requires a failed rebuttal capture to be **audit-storable but not live eligible**, even if `capturedMode`, `capturedProvider`, and `capturedModel` are present. The base provenance predicate alone did not enforce that distinction. Before the live call, the rebuttal artefact schema and harness scanner were adjusted so both sides must be `COMPLETE` with raw output for `liveEligible=true`; otherwise a captured failure is **audit-only / `liveEligible=false`**. The harness now records verified claims' evidence/policy/structured-fact references and verified rebuttals' targets/stances/evidence/policy/structured-fact references when those stages complete, without changing production prompts or the provider.

The harness also now blocks the **next case** after any incomplete, non-provider-failed stage, invalid Judge result, changed deterministic result, or missing/non-eligible fresh capture. A successful `PENDING_HUMAN_REVIEW` is accepted only when `executable=false`; it can never be mistaken for an automated result. This complements the per-attempt provider StopRule; it does not change production orchestration.

Offline, under a **non-vacuous socket egress guard**: targeted capture/stop tests **61 passed**; the full backend suite **502 passed / 79 failed**. The 79 are the existing stale-fixture Bucket B failures, unchanged (prior baseline 494 passed / 79 failed; +8 new assertions/tests). The guard was independently shown to block non-loopback DNS. No external network was used for these tests.

## Next action

**This live session is closed.** Do not retry after this 429 within it and do not advance to DISP-002 or DISP-003. A future authorised session may retry DISP-005 with a fresh ledger when the provider is available, then evaluate the rest of the sequence only after a complete, validated route pipeline and offline FULL_AI replay. The previously exposed demo credential remains unrotated; the user had accepted using it for this validation, and its value was neither printed nor included here.
