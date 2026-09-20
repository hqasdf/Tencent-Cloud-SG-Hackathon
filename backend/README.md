# RydeResolve Case API

FastAPI backend for RydeResolve. It owns the deterministic dispute engine and the
Stage 4 Rider + Driver advocate agents.

## Architecture principle

> **CODE calculates facts. AI argues from facts. CODE checks AI claims.
> The Judge decides later, using only verified information.**

The deterministic engine (Milestone 3) is the single source of truth for distances,
durations, pickup checks, waiting duration, fares, refunds, policy thresholds,
evidence validity, confidence, and escalation. The advocates argue from that
evidence and never recompute anything. A separate deterministic verifier then checks
every claim an advocate makes before it can be used. The Judge is intentionally not
implemented in this milestone.

## Local development

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

If `python -m uvicorn` reports `No module named 'uvicorn'`, you are running a
different interpreter than the one you installed into. See the repository
[`RUNNING.md`](../RUNNING.md) for the two working forms (activate the venv first, or
invoke the venv interpreter directly with `PYTHONPATH=.`).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness, plus the resolved agent configuration (never credentials). |
| `GET` | `/api/cases` | Case summaries for the dashboard. |
| `GET` | `/api/cases/{case_id}` | Full case record, including timeline and evidence. |
| `GET` | `/api/cases/{case_id}/analysis` | Deterministic analysis: policy evaluation, recommendation, confidence, escalation. |
| `POST` | `/api/cases/{case_id}/advocates/run` | Runs both advocates, verifies every claim, and returns the audit trail. |

`advocates/run` is **POST**, not GET, because running advocates may trigger external
model calls and incurs latency and token cost. It is intended to become a persisted,
operator-triggered action. In `mock` mode it is deterministic and free.

### Error behaviour for `advocates/run`

| Status | Condition |
| --- | --- |
| `404` | Unknown case. |
| `422` | CaseReplay validation failed for the case record. |
| `503` | Agent misconfiguration (for example `AGENT_MODE=real` without credentials). |

A provider outage is **not** an HTTP error: the affected side reports
`status: "FAILED"` with a structured `failureReason`, and the rest of the response —
including the deterministic analysis — remains readable. This is deliberate: the
result contract is "degrade, don't 500". Failures never leak credentials, headers, or
stack traces.

## Agent configuration

Copy `.env.example` to `.env` and adjust. The defaults require no credentials.

| Variable | Default | Notes |
| --- | --- | --- |
| `AGENT_MODE` | `mock` | `mock` or `real`. |
| `AGENT_PROVIDER` | `mock` | `mock` or `openai_compatible`. |
| `AGENT_MODEL` | — | Required in real mode. |
| `AGENT_BASE_URL` | — | OpenAI-compatible base URL; required in real mode. |
| `AGENT_API_KEY` | — | Required in real mode. Never logged. |
| `AGENT_TIMEOUT_SECONDS` | `20` | Per-request timeout. |
| `AGENT_TEMPERATURE` | `0.0` | Advocates should be deterministic, so this defaults to 0. |

**Real mode fails loudly.** If `AGENT_MODE=real` is set without valid credentials,
the service raises `AgentConfigurationError` and `advocates/run` returns `503`. It
never silently falls back to mock output, because presenting canned text as though a
real model produced it would destroy the audit value of the whole pipeline.

### Providers

- **`mock`** — deterministic, offline, no credentials. Output is *derived* from the
  supplied context rather than being a fixed string, so it stays realistic as
  fixtures evolve while remaining fully reproducible. This is the default and what
  the test suite runs against.
- **`openai_compatible`** — a generic OpenAI-compatible chat-completions client.
  Deliberately vendor-agnostic: nothing in the code assumes a specific vendor's
  endpoint or auth scheme. A provider with a genuinely different request shape
  belongs in its own module behind the same `LlmProvider` protocol.

## Stage 4 safety properties

These are enforced in code and covered by tests. Do not weaken them.

1. **Allow-list context.** `AgentCaseContext` is built by explicit projection, never
   by serialising the whole case. Advocates receive facts, evidence, policy, and
   policy evaluation — and no answer.
2. **The answer is structurally unreachable.** The context never contains
   `resolutionRecommendation`, `refundAmount`, the recommended action, confidence,
   confidence penalties, `resolutionMode`, or `escalationReasons`. `analysis_input`
   is `Field(exclude=True)`, so raw calculation inputs cannot leak.
3. **Fairness by construction.** No rating, trip history, prior-complaint, or
   reputation field exists in the context at all. The guarantee is architectural,
   not merely a prompt instruction.
4. **Independent advocacy.** Rider output is never passed to the Driver and vice
   versa. There is no cross-examination.
5. **Structured facts are hard-verified.** `assertedFacts` are compared against the
   already-computed trusted values with a `1e-6` tolerance. A contradiction is
   rejected. Facts are never recalculated by the agent.
6. **Prose contradictions are warnings only.** Milestone 4 deliberately does not
   build a second AI system to judge whether the first AI hallucinated.
7. **Rejected claims are never repaired.** A hallucinated `E99` stays rejected
   exactly as produced and remains visible in the audit output.
8. **Failure isolation.** One advocate failing does not corrupt the other advocate
   or the deterministic analysis.
9. **The orchestrator never mutates deterministic data.** It reads the canonical case
   and analysis and returns a separate response object. There is deliberately no
   method that merges advocate output back into the case.
10. **The Judge is `NOT_RUN`.** It is not implemented at this stage.

## Data

The app deliberately uses local synthetic data through `MockCaseRepository`. API
routes depend on `CaseService`, so a future `PostgresCaseRepository` can replace the
in-memory source without changing route behaviour.

## Tests

```bash
python -m pytest          # full suite
python -m pytest -q       # quiet
```

The suite covers the deterministic engine, the context allow-list (including
negative leak assertions), claim verification, the shared output schema, the
provider abstraction and settings, both agents, the orchestrator's failure
isolation, and the API end to end. It includes a deliberate hallucination test
asserting that a claim citing `E_DOES_NOT_EXIST` is rejected, stays in the audit
output, and never enters `verifiedClaims`.
