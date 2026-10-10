# ReStock-style bounded typed LLM request layer

**Status: implementation and offline verification complete. No live Gemini request has been made.**

This is the report the brief asked for at §19. It stops here on purpose: the
brief says *"DO NOT send a Gemini request until I review that report."* The
request shape is measured, not asserted, and the one open question that cannot
be settled offline is called out explicitly in §7 and §15.

Revision measured (md5 of the files the numbers describe):

| file | md5 |
| --- | --- |
| `app/agents/config.py` | `4315a2d84b9e169ba93eff6da89a458b` |
| `app/agents/typed_call.py` | `f759cbb1bf8d1ea59d66e75c5bdb7669` |
| `app/agents/base_advocate.py` | `032b2f03585e9f9c557503f82b7052df` |
| `app/agents/judge_agent.py` | `52323e24e72b6eb600f7d1d974f0c7fb` |
| `app/agents/rebuttal_agent.py` | `f3bcfec753af4edc5fb4c84b2fb853ae` |
| `app/agents/providers/openai_compatible.py` | `38c78e671b362e0f3ff56ee9759d0bec` |
| `app/agents/prompts/output_contract.md` | `3cd243bdc7e7e78fefc35fea6883b7b2` |
| `tests/test_agent_request_contract.py` | `138209877c6d51275cbb34d5626acd38` |

---

## 1. What ReStock actually does

Read from `services/api/src/organiser_gateway.py`.

**Request construction is one function with a hard envelope.**

| ReStock constant | Value |
| --- | --- |
| `DEFAULT_TIMEOUT_SECONDS` | 30 |
| `MAX_TIMEOUT_SECONDS` | 120 |
| `DEFAULT_NUM_PREDICT` | 2048 |
| `MAX_NUM_PREDICT` | 4096 |
| `TYPED_DECISION_NUM_PREDICT` | 1024 |

A typed specialist call sets, in one place: `format` = the JSON schema,
`think = False`, `options.num_predict = min(num_predict, 1024)`,
`options.temperature = 0`, plus a short system instruction that states the
response contract.

**Response handling is two steps and one bounded repair.** The body is validated
against a Pydantic model. `complete_json` loops `for attempt in range(2)` — that
is one repair, not a retry policy — and catches *only* the two model-output
error types (`OrganiserModelOutputMalformedError`,
`OrganiserModelOutputValidationError`). A transport failure is a different
exception class with a different code, so it never enters the repair path.

**Failure taxonomy is explicit.** `TRANSPORT_TIMEOUT`, `NETWORK_ERROR`,
`RATE_LIMITED`, `SERVER_ERROR`, `AUTH_ERROR` are separate codes. `complete()`
never retries a transport failure. Provider errors are sanitised before they
reach a log or a response.

**Context is narrow per specialist.** Each call receives the slice of material
its own job needs, not the whole session.

---

## 2. What was copied conceptually

The mapping, in the order the brief listed the patterns.

| ReStock behaviour | RydeResolve before | Change required | Done |
| --- | --- | --- | --- |
| A. Hard bounded timeout | 60 s from `.env`, 20 s code default | 30 s, enforced not promised | yes |
| B. Bounded typed-output budget | 8192 | 1024, hard ceiling | yes |
| C. Actual response schema sent to the model | `{"type":"json_object"}` only | real schema as a structured-output constraint | yes |
| D. Deterministic temperature | 0 already | pinned explicitly in the payload | yes |
| E. Reduced reasoning | unset → provider default (medium) | `reasoning_effort="low"` | yes |
| F. Concise system instruction | rules **plus** 3.5–4.5 KB of pasted schema | rules plus a short contract; schema no longer pasted | yes |
| G. Exactly one JSON object, no Markdown | one sentence at the end of the rules | explicit shared contract | yes |
| H. No duplicated explanations | `reasoningSummary` guidance existed | contract says "do not repeat the same argument in more than one field" | yes |
| I. No think-aloud | present in the rules | kept, restated in the contract | yes |
| J. Pydantic validation after the response | already present | **kept unchanged** as defence in depth | yes |
| K. One repair for malformed/schema-invalid output only | none — a malformed response failed the stage | `complete_typed`, `MAX_REPAIR_ATTEMPTS = 1` | yes |
| L. Transport failure kept separate from model-output failure | partially: separate codes, but no repair path existed | separation is now *structural*: different exception base class | yes |
| M. Sanitised provider errors | already present | unchanged | yes |
| N. No endless transport retry loop | `max_retries=2` from `.env` | `max_retries=1` | yes |
| O. Narrow context per specialist | already: three separate context builders | unchanged | yes |
| P. One bounded repair, never a rewrite of rejected claims | n/a | trust-boundary rejections are unrepairable *structurally* | yes |

