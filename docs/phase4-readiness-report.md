# Phase 4 QA and readiness report

## Overall status

**READY WITH KNOWN LIMITATIONS. Phase 4 offline QA and stabilization are COMPLETE.** The approved required-route-evidence safety correction is implemented and the complete backend suite passes. Manual Opera visual sign-off, live Gemini quota availability and target-host deployment validation remain separate limitations.

AI Intake integrates with the five-role pipeline. Attributed interview allegations remain separate from code-calculated evidence. Deterministic verification produces the Judge projection, and refunds remain code-controlled. The saved DISP-005 mock demo retains intake provenance and replays all five roles with zero live Gemini calls.

No live Gemini requests, browser automation, credential changes, developer database resets, checkpoint deletions, commits or pushes were performed. Tests use temporary intake, replay and checkpoint storage. Existing servers were not restarted or duplicated.

## Test results

| Run | Passed | Failed | Notes |
|---|---:|---:|---|
| Previously reported baseline | 725 | 103 | Earlier task |
| Reproduced isolated baseline | 726 | 103 | One additional replay test was already present |
| Original suite after fixture/contract repairs | 837 | 0 | Eight additional parameter cases from testing all available fixtures |
| Before the approved safety correction | 854 | 1 | New regression exposed required route evidence marked Missing being accepted |
| Final full suite after the approved correction | 864 | 0 | Former failure fixed; nine additional required-evidence availability cases pass |
| Frontend tests | 13 | 0 | Includes actual App execution with deferred services and hook harness |
| TypeScript and Vite production build | Passed | - | No browser testing claimed |

All 103 original failures and the subsequently discovered missing-status failure are repaired. No tests were removed, skipped or weakened to hide failures. The required human-review expectation remains strict and passes.

Original failures: 79 obsolete fixtures (A), 24 outdated contracts (B), zero established baseline production regressions (C), zero unknown (D). Every original failed test and correction is listed in [phase4-failure-classification.md](phase4-failure-classification.md). Some fixture failures also exposed old atomic-claim expectations after the missing case was replaced.

One existing Starlette/AnyIO dependency deprecation warning remains; it did not fail a test.

## All available cases

The repository has eight supported source cases. The developer database was inspected read-only: saved intake maps to DISP-002, DISP-003 and DISP-005; it does not define additional supported dispute fixtures. All testing used isolated data.

Raw fixture display resolutions say ANALYSE_CASE and are placeholders, not expected outcomes. The expectations below were established from the existing structured inputs and current deterministic policy. No thresholds or refund formulas were invented or changed.

| Case | Scenario | Expected / actual deterministic action | Refund SGD | Human review | Five-role outcome | Result |
|---|---|---|---:|---|---|---|
| DISP-002 | No-show charge dispute — Tiong Bahru Plaza | UPHOLD_CANCELLATION_CHARGE / UPHOLD_CANCELLATION_CHARGE | 0.00 | No | All COMPLETE | PASS |
| DISP-003 | Route deviation overcharge — Jurong East to Changi Airport | HUMAN_REVIEW / HUMAN_REVIEW | 0.00 | Yes | Four COMPLETE; Judge PENDING_HUMAN_REVIEW | PASS |
| DISP-004 | No-show dispute — wrong pickup spot at Bugis Junction | UPHOLD_CANCELLATION_CHARGE / UPHOLD_CANCELLATION_CHARGE | 0.00 | No | All COMPLETE | PASS |
| DISP-005 | Route deviation with road closure — Ang Mo Kio to Raffles Place | NO_REFUND / NO_REFUND | 0.00 | No | All COMPLETE | PASS |
| DISP-006 | No-show dispute — driver was late, rider cancelled — Clementi Mall | HUMAN_REVIEW / HUMAN_REVIEW | 0.00 | Yes | Four COMPLETE; Judge PENDING_HUMAN_REVIEW | PASS |
| DISP-007 | Unscheduled stop overcharge — Clarke Quay to Woodlands | FULL_FARE_DIFFERENCE_REFUND / FULL_FARE_DIFFERENCE_REFUND | 8.50 | No | All COMPLETE | PASS |
| DISP-008 | Rider-requested stop dispute — Bedok to Gardens by the Bay | NO_REFUND / NO_REFUND | 0.00 | No | All COMPLETE | PASS |
| DISP-009 | Driver no-show — Punggol to Changi Airport (morning flight) | HUMAN_REVIEW / HUMAN_REVIEW | 0.00 | Yes | Four COMPLETE; Judge PENDING_HUMAN_REVIEW | PASS |

DISP-006 and DISP-009 deliberately exercise rejection of a mock D2 claim containing an unsupported evidence reference. The claim remains rejected and does not reach the Judge. This is successful safety behavior, not a reason to force every claim into the verified set.

## Critical coverage and integrity

