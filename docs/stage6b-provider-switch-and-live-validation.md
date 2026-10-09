# Stage 6B — Native provider switch + live end-to-end validation

Date: 2026-10-09
Scope: switch the active runtime provider to `gemini_native`, then validate the
already-fixed orchestration end to end in a real browser.

Nothing in the orchestration, schema, workflow semantics or UI styling was changed
in this pass. It is a verification pass.

---

## 1. Provider before

| Setting | Value |
|---|---|
| `AGENT_MODE` | `real` |
| `AGENT_PROVIDER` | `openai_compatible` |
| `AGENT_MODEL` | `gemini-3.8-flash` |

All five roles resolved **`OpenAiCompatibleProvider`**.

## 2. Provider after

| Setting | Value |
|---|---|
| `AGENT_MODE` | `real` |
| `AGENT_PROVIDER` | `gemini_native` |
| `AGENT_MODEL` | `gemini-3.8-flash` |
| `AGENT_API_KEY` | unchanged (not printed) |

`openai_compatible` support was **not** removed; only the selected registration changed.

## 3. All five role/provider resolutions

Resolved offline through the real construction path (`get_provider(settings)`), with
`complete` replaced before any call so no socket could open:

| Role | Provider |
|---|---|
| Rider Advocate | `GeminiNativeProvider` |
| Driver Advocate | `GeminiNativeProvider` |
| Rider Rebuttal | `GeminiNativeProvider` |
| Driver Rebuttal | `GeminiNativeProvider` |
| Judge | `GeminiNativeProvider` |

No role retained `OpenAiCompatibleProvider`.

## 4. Config changed

`backend/.env` — one line:

```
AGENT_PROVIDER=gemini_native
```

`AGENT_BASE_URL` is retained: it is harmless for `gemini_native` (that provider is in
`PROVIDERS_WITHOUT_BASE_URL`) and is still required if `openai_compatible` is ever
selected again. `AGENT_MODE`, `AGENT_MODEL`, the key, timeout, tokens, temperature and
reasoning effort are unchanged.

## 5. Native request contract (verified)

For all five roles, built through the real `complete_typed` path:

| Property | Value |
|---|---|
| timeout | 30 s |
| max_output_tokens | 1024 |
| temperature | 0 |
| thinking level | LOW (`reasoning_effort="low"`) |
| streaming | off |
| response_mime_type | `application/json` |
| response_schema | Pydantic-derived (`provider_schema_for(AdvocateOutput / RebuttalOutput / JudgeOutput)`) |

Pydantic remains the single source of truth for the schema.

## 6. Offline regression

Targeted suites (`test_gemini_native`, `test_advocate_agents`, `test_rebuttal`,
`test_judge`, `test_workflow_state`, `test_replay`, `test_live_resume`,
`test_live_stop_rule`, `test_api`):

```
13 failed, 329 passed
```

The 13 failures are all `test_advocate_agents.py` — the known Bucket-B
`CASE-2026-1041` `StopIteration` set.

Full backend suite:

```
79 failed, 651 passed
```

Failures by file — exactly the known Bucket-B distribution, no new failures:

| File | Failures |
|---|---|
| test_advocate_orchestrator.py | 20 |
| test_claim_verification.py | 19 |
| test_advocate_agents.py | 13 |
| test_advocate_context.py | 11 |
| test_advocate_api.py | 10 |
| test_provider_mock.py | 6 |
| **total** | **79** |

## 7. Frontend build

`tsc -b` → exit 0, clean.

## 8. Backend health / runtime confirmation

Restarted cleanly (`uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`).
`GET /api/health`:

```json
{"status":"ok","service":"ryderesolve-case-api",
 "agents":{"mode":"real","provider":"gemini_native","model":"gemini-3.8-flash",
           "configured":true,"credential_configured":true}}
```

The process serving port 8000 was confirmed to be the process that logged this startup
(the previous stale-backend trap was avoided: the port-8000 PID was matched against the
reload log, and a surviving uvicorn child left over from a killed reloader was killed
explicitly before restart).

## 9. Opera live run — DISP-005

Browser: Opera 136 (headless `--headless=new`, CDP on 127.0.0.1:9444, viewport 1600×900),
real frontend on `http://127.0.0.1:5173/`. The pipeline was **not** called manually first —
the run was started by clicking **Run full analysis** in the UI.

**Gemini returned a 5xx on the Driver Advocate**, so this is the §6 failure case.
No repeated retries were issued; one bounded run.

### Stage-by-stage trace (live)

| Stage | Status | Provider / model | Latency | Claims | Failure code |
|---|---|---|---|---|---|
| Case received | COMPLETE | — | — | — | — |
| Evidence validated | COMPLETE | — | — | — | — |
| Deterministic analysis | COMPLETE | — | — | — | — |
| Rider Advocate | **COMPLETE** | gemini_native / gemini-3.8-flash | 14440 ms | 2 generated, 2 verified, 0 rejected | — |
| Driver Advocate | **FAILED** | gemini_native / gemini-3.8-flash | 7992 ms | — | **SERVER_ERROR** |
| Claim verification | COMPLETE | — | — | — | — |
| Rider Rebuttal | NOT RUN (BLOCKED) | — | — | — | — |
| Driver Rebuttal | NOT RUN (BLOCKED) | — | — | — | — |
| Rebuttal verification | NOT RUN | — | — | — | — |
| Judge | NOT RUN (BLOCKED) | — | — | — | — |
| Judge validation | NOT RUN | — | — | — | — |
| Final deterministic resolution | **COMPLETE** | code | — | — | — |
| Explanation | **COMPLETE** | code | — | — | — |

