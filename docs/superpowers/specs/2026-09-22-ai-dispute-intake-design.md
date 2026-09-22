# AI-guided dispute intake design

## Purpose

Extend RydeResolve so a rider and a driver can each describe a dispute in natural language before the existing evidence-based advocate and judge workflow runs.

The outcome is a convincing hackathon demonstration of a real LLM interviewing both parties, while keeping final outcomes auditable: the LLM gathers and structures statements; existing deterministic evidence and policy services determine a refund, upheld charge, or human escalation.

## Confirmed scope

- The prototype starts from the existing synthetic Ryde case dataset.
- A user selects a trip ID in the UI. The selected trip loads its matching GPS, fare, timeline, evidence, and known dispute scenario.
- The intake screen shows two phone-shaped mobile app mockups side by side: Rider on the left and Driver on the right. They remain left/right; narrow displays may scroll horizontally rather than stacking the parties vertically.
- Each party has an independent, real LLM-backed chat interview.
- The backend stores cases and both conversation histories in SQLite.
- Tencent Hunyuan is called from the FastAPI backend. No API key is exposed to the browser.
- The existing deterministic route-deviation and no-show analysis remains the sole source for executable policy outcomes.

## Non-goals for this iteration

- Real rider/driver authentication, notifications, phone numbers, or separate devices.
- Live Ryde APIs, a production database, payment execution, or policy administration.
- The LLM deciding a refund, asserting unseen evidence, or replacing the deterministic judge/policy workflow.
- Image, damage, safety, fraud, or precedent/RAG capabilities.

## User flow

1. The demo user opens the Intake workspace and selects an existing trip: `TRP-1041`, `TRP-1042`, `TRP-1043`, or `TRP-1044`.
2. The service maps that trip ID to its existing source case (`CASE-2026-1041` through `CASE-2026-1044`). It loads the corresponding synthetic trip/evidence record without altering it.
3. The Rider phone asks an opening question. The demo user enters the rider's natural-language complaint and answers any follow-up questions.
4. The Driver phone independently asks for the driver's account and follow-up details. It receives only safe shared context: that a dispute exists, the selected trip ID, and the dispute topic once detected. It does not receive the rider's private wording as if it were evidence.
5. Each LLM response includes a short visible reply and validated structured intake state: suggested category, extracted facts attributed to that party, missing details, and an interview-complete flag.
6. When both interviews are complete, the shared strip marks the case `READY_FOR_ANALYSIS` and enables the analysis button.
7. The analysis button passes the selected existing case data to the current deterministic backend services. The result is displayed through the existing evidence, advocate, policy, confidence, and resolution panels.

## Architecture

```text
React intake workspace
  +-- selected source trip and shared status
  +-- Rider mobile-chat mockup
  +-- Driver mobile-chat mockup
       |
       v
FastAPI intake API
  +-- SQLite persistence
  +-- Hunyuan conversation client
  +-- strict structured-response validation
  +-- selected-case adapter
       |
       +--> Tencent Hunyuan (interviewing and extraction only)
       |
       +--> existing DisputeAnalysisService
              |
              +--> policy evaluation, confidence, escalation, recommendation
```

### Backend components

`IntakeCaseRepository`
: Owns SQLite records. It creates and retrieves intake cases, messages, per-party interview state, and persisted analysis snapshots. A database file under `backend/` is ignored by Git.

`ConversationService`
: Saves an incoming party message, builds the bounded prompt, calls Hunyuan, validates the structured reply, saves the assistant response and updated interview state, and returns the refreshed intake case.

`HunyuanClient`
: A small provider boundary around Tencent's Singapore TokenHub OpenAI-compatible endpoint. Its base URL, API key, and model ID come from environment variables. It supports a safe unavailable-provider response for local development without a key.

`IntakeAnalysisService`
: Verifies both interviews are complete, loads the selected synthetic source case, invokes the current `DisputeAnalysisService`, and saves the returned analysis. It does not treat party statements as verified GPS, fare, or policy evidence.

### Environment configuration

```text
TENCENT_TOKENHUB_API_KEY=...
TENCENT_TOKENHUB_BASE_URL=https://tokenhub-intl.tencentcloudmaas.com/plan/v3
TENCENT_MODEL=hunyuan-turbos-latest
```

The server must fail safely when the key is missing: it returns a readable `LLM_UNAVAILABLE` error and keeps the user's saved message. It must never put these values in a Vite environment variable or frontend source file.

