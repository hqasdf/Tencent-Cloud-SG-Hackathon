# Stage 4 test migration — scenario mapping and assertion triage

**Status:** analysis only. No Stage 4 test has been modified.
**Author:** analysis performed after merge `018d31d`.
**Purpose:** decide how to migrate the failing Stage 4 tests onto the renamed and
rewritten `DISP-*` dataset, without silently changing what each test means.

**Verified suite state** (`pytest -q`, full backend suite): `79 failed, 61 passed`.
The per-file triage in §5 sums to exactly 79, so the earlier "81" figure was stale.
Of the 61 passing, 19 are the Bucket A files and 2 are known vacuous passes (§5).

---

## 1. Headline findings

1. **The dataset did not just get renamed — the scenario shapes changed.** The old
   suite covered four distinct behaviours. The new dataset covers five, but one of
   the old ones has **no equivalent at all**.
2. **`PARTIAL_REFUND` is now unreachable from any fixture.** `ResolutionEngine.recommend_route`
   chooses it only when `unexplained_fare_impact != fare_difference`, i.e. when the
   deviation is *partially* explained. Every new route case has a ratio of exactly
   `0.0` or `1.0`. The old `CASE-2026-1041` was the only partial case.
3. **The pass count is inflated.** Some currently-"passing" tests pass *vacuously*.
   `test_deterministic_analysis_is_identical_before_and_after_advocates_run` compares
   a 404 against a 404 and therefore asserts nothing. Treat the 59 green as
   ~55 real.
4. **`DISP-004` has a fixture inconsistency.** Its `status` is `Human Review` but the
   deterministic engine produces `AUTO_RESOLVE` / `UPHOLD_CANCELLATION_CHARGE` at
   0.95 confidence. The dashboard and the analysis panel disagree on screen.

---

## 2. Bucket A — done

Both stale count assertions were replaced with registry-derived assertions rather
than a new magic number, so they survive future fixture additions.

| File | Was | Now | Result |
|---|---|---|---|
| `tests/test_api.py` | `assert len(payload) == 1` | set of returned IDs `==` set of `MOCK_CASES` IDs, plus per-row shape checks and a representative-case check | 6/6 pass |
| `tests/test_intake.py` | `assert len(trips) == 1` | set of trips `==` set of `case.trip.trip_id` from `MOCK_CASES`, plus the representative trip | 13/13 pass |

---

## 3. New case profiles (DISP-002 … DISP-009)

All values below are read from `DisputeAnalysisService`, not inferred.

| | DISP-002 | DISP-003 | DISP-004 | DISP-005 |
|---|---|---|---|---|
| Dispute type | no_show | route | no_show | route |
| Case `status` | Auto Resolved | Investigating | **Human Review** | Auto Resolved |
| Key evidence | all Verified | E05 **Conflicting** | all Verified | all Verified |
| Core facts | within radius (0 m), wait **480 s** | diff **8.6 km**, **47.25 %**, explained **0.0**, unexplained **8.6** | within radius (0 m), wait **540 s** | diff **3.2 km**, **27.83 %**, explained **3.2**, unexplained **0.0** |
| Condition | — | `traffic_diversion`, no evidence | — | `road_closure` ← E06 |
| Policy outcome | UPHOLD_CANCELLATION_CHARGE | POLICY_NOT_APPLICABLE | UPHOLD_CANCELLATION_CHARGE | NO_ELIGIBLE_UNEXPLAINED_DEVIATION |
| Action | UPHOLD_CANCELLATION_CHARGE | HUMAN_REVIEW | UPHOLD_CANCELLATION_CHARGE | NO_REFUND |
| Refund / charge | charge 5.0 | 0 | charge 5.0 | 0 |
| Confidence | 0.95 | **0.29** | 0.95 | 0.95 |
| Mode | AUTO_RESOLVE | HUMAN_REVIEW | **AUTO_RESOLVE** | AUTO_RESOLVE |
| Escalations | — | CONTRADICTORY_EVIDENCE, POLICY_NOT_CLEARLY_APPLICABLE, LOW_CONFIDENCE | — | — |