---

## 3. What was Ollama-specific and NOT copied

| ReStock construct | Why it was not copied |
| --- | --- |
| `format` = schema (top-level) | Ollama's native field. The OpenAI-compatible transport takes `response_format`, which is what was implemented instead. |
| `think = False` | An Ollama knob. Gemini has no equivalent that turns reasoning off; `reasoning_effort="low"` is the closest documented control, and it cannot be combined with `thinking_level`/`thinking_budget`. |
| `options.num_predict`, `options.temperature` | Ollama's nested `options` block. OpenAI-compatible uses top-level `max_tokens` and `temperature`. |
| `POST /api/chat` | Vendor path. This project posts to `{base_url}/chat/completions`. |
| `X-API-Key` header | Ollama Cloud auth. This project uses `Authorization: Bearer`. |
| The `<restock_decision_v1>` envelope | ReStock's own wire format for its own decision object. RydeResolve already has its own schemas; adopting the envelope would have copied unrelated business logic. |
| ReStock's specialist prompts and domain rules | Explicitly out of scope — the brief says *"Do NOT copy unrelated ReStock business logic."* |

The distinction that mattered: **the envelope and the transport are ReStock's;
the discipline is portable.** Timeout, budget, temperature, one repair, and the
provider/model-output split were copied. The wire format was not.

---

## 4. Files changed

New:

| file | what it is |
| --- | --- |
| `app/agents/typed_call.py` | `complete_typed()` — the one bounded repair, plus `ModelOutputContractError` |
| `app/agents/prompts/output_contract.md` | the shared output contract (replaces the pasted schema) |
| `tests/test_agent_request_contract.py` | 48 offline tests for the request layer |
| `.live-validation/measure_request.py` | the BEFORE/AFTER measurement harness |

Modified:

| file | change |
| --- | --- |
| `app/agents/config.py` | bounded defaults 30 / 1024 / `low`; `_bounded_float_env` and `_bounded_int_env` **reject** a value above the ceiling instead of clamping it; `_reasoning_effort_env` |
| `app/agents/base_advocate.py` | calls `complete_typed`; schema no longer pasted into the system prompt; `AdvocateAgentError` now derives from `ModelOutputContractError` |
| `app/agents/judge_agent.py` | same three changes |
| `app/agents/rebuttal_agent.py` | same three changes |
| `app/agents/providers/openai_compatible.py` | `build_response_format()` / `provider_schema()`; `response_format` now carries the real schema; `stream: false` sent explicitly |
| `app/agents/prompts/rider_advocate.md`, `driver_advocate.md` | the fenced JSON example removed — a code-fenced example in the prompt invites the model to fence its own answer, which the contract forbids |
| `tests/test_provider_tokenhub.py`, `tests/test_benchmark.py` | updated to the new bounded contract and the repair semantics |
| `app/replay/artifacts.py`, `app/replay/service.py` | from the earlier checkpoint/resume task, not this brief |

Unchanged on purpose: every validator (`agents/monetary.py`, Stage 4
`fact_values`/`values_match`), `AdvocateClaimVerificationService`,
`JudgeOutputValidationService`, `RebuttalValidationService`, and all three
deterministic engines.

---

## 5. Before / after request shape

Measured offline for `DISP-005` through the real agents and the real provider
with a stub transport. `before` = `request-shape-before.json`,
`after` = `request-shape-after.json`.

### Settings

