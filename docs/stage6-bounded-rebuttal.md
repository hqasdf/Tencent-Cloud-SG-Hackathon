# Stage 6 — Bounded Rebuttal / Cross-Examination + Explainability

**Status:** implemented and validated offline. Live model validation not yet executed.
**Scope:** exactly one bounded rebuttal round. No new autonomous agents, no unbounded conversations, no repeated debate.
**Core principle, unchanged:** CODE calculates facts. AI argues from facts. CODE checks AI claims. Judge decides only using trusted verified information. CODE validates Judge output and applies deterministic execution.

---

## 1. Stage 6 architecture

One new layer is inserted between claim verification and the Judge. Nothing else moves.

```
DisputeCase + CaseAnalysisResponse              (deterministic, already computed)
        |
        v
AdvocateContextBuilder -> AgentCaseContext       (Stage 4, reused unchanged)
        |
        +--> AdvocateOrchestratorService  ->  Rider + Driver advocates + claim verification
        |
        v
RebuttalContextBuilder -> RebuttalCaseContext    (allow-list: verified claims only)
        |
        +--> Rider rebuttal  ---\
        +--> Driver rebuttal ---/  one round, no loop
        |
        v
RebuttalValidationService -> verified / rejected  (Stage 6 trust check)
        |
        v
JudgeContextBuilder -> JudgeCaseContext          (now carries verified rebuttals)
        |
        v
JudgeAgent -> JudgeOutputValidationService -> ResolutionOrchestratorService
        |
        v
CaseResolutionResponse
   ├── rider / driver              (initial arguments)
   ├── rebuttals                   (bounded cross-examination)
   ├── judge                       (advisory)
   ├── deterministicResolution     (authoritative)
   ├── explanation                 (deterministic "why")
   └── counterfactual              (deterministic "what would change it")
```

**Where rebuttal sits, and why there.** The rebuttal layer is a sibling of the advocate layer, not part of it. `AdvocateOrchestratorService` is untouched, so the Stage 4 contract cannot regress. The rebuttals are sequenced by `ResolutionOrchestratorService`, which already owns stage ordering.

**One round, structurally.** Three independent mechanisms make a second round unrepresentable rather than merely disabled:

1. `MAX_REBUTTAL_ROUNDS = 1` with no configuration path to change it.
2. `RebuttalCaseContext` carries only *initial* verified claims. There is no field that could name a rebuttal as a target.
3. `RebuttalOrchestratorService.run()` contains no loop and no round parameter.

A rebuttal-to-a-rebuttal cannot be expressed, so it cannot occur.

## 2. New files

| File | Role |
| --- | --- |
| `backend/app/models/rebuttal.py` | All Stage 6 contracts: context, output, rejection, verified rebuttal, side result, run response |
| `backend/app/models/explanation.py` | Deterministic explanation and counterfactual contracts |
| `backend/app/agents/rebuttal_ids.py` | `RIDER-RB1` / `DRIVER-RB1` namespacing, assigned by code |
| `backend/app/agents/rebuttal_context_builder.py` | The Stage 6 allow-list projection |
| `backend/app/agents/rebuttal_agent.py` | One provider call, parsed. Provider-agnostic |
| `backend/app/agents/rebuttal_validation.py` | Deterministic trust check (11 rejection codes) |
| `backend/app/agents/monetary.py` | Shared monetary-prose detection (extracted from the Judge validator) |
| `backend/app/agents/prompts/rebuttal_common.md` | 21 numbered prompt rules |
| `backend/app/agents/prompts/rebuttal_output_schema.json` | JSON Schema, `additionalProperties: false` |
| `backend/app/services/rebuttal_orchestrator.py` | Runs both rebuttals once, verifies, records audit |
| `backend/app/services/explanation.py` | `DecisionExplanationService` |
| `backend/app/services/counterfactual.py` | `CounterfactualService` |
| `backend/tests/test_rebuttal.py` | 62 offline tests |
| `frontend/src/types/rebuttal.ts` | Stage 6 response types |
| `frontend/src/components/CrossExaminationPanel.tsx` | Cross-examination + trust sequence |
| `frontend/src/components/ExplanationPanel.tsx` | Deterministic explainability panels |