| | DISP-006 | DISP-007 | DISP-008 | DISP-009 |
|---|---|---|---|---|
| Dispute type | no_show | route | route | no_show |
| Case `status` | Investigating | Investigating | Auto Resolved | Investigating |
| Key evidence | E05 **Conflicting** | all Verified | all Verified | E05 **Conflicting** |
| Core facts | within radius, wait **0 s** | diff **1.7 km**, **8.46 %**, explained **0.0**, unexplained **1.7** | diff **3.7 km**, **37.76 %**, explained **3.7**, unexplained **0.0** | within radius, wait **0 s** |
| Condition | — | `rider_requested_stop`, no evidence | `rider_requested_stop` ← E03, E04 | — |
| Policy outcome | POLICY_NOT_APPLICABLE | ELIGIBLE_ROUTE_ADJUSTMENT | NO_ELIGIBLE_UNEXPLAINED_DEVIATION | POLICY_NOT_APPLICABLE |
| Action | HUMAN_REVIEW | **FULL_FARE_DIFFERENCE_REFUND** | NO_REFUND | HUMAN_REVIEW |
| Refund / charge | 0 | refund **8.50** | 0 | 0 |
| Confidence | 0.29 | 0.95 | 0.95 | 0.29 |
| Mode | HUMAN_REVIEW | AUTO_RESOLVE | AUTO_RESOLVE | HUMAN_REVIEW |
| Escalations | same 3 as DISP-003 | — | — | same 3 as DISP-003 |

**Distinct shapes.** The eight cases collapse to five distinct behaviours:
`no_show upheld` (002, 004), `no_show conflicting → review` (006, 009),
`route fully explained → no refund` (005, 008), `route unexplained → refund` (007),
`route conflicting → review` (003).

---

## 4. Scenario mapping — OLD → NEW

Mapping is by **semantics**, not ID order. As it happens the ID order does not line up.

| Old scenario | Best new equivalent | Verdict | Why |
|---|---|---|---|
| **1041** route, partially explained (0.5 / 0.8 km) → `PARTIAL_REFUND` 1.42, AUTO_RESOLVE, 0.95 | **DISP-007** route, unexplained (0.0 / 1.7 km) → `FULL_FARE_DIFFERENCE_REFUND` 8.50, AUTO_RESOLVE, 0.95 | **PARTIAL MATCH** | Same family — unexplained route deviation, refund granted, auto-resolved, same confidence. But the refund is the **full** fare difference, not proportional. No fixture in the new dataset has a mixed explained/unexplained split, so the proportional-refund branch is no longer exercised. |
| **1042** route, fully explained → `NO_REFUND`, 0 | **DISP-005** explained 3.2 / unexplained 0.0 → `NO_REFUND`, 0 | **EXACT MATCH** | Identical rule outcome (`NO_ELIGIBLE_UNEXPLAINED_DEVIATION`), identical action, identical refund, same condition-explains-everything shape. `DISP-008` is a structural twin. |
| **1043** no-show, within radius, wait 372 s → `UPHOLD_CANCELLATION_CHARGE`, AUTO_RESOLVE | **DISP-002** within radius, wait 480 s → `UPHOLD_CANCELLATION_CHARGE`, AUTO_RESOLVE, 0.95 | **EXACT MATCH** | All four no-show rules pass, same action, same mode, same confidence, and it is the "clean" variant (no rider messages, driver messages only). Only the wait duration changed. `DISP-004` is a twin at 540 s. |
| **1044** conflicting evidence → `HUMAN_REVIEW`, escalation `CONTRADICTORY_EVIDENCE` | **DISP-006** (or its twin **DISP-009**) | **EXACT MATCH** | No-show domain, `E05` conflicting, `POLICY_NOT_APPLICABLE`, `HUMAN_REVIEW`, confidence 0.29, and `CONTRADICTORY_EVIDENCE` is present. Two extra escalation reasons are now also emitted. |
| — | **DISP-003** route + conflicting → `HUMAN_REVIEW` | **NEW, no old counterpart** | A route-domain conflict case did not exist before. Worth keeping as the route-side twin of the 1044 scenario. |

### Coverage that no longer exists