| field | BEFORE | AFTER |
| --- | --- | --- |
| `timeoutSeconds` | 60 | **30** |
| `maxTokens` | 8192 | **1024** |
| `temperature` | 0 | 0 |
| `reasoningEffort` | *(absent)* | **low** |
| `maxRetries` | 2 | **1** |
| `responseFormat` | `{"type":"json_object"}` | **`{"type":"json_schema","json_schema":{name,schema}}`** |
| `stream` | *(absent)* | **false** |

### Per role

| role | metric | BEFORE | AFTER | delta |
| --- | --- | --- | --- | --- |
| RIDER_ADVOCATE | systemPromptChars | 9305 | 5858 | **−3447** |
| | schemaCharsInPrompt | 4487 | **0** | −4487 |
| | schemaDuplicatedInPrompt | yes | **no** | |
| | totalInputChars | 15872 | 12425 | −3447 |
| | est. input tokens | 3968 | 3106 | −862 |
| | bodyBytes | 17379 | 16307 | −1072 |
| | maxTokens | 8192 | 1024 | −7168 |
| DRIVER_ADVOCATE | systemPromptChars | 9606 | 6159 | **−3447** |
| | totalInputChars | 16173 | 12726 | −3447 |
| | bodyBytes | 17685 | 16613 | −1072 |
| RIDER_REBUTTAL | systemPromptChars | 9476 | 5800 | **−3676** |
| | totalInputChars | 16281 | 12605 | −3676 |
| | bodyBytes | 17737 | 16698 | −1039 |
| DRIVER_REBUTTAL | systemPromptChars | 9476 | 5800 | **−3676** |
| | totalInputChars | 16282 | 12606 | −3676 |
| JUDGE | systemPromptChars | 10260 | 7567 | **−2693** |
| | totalInputChars | 19228 | 16535 | −2693 |
| | bodyBytes | 20937 | 20760 | −177 |

**Across the five roles: 83,836 → 66,897 input characters, −16,939 (−20.2%).**

The schema is no longer sent twice. It used to be counted twice in the bytes
actually transmitted — once as `json_object` (nothing) plus the full
pretty-printed schema pasted into the system prompt (3.5–4.5 KB per call). It is
now sent once, as the provider's own structured-output constraint, and the
projection to the documented subset is *smaller* than the pretty-printed
original (e.g. `AdvocateOutput`: 4487 chars pretty-printed in the prompt →
2710 chars compact in `response_format`). `bodyBytes` therefore falls less than
`totalInputChars`, which is the correct shape: the schema moved, it did not
merely disappear.

---

## 6. `timeout = 30` confirmed

Yes, and it is enforced rather than promised:

* `DEFAULT_TIMEOUT_SECONDS = 30.0`, `MAX_TIMEOUT_SECONDS = 30.0`.
* `AGENT_TIMEOUT_SECONDS=30` in `.env`.
* A value above 30 is **rejected** with an error naming the ceiling, not clamped
  — clamping would leave an operator believing `AGENT_TIMEOUT_SECONDS=120` was
  in force.
* `test_timeout_is_thirty_seconds`, `test_the_provider_is_constructed_with_the_thirty_second_timeout`,
  `test_a_timeout_above_the_ceiling_is_rejected_not_clamped`.

---

## 7. `output cap = 1024` confirmed — with one STOP-and-report finding

Yes for the cap: `DEFAULT_MAX_TOKENS = 1024`, `MAX_OUTPUT_TOKENS = 1024`,
`AGENT_MAX_TOKENS=1024` in `.env`, above-ceiling values rejected.

**But the brief's §3 asks a second question, and the honest answer is "not
settled":**

> *"If 1024 genuinely cannot hold one of our required schemas after prompt and
> schema simplification, STOP and report why before changing the cap."*

Offline, the cap cannot be tested against a real completion — that needs a live
call, which the brief forbids. What *can* be measured offline is how large a
valid answer is. Measured with `model_dump_json(by_alias=True)`:

