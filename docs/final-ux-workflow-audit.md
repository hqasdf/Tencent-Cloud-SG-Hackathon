# Final UX and workflow audit

Date: 2026-10-11. Scope: local UI simplification and offline validation only.

## Product changes

The active application is `frontend/src/App.tsx` -> `CaseDetail.tsx`. The root Node application is legacy. `DisputeDashboard` and `IntakeWorkspace` are not separate screens reached by the active App. The active case list remains the sidebar.

The previous workspace had nine navigation sections, repeated party/contradiction panels, three competing analysis buttons, long boundary explanations, and detailed claim/provider information mixed with ordinary case information. It now has four sections:

1. **Hear Both Sides**: existing rider interview, driver interview and optional rider clarification; attributed statements; original accounts and trip details expandable.
2. **Check the Evidence**: evidence summaries with explicit Verified/Pending/Conflicting/Missing status; statement disagreements; expandable timeline, calculations and policy checks.
3. **AI Review**: five ordered role summaries with real backend status and accepted/rejected counts; supporting evidence and all advocate, rebuttal and Judge verification records expandable.
4. **Final Decision**: code-owned action, refund, confidence and human-review requirement; reasons visible; deeper explanation, counterfactual and audit trail expandable.

The primary action runs analysis, retries incomplete AI review, or opens the decision after completion. Saved demo, advocates-only and explicit rerun actions are under More options. Advocates-only remains available before a full result, avoiding a new advocate response appearing alongside an older full result. LIVE, REPLAYED LIVE and MOCK DEMO provenance remain visible. Details retain stage provenance, provider/timing information, HTTP status, attempt count, cooldown, verification reasons and atomic proposition kinds/statuses. An accepted argument is labelled Accepted by code; this does not claim all advocacy positions are factual evidence.

Native buttons and details controls remain keyboard accessible. Inputs have party-specific accessible names. Focus outlines, a four-item responsive rail, and reduced-motion styling were added using existing CSS. No new dependencies.

## Bugs corrected

- Interview drafts previously cleared before the API response, losing typed text on failure. They now clear only after a successful response.
- Interview send and skip actions previously depended solely on asynchronous render state for exclusion. They now share an immediate ref guard; both parties and clarification skipping are disabled while either action is active.
- The intake completion effect previously reopened step 2 whenever a user reopened completed intake. It now advances on completion changes only, allowing review of saved interviews.
- The ordinary claim view did not expose atomic proposition verification records. They now remain accessible in View details, preserving FACT, CAUSAL, POLICY_APPLICATION and ADVOCACY_POSITION distinctions.

## Workflow and safety evidence

No backend production code was changed in this pass. Existing lifecycle transitions and historical/partial-intake resolution permissions remain intact.

| Boundary or edge | Offline evidence |
| --- | --- |
| Rider -> Driver -> optional Rider clarification; closed phases; turn limits | `test_intake.py`, `test_intake_stabilization.py` |
| Empty, partial and completed intake; saved provenance; restart; corrected allegations | `test_intake_resolution.py`, `test_intake_stabilization.py` |
| Party privacy; alleged statements cannot become trusted evidence | `test_intake_resolution.py`, `test_advocate_context.py`, `test_claim_verification.py` |
| Supported and unsupported contradictions; missing GPS/fare/evidence status | `test_phase4_cases.py`, deterministic analysis suites, intake resolution suite |
| Advocate contexts, strict atomic schemas, unsupported claims | `test_advocate_schema_alignment.py`, `test_claim_verification.py`, `test_phase4_cases.py` |
| Bounded rebuttals, verification, Judge projection/validation | `test_rebuttal.py`, `test_judge.py`, `test_phase4_cases.py` |
| Code-owned refunds and mandatory human review | `test_phase4_cases.py`, `test_workflow_state.py`, `test_agent_recovery.py` |
| 429 guidance, excessive cooldown, no blind retry, bounded 503, safe diagnostics | `test_gemini_rate_limits.py`, `test_gemini_native.py` |
| Partial recovery, signed identity checks, overlapping requests | `test_agent_recovery.py` |
| Compatible replay, rejection of incompatible artifacts, no fallback call | `test_replay.py`, `test_deployment.py` |
| Eight-case list, selection payload, analysis and complete HTTP resolution contract | `test_final_workflow_contract.py` (new) |
| Rapid case switching, stale successes/errors, duplicate analysis actions | `frontend/test/navigation.test.ts` |
| Four sections, primary action, loading/errors, provenance, draft retention, intake guard | `frontend/test/simplifiedWorkflow.test.ts` (new) |

Tests execute actual services/HTTP response models and React components with controlled providers or server rendering/hook harnesses. They are not browser automation. The completed-intake API integration test traces saved statements to both Advocates and excludes them from the Judge projection. Completed and incompatible checkpoint/replay tests revalidate outputs rather than trusting saved text.

## Eight supported cases

All eight passed the fixture-to-five-role service tests and the new real HTTP contract tests.

