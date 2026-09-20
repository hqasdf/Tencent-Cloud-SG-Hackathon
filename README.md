# RydeResolve

An evidence-driven dispute resolution prototype for ride-hailing route-deviation and
no-show-cancellation disputes.

## ⚠️ Two implementations exist in this repository

This repository contains **two separate implementations**. Only one of them is the
running application. Please read this before exploring the code.

### 1. The active application — `backend/` + `frontend/`

**This is the running product.** Use it.

| Path | Role |
| --- | --- |
| `backend/` | FastAPI backend. Deterministic analysis engine, evidence validation, case replay, and the Stage 4 Rider + Driver advocate agents. |
| `frontend/` | React + TypeScript case workspace UI. Talks to the backend over HTTP. |

Start it with `start-backend.bat` and `start-frontend.bat`, or see
[`RUNNING.md`](./RUNNING.md) for the full instructions, URLs, and test commands.

### 2. The legacy Node pipeline — `src/`, `test/`, `policies/`, `examples/`

> **LEGACY IMPLEMENTATION — NOT USED BY THE RUNNING REACT + FASTAPI APPLICATION.
> THE ACTIVE BACKEND IS `backend/`.**

The root-level Node.js code (`src/advocates.js`, `src/judge.js`, `src/pipeline.js`,
and friends) was the original end-to-end spike. It is **retained for reference only**
and is deliberately **not** wired into the running application.

It is **frozen**: not integrated, not extended, and not deleted. Its tests still run
via `npm test` at the repository root, but passing them says nothing about the
behaviour of the active application. Do not add features here; make changes in
`backend/` instead.

## Architecture principle

The whole system is built around one rule:

> **CODE calculates facts. AI argues from facts. CODE checks AI claims.
> The Judge decides later, using only verified information.**

In practice this means:

- The deterministic engine is the single source of truth for distances, durations,
  pickup checks, waiting duration, fares, refunds, policy thresholds, evidence
  validity, confidence, and escalation.
- Advocates receive a deliberately restricted, allow-listed view of the case
  (facts, evidence, policy, policy evaluation). They never receive the
  recommendation, the refund amount, confidence, or the resolution mode, and they
  cannot recompute anything because the raw calculation inputs are never exposed.
- Every claim an advocate makes is verified deterministically before it is used.
  Rejected claims are never repaired, rewritten, or dropped — they stay visible for
  audit.
- The Judge is intentionally **not implemented** at this stage; the pipeline reports
  it as `NOT_RUN`.

## Quick start

```bash
# backend
cd backend
..\..\.workbuddy-ai\binaries\python\envs\default\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# frontend (in a second terminal)
cd frontend
npm run dev
```

Then open the frontend URL and select a case. See [`RUNNING.md`](./RUNNING.md) for
first-time setup, the `--reload` variant, and troubleshooting.

## Tests

```bash
cd backend && python -m pytest      # active application
npm test                            # LEGACY Node pipeline only
cd frontend && npm run build        # type-check + production build
```