| shape | chars | tokens @4 ch | tokens @3.5 ch | fits 1024? |
| --- | --- | --- | --- | --- |
| ADVOCATE — terse (1 claim, 1 fact) | 551 | 137 | 157 | yes |
| ADVOCATE — realistic (2 claims, 2 facts, 1 disputed) | 1826 | 456 | 521 | yes |
| **ADVOCATE — maximal (4 claims, ~500-char `claim` + ~500-char `reasoningSummary` each, 3 facts, 2 disputed)** | **5610** | **1402** | **1602** | **NO** |
| REBUTTAL — terse (1 response) | 458 | 114 | 130 | yes |
| REBUTTAL — realistic (2 responses) | 1251 | 312 | 357 | yes |
| REBUTTAL — maximal (4 responses) | 3371 | 842 | 963 | yes |
| JUDGE — maximal (4+4 accepted, 2+2 rejected, 2+2 rebuttals, 3 uncertainties) | 2634 | 658 | 752 | yes |
| The mock provider's own output (all five roles) | 543–1059 | 135–264 | 155–302 | yes |

**Finding.** The rebuttal and the Judge fit at every verbosity tested. The
advocate fits comfortably at realistic verbosity (456 tokens) but **not** at
maximal verbosity: 4 claims each carrying ~1 KB of prose needs ~1400 tokens, and
that is the JSON half of the budget only — for a thinking model, reasoning is
billed against the same 1024.

**What this means.** The cap is safe *conditional on the model obeying the
contract's verbosity rules* ("short, evidence-grounded sentences"; "do not repeat
the same argument in more than one field"). That condition is exactly what the
new prompt is designed to create, and it is exactly what cannot be verified
without a live call.

**I did not change the cap.** Raising it would be guessing at the answer the
brief asked me to establish. The correct next step is a live run that inspects
`finish_reason` per stage:

* `finish_reason == "stop"` on all five stages → the cap is right, and the
  prompt simplification is sufficient.
* `finish_reason == "length"` on the advocate → the failure is a *prompt*
  problem first. The measured evidence already points at the specific cause:
  per-claim prose, not the schema. The fix to try is a hard per-claim length
  rule, not a bigger budget.

Truncation is already handled safely in the meantime: `LlmCompletion.truncated`
is `finish_reason == "length"`, a truncated response is reported as
`OUTPUT_TRUNCATED` rather than `MALFORMED_JSON`, and `OUTPUT_TRUNCATED` is
deliberately **not** repairable — re-asking with the same budget truncates the
same way.

---

## 8. `reasoning_effort = low` confirmed

Yes: `DEFAULT_REASONING_EFFORT = "low"`, `AGENT_REASONING_EFFORT=low` in `.env`,
and `low` appears in the outbound payload for all five roles (verified at the
wire level, not just on the settings object).

Two deliberate details:

* Unset no longer means "let the provider choose a medium default". The brief
  forbids that, and medium spends output budget on deliberation a typed decision
  does not need.
* The default can still be stepped back from with an explicit empty value
  (`AGENT_REASONING_EFFORT=`), which restores "defer to the provider" without a
  code change. An unsupported value is rejected rather than passed through.

---

## 9. `temperature = 0` confirmed

Yes. `AgentSettings.temperature = 0.0`, `AGENT_TEMPERATURE=0` in `.env`, and
`payload["temperature"] = 0.0` measured at the wire level for all five roles.
This was already true; it is now pinned by a test rather than assumed.

---

## 10. Actual structured schema confirmed

Yes. This was the real defect: `AgentCompletionRequest.response_schema` existed
and was populated, and `OpenAiCompatibleProvider` ignored it, sending only
`{"type":"json_object"}`. The schema travelled in the system prompt as prose and
never constrained generation.

Now:

```
Pydantic contract → checked-in JSON schema → provider_schema() projection
                  → response_format.json_schema → model generates
                  → returned JSON → Pydantic validation (unchanged)
```

Measured `response_format` after the change:

| role | type | name | projected schema chars |
| --- | --- | --- | --- |
| RIDER/DRIVER_ADVOCATE | `json_schema` | `AdvocateOutput` | 2710 |
| RIDER/DRIVER_REBUTTAL | `json_schema` | `RebuttalOutput` | 2911 |
| JUDGE | `json_schema` | `ryderesolve_output` | 2751 |

