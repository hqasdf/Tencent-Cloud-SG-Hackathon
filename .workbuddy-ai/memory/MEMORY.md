# RydeResolve — project notes

## Model selection (decided 2026-09-28)

**`gemini-3.8-flash` is the project-selected advocate model.** This is a settled
decision, not a candidate.

- Ollama / Qwen is **no longer a benchmarking target**. Do not run, start, configure,
  or benchmark Ollama.
- Do **not** delete Ollama/provider-compatibility code. It is retained as an **unused
  fallback** — `QWEN_LOCAL` in `backend/app/benchmarks/models.py` stays in the
  catalogue, because deleting it would delete the only evidence that the transport is
  not Gemini-specific.
- Do **not** declare Gemini "best". It is the *selected* model. Those are different
  statements, and only the second is supported by the data.

## Stage 4C is a characterization benchmark, not a competition

It measures how the one selected model behaves — regression testing plus provider-reliability,
latency, token, hallucination and claim-verification metrics. The CLI ranks nothing and computes
no aggregate quality score. `MODEL_CATALOGUE` and `--models a,b` stay multi-model capable on
purpose: that is a retained capability, not a Stage 4C goal. Write-up in
`docs/stage4c-advocate-benchmark.md`.

## Core principle (unchanged, load-bearing)

**CODE calculates facts. AI argues from facts. CODE checks AI claims. Judge later
decides using only verified information.**

Two rules that fall out of it and keep getting exercised:

1. **Provider failure ≠ model-output failure.** A 503 is never a hallucination; a
   rejected claim is never downtime. `classify_failure` puts each code on exactly one
   side, and an unrecognised code becomes `INTERNAL_ERROR` rather than being guessed at.
2. **Unknown is not zero.** Missing tokens, an undefined verification rate (no claims
   generated), and absent pricing all stay `null`. `0.0` would be a factual claim the
   data does not support.

## Trust boundaries are structural, not runtime (Stage 5 + 6 pattern)

Every stage that feeds the Judge follows one rule: **the builder has no parameter for untrusted
material.** `JudgeContextBuilder._side()` never reads `rejected_claims`; `_rebuttals()` never
reads `rejected_rebuttals`. There is nothing to forget to filter, and no "NOT TRUSTED" label to
leak into the context window — a label can still shape reasoning while staying out of citations.

Same idea bounds the rebuttal: `RebuttalCaseContext` has no field that could name a rebuttal as a
target, so a second rebuttal round is *unrepresentable*, not merely disabled.
`MAX_REBUTTAL_ROUNDS = 1` is a constant, not a config knob. Do not add a config knob.

Consequences worth keeping: `extra="forbid"` on model-facing schemas; code assigns rebuttal IDs
(`RIDER-RB1`, dense, verified-only) while advocates author claim ids; advocate failure blocks the
Judge but **rebuttal failure does not** (rebuttals are supplementary — never fabricate a missing one).

**Provider failure ≠ model-output failure ≠ internal error.** A 503 is never a hallucination.

**Never reimplement a validator.** `agents/monetary.py` and Stage 4's `fact_values`/`values_match`
are shared on purpose: a second copy drifts, and the weaker copy becomes the effective guarantee.

## Frontend: the case page is a workflow, not a report (decided 2026-09-29)