## Persistence model

### `intake_cases`

- generated intake case ID
- selected source case ID and trip ID
- lifecycle status: `RIDER_INTERVIEW`, `DRIVER_INTERVIEW`, `READY_FOR_ANALYSIS`, `ANALYSING`, `AUTO_RESOLVED`, or `HUMAN_REVIEW`
- detected dispute type, created/updated timestamps, and final analysis JSON

### `intake_messages`

- message ID and intake case ID
- party: `rider` or `driver`
- sender: `user` or `assistant`
- content and timestamp

### `interview_states`

- intake case ID and party
- JSON facts explicitly attributed to that party
- JSON missing details
- suggested dispute type
- completion flag and update time

SQLite is appropriate for the local hackathon demo: it persists refreshes without requiring a database server. The repository boundary allows a future PostgreSQL implementation without changing the API routes.

## API contract

### `POST /api/intake/cases`

Creates an intake case from a selected known trip.

```json
{ "tripId": "TRP-1041" }
```

The backend rejects unknown trip IDs and maps valid IDs to existing mock source cases.

### `POST /api/intake/cases/{intake_case_id}/messages`

Adds one party message and runs one LLM interview turn.

```json
{ "party": "rider", "content": "The driver took a much longer route and I was overcharged." }
```

The response returns the new assistant message, both party states, extracted facts, missing details, and case lifecycle. Requests for an invalid party, blank content, unavailable case, malformed model reply, or unavailable LLM return an explicit error without silently creating facts.

### `GET /api/intake/cases/{intake_case_id}`

Returns the saved case and both chat histories. The frontend uses it to restore the shared workspace after refresh.

### `POST /api/intake/cases/{intake_case_id}/analyse`

Only works once both party states are complete. It runs deterministic analysis against the selected source case and returns the analysis contract already consumed by the existing detail panels.

## LLM contract and safety rules

The system prompt establishes that Hunyuan is an intake interviewer, not an arbitrator. It must:

1. Ask one concise, relevant follow-up question when material details are missing.
2. Attribute facts only to the current party, using `stated_by_rider` or `stated_by_driver` semantics.
3. Use `unknown` rather than infer unmentioned facts.
4. Never claim it checked GPS, fare, company policy, chat logs, or the other party's private account unless the backend supplied that data.
5. Never promise a refund or make a final ruling.
6. Return strict JSON matching a Pydantic model: `assistant_message`, `suggested_dispute_type`, `party_facts`, `missing_details`, and `interview_complete`.

The backend parses and validates this JSON before persisting it. If it cannot be validated, it records no extraction, gives a retry message, and leaves the interview incomplete.

## Frontend design

The Intake workspace is a mobile-app simulation, not a conventional dashboard:

- A compact prototype control at the top selects one trip ID and shows which objective evidence bundle is loaded.
- The Rider phone frame is on the left; the Driver phone frame is on the right.
- Each phone has a branded compact header, a scrollable chat area, message bubbles, an interview-progress chip, and a bottom composer/send button.
- A narrow shared status strip between/above the phones displays selected trip, detected dispute type, rider and driver completion, and analysis readiness.
- Once analysis completes, the current RydeResolve detailed decision UI is rendered below the two phone frames, preserving the existing evidence trail.

The trip selector is explicitly labelled as a prototype control. It exists so judges can run each existing synthetic case interactively while retaining deterministic GPS/fare/evidence outcomes.

## Verification

Backend tests will cover:

- valid/unknown trip selection and source-case mapping
- persistence and chronological retrieval of rider and driver messages
- party isolation in prompt construction
- valid structured Hunyuan reply persistence
- malformed or unavailable Hunyuan response handling
- analysis rejection until both interviews are complete
- hand-off for all four known source cases and their expected deterministic outcomes

Frontend checks will cover:

- rider frame stays left and driver frame stays right
- trip selector creates/loads the correct case
- each send action updates only the intended chat
- recovery of a saved intake case after reload
- disabled analysis before both interviews complete
- visible loading and API-error states

## Acceptance criteria

The work is complete when a local user can select every existing trip, have a real Tencent-backed conversation in both labelled mobile frames, refresh without losing saved interview state, and run the existing evidence-based resolution flow. The UI must visibly separate claimed statements from the selected objective evidence and must safely preserve human escalation for contradictory cases.