`provider_schema()` is a **projection, not a redefinition**, and it is applied to
the outbound request only:

* `$schema` / `$id` / `$comment` are dropped — they describe the document, not
  the value, and are not in the documented subset.
* A bare multi-type array `{"type":["number","boolean","string"]}` becomes
  `anyOf` — the only construct in our three schemas that is *not* documented as
  supported in that form. `anyOf` means the same thing and *is* documented.
* The `["<type>","null"]` form is left alone, because that one is documented.

The schema the response is validated against is untouched and remains the
authority, so this cannot loosen the contract.

One cosmetic gap: the Judge schema has no `title`, so its `json_schema.name`
falls back to `ryderesolve_output` instead of `JudgeOutput`. It is a label, not a
constraint. Adding `"title": "JudgeOutput"` is a one-line follow-up.

---

## 11. One malformed-output repair confirmed

Yes. `app/agents/typed_call.py`:

* `MAX_REPAIR_ATTEMPTS = 1` — a constant, not a config knob.
* `REPAIRABLE_OUTPUT_CODES = {"MALFORMED_JSON", "SCHEMA_VALIDATION_FAILED"}` —
  deliberately not a catch-all. `OUTPUT_TRUNCATED` is excluded (same budget
  truncates the same way); `WRONG_SIDE` is excluded (a schema-valid response
  naming the other side is not a shape problem).
* The repair appends a short instruction to the *existing* user prompt. It does
  not resubstitute the prompt, so the model still sees the context it was
  answering.
* After the second invalid response the stage **fails**. The second failure is
  chained to the first (`raise error from first_failure`), so the traceback shows
  the model was malformed *twice* rather than once unluckily.
* The returned completion covers **both** attempts: `attempt_count` is summed
  (2), tokens and latency are summed, and a token count is summed only when
  **both** attempts reported one — adding a known count to an unknown one would
  invent a total the provider never reported.

Note: a bug was found and fixed here during this work. The chained raise
originally read `raise error from first` where `first` was the *completion*, not
the exception, producing
`TypeError: exception causes must derive from BaseException` on the
second-failure path. It was caught by
`tests/test_rebuttal.py::test_malformed_rebuttal_json_fails_safely` and the two
benchmark malformed-output tests. This is the exact path the brief cares most
about, and it would have been the least-exercised one live.

---

## 12. Provider failures separated from model-output failures

Yes, and the separation is **structural rather than a check**.

* A provider failure (429 / 503 / timeout / network / auth) is raised by the
  provider as `LlmProviderError` with a `ProviderErrorCode`. A model-output
  failure is raised by the agent as a subclass of `ModelOutputContractError`.
* `_repairable()` is `isinstance(error, ModelOutputContractError) and code in
  REPAIRABLE_OUTPUT_CODES`. A `LlmProviderError` is not an instance, so it cannot
  reach the repair path — there is no "and not a provider error" condition that
  could be forgotten.
* An unrecognised error is not repairable, which is the safe default: a bug in a
  parser is not something to re-ask a model about.
* Parameterised test over `TIMEOUT`, `NETWORK_ERROR`, `RATE_LIMITED`,
  `SERVER_ERROR`, `AUTHENTICATION_FAILED`: **zero repair requests** for every
  one.

The third case, which is RydeResolve's own, is also structural: a response that
parses and validates but whose *claims* are rejected
(`EVIDENCE_ID_NOT_FOUND`, `POLICY_REF_NOT_FOUND`,
`FACT_CONTRADICTS_DETERMINISTIC_ANALYSIS`, `UNKNOWN_ASSERTED_FACT`,
`INVENTED_MONETARY_VALUE`) is rejected by
`AdvocateClaimVerificationService`, which runs **downstream** of `complete_typed`
and therefore cannot reach the repair loop at all. There is no code path that
could ask the model to rewrite a rejected claim into an accepted one. Evidence
stays first: invalid claim → rejected → visible in the audit → never trusted.

---

