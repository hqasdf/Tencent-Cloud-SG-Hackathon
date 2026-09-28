# Stage 5 — Judge Agent

**Status:** implementation complete and validated offline. Live model validation not yet executed.
**Model policy:** Gemini 3.8 Flash is the project-selected cloud model. Ollama/Qwen is an unused fallback and was not used.
**Core principle, unchanged:** CODE calculates facts. AI argues from facts. CODE checks AI claims. The Judge decides using only verified information.

---

## 1. Judge architecture

The Judge is the third AI role and the first one whose output a human is meant to act on, so the design treats it as a *bounded advisor* rather than a decision-maker.

The pipeline is a sequence of small collaborators, each independently testable:

```
DisputeCase + CaseAnalysisResponse        (deterministic, already computed)
        |
        v
AdvocateContextBuilder  ->  AgentCaseContext        (Stage 4, reused unchanged)
        |
        +--> AdvocateOrchestratorService  ->  Rider + Driver advocates + claim verification
        |
        v
JudgeContextBuilder  ->  JudgeCaseContext           (allow-list projection, second boundary)
        |
        v
JudgeAgent  ->  one LlmProvider call, parsed        (provider-agnostic, no Gemini-specific code)
        |
        v
JudgeOutputValidationService  ->  valid / issues    (pure, offline, rejects the whole response)
        |
        v
ResolutionOrchestratorService  ->  CaseResolutionResponse
                                   ├── judge                    (advisory)
                                   └── deterministicResolution  (authoritative)
```

`ResolutionOrchestratorService` only sequences. It performs no calculation of its own and cannot alter a deterministic value: `DeterministicResolution` is a direct projection of the `CaseAnalysisResponse` it was handed.

Two structural guarantees carry most of the safety weight:

- **`extra="forbid"` on the Judge output schema.** The Judge has no amount field. An invented `refundAmount` key is a schema failure, not a silently discarded extra. "The Judge cannot state an amount" is a property of the type, not a request in a prompt.
- **The allow-list context builder.** `JudgeCaseContext` is constructed field by field from trusted sources. There is no code path by which a rejected claim or the deterministic answer can reach the prompt.

---

## 2. New files

| File | Role |
| --- | --- |
| `backend/app/models/judge.py` | All Stage 5 contracts: context, output, result, deterministic projection, audit event, response envelope |
| `backend/app/agents/judge_claims.py` | Claim-ID namespacing (`RIDER-C1` / `DRIVER-C1`), index building |
| `backend/app/agents/judge_context_builder.py` | The allow-list projection — Stage 5's safety boundary |
| `backend/app/agents/judge_agent.py` | One provider call; parse, schema-validate, raise typed errors |
| `backend/app/agents/judge_validation.py` | Deterministic trust check on Judge output (12 issue codes) |
| `backend/app/agents/prompts/judge_common.md` | 18 numbered prompt rules (authority hierarchy, evidence/policy/claim discipline, money, human review, honesty) |
| `backend/app/agents/prompts/judge_output_schema.json` | JSON Schema, `additionalProperties: false`, 11 required fields |
| `backend/app/services/audit.py` | `AuditTrail` + the 8 stable event names + forbidden-metadata guard |
| `backend/app/services/resolution_orchestrator.py` | Sequences Stage 4 + Judge + deterministic projection |
| `backend/tests/test_judge.py` | 42 offline tests |
| `frontend/src/types/judge.ts` | Judge response types |
| `frontend/src/components/JudgePanel.tsx` | Two-column advisory/authoritative presentation + audit strip |

## 3. Modified files

| File | Change |
| --- | --- |
| `backend/app/services/advocate_orchestrator.py` | `run(case, analysis, context=None)` — accepts a shared `AgentCaseContext`. Optional, so Stage 4 callers are unaffected. |
| `backend/app/agents/providers/mock.py` | Added a `role == "judge"` branch. Mock Judge reasoning deliberately contains no currency token. |
| `backend/app/api/cases.py` | Added `POST /cases/{case_id}/resolution/run`. `/advocates/run` untouched. |
| `backend/app/services/case_service.py` | Added `get_resolution_run()`. |
| `frontend/src/services/caseService.ts` | `ResolutionRunResult` type + `runResolution()`. |
| `frontend/src/components/CaseDetail.tsx` | Wired `JudgePanel`; relabelled the mislabelled "Judge decision" panel to "Deterministic decision · CODE". |
| `frontend/src/App.tsx` | `resolutionRun` / `resolutionRunning` / `resolutionError` state, cleared per case. |
| `frontend/src/styles.css` | Stage 5 Judge block. |