| Old behaviour | Status now |
|---|---|
| `PARTIAL_REFUND` (mixed explained/unexplained route) | **Unreachable.** No fixture produces it. |
| `REFUND_CANCELLATION_CHARGE` (no-show where the charge is refunded) | **Unreachable.** Only existed as a *derived* mutation of 1043 in the old test; no fixture covers it. |
| Escalation `MISSING_CRITICAL_EVIDENCE` | Not emitted by any fixture. |
| Escalation `INVALID_EVIDENCE_REFERENCES` | Not emitted by any fixture. |
| `UPHOLD_CHARGE` / `REFUND_CHARGE` action names | Superseded by the `*_CANCELLATION_CHARGE` names. |

---

## 5. Assertion triage for the failing Stage 4 tests

**A — ID-dependent only.** The assertion is fine; only the case identifier is stale.
**B — fixture-value dependent.** Bound to old numbers or evidence IDs; must be re-derived.
**C — behaviour / invariant.** Should survive any dataset, once it stops hardcoding IDs.
**D — architecture / safety.** Must survive unchanged; the guarantee is structural.

| File | Failing | A | B | C | D | Survive unchanged? |
|---|---|---|---|---|---|---|
| `test_advocate_context.py` | 11 | 1 | 4 | 2 | 4 | D only |
| `test_claim_verification.py` | 19 | 2 | 5 | 12 | 0 | C needs ID decoupling |
| `test_advocate_agents.py` | 13 | 1 | 0 | 12 | 0 | C needs ID decoupling |
| `test_advocate_orchestrator.py` | 20 | 1 | 2 | 17 | 0 | C needs ID decoupling |
| `test_advocate_api.py` | 10 | 2 | 3 | 5 | 0 | C needs ID decoupling |
| `test_provider_mock.py` | 6 | 1 | 3 | 2 | 0 | C needs ID decoupling |

### Category D — survives as written (only the *iteration source* must change)

These are the safety properties and they do not care which case they run against:

- `test_context_does_not_leak_the_recommendation_or_any_answer`
- `test_context_has_no_recommendation_confidence_or_escalation_fields`
- `test_context_never_exposes_the_raw_calculation_input`
- `test_context_excludes_historical_and_fairness_sensitive_data`
- `test_no_answer_fields_are_present_in_the_response` (already passing)
- `test_mock_provider_emits_nothing_but_the_contract_fields`

They currently iterate a hardcoded ID list. **Changing that list to all registered
cases makes them stronger, not weaker** — the leak guarantee then holds across the
whole fixture set instead of three hand-picked cases.

### Category C — the largest group, and the cheapest to fix

All 12 rejection tests in `test_claim_verification.py` (hallucination, no evidence
reference, unknown policy ref, duplicate claim IDs, unverified-evidence quality,
prose warnings), the four failure-isolation tests, the JUDGE-is-`NOT_RUN` tests, the
response-shape tests and the whole prompt/side-enforcement group in
`test_advocate_agents.py` assert *behaviour*, not data. They fail purely because
their setup helper points at a dead ID. Once setup selects a case by **outcome**
rather than by name, they pass unchanged.

### Category B — must be re-derived against new facts

- `test_route_case_exposes_correct_trusted_facts` — 5.8 / 7.1 / 1.3 / 22.41 / 8 / 2.3 / 0.5 / 0.8
- `test_route_case_exposes_only_verified_conditions_and_supporting_evidence` — `traffic_diversion` + `E06`
- `test_no_show_case_exposes_correct_trusted_facts` — 372 s / 6.0 / `E06`
- `test_context_flags_missing_and_conflicting_evidence` — assumed 1041 clean, 1044 conflicted
- `test_fact_contradiction_is_hard_rejected` — literal 0.8 / 1.3
- `test_float_values_within_tolerance_is_accepted` — literal 0.5000000001
- `test_boolean_fact_is_compared_strictly` — `WITHIN_PICKUP_RADIUS = False`
- `test_route_only_fact_is_unknown_for_no_show_case` — `DEVIATION_PERCENTAGE` on a no-show
- `test_claim_resting_only_on_unverified_evidence_is_rejected` — needs a case with unverified evidence
- `test_mock_provider_uses_only_trusted_context_values` — 22.41 / 0.8 / 0.5
- `test_advocates_are_independent_and_may_disagree` — **asserts `PARTIAL_REFUND` vs `NO_REFUND`; that pairing no longer exists.** Needs redefining (see §7).
- `test_mock_provider_reports_different_outcomes_per_side_on_a_contestable_case` — same problem.