The audience is an **ops agent triaging a queue**, and the user's brief was
*"step by step … must be user friendly"*. `CaseDetail.tsx` is therefore eight numbered,
collapsible steps (Review the case → Examine the evidence → Check the facts → Read the
arguments → Cross-examination → The Judge's view → The decision → Why this decision), built
from `WorkflowStep` / `WorkflowRail` / `StepPending`.

Four rules. They are load-bearing — breaking one is how the page got unreadable before.

1. **One primary action.** "Run full analysis" exists exactly once, in the toolbar. Any
   second run button is a regression.
2. **Every number has one home.** The deterministic outcome lives in step 7 only; confidence
   in step 3 only. `DisputeCase.resolution`, `.confidence` and `.activity` are fixture fields
   that the live pipeline supersedes — they are **deliberately not rendered**. Rendering both
   sources is what produced the old triplication.
3. **No internal stage names in user-facing copy.** "Stage 4/5/6", "the panel above" and
   "this milestone" are developer vocabulary. Verify with a grep, not by eye.
4. **The Judge never looks authoritative.** Step 6 is advisory and says so; step 7 is the only
   place an outcome appears. The `JudgePanel` two-column composite was split precisely so the
   separation is enforced by sequence — do not re-merge it.

Enum codes reaching the UI go through `humanize()` (`utils/format.ts`), not `labelize()`:
`labelize("CONTRADICTORY_EVIDENCE")` is "CONTRADICTORY EVIDENCE", `humanize` gives
"Contradictory Evidence".

Verification harness (throwaway, gitignored inside `node_modules`):
`frontend/node_modules/.tmp-verify/workflow.tsx` — esbuild + `react-dom/server`, renders the
real component against the live API and asserts 36 properties. Rebuild with
`./node_modules/.bin/esbuild … --define:import.meta.env.VITE_API_BASE_URL='"http://127.0.0.1:8000"'`,
then `node node_modules/.tmp-verify/workflow.mjs`. Run it for at least one route-deviation and
one no-show case; `CHECK_CASE_ID` overrides the default.

## Environment / tooling

- The project virtualenv is **not** in the repo. Use
  `C:\Users\Han Qian\.workbuddy-ai\binaries\python\envs\default\Scripts\python.exe`.
  The managed interpreter has no pytest installed.
- Run backend tests with that interpreter from `backend/` (it needs `backend/` on the
  path; `pytest.ini` handles it, but ad-hoc scripts need `PYTHONPATH=.`).
- **79 pre-existing test failures** ("Bucket B") are expected and must not be touched.
  Every one is a `StopIteration` on `CASE-2026-1041`, a fixture id replaced by `DISP-*`
  in an earlier stage. Baseline after Stage 6: **367 passed / 79 failed** (the 79 never move;
  the passing count is the regression signal).
- Suite sizes: `tests/test_rebuttal.py` 62, `tests/test_judge.py` 42.
- Fixture ambiguity: route `DISP-005`/`DISP-008` and no-show `DISP-002`/`DISP-004` are
  each two equivalent clean fixtures, so benchmark case selection cannot be unique.
  It picks the lowest id deterministically and records the tie.
- `backend/benchmark_results/` is gitignored. The Stage 4C write-up lives in
  `docs/stage4c-advocate-benchmark.md`.
- **Egress guard lives outside the repo** (temp dir, so it will not survive cleanup):
  `%LOCALAPPDATA%\Temp\ryderesolve_live\netguard.py` is a pytest plugin (`-p netguard`);
  `egress_guard.py` + `guard_selftest.py` prove the guard is not vacuous. Run with
  `PYTHONPATH="<tempdir>;."`. Recreate it if missing — it is ~40 lines.
- **Inadvertent-live-call hazard:** only `pytest` is protected by `conftest.py`. Ad-hoc scripts
  must set `AGENT_MODE=mock AGENT_PROVIDER=mock` explicitly, or they will hit the real provider.
- The `.env` Gemini credential **is** present, so "no live call" is always restraint, never
  inability. Confirm it by checking that `benchmark_results/` has no new run.
- **Zombie dev servers.** `TaskStop` can report a task killed while the node child survives.
  A restarted `vite` then silently moves to 5174 and every request to 5173 is served stale
  code — which looks exactly like a broken file watcher. Free the port with
  `Get-NetTCPConnection -LocalPort 5173 -State Listen` → `Stop-Process -Id <pid> -Force`, then
  confirm freshness with `curl http://127.0.0.1:5173/src/components/CaseDetail.tsx`.
- `agent-browser screenshot <path>` treats the first positional argument as a **selector**, not
  a path. Use `--screenshot-dir <dir>`.