## 3. Modified files

| File | Change |
| --- | --- |
| `backend/app/models/judge.py` | `JudgeCaseContext` gains `verifiedRiderRebuttals` / `verifiedDriverRebuttals`. `JudgeOutput` / `JudgeResult` gain `consideredRiderRebuttalIds` / `consideredDriverRebuttalIds`. `CaseResolutionResponse` gains `rebuttals`, `explanation`, `counterfactual`. **Also fixed:** `JudgeSideClaims` had no camelCase aliases, so the Judge prompt mixed `verifiedClaims` with `verified_claim_count`. |
| `backend/app/agents/judge_context_builder.py` | `build(..., rebuttal_run=None)`; new `_rebuttals()` projects verified only |
| `backend/app/agents/judge_validation.py` | New `_check_rebuttals()`; 3 new issue codes. Monetary check now delegates to the shared module |
| `backend/app/agents/claim_verification.py` | Extracted public `fact_values()`, `trusted_facts()`, `values_match()` for reuse. Behaviour unchanged; private aliases retained |
| `backend/app/agents/prompts/judge_common.md` | New rules 12a–12h on rebuttal discipline |
| `backend/app/agents/prompts/judge_output_schema.json` | Two new optional properties |
| `backend/app/agents/providers/mock.py` | New `role == "rebuttal"` branch; mock Judge cites verified rebuttals |
| `backend/app/services/audit.py` | 10 new Stage 6 events + `SIDE_EVENTS` map |
| `backend/app/services/resolution_orchestrator.py` | Runs the rebuttal layer; splices rebuttal stages before the Judge; builds explanation and counterfactual |
| `backend/app/services/case_service.py` | Docstring only |
| `backend/app/api/cases.py` | Docstring only. Endpoint shape unchanged |
| `frontend/src/services/caseService.ts` | `ResolutionRunResult` extended |
| `frontend/src/components/CaseDetail.tsx` | Wires `TrustSequence`, `CrossExaminationPanel`, `ExplanationPanel` |
| `frontend/src/styles.css` | Stage 6 block |
| `backend/tests/test_judge.py` | One test updated: the audit-trail test now asserts the Judge events as an ordered subsequence, since Stage 6 legitimately prepends rebuttal events |

## 4. RebuttalContext fields

| Field | Note |
| --- | --- |
| `caseId`, `disputeType`, `currency` | identifiers |
| `ownSide` | names the side, so one prompt and one schema serve both |
| `deterministicFacts` | the same object the advocates saw |
| `evidence` | shared, not rebuilt |
| `applicablePolicy`, `policyEvaluation` | PolicyTwin output, not the recommendation |
| `ownVerifiedClaims` | this side's verified claims, namespaced |
| `opposingVerifiedClaims` | **the only valid rebuttal targets** |
| `allowedStances` | the closed stance vocabulary |
| `rebuttalRound`, `maxRebuttalRounds` | both 1, stated so the agent is told this is the only round |

Never present: rejected claims (either side), the other side's rebuttal, Judge output, `resolutionRecommendation`, `refundAmount`, confidence, `escalationReasons`, `analysisInput`, reputation, history. Note `resolutionMode` is also absent — unlike the Judge, a rebuttal does not need to know whether the case may be automated.

## 5. RebuttalOutput schema

```
{ side, responses: [ { targetClaimId, stance, reasoningSummary,
                       evidenceIds, policyRuleIds, assertedFacts } ],
  concessions, overallSummary }
```

- `stance` ∈ `CHALLENGE` | `CONCEDE` | `PARTIALLY_CONCEDE`. There is deliberately no "false" stance: only deterministic facts decide what is true.
- `concessions` holds **target claim IDs, not prose**, so it is machine-checkable against the stances actually taken.
- No amount field, no action field, no reasoning/deliberation field.
- Rebuttal IDs (`RIDER-RB1`) are **assigned by code**, densely over the responses that passed verification. The model never names its own rebuttal.

## 6. Validation rules

`RebuttalValidationService` — pure and offline. **A rejection rejects the whole response**; nothing is repaired or partially accepted.