| Area | Verification |
|---|---|
| Intake lifecycle | Completed interviews, Rider-only, Driver-only, incomplete interviews, rider clarification, refresh and database restart covered by intake/integration suites |
| Attribution and privacy | Server-owned attribution, source IDs, append-only corrections, active allegation filtering, wrong case/trip, cross-party message filtering covered |
| Contradictions | Disputed allegations require review; evidence-resolved contradictions use trusted values; disagreement itself does not establish fault |
| Claims | FACT, CAUSAL, POLICY_APPLICATION and ADVOCACY_POSITION remain distinct; legacy assertions cannot become trusted facts; malformed/unsupported propositions rejected |
| Judge boundary | Only verified material reaches permitted projection; rejected claim wording and interview content excluded; human-review override attempts rejected |
| Policy | Justified/unjustified route, fare difference, proportional partial refund, zero fare difference, no-show radius/wait, conflicts, low confidence and human review exercised |
| Provider reliability | Malformed JSON, bounded repair success/failure, truncation, guided/unguided 429, excessive cooldown and 503 covered by offline transports |
| Recovery | Partial advocate completion, rebuttal/Judge failure, signed compatible reuse, incompatible/tampered checkpoint rejection and overlapping request rejection covered |
| Frontend | Case-load/analysis races, stale errors, A-B-A navigation and duplicate AI actions tested with the actual App; quota wording and provenance tested |
| Demo | Existing artifacts revalidated; altered evidence, intake and schema safely rejected with no live fallback |

The approved missing-record-status correction is resolved. RouteDeviationService now regards a required ID as missing when its record is absent or its status is Missing, matching NoShowAnalysisService. The existing policy, confidence and escalation engines then require HUMAN_REVIEW. All E03/E04/E05 combinations of missing, absent and available evidence are covered. Available evidence retains AUTO_RESOLVE / NO_REFUND for DISP-005; unavailable evidence returns HUMAN_REVIEW and cannot make the Judge executable. No thresholds, fare calculations or refund formulas changed. All eight original fixture outcomes are unchanged.

Two independently discovered defects were fixed: delayed frontend responses could populate another selected case, and the mock Judge incorrectly used pickup-radius success as a substitute for wait-rule success. Duplicate overlapping AI actions are also blocked synchronously. The mock correction affects advisory simulation only, never deterministic authority.

## Changes and interfaces

- `backend/tests/conftest.py`: isolate intake SQLite, checkpoint and replay state; block local replay/token settings from contaminating offline runs.
- `backend/tests/test_advocate_agents.py`, `test_advocate_api.py`, `test_advocate_context.py`, `test_advocate_orchestrator.py`, `test_claim_verification.py`, `test_provider_mock.py`: replace removed cases with supported DISP scenarios, test every case where appropriate, and assert current atomic projection/metadata behavior.
- `backend/tests/test_agent_request_contract.py`: verify realistic output headroom against the configured Gemini 4096 envelope, update the current schema hash identity, and use a versioned fabricated atomic claim for the trust-rejection test.
- `backend/tests/test_benchmark.py`, `test_provider_tokenhub.py`, `test_workflow_state.py`: make scripted outputs comply with current atomic contracts, typed evidence support and bounded request settings; preserve each deliberate failure injection.
- `backend/app/agents/providers/mock.py`: mock no-show Judge consults the already evaluated wait rule independently of pickup radius.
- `backend/app/services/route_deviation.py`: approved minimal missing-status evidence availability check, matching the existing no-show approach.
- `backend/tests/test_phase4_cases.py`: all eight fixtures, ten policy-permitted edge variants and nine required-evidence availability combinations; the original safety assertion is preserved and passes.
- `frontend/src/App.tsx`: discard stale case loads/results/errors; synchronously prevent overlapping AI actions; remount intake on case selection.
- `frontend/src/components/CaseDetail.tsx`: disable full/demo actions while advocates are already running.
- `frontend/test/navigation.test.ts`: deferred-service tests execute actual App behavior without browser automation or new dependencies.
- `backend/.env.example`: correct commented Gemini example to gemini-3.8-flash, 4096 tokens and one transport retry; runtime credentials/configuration untouched.
- This report, failure classification and [phase4-demo-and-manual-qa.md](phase4-demo-and-manual-qa.md): auditable results and portable demo instructions.

No database schema or API response contract changed. Existing developer data and signed checkpoints remain intact. Gemini Native implementation, configured model/token budget and bounded retries remain unchanged.

## Risks and minimum next steps

1. The approved safety correction and complete offline regression verification are finished; no known blocking bug remains in the tested MVP scope.
2. Complete the manual Opera checklist. Offline hook/service tests and compilation do not establish real browser rendering, scrolling or interaction quality.
3. Regenerate the demo on the submission machine after opening DISP-005 once. Do not package private SQLite data, local credentials or existing private-context artifacts.
4. Verify deployment configuration and dependency installation on the target host. Local builds are not a deployed-system validation.
5. Treat live Gemini quota as an external limitation. No RPM/TPM/daily quota or availability was confirmed here, and no live retry is authorized by this task.