| Case | Code-owned action | Refund SGD | Review mode | Judge status | Result |
| --- | --- | ---: | --- | --- | --- |
| DISP-002 | UPHOLD_CANCELLATION_CHARGE | 0.00 | AUTO_RESOLVE | COMPLETE | PASS |
| DISP-003 | HUMAN_REVIEW | 0.00 | HUMAN_REVIEW | PENDING_HUMAN_REVIEW | PASS |
| DISP-004 | UPHOLD_CANCELLATION_CHARGE | 0.00 | AUTO_RESOLVE | COMPLETE | PASS |
| DISP-005 | NO_REFUND | 0.00 | AUTO_RESOLVE | COMPLETE | PASS |
| DISP-006 | HUMAN_REVIEW | 0.00 | HUMAN_REVIEW | PENDING_HUMAN_REVIEW | PASS |
| DISP-007 | FULL_FARE_DIFFERENCE_REFUND | 8.50 | AUTO_RESOLVE | COMPLETE | PASS |
| DISP-008 | NO_REFUND | 0.00 | AUTO_RESOLVE | COMPLETE | PASS |
| DISP-009 | HUMAN_REVIEW | 0.00 | HUMAN_REVIEW | PENDING_HUMAN_REVIEW | PASS |

Both Advocates and both Rebuttals complete in every case. PENDING_HUMAN_REVIEW is an intentional reviewed recommendation, not a failed Judge. Policy thresholds, fares and refund formulas were unchanged. Mock verification correctly retains any rejected claims outside the Judge's trusted claim set.

## Demo validation

The configured local replay store was loaded with the canonical read-only intake loader. A forbidden provider made any fallback model call an assertion failure.

- **DISP-005**: existing artifacts revalidated unchanged; five COMPLETE roles; MOCK_DEMO; NO_REFUND; SGD 0.00; saved intake AUTO_RESOLVED with comparison allowed. Compatibility was checked against the current saved intake, not an empty replacement.
- **DISP-002**: fresh mock artifacts generated without overwriting any prior files, copied into the previously absent DISP-002 directory of the configured local replay store, and revalidated; five COMPLETE roles; MOCK_DEMO; charge upheld; SGD 0.00; intake RIDER_INTERVIEW, comparison not allowed. This is not a completed AI interview.
- Both full replays made **zero provider calls**. Deterministic checks ran. No live Gemini request occurred, and no credentials or environment settings were changed.
- Artifacts remain ignored local files; they are not part of the source diff. A fresh capture root is `backend/replay_artifacts/ux-demo-DISP-002-20261011232406`. Existing DISP-005 artifacts were hash-checked before/after and unchanged.

## Final regression results

- Backend: **895 passed, zero failures**, 29.09 seconds. Baseline 887 plus eight HTTP contract cases. One existing Starlette/AnyIO deprecation warning.
- Frontend: **24 passed, zero failures**. Baseline 17 plus seven targeted rendering/interaction tests.
- TypeScript project compilation: passed.
- Vite production build: passed with same-origin production API configuration.
- `git diff --check`: passed.
- Production environment/storage/CORS/demo safety tests were included in the full backend suite. The Docker executable was unavailable on this session's PATH, so the earlier reported image builds were not independently revalidated here.

No live external calls, browser automation, database resets, checkpoint deletion, credential changes, public deployment, commits or pushes. Existing deployment-preparation work was retained. Existing services on 8000 and 5173 were not restarted or replaced.

## Remaining confirmation and manual Opera checklist

Offline workflows and both demo categories are ready. Live Gemini availability is not validated by mock tests. No visual QA or manual browser pass is claimed. Existing production images must be rebuilt to include these new UI sources before deployment.

Use the existing Opera tab at `http://127.0.0.1:5173`; do not open extra tabs. This checklist requires no live analysis or interview request:

1. Refresh. Select DISP-005. Confirm four section names, clear status, and one primary action; verify keyboard focus and small-window layout.
2. Open Hear Both Sides. Confirm saved interviews/accounts remain; open source details. Reopen this section after reviewing evidence and confirm it stays open.
3. Open Check the Evidence. Check readable status labels, original trip timeline and expandable policy/calculation records.
4. Choose **More options -> View saved demo** once. Confirm **MOCK DEMO** remains visible, five roles complete, counts match their detailed verification panels, and the Judge says advisory.
5. Choose **View decision**. Confirm NO_REFUND / SGD 0.00 and Calculated by code. Open explanation and audit details.
6. Select DISP-002 and choose **More options -> View saved demo**. Confirm charge upheld / SGD 0.00; incomplete intake must not be described as a completed interview.
7. Select DISP-003, DISP-006 and DISP-009 to inspect evidence and human-review conditions. Offline tests verify their full outcomes; do not click Run analysis on the existing real-provider server during this offline-only review.
8. Switch cases rapidly, then refresh. Confirm case identity stays consistent and saved intake reloads. Full AI result panels are session state and may need a compatible saved replay to redisplay after refresh; intake and signed checkpoints persist server-side.

The final visual/product sign-off remains pending this manual check. If a saved replay is rejected, report the visible reason; do not fall back to a live call.