---

## 4. `JudgeCaseContext` fields

The complete and only set of information the Judge may see:

| Field | Source | Note |
| --- | --- | --- |
| `caseId` | case | identifier only |
| `disputeType` | case | `route_deviation` / `no_show_charge` |
| `currency` | case | needed to read facts correctly |
| `deterministicFacts` | `AgentCaseContext` | the same object the advocates saw |
| `evidence` | `AgentCaseContext` | shared, not rebuilt |
| `applicablePolicy` | policy registry | rule IDs + descriptions |
| `policyEvaluation` | `AgentCaseContext` | PolicyTwin's rule-by-rule evaluation, **not** the recommendation |
| `rider` | `AdvocateSideResult` | **verified claims only** |
| `driver` | `AdvocateSideResult` | **verified claims only** |
| `resolutionMode` | analysis | `AUTO_RESOLVE` / `HUMAN_REVIEW` — *whether* automation is allowed, never *what* the answer is |
| `allowedOutcomes` | `JUDGE_ALLOWED_OUTCOMES` | the closed outcome vocabulary for this dispute type |

Deliberately absent: `resolutionRecommendation`, `recommendedAction`, `ruling`, `refundAmount`, confidence, `escalationReasons`, `analysisInput`, rejected claims, frontend decision text, hidden Judge criteria, other cases, conversation history.

`JudgeSideClaims` also states `verifiedClaimCount` and `hasVerifiedClaims` explicitly, so the zero-claim case is unmistakable rather than inferable.

## 5. `JudgeOutput` schema

11 required fields, `additionalProperties: false`:

`status`, `recommendedOutcome`, `acceptedRiderClaimIds`, `acceptedDriverClaimIds`, `rejectedRiderClaimIds`, `rejectedDriverClaimIds`, `reasoningSummary`, `evidenceIds`, `policyRuleIds`, `uncertainties`, `requiresHumanReview`

There is **no amount field and no action field**. `reasoningSummary` is a concise human-facing summary — not chain-of-thought, and the prompt never asks for deliberation.

Outcome vocabulary is exactly the non-escalation set `ResolutionEngine` can emit, so no second remedy vocabulary exists:

- `route_deviation` → `NO_REFUND`, `PARTIAL_REFUND`, `FULL_FARE_DIFFERENCE_REFUND`
- `no_show_charge` → `UPHOLD_CANCELLATION_CHARGE`, `REFUND_CANCELLATION_CHARGE`

`HUMAN_REVIEW` is excluded on purpose: escalation is deterministic, and a model that could recommend it could also appear to authorise it.

> **Note on the brief:** the brief named `FULL_REFUND`. That value does not exist in this repository, so the implementation uses the repo's `FULL_FARE_DIFFERENCE_REFUND` rather than inventing a parallel vocabulary. This is a deliberate, documented divergence.

## 6. Judge validation rules

`JudgeOutputValidationService.validate(output, context) -> JudgeValidationResult`. Pure and offline. **A validation failure rejects the whole response** — nothing is repaired, nothing partially accepted, no invalid reference silently dropped.

| Issue code | Checks |
| --- | --- |
| `OUTCOME_NOT_ALLOWED_FOR_DISPUTE` | outcome is in the closed vocabulary for this dispute type |
| `HUMAN_REVIEW_OVERRIDE_ATTEMPTED` | Judge did not return `PENDING_HUMAN_REVIEW` when code required it |
| `HUMAN_REVIEW_FLAG_INCONSISTENT` | `requiresHumanReview` contradicts `status` |
| `UNKNOWN_CLAIM_ID` | every cited claim is a VERIFIED claim in this case |
| `CLAIM_ID_BELONGS_TO_OTHER_SIDE` | a `DRIVER-*` ID cannot be cited in a Rider field (and vice versa) |
| `CLAIM_BOTH_ACCEPTED_AND_REJECTED` | a claim cannot be both |
| `DUPLICATE_CLAIM_ID` | no repeated citations |
| `UNKNOWN_EVIDENCE_ID` | evidence exists in the trusted context |
| `UNKNOWN_POLICY_RULE_ID` | rule is applicable to this dispute |
| `EMPTY_REASONING_SUMMARY` | a summary was produced |
| `MISSING_CLAIM_CITATIONS` | claims were assessed but no evidence and no policy rule was cited |
| `INVENTED_MONETARY_VALUE` | no monetary figure in prose that is absent from the trusted facts |