### Category A — mechanical

`ALL_CASES` tuples in `test_advocate_orchestrator.py` and `test_advocate_api.py`, the
default argument in `test_advocate_agents.py::context_for`, and the `ROUTE_CASE` /
`NO_SHOW_CASE` constants in `test_claim_verification.py`.

### Two tests that pass but assert nothing

- `test_deterministic_analysis_is_identical_before_and_after_advocates_run` — 404 == 404.
  Must be repaired, not just re-pointed.
- `test_unknown_case_returns_404` — genuinely fine.

---

## 6. Proposed fixture-selection helper

Selection should be **by outcome**, so the suite survives any future dataset edit.
Suggested home: `backend/tests/conftest.py` (or `tests/fixtures.py`).

```python
def all_cases() -> list[DisputeCase]
def analysed() -> list[tuple[DisputeCase, CaseAnalysisResponse]]

# Domain selectors
def route_cases() -> list[DisputeCase]
def no_show_cases() -> list[DisputeCase]

# Outcome selectors — the ones the tests actually mean
def case_with_action(action: str) -> DisputeCase        # UPHOLD_CANCELLATION_CHARGE, NO_REFUND, ...
def case_with_mode(mode: str) -> DisputeCase            # AUTO_RESOLVE, HUMAN_REVIEW
def case_with_conflicting_evidence() -> DisputeCase
def case_with_fully_explained_deviation() -> DisputeCase
def case_with_unexplained_deviation() -> DisputeCase
def case_with_unverified_evidence() -> DisputeCase
```

Each raises a clear `pytest.fail(...)` if no fixture matches, so a dataset change
produces an explanatory failure instead of a `StopIteration`.

The four helpers proposed in the request map as follows:

| Requested helper | Status |
|---|---|
| `get_route_justified_case()` | ✅ implementable — outcome `NO_REFUND` on a route case |
| `get_no_show_upheld_case()` | ✅ implementable — outcome `UPHOLD_CANCELLATION_CHARGE` |
| `get_human_review_case()` | ✅ implementable — mode `HUMAN_REVIEW` (2 domains available) |
| `get_route_partial_refund_case()` | ❌ **no fixture matches** — see §7 |

---

## 7. Open decisions (need your call before any rewrite)

1. **`PARTIAL_REFUND` coverage.** Either
   (a) add a new fixture with a mixed explained/unexplained split (e.g. 3.2 km of a
   5.0 km deviation explained) so the proportional-refund branch stays tested, or
   (b) accept the branch is untested and re-point those tests at `FULL_FARE_DIFFERENCE_REFUND`.
   I recommend (a) — it is the only thing exercising the `ratio_applied` maths, and it
   restores the old 1041 scenario rather than deleting it.
2. **`test_advocates_are_independent_and_may_disagree`.** Its whole point is that the two
   advocates reach *different* outcomes on the same facts. No new case has rider ≠ driver.
   Options: assert disagreement on a case where the mock *can* differ, or reframe the test
   as "both advocates argue independently from the same context" without requiring
   disagreement. Needs a product decision, not just a rename.
3. **`REFUND_CANCELLATION_CHARGE`.** The old suite covered it only via a derived mutation
   of 1043 (wait forced to 120 s). Worth deciding whether to add a real fixture.
4. **`DISP-004` status/mode mismatch.** Fixture bug or intentional? If intentional, the
   dashboard needs to explain why a 0.95-confidence auto-resolved case shows as
   "Human Review".
5. **Vacuous tests.** Should the suite gain a guard (e.g. assert `status_code == 200`
   before comparing bodies) so a 404 can never silently satisfy an equality assertion?

---

## 8. Recommended migration order (once approved)

1. Add `conftest.py` with the outcome-based selectors. No test changes yet.
2. Migrate category **D** — swap the hardcoded ID list for `all_cases()`. Should be a
   pure strengthening.
3. Migrate category **C** — re-point setup helpers at the selectors. Largest group,
   lowest risk, no assertion text changes.
4. Migrate category **A** — mechanical constant updates.
5. Migrate category **B** — re-derive each expected value against the chosen new case,
   recording the old→new value in the test so the change is reviewable.
6. Resolve the §7 decisions, then re-run the full suite.