Driver Advocate failure text shown in the UI:

> Agent provider unavailable (gemini_native: SERVER_ERROR). The case analysis is unaffected.

### Frontend state (live)

- Toolbar: **"5 of 8 steps complete"**, flags `1 FAILED`, `2 BLOCKED`
- Pills: `1 DONE · 2 DONE · 3 DONE · 4 FAILED · 5 BLOCKED · 6 BLOCKED · 7 DONE · 8 DONE`
- Rail: `✓ ✓ ✓ ! – – ✓ ✓`
- Amber notice: *"The AI stages did not complete for this case. Cross-examination and the
  Judge's view could not run, so there is no AI recommendation. The decision and the
  explanation below are calculated by code and are unaffected."*
- Retry button: relabelled **"Run again"**, enabled, no automatic retry loop

The UI did **not** claim 8 of 8. It did **not** claim DONE / COMPLETE / READY anywhere the
backend said FAILED / BLOCKED / NOT RUN.

## 10. Deterministic outcome preserved?

Yes. Step 7 remained `DONE` and authoritative while the AI stages failed:

```
FINAL ACTION   NO REFUND
REFUND AMOUNT  $0.00
CONFIDENCE     95%
HUMAN REVIEW   Not required
```

Step 8 (`Why this decision?`) was likewise `DONE`, assembled from verified claims, verified
rebuttals and the policy evaluation only.

## 11. Replay success flow

Live Gemini was unavailable, so the success path was validated with the existing
`FULL_AI` replay (provenance-valid dry-run artefacts for DISP-005), through the real UI.

- `POST /resolution/run {"replayMode":"FULL_AI"}` → **200 in 0.20 s, zero provider calls**
- `workflow`: deterministic COMPLETE · advocates rider/driver COMPLETE · rebuttals
  rider/driver COMPLETE · judge COMPLETE · finalResolution COMPLETE · explanation COMPLETE

In Opera:

- Toolbar: **"8 of 8 steps complete"**, no flags
- Pills: `1–5 DONE · 6 ADVISORY · 7 DONE · 8 DONE`
- Rail: all `✓`
- Provenance badge: **`REPLAYED · advocates + rebuttals + judge`** — clearly REPLAYED, not LIVE
- Step 4 internal list: all ten stages COMPLETE; Rider 2 claims (R1, R3 verified),
  Driver 1 claim (D1 verified); 3 verified by code, 0 rejected
- Step 5: rider rebuttal 1 verified, driver rebuttal 2 verified, one round only
- Step 6: Judge `ADVISORY`, recommendation `NO REFUND`, accepted/rejected claims listed
- Step 7: deterministic outcome, identical authority to the live path
- Step 8: decisive facts, verified claims, policy rules applied

## 12. Replay provenance caveat

The app-side replay validator (`app/replay/validation.py`) rejects on:

- `SCHEMA_VERSION_MISMATCH`
- `CASE_ID_MISMATCH`
- `DISPUTE_TYPE_MISMATCH`
- `ANALYSIS_HASH_MISMATCH`
- `TRUSTED_CONTEXT_HASH_MISMATCH`

It does **not** reject on provider or model. `captured_provider` and `captured_model` are
recorded by the capture path (`app/replay/service.py`) but are never compared at load time.

**Confirmed by direct observation:** the replay artefacts loaded above were captured with
`provider: mock`, and they loaded successfully while the active runtime was
`gemini_native`. The UI displayed `mock` / `0 ms` inside the stage bodies next to a
`REPLAYED` badge, with no complaint.

So: **yes**, a replay captured under `openai_compatible` (or `mock`) can be loaded while
the runtime is `gemini_native`. Flagged as a later provenance-hardening task — not fixed
in this pass, per instruction.

The live-validation harness, by contrast, *does* gate on `capturedMode` /
`capturedProvider` / `capturedModel` / `is_live_eligible`. The gap is app-side only.

## 13. Remaining blockers

1. **Gemini availability.** The live run hit a 5xx (`SERVER_ERROR`) on the Driver
   Advocate — high demand / capacity, not an orchestration fault. The success path was
   therefore proven by replay, not live. A live all-green run is still owed once Gemini
   capacity returns.
2. **Replay provenance hardening (app-side).** As §12 — provider/model recorded but not
   enforced at load. Deferred by instruction.
3. The 79 Bucket-B test failures remain (pre-existing, untouched files).

---

## Evidence

| Artefact | Path |
|---|---|
| Live run report | `.ui-test/run23.json` |
| Live screenshots | `.ui-test/shots/200-…208-*.png`, `210-step4-failed-body.png` |
| Replay success report | `.ui-test/run24.json` |
| Replay screenshots | `.ui-test/shots/90-…96-*.png`, `211-success-step4.png` |
| Replay API response | `.ui-test/api-fullai2.json` |
