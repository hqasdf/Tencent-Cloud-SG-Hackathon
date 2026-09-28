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

It measures how the one selected model behaves, for regression testing and for
provider-reliability, latency, token, hallucination and claim-verification metrics.
The CLI ranks nothing and computes no aggregate quality score.

- `MODEL_CATALOGUE` and `--models a,b` remain multi-model capable on purpose. That is a
  retained capability, not a Stage 4C goal.

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

## Environment / tooling

- The project virtualenv is **not** in the repo. Use
  `C:\Users\Han Qian\.workbuddy-ai\binaries\python\envs\default\Scripts\python.exe`.
  The managed interpreter has no pytest installed.
- Run backend tests with that interpreter from `backend/` (it needs `backend/` on the
  path; `pytest.ini` handles it, but ad-hoc scripts need `PYTHONPATH=.`).
- **79 pre-existing test failures** ("Bucket B") are expected and must not be touched.
  Every one is a `StopIteration` on `CASE-2026-1041`, a fixture id replaced by `DISP-*`
  in an earlier stage. Baseline: 263 passed / 79 failed.
- Fixture ambiguity: route `DISP-005`/`DISP-008` and no-show `DISP-002`/`DISP-004` are
  each two equivalent clean fixtures, so benchmark case selection cannot be unique.
  It picks the lowest id deterministically and records the tie.
- `backend/benchmark_results/` is gitignored. The Stage 4C write-up lives in
  `docs/stage4c-advocate-benchmark.md`.