| Code | Checks |
| --- | --- |
| `TARGET_CLAIM_NOT_FOUND` | target is a verified claim on the opposing side (covers "does not exist" and "failed verification", which are indistinguishable by design) |
| `TARGET_CLAIM_IS_OWN_SIDE` | a side may not rebut its own claim |
| `DUPLICATE_TARGET_CLAIM` | a target may be addressed once |
| `EVIDENCE_ID_NOT_FOUND` | every cited evidence ID exists in the trusted context |
| `POLICY_REF_NOT_FOUND` | every cited rule is applicable to this dispute |
| `UNKNOWN_ASSERTED_FACT` | the fact is verifiable for this dispute type (catches wrong-domain facts) |
| `FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS` | the asserted value matches the trusted value |
| `CONCESSION_CONTRADICTS_STANCE` | a concession must correspond to a conceding stance |
| `CONCESSION_NOT_A_TARGET` | a concession must name a response target |
| `EMPTY_REASONING_SUMMARY` | a summary was produced |
| `INVENTED_MONETARY_VALUE` | no monetary figure in prose absent from the trusted facts |

Fact verification reuses `fact_values()` and `values_match()` from Stage 4 rather than reimplementing them, so a rebuttal cannot assert something a claim could not.

## 7. One-round enforcement

| Mechanism | Effect |
| --- | --- |
| `MAX_REBUTTAL_ROUNDS = 1` | no configuration raises it |
| `RebuttalCaseContext` has no rebuttal field | a rebuttal target cannot be a rebuttal |
| `RebuttalOrchestratorService.run()` has no loop | one pass, two calls, no recursion |
| `RebuttalAgent` accepts only `RebuttalCaseContext` | the type system forbids feeding a rebuttal back in |

Pinned by `test_rebuttal_context_cannot_name_a_rebuttal_as_a_target` and `test_only_one_round_of_rebuttal_requests_is_made`.

## 8. Rebuttal failure behaviour

| Situation | Behaviour |
| --- | --- |
| Either **initial advocate** failed | No rebuttal runs at all. Both sides `NOT_RUN`. The Judge does not run either (`ADVOCATE_INPUT_INCOMPLETE`). |
| **One rebuttal** failed | The other still stands. The Judge still runs. The failed side reports `FAILED` with zero verified rebuttals. |
| **Both rebuttals** failed | The Judge still runs on the initial verified claims. |
| **All responses rejected** | `verifiedRebuttals = []`; the Judge still runs. Rejected responses stay visible for audit. |

The asymmetry between rows 1 and 2–4 is the point: the initial arguments **are** the case, while rebuttals are responses to it. A missing rebuttal is a side that declined to respond, not a case that cannot be decided. Nothing is ever fabricated to fill a gap.

## 9. Judge context changes

`JudgeCaseContext` gains `verifiedRiderRebuttals` and `verifiedDriverRebuttals`. **Only verified rebuttals.** There is no `rawRebuttals` and no `rejectedRebuttals` field, and `JudgeContextBuilder._rebuttals()` never reads `rejected_rebuttals` — the same treatment rejected claims already receive.

`JudgeOutput` gains `consideredRiderRebuttalIds` / `consideredDriverRebuttalIds`, both optional with an empty default, because reaching a conclusion without relying on any rebuttal is a legitimate answer.

New Judge validation codes: `UNKNOWN_REBUTTAL_ID`, `REBUTTAL_ID_BELONGS_TO_OTHER_SIDE`, `DUPLICATE_REBUTTAL_ID`. A rejected rebuttal resolves to `UNKNOWN_REBUTTAL_ID`, since it is not in the context.

The Judge prompt gained rules 12a–12h: initial claims are established; rebuttals are arguments to weigh; **a rebuttal does not automatically invalidate its target**; a concession carries weight; only context rebuttal IDs may be cited.

## 10. Deterministic explainability changes

`DecisionExplanationService` projects:

- `acceptedClaims` — every verified claim, marked with whether the Judge relied on it. Verified-but-uncited claims are included with `accepted: false`, because omitting them would imply the Judge considered only what it cited.
- `relevantRebuttals` — verified rebuttals only, marked `consideredByJudge`.
- `decisiveFacts` — measured facts paired with the policy threshold each was tested against.
- `policyRules` — the PolicyTwin evaluations, verbatim.
- `finalDeterministicAction`, `ruling`, `deterministicBasis` — copied, never paraphrased.
- `judgeAdvisoryDiffers` — surfaces a disagreement between the AI's recommendation and the code's action rather than hiding it.

The service has no model, no provider and no prompt. It is a pure function of its inputs.

## 11. Deterministic counterfactual changes

`CounterfactualService` varies exactly one thing: the measured value a policy rule compares against **its own** `requiredValue`. No threshold is defined, guessed or rounded here.

Directions are derived from how each rule is evaluated, not chosen:

| Rule | Passes when | Counterfactual direction |
| --- | --- | --- |
| `ROUTE_UNEXPLAINED_DEVIATION` | actual ≥ required | `BELOW` |
| `NO_SHOW_PICKUP_RADIUS` | actual ≤ required | `ABOVE` |
| `NO_SHOW_WAIT_TIME` | actual ≥ required | `BELOW` |

Getting a direction backwards would produce a fluent, confident, wrong explanation, so each is pinned by a test. The output carries `generatedBy: "DETERMINISTIC_ENGINE"` so a reader cannot mistake it for model output. There is no field for a hypothetical event — "maybe the rider called the driver" is not a counterfactual a deterministic engine can evaluate.

## 12. API changes

**Extending:** `POST /api/cases/{case_id}/resolution/run` now runs the full Stage 6 sequence and returns `rebuttals`, `explanation` and `counterfactual` as additional separated layers. No new parallel pipeline was created.

**Unchanged:** `POST /api/cases/{case_id}/advocates/run`. Pinned by `test_stage_4_advocate_endpoint_is_unchanged`, which asserts the response has no `rebuttals` and no `judge` key.

## 13. Frontend changes

No redesign. Three additions:

1. **`TrustSequence`** — renders `Advocates → Claim verification → Cross-examination → Rebuttal verification → Judge → Judge validation → Deterministic execution`, each labelled AI or CODE, so the repeated verification reads as deliberate.
2. **`CrossExaminationPanel`** — sits between the advocates and the Judge. Shows the round counter (`Round 1 of 1`), both sides, and per response: target claim, stance chip, reasoning, evidence refs, policy refs, and `VERIFIED`. Rejected responses render on a red background with the badge **"Rejected — not used by Judge"** plus the reason code and detail.
3. **`ExplanationPanel`** — "Why this decision? · CODE" and "What would change the decision? · CODE", each with a `Deterministic` badge, so a reader does not suspect a model invented the thresholds.

## 14. Audit events

New: `REBUTTAL_CONTEXT_BUILT`, `RIDER_REBUTTAL_STARTED/COMPLETED/FAILED`, `DRIVER_REBUTTAL_STARTED/COMPLETED/FAILED`, `REBUTTAL_VERIFIED`, `REBUTTAL_REJECTED`, `REBUTTALS_NOT_RUN`.

All Stage 5 events retained. Events are recorded **as each step happens**, not assembled afterwards, so a timestamp reflects the call. `JUDGE_CONTEXT_BUILT` now also records `rider_verified_rebuttals`, `driver_verified_rebuttals` and `rejected_rebuttals_included: false`.

Metadata only — never prompts, payloads, secrets or reasoning. A forbidden-key guard blocks `api_key`, `authorization`, `headers`, `prompt`, `raw_response`, `chain_of_thought`, `reasoning` and similar.

## 15. Tests added

`backend/tests/test_rebuttal.py` — **62 tests**, all offline, covering all 32 required concerns:

| Concern | Representative test |
| --- | --- |
| 1–2. Each side sees only the other's verified claims | `test_rider_sees_only_verified_driver_claims`, `test_driver_sees_only_verified_rider_claims` |
| 3. Rejected claims never enter rebuttal context | `test_rejected_initial_claims_never_enter_rebuttal_context` |
| 4–6. Target discipline | `test_unknown_target_claim_is_rejected`, `test_own_side_target_is_rejected`, `test_the_same_claim_cannot_be_targeted_twice` |
| 7–8. Evidence / policy | `test_unknown_evidence_id_is_rejected`, `test_unknown_policy_rule_is_rejected` |
| 9–10. Facts | `test_wrong_asserted_deterministic_fact_is_rejected`, `test_wrong_domain_fact_is_rejected` |
| 11. Valid rebuttal verified | `test_valid_rebuttal_is_verified` |
| 12–15. Failure tolerance | `test_zero_verified_rebuttals_still_allows_the_judge`, `test_rider_rebuttal_failure_still_allows_the_judge`, `test_driver_rebuttal_failure_still_allows_the_judge`, `test_both_rebuttals_failing_still_allows_the_judge` |
| 16–17. Advocate failure blocks everything | `test_initial_rider_failure_blocks_rebuttals_and_judge`, `test_initial_driver_failure_blocks_rebuttals_and_judge` |
| 18. Rejected rebuttal never enters Judge context | `test_rejected_rebuttal_never_enters_judge_context` |
| 19–20. Judge reference rules | `test_judge_can_reference_a_verified_rebuttal`, `test_judge_cannot_reference_a_rejected_rebuttal`, `test_judge_cannot_cite_a_rebuttal_from_the_other_side` |
| 21–24. Determinism preserved | `test_human_review_is_unchanged_by_rebuttals`, `test_refund_confidence_and_escalation_are_unchanged`, `test_rebuttal_prose_cannot_move_the_deterministic_result` |
| 25. Counterfactuals from thresholds | `test_counterfactual_thresholds_come_from_policy_evaluation`, `test_counterfactual_directions_match_how_each_rule_is_evaluated`, `test_counterfactual_contains_no_hypothetical_evidence` |
| 26. No chain-of-thought | `test_response_contains_no_chain_of_thought_field`, `test_rebuttal_output_schema_has_no_reasoning_field` |
| 27. No secrets in audit | `test_audit_never_carries_a_prompt_a_secret_or_reasoning` |
| 28. Mock pipeline | `test_mock_mode_drives_the_full_stage_6_pipeline` |
| 29. Stage 4 compatibility | `test_stage_4_advocate_endpoint_is_unchanged` |
| 30–32. Malformed / 429 / 503 | `test_malformed_rebuttal_json_fails_safely`, `test_provider_429_on_rebuttal_fails_safely`, `test_provider_503_on_rebuttal_fails_safely` |

Plus one-round enforcement, concession consistency, audit event coverage and pipeline ordering.

## 16–21. Validation results

| Step | Result |
| --- | --- |
| **A. Stage 6 tests** | **62 passed** |
| **B. Stage 5 Judge tests** | **42 passed** |
| **C. Stage 4 advocate tests** | 79 failed / 31 passed — the unchanged Bucket B set |
| **D. Full backend suite** | **79 failed, 367 passed** — 79 = unchanged Bucket B; 367 = 305 baseline + 62 new. **No regression.** |
| **E. Frontend build** | clean, **40 modules**, 3.48s, no type errors |
| **F. Browser (mock)** | Cross-examination renders: `Round 1 of 1`, 3 verified rows, stances `CHALLENGE/CHALLENGE/CONCEDE`, targets `DRIVER-D1/RIDER-R1/RIDER-R3`, conceded `RIDER-R3`; trust sequence shows AI/CODE alternating; both explanation panels badged `Deterministic`; counterfactual direction `Would need to fall below`; 0 console errors, 0 page errors |
| **F2. Browser (rejected path)** | Rejected row renders with badge `Rejected — not used by Judge`, reason `EVIDENCE_ID_NOT_FOUND`, detail text; background `rgb(253,232,229)` vs verified `rgb(251,249,244)` — visually distinct; rejected rebuttal ID **absent** from the Judge panel; 0 errors |
| **G. Zero-egress proof** | Stage 6: **62 passed identical** with and without the guard. Full suite with guard: **79 failed / 367 passed — identical**. Guard proven non-vacuous: blocked `generativelanguage.googleapis.com`, blocked a raw `socket.connect(('8.8.8.8', 53))`, allowed loopback |