The last rule closes the one remaining route for money to appear: the schema removes the field, so prose is the only place left. Amounts are compared to the deterministic fact values with a $0.005 tolerance, so quoting a supplied figure is allowed while inventing one is not.

## 7. HUMAN_REVIEW behaviour

When deterministic analysis sets `resolutionMode = HUMAN_REVIEW`:

1. The Judge **must not** issue an executable final verdict. Validation rejects a response that claims `COMPLETE`, and rejects one whose `requiresHumanReview` flag is inconsistent.
2. `JudgeResult.status` is `PENDING_HUMAN_REVIEW` **regardless of what the model returned** — the status is computed by code, not copied.
3. `executable` is `false`. A recommendation is only ever marked executable when code has already decided the case may be automated. The Judge never promotes its own advice.
4. The Judge may still supply `reasoningSummary`, claim assessments, policy references and `uncertainties` — the analytical content is preserved for the human reviewer.
5. The deterministic `resolutionMode`, `refundAmount` and confidence pass through untouched.

The pipeline stage maps `PENDING_HUMAN_REVIEW` → `COMPLETE`, because the stage *did* run successfully and reached a non-executable conclusion. Reporting otherwise would conflate "the Judge could not run" with "the Judge ran and code requires a human sign-off". The distinction stays visible in `judge.status`.

## 8. Advocate failure behaviour

If **either** advocate side is not `COMPLETE`, the Judge does not run:

- `status = NOT_RUN`, `skipReason = ADVOCATE_INPUT_INCOMPLETE`
- `recommendedOutcome = null`
- audit records `JUDGE_NOT_RUN` with the failing sides

Rationale: deciding a dispute with one party absent would misrepresent the record. `NOT_RUN` is deliberately distinct from `FAILED` — nothing was attempted, so there is nothing to retry, whereas "the Judge failed" invites a retry that cannot help.

If the **Judge itself** fails (provider error, malformed JSON, truncated output, or rejected validation), the advocates, their verified claims and the entire deterministic analysis remain intact and visible. The Judge block reports `FAILED`. No result is fabricated, and a deterministic human-review decision survives untouched.

## 9. API changes

**Added:** `POST /api/cases/{case_id}/resolution/run` → `CaseResolutionResponse`

Sequence: deterministic analysis → Rider advocate → Driver advocate → claim verification → Judge → Judge output validation → deterministic remedy. POST because it may trigger external model calls and incur token cost.

The response keeps the two halves as **separate top-level fields** (`judge`, `deterministicResolution`). Merging them would make an advisory recommendation look like an authorisation.

**Unchanged:** `POST /api/cases/{case_id}/advocates/run` remains the Stage 4 contract. Stage 5 extends the workflow rather than replacing it. Pinned by `test_stage_4_advocate_endpoint_is_unchanged`.

## 10. Frontend changes

`JudgePanel.tsx` renders two visually distinct columns rather than redesigning the UI:

- **`JUDGE AGENT · AI REASONING`** — dashed accent border, advisory. Shows status, recommended outcome, accepted/rejected claim IDs, evidence IDs, policy rule IDs, reasoning summary, uncertainties. **No monetary value appears in this column.**
- **`DETERMINISTIC EXECUTION · CODE`** — solid accent border, authoritative. Shows final action, refund amount, confidence, human-review requirement.

An audit strip below lists the recorded events. The legacy panel previously mislabelled "Judge decision" is relabelled "Deterministic decision · CODE", removing a genuine ambiguity about which layer decides.

## 11. Audit events

`AuditTrail` records ordered, timestamped events with **metadata only — never payloads**:

`JUDGE_CONTEXT_BUILT`, `JUDGE_STARTED`, `JUDGE_COMPLETED`, `JUDGE_FAILED`, `JUDGE_OUTPUT_VALIDATED`, `JUDGE_OUTPUT_REJECTED`, `JUDGE_NOT_RUN`, `PENDING_HUMAN_REVIEW`

