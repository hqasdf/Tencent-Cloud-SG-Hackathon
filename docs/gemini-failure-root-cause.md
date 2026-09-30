# Root-cause diagnosis: Gemini advocate calls failing with 503 / timeout

**Status:** `ROOT CAUSE IDENTIFIED — PROVIDER-SIDE QUOTA EXHAUSTION (429) WITH CAPACITY
UNAVAILABILITY (503)`. No product code change is required to fix this, and no further
live Stage 6 validation is justified until quota is restored.

**Method:** offline inspection first; **8 new live requests** total (3 differential
matrix + 3 replication + 2 provenance). No prompts, schemas, PolicyTwin rules,
refund/confidence/escalation logic or fixtures were modified.

---

## 1. Headline

The failures are emitted by the **provider**, not by our code, configuration, request
shape, or network path. A minimal, well-formed 126-byte request — no
`response_format`, no `max_tokens`, tiny prompt — fails the same way the full
production payload does, and **the same payload flips between 200, 503 and 429 across
identical attempts**.

The endpoint's own error body reports HTTP **429** with `"message": "You exceede…"`
(quota exhausted). This is the same condition the repository had already recorded:
`docs/stage4c-advocate-benchmark.md` states
`GEMINI CHARACTERIZATION : PENDING — QUOTA EXHAUSTED`.

---

## 2. The evidence

| Probe | Payload | Result |
|---|---|---|
| `A_MINIMAL` | 126 B, tiny prompt, no `response_format` | **200 OK** (14.0 s) |
| `B_STRUCTURED_MINIMAL` | A + `response_format: {"type":"json_object"}` | 503 (3.7 s) |
| `C_FULL_RIDER` | production Rider payload, 17 396 B | 503 (1.6 s) |
| `A2` — byte-identical repeat of A | | **503** |
| `B2` `json_object` / `B3` `json_schema` | | 503 / 503 |
| `P1` via proxy / `P2` direct, identical payload | | **429 / 429** |

### A false positive, corrected

On the first pass `A`→200 while `B`→503, which looked like a decisive single-variable
result: `response_format` as the trigger. **Replication disproved it** — `A2`,
byte-identical to `A`, returned 503. Since the outcome varies across *identical*
requests, the cause is intermittent and server-side. `response_format` is exonerated
and must not be "fixed".

---

## 3. Ruled out, with evidence

| Class | Finding |
|---|---|
| Configuration | Base URL, model, key hygiene (trimmed, no quotes/newline), env precedence, single listener on :8000 — all correct |
| Endpoint form | `…/v1beta/openai/chat/completions`; no double `/v1`, no missing `/openai`, no duplicated path |
| Request shape | 2 messages (system, user); `temperature 0`; `max_tokens 8192`; no `reasoning_effort`; no `stream`; 17.4 KB body; ~4.3k est. input tokens |
| Payload size | Minimal request fails identically → size is not implicated |
| Schema complexity | **The JSON schema is never sent.** `complete()` ignores `request.response_schema` and hardcodes `{"type": "json_object"}`. The schema appears once, as text, inside the system prompt |
| Prompt duplication | None. 9305 = 2784 (common) + 1941 (rider) + 4108 (schema) + wrapper, each exactly once. No stale Ollama/Qwen references |
| Connection pooling | A **fresh `httpx.Client` per attempt**, constructed inside the retry loop → no stale-pool or keep-alive reuse is possible |
| Local proxy | `HTTP(S)_PROXY=http://127.0.0.1:60811` is set and httpx defaults to `trust_env=True`, so traffic does traverse it — but the proxy forwards correctly (`generate_204` → 204 in ~300 ms) and direct vs proxied results are identical |

---

## 4. Request shape as actually sent (redacted)

```json
{
  "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
  "method": "POST",
  "headerNames": ["Authorization", "Content-Type"],
  "model": "gemini-3.8-flash",
  "temperature": 0.0,
  "max_tokens": 8192,
  "response_format": {"type": "json_object"},
  "messageCount": 2,
  "roleSequence": ["system", "user"],
  "systemPromptChars": 9305,
  "userPromptChars": 6567,
  "schemaChars": 2883,
  "requestBodyBytes": 17381,
  "estimatedInputTokens": 4342
}
```

---

## 5. Anomaly worth a decision

Every response carries `Server: scaffolding on HTTPServer2`, which is **not** a Google
production banner, even though DNS resolves the host to real Google IPs
(172.217.115.4) and a direct TCP connect reaches them. Whatever is answering is a
scaffolding/stub layer.

This is recorded as an observation, not a conclusion. It does not change the finding
that the failures originate at the endpoint rather than in our request — but if a real
Gemini response is required, it is worth confirming that the environment is actually
proxying to Google's live service.

---

## 6. Product findings (none of which cause this outage)

1. **`408` maps to `REQUEST_REJECTED` (non-retryable).** A server-side request timeout
   is arguably retryable. Minor.
2. **503 backoff is `1.0 * 2^(n-1)` = 1 s.** Very aggressive against a saturated
   endpoint; the surrounding comment argues for waiting longer.
3. **Failure observability is thin.** Only the status code is retained. An opt-in,
   development-only capture of the `Server` banner and Google's canonical
   `error.status` enum would have made this diagnosis a one-call job. The existing
   "never surface the upstream body" property should be preserved.
4. **Tooling note:** this endpoint returns error bodies as a JSON **array** (`[{…}]`),
   not an object. The diagnostic helper assumed an object and so reported no category.

---

## 7. Answers to the closing questions

| Question | Answer |
|---|---|
| Does production code need changing? | **No** — no defect causes these failures. Items in §6 are optional improvements |
| Should the timeout change? | **No.** `httpx.Client(timeout=60.0)` sets connect/read/write/pool to 60 s. A success was observed in 14 s; failures return in 0.7–3.7 s |
| Should `response_format` / schema change? | **No.** Replication exonerated it, and the schema is not even transmitted |
| Should the OpenAI-compatible endpoint be replaced? | **No.** It serves 200 OK successfully. The native path uses a *different* credential (`GEMINI_API_KEY`/`GOOGLE_API_KEY`, not `AGENT_API_KEY`), so switching would not address a quota limit |
| Is another full Stage 6 live validation justified yet? | **No.** It would burn quota and reproduce the same failure. Wait for quota to be restored |

---

## 8. Diagnostic tooling

All under `backend/.live-validation/` (throwaway, gitignored):

| Script | Purpose | Live calls |
|---|---|---|
| `proxy_probe.py` | Is egress proxied, and is the proxy healthy? | 0 Gemini calls |
| `capture_request.py` | Exact redacted request shape, via the real provider | 0 |
| `diagnostic_matrix.py` | A / B / C differential matrix | 3 |
| `confirm_response_format.py` | Replication + `json_schema` probe + DNS | 3 |
| `provenance_probe.py` | Proxied vs direct, same payload | 2 |