## 13. Checkpoint / resume status

Unchanged by this brief, and already in place from the earlier task.

* The checkpoint **is** the replay artefact. `ReplayArtifactBase` already carries
  case id, schema/format versions, `deterministicAnalysisHash`,
  `trustedContextHash`, mode/provider/model provenance, raw output, execution
  metadata and the verification fingerprint. No new checkpoint format was
  introduced.
* One artefact is written per successful validated stage, so a later provider
  failure cannot destroy earlier work. In particular a Judge failure preserves
  the advocate and rebuttal checkpoints.
* Resume is a prefix chain, not a policy: `NONE ⊂ ADVOCATES ⊂
  ADVOCATES_AND_REBUTTALS ⊂ FULL_AI`. Replaying rebuttals without the advocates
  is *unrepresentable*, so dependency invalidation needs no code.
* Validation status is deliberately **not** stored. The verified/rejected split
  is recomputed on every load under current code, which is what makes
  "revalidates under current code" true rather than asserted.
* Provenance — "did this come from the model under test?" — is the one check the
  replay layer does not make, so it is enforced harness-side and yields
  `NOT_ELIGIBLE`, not `REFUSED`.

Interaction with this brief: the repair loop reports `attempt_count = 2` on a
repaired stage, and the checkpoint stores the *successful* completion, so a
resumed stage does not re-pay for the repair.

---

## 14. Offline test results

**Zero external network for every number below.** The egress guard is a
socket-level block installed before the run; it was re-proved non-vacuous first.

| check | result |
| --- | --- |
| Egress guard self-test | blocked `generativelanguage.googleapis.com`; blocked raw connect to `8.8.8.8`; loopback allowed |
| `tests/test_agent_request_contract.py` **under `-p netguard`** | **48 passed** |
| Full backend suite | **79 failed / 579 passed** |
| Full suite failures that are *not* the pre-existing Bucket B | **0** |

The 79 failures are the known Bucket B — every one is a `StopIteration` (or its
`test_advocate_api.py` variant, `"Case CASE-2026-1041 was not found"`) on the
fixture id `CASE-2026-1041`, which was replaced by `DISP-*` in an earlier stage.
They are confined to `test_advocate_agents.py`, `test_advocate_api.py`,
`test_advocate_context.py`, `test_advocate_orchestrator.py`,
`test_claim_verification.py` and `test_provider_mock.py`. Bucket B is unchanged:
79 before, 79 after. Passing went 528 → 579.

What the 48 contract tests actually prove, mapped to the brief's §17 list:

| brief's requirement | test |
| --- | --- |
| timeout exactly 30 | `test_timeout_is_thirty_seconds`, `test_the_provider_is_constructed_with_the_thirty_second_timeout` |
| cap exactly 1024 | `test_typed_output_cap_is_1024` |
| `reasoning_effort` low | `test_reasoning_effort_defaults_to_low` |
| temperature 0 | `test_temperature_is_zero` |
| the real schema reaches the provider payload | `test_the_real_schema_reaches_the_provider_payload` |
| the schema is no longer duplicated in the prompt | `test_the_schema_is_no_longer_duplicated_in_the_prompt` |
| no streaming | `test_streaming_is_disabled_explicitly` |
| successful structured output parses | `test_a_valid_response_parses_and_validates` |
| schema validation still runs | `test_schema_validation_still_runs_after_the_provider_constraint` |
| malformed output gets exactly one repair | `test_malformed_output_gets_exactly_one_repair` |
| a second malformed output fails | `test_a_second_malformed_output_fails` |
| 429 / 503 / timeout get no repair | `test_provider_failures_are_never_repaired` (parameterised) |
| a trust-boundary rejection gets no repair | `test_a_trust_boundary_rejection_does_not_trigger_a_repair` |
| the API key is never logged | `test_the_credential_never_appears_in_a_provider_error`, `..._not_in_the_request_body_or_url`, `..._only_materialised_in_the_auth_header` |
| existing claim verification unchanged | `test_claim_verification_is_unchanged_by_the_request_layer` |
| Judge trust boundary unchanged | `test_the_judge_trust_boundary_is_unchanged` |
| deterministic engines untouched | `test_the_deterministic_engines_are_untouched` |
| schema projection is semantics-preserving | `test_document_meta_keywords_are_stripped`, `test_a_bare_multi_type_array_becomes_any_of`, `test_a_nullable_type_array_is_left_alone`, `test_the_projection_is_applied_recursively`, `test_the_real_advocate_schema_projects_without_a_bare_union` |