`JUDGE_CONTEXT_BUILT` records context *size and shape* (`evidence_count`, `policy_rule_count`, verified claim counts, `resolution_mode`, `rejected_claims_included=False`) — not the context itself. A forbidden-key guard blocks `api_key`, `authorization`, `headers`, `prompt`, `raw_response`, `chain_of_thought`, `reasoning` and similar keys from ever entering the trail. This is checked rather than assumed, because "we did not log the key" is exactly the claim that decays silently.

## 12. Tests added

`backend/tests/test_judge.py` — **42 tests**, all offline. Coverage maps to the brief's 20 required concerns:

| Concern | Representative tests |
| --- | --- |
| Judge sees only verified claims | `test_judge_receives_only_verified_claims` |
| Rejected claims never trusted input | `test_rejected_claims_never_appear_as_trusted_judge_context`, `test_the_judge_context_serialized_to_the_prompt_has_no_rejected_claims` |
| No forbidden fields leak | `test_judge_context_contains_no_forbidden_fields`, `test_the_judge_does_not_receive_the_refund_amount` |
| Zero-claim case explicit | `test_judge_context_declares_the_zero_claim_case_explicitly` |
| Invented claim/evidence/policy IDs rejected | `test_invented_claim_id_is_rejected`, `test_invented_evidence_id_is_rejected`, `test_invented_policy_id_is_rejected` |
| Outcome vocabulary enforced | `test_unsupported_outcome_is_rejected` |
| Cross-side / duplicate / contradictory citations | `test_claim_cited_for_the_wrong_side_is_rejected`, `test_duplicate_claim_id_is_rejected`, `test_claim_both_accepted_and_rejected_is_rejected` |
| Money discipline | `test_an_invented_refund_amount_in_prose_is_rejected`, `test_quoting_a_supplied_deterministic_value_is_allowed`, `test_an_invented_amount_field_fails_schema_validation`, `test_an_invented_amount_cannot_change_the_deterministic_refund` |
| HUMAN_REVIEW cannot be overridden | `test_human_review_case_cannot_become_executable`, `test_human_review_case_returns_pending_status_when_the_judge_complies` |
| Advocate failure blocks the Judge | `test_one_advocate_failed_means_the_judge_does_not_run`, `test_both_advocates_failed_means_the_judge_does_not_run` |
| Failure isolation | `test_malformed_judge_json_produces_a_safe_failed_state`, `test_judge_provider_429_produces_a_safe_failed_state`, `test_judge_provider_503_produces_a_safe_failed_state`, `test_a_judge_failure_leaves_everything_deterministic_visible`, `test_a_judge_failure_does_not_erase_a_human_review_decision` |
| Both dispute types succeed | `test_a_valid_route_case_judge_succeeds`, `test_a_valid_no_show_case_judge_succeeds` |
| Determinism untouched | `test_deterministic_refund_confidence_and_escalation_ignore_judge_prose` |
| Prompt carries binding rules | `test_the_judge_prompt_states_the_binding_rules` |
| Mock mode end-to-end | `test_mock_mode_drives_the_judge_end_to_end`, `test_mock_judge_is_deterministic_across_calls`, `test_mock_judge_output_passes_real_validation_for_both_dispute_types` |
| Stage 4 API compatibility | `test_stage_4_advocate_endpoint_is_unchanged` |
| Stage 5 API | `test_stage_5_resolution_endpoint_returns_both_halves`, `test_resolution_endpoint_rejects_an_unknown_case` |
| Audit trail | `test_audit_trail_records_the_full_success_sequence`, `test_audit_trail_records_a_rejection`, `test_audit_metadata_never_carries_a_prompt_or_a_secret`, `test_audit_records_that_rejected_claims_were_excluded` |

## 13. Test results

```
tests/test_judge.py ................ 42 passed, 1 warning in 0.81s
```

## 14. Backend regression result

```
79 failed, 305 passed, 1 warning in 7.62s
```

Identical to the pre-Stage-5 baseline: the 79 failures are the unchanged stale Bucket B set (untouched, unapproved), and 305 = 263 baseline passes + 42 new Judge tests. **Stage 5 introduced no regression.**

## 15. Frontend build result

```
tsc -b && vite build
✓ 38 modules transformed
dist/assets/index-D5BYy9RS.js   256.52 kB │ gzip: 77.85 kB
✓ built in 3.63s
```

