# Gemini reliability and offline demonstration

Gemini Native retains the configured `gemini-3.8-flash` model, 4096 output-token
budget, and maximum of one transport retry. The Google SDK makes one attempt;
the application owns the bounded retry. Advocate calls remain sequential.

HTTP 429 is recoverable `RATE_LIMITED`. Only bounded quota metric and quota ID
identifiers are retained. Request bodies, upstream messages, quota descriptions,
project dimensions, interview text and credentials are excluded from diagnostics.
Execution metadata exposes HTTP status, attempt count, retry delay and safe quota
identifiers. An SDK error body is suppressed in the normal application traceback.

`google.rpc.RetryInfo.retryDelay` and `Retry-After` are parsed. If both are usable,
the longer delay wins. Retry waits never shorten provider guidance. The existing
exponential backoff adds up to 25 percent positive jitter and is capped by the
existing maximum backoff setting. That setting also bounds the total time spent
waiting per provider completion (20 seconds by default). If the proposed wait
would exceed that budget, the call stops and returns the original cooldown. A
429 without usable timing guidance stops immediately. Temporary 5xx and network
errors can use the bounded backoff retry. HTTP timeout bounds are unchanged.

A cooldown is a minimum delay measured from the error, not a promise that quota
will become available. There is no automatic UI retry. The quota alert states
that AI analysis did not complete, compatible completed stages remain saved,
and the deterministic decision is still available. Existing signed checkpoint
identity checks, parser validation and claim/Judge validation remain enforced.

## Offline fallback

From `backend`, prepare an explicitly simulated demo using a **fresh**, ignored
artifact directory:

```powershell
python -m app.replay.cli mock-demo --case DISP-005 --output replay_artifacts/my-fresh-demo
```

This command injects the existing mock provider, reads existing intake through a
read-only SQLite connection, captures the three existing artifact stages, then
replays and revalidates all five agents with live provider calls prohibited. It
preserves existing artifact directories and checkpoints. It does not change
the runtime provider, credentials, database records or final policy decision.
The CLI's canonical case loader now includes intake, matching the API context.

To use that artifact through the existing frontend, the backend needs the
existing developer settings `REPLAY_API_ENABLED=1` and `REPLAY_ARTIFACT_DIR`
pointing to that directory. Reload the backend if those settings were changed.
Select DISP-005 and click **Show saved demo (replay)**. This sends `FULL_AI`; it
does not call Gemini. The ordinary **Run full analysis** action remains live.

The badge distinguishes `LIVE`, `REPLAYED LIVE`, and `MOCK DEMO`. This prepared
scenario is **MOCK DEMO**, never proof of successful live Gemini execution. A
previous genuine live capture can instead be shown as `REPLAYED LIVE` after
passing the same compatibility and deterministic revalidation checks.

If intake, evidence, schema or trusted context changes, replay is refused with
an explanation. It never silently falls back to live calls. Prepare a new
compatible mock demo in a fresh directory rather than deleting checkpoints.

No specific RPM, TPM or daily quota is assumed. If Google supplies no usable
quota identifier or retry guidance, the application cannot determine the quota
window or availability. Confirm actual project quota in Google's console before
an explicitly approved live retry.