## 22. Whether any live Gemini call occurred

**No.** Every call in this stage was served by the injected stub or the mock provider. The zero-egress proof confirms no non-loopback connection was possible during any test run. No live Gemini call has been made in Stage 6.

## 23. Expected calls for one live Stage 6 run

| Call | Role |
| --- | --- |
| 1 | Rider advocate |
| 2 | Driver advocate |
| 3 | Rider rebuttal |
| 4 | Driver rebuttal |
| 5 | Judge |
| **Total** | **5** |

Not executed. No connectivity or model-list probes were performed. Awaiting explicit approval.

## 24. Remaining limitations

1. **No live Stage 6 validation.** All evidence is offline.
2. **Replay support (§20) was not implemented.** It would require a stored-artifact seam keyed on case ID + deterministic analysis hash + schema version, plus invalidation logic. That is a real subsystem, and building it inside this task would have been more invasive than the benefit justified — the same conclusion the brief anticipates. **Consequence:** a live Stage 6 run costs 5 calls, not 3, because the advocate outputs cannot be replayed. This is the single most valuable follow-up if quota stays tight.
3. **The mock provider never produces a rejected rebuttal**, so the default end-to-end flow does not exercise the rejection path. That gap is closed by an explicit browser check that serves a mutated payload, and by unit tests — but it is a real asymmetry between mock and live behaviour.
4. **`INVENTED_MONETARY_VALUE` is regex-based prose detection**, inherited from Stage 5. A spelled-out amount evades it. The absence of the field remains the primary control.
5. **Counterfactuals cover threshold rules only.** Evidence-presence and consistency rules have no meaningful "what would change it" statement and are excluded rather than given a misleading one.
6. **The audit trail is in-memory and per-request.** Not persisted.
7. **Not implemented by design** (per the brief): Fraud / Evidence / Image / Safety agents, multiple Judges, Judge voting, repeated debate, self-reflection loops, policy rewriting, historical guilt scoring, autonomous policy learning.
8. **79 stale Bucket B tests remain failing and untouched**, pending separate approval.

## 25. Confirmation — rejected claims and rebuttals never become trusted Judge input

**Confirmed, structurally.**

- `JudgeContextBuilder._side()` reads `verified_claims` and **never reads `rejected_claims`**.
- `JudgeContextBuilder._rebuttals()` reads `verified_rebuttals` and **never reads `rejected_rebuttals`**.
- `JudgeCaseContext` has no field able to carry either.
- The audit trail records `rejected_claims_included: false` and `rejected_rebuttals_included: false` on every run, making the guarantee observable.
- Pinned by `test_rejected_initial_claims_never_enter_rebuttal_context`, `test_rejected_rebuttal_never_enters_judge_context`, and the browser check asserting the rejected rebuttal ID is absent from the Judge panel.

Rejected material remains fully visible to human reviewers in the advocate and cross-examination panels, marked as not used by the Judge.

## 26. Confirmation — refund, confidence and escalation remain deterministic

**Confirmed.** No deterministic calculation was modified in Stage 6.

- `ResolutionEngine`, `ConfidenceEngine`, `EscalationEngine`, `PolicyTwin`, the refund formulas, the confidence formula and the escalation thresholds were **not edited**.
- `DeterministicResolution` remains a read-only projection of `CaseAnalysisResponse`.
- Rebuttals cannot introduce money: no amount field exists, and an invented figure in prose is rejected.
- `explanation` and `counterfactual` are pure projections; neither can alter a value.
- `HUMAN_REVIEW` is still enforced by code. Rebuttals cannot convert it: `test_human_review_is_unchanged_by_rebuttals` asserts `status == PENDING_HUMAN_REVIEW`, `executable is False`, and `recommended_action == HUMAN_REVIEW`.
- Pinned by `test_refund_confidence_and_escalation_are_unchanged` across all three dispute modes, and by `test_rebuttal_prose_cannot_move_the_deterministic_result`, which asserts a demanding rebuttal changes nothing.