Clean build, no type errors.

## 16. Whether any live Gemini call occurred

**No live Gemini call was made while implementing or validating Stage 5.**

- No Judge run artifacts exist under `backend/benchmark_results/` — the only files there are the Stage 4C benchmark run.
- All 42 Judge tests are offline, driven by stub and mock providers.
- Verified by socket-level egress guard: `42 passed` identically with and without the guard installed.

For full transparency: earlier in this project, two inadvertent live calls occurred from an ad-hoc `TestClient` script run outside pytest, where `conftest.py` isolation did not apply. That incident is recorded and was reported honestly. No such call has occurred in Stage 5.

## 17. Expected calls for live validation

A live Judge validation costs **3 external calls per case**:

| Call | Role |
| --- | --- |
| 1 | Rider advocate |
| 2 | Driver advocate |
| 3 | Judge |

The Judge needs both advocates to have produced verified claims, and no replay mechanism exists for stored advocate outputs, so the advocate calls cannot be avoided. A single-case live validation is therefore **3 calls**, not 1.

Not yet executed. Per the brief, the implementation must be fully validated offline first, and the expected call count must be shown before execution — both now satisfied.

## 18. Remaining Stage 5 limitations

1. **No live Judge validation has been run.** Everything above is offline evidence. The Judge has never seen a real model response end to end.
2. **Audit trail is in-memory and per-request.** The event vocabulary is stable; persistence is not implemented.
3. **No replay of stored advocate outputs.** This is what makes a live Judge validation cost 3 calls instead of 1.
4. **Judge cost and latency are unmeasured** against a real provider.
5. **The `INVENTED_MONETARY_VALUE` check is regex-based prose detection.** It catches currency-token patterns; a spelled-out amount ("two hundred dollars") would not be caught. The schema removes the field, so this is defence in depth rather than the primary control.
6. **Not implemented by design** (per the brief): cross-examination, rebuttal rounds, fraud agent, image agent, reputation scoring, feedback learning, multi-model/voting Judge, automatic policy rewriting.
7. **79 stale Bucket B tests remain failing and untouched**, pending separate approval.

## 19. Confirmation — refund, confidence and escalation remain deterministic

**Confirmed.** No deterministic calculation was modified in Stage 5.

- `DeterministicResolution` is a direct read-only projection of `CaseAnalysisResponse`: `ruling`, `recommendedAction`, `refundAmount`, `currency`, `resolutionMode`, `confidence`, `escalationReasons`, `explanation`, `counterfactualExplanation`.
- `ResolutionOrchestratorService` contains no arithmetic. The Judge's output is never an input to any of these values.
- `ResolutionEngine`, `ConfidenceEngine`, `EscalationEngine`, `PolicyTwin` and the refund formulas were not edited.
- The Judge has no amount field and cannot emit one; prose containing an invented amount is rejected outright.
- Pinned by `test_deterministic_refund_confidence_and_escalation_ignore_judge_prose` and `test_an_invented_amount_cannot_change_the_deterministic_refund`.

The Judge may disagree with `recommendedAction`. It cannot change it, and it cannot change `refundAmount`.

## 20. Confirmation — rejected claims never become trusted Judge input

**Confirmed, structurally rather than by policy.**

`JudgeContextBuilder._side()` reads `result.verified_claims` and **never reads `result.rejected_claims` at all**. The field is not filtered, hidden, or labelled — it is never touched. There is no code path by which a rejected claim can reach the Judge prompt, because `JudgeCaseContext` has no field to carry one.

> **Deliberate divergence from the brief:** the brief permitted showing rejected claims in a separate audit-only section marked "NOT TRUSTED". This implementation excludes them entirely instead. A large language model does not reliably honour a label: a section saying "do not use the following" still places the assertion in the context window, where it can shape reasoning while being absent from the citations. Exclusion is a structural guarantee; labelling is a request. Rejected claims remain fully visible to humans in the Stage 4 advocate panels, which is where the audit need actually lives.

The audit trail records `rejected_claims_included=False` on every run, so the guarantee is observable rather than merely asserted. Pinned by `test_rejected_claims_never_appear_as_trusted_judge_context` and `test_the_judge_context_serialized_to_the_prompt_has_no_rejected_claims`.