Also verified: a truncated response is **not** treated as valid
(`test_a_truncated_response_is_not_treated_as_valid`), and unknown token counts
stay unknown across a repair (`test_unknown_token_counts_stay_unknown_across_a_repair`).

No live call was made. Confirmed by the absence of any new artefact:
`backend/benchmark_results/` last written 2026-09-28, and
`.live-validation/results/` last written 2026-10-05 10:05 — both before this
work. The `.env` Gemini credential is present, so "no live call" is restraint,
not inability.

---

## 15. Did any existing behaviour or regression change?

Yes — five deliberate behaviour changes, all required by the brief. Every one is
covered by an updated test, and **none of them is a silent change**.

| change | old | new | why | risk |
| --- | --- | --- | --- | --- |
| Default timeout | 60 s (`.env`), 20 s (code) | 30 s, hard ceiling | brief §2 | a slow provider now fails where it previously succeeded. Bounded by design. |
| Default output cap | 8192 | 1024, hard ceiling | brief §3 | **the one open risk** — see §7 |
| Default reasoning | unset (provider default = medium) | `low` | brief §4 | less deliberation; the measurement will say whether that costs quality |
| Default `max_retries` | 2 | 1 | brief §1 pattern K / §13 | one fewer transport retry |
| Advocate/Judge/Rebuttal system prompt | rules + pasted schema | rules + short contract | brief §6/§7 | the model no longer sees the schema in prose, only as a generation constraint |

Tests updated to match, rather than deleted:

* `test_default_max_tokens_is_unchanged_for_cloud_models` — pinned 2000, now
  pins the bounded default and the ceiling.
* `test_default_agent_request_sends_no_reasoning_effort` — asserted the knob was
  absent by default; now asserts `low` is requested, and that an explicit empty
  value still steps it back.
* `test_max_tokens_is_configurable_from_the_environment` — used 8192 as the
  example; now asserts an in-range value is accepted *and* that an above-ceiling
  value is rejected by name.
* The two malformed-output tests and `test_malformed_rebuttal_json_fails_safely`
  — previously one canned malformed response; now serve two, because a malformed
  response earns exactly one repair. Their assertions are unchanged in
  substance: malformed model output is still reported as a model-output failure,
  never as a provider outage.
* `test_benchmark.py::test_each_call_uses_the_real_context_and_prompt` —
  asserted `'Required output schema' in <system prompt>`; the schema is no longer
  pasted there, so it now asserts the contract is present instead.

**Not changed, deliberately:**

* Every trust boundary: claim verification, Judge context allow-list, rebuttal
  target scoping, `extra="forbid"` on model-facing schemas.
* The three deterministic engines.
* `benchmarks/models.py` still configures 60 s / 8192 for the Stage 4C
  catalogue. This is **inconsistent with the new production shape and is flagged,
  not fixed**: the Stage 4C write-up records a specific configuration, and
  silently rewriting it would make a recorded result unreproducible. Changing it
  is a decision for the user, not a side effect of this brief.
* Bucket B (79 failures) untouched.

---

## What I recommend next

1. **Review §7 before anything else.** The 1024 cap is the only item I could not
   settle offline, and it is the item most likely to bite.
2. If you accept the finding, the first live run should be **one stage, not
   five**: `DISP-005` Rider advocate only, with `finish_reason` inspected. That
   answers the truncation question for ~3k input tokens instead of ~67k.
3. Do not raise the cap until that run says `finish_reason == "length"` *and* the
   output is otherwise well-formed. The measured evidence says the pressure
   comes from per-claim prose, which is a prompt fix.

**No live Gemini request has been made. I am stopping here as instructed.**
