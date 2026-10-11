# Portable offline demo and manual Opera checklist

## Regenerate; do not copy private data

Package the source, dependency manifests/lockfiles and synthetic case fixtures.
Do not copy `backend/.env`, API keys, `intake.db`, signed checkpoints, browser
profiles or the existing local replay artifacts. Artifacts are bound to the
current intake/evidence/schema identity and may contain AI-generated wording.

On a new machine:

1. Install backend requirements in a virtual environment and run `npm ci` in
   `frontend`. Start the existing backend/frontend entry points using offline
   mock settings from the credential-free `.env.example`. Keep secrets server-side.
2. Open DISP-005 once in the website. Its intake panel automatically creates an
   empty local intake record if none exists. Do **not** send an interview message;
   that would need an intake provider. Wait for the panel to finish loading.
3. From `backend`, run:

   ```powershell
   python -m app.replay.cli mock-demo --case DISP-005 --output replay_artifacts/submission-demo
   ```

   Choose a fresh directory. The command injects only the mock provider, reads
   existing intake without writing it, verifies all five stages and tests a
   full replay with provider calls prohibited. Opening the case **before**
   capture matters: otherwise automatic empty-intake creation changes the hash.
4. Set the existing local developer flags `REPLAY_API_ENABLED=1` and
   `REPLAY_ARTIFACT_DIR` to that directory's absolute path. Reload the new
   backend to apply its configuration.
5. Select DISP-005 and click **Show saved demo (replay)**. Expect **MOCK DEMO**,
   five completed roles, and code-controlled `NO_REFUND`, SGD 0.00.

This uses the current UI, replay store and validators. It introduces no seed
database, new API or alternate architecture. It demonstrates the five-agent
resolution flow with explicitly simulated output. A fresh machine's empty
intake is labeled partial, so this demo alone does not prove completed AI
interviews; the offline integration tests cover that lifecycle. Real interviews
and a live Gemini demonstration require separate authorization and quota.

If intake/evidence/schema changes, replay should be refused. Regenerate into a
new directory; do not delete checkpoints or silently invoke live analysis.

## Manual Opera checklist for the existing machine

Use the existing Opera tab at `http://127.0.0.1:5173`; backend is on port 8000.
These steps are manual. No browser rendering or interaction has been automated.

- Switch among DISP-002 through DISP-009. Check breadcrumb, parties and evidence
  belong to the selected case. Switch quickly and confirm late results do not
  replace the selected case.
- For DISP-005, inspect the existing saved intake, attributed statements and
  source references. Refresh once and confirm saved lifecycle/accounts remain.
  Do not send messages or click the live analysis buttons during offline QA.
- Click **Show saved demo (replay)** once. Confirm **MOCK DEMO**, four completed
  advocate/rebuttal stages and a completed Judge. Inspect verified/rejected
  claims, cross-examination and the Judge's advisory wording.
- Confirm the final decision remains `NO_REFUND`, SGD 0.00; the final panel
  explains that code controls the decision. Inspect confidence and explanation.
- Inspect saved human-review cases without starting live analysis. Confirm the
  human-review label is distinct from an automated decision.
- Check expanded panels, scrolling, text wrapping and controls at a narrow
  window width. This visual QA cannot be established by TypeScript/build checks.
- If an existing failed result is available, confirm the quota alert says AI
  did not complete and describes known/unknown cooldown and saved work. Do not
  deliberately trigger a live 429 to test it.

**Run full analysis**, **Retry / reuse completed work**, **Advocates only** and
intake **Send** can call the configured live Gemini provider on this machine.
Do not use those actions without a separate approved live validation.
