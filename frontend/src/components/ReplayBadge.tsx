import type { ReplayMetadata } from "../types/replay";
import { replayedStages } from "../types/replay";

/**
 * Provenance badge for the resolution run.
 *
 * This component exists because the obvious implementation is wrong. The
 * existing badge reads `agentRun.mode` and renders "REAL MODEL" or "MOCK" —
 * and `agentRun.mode` describes the *configured* provider, not what happened
 * during this request. A FULL_AI replay is served entirely from stored
 * artefacts, spends nothing, and can still report `mode: "real"` because that
 * is what the server is configured for. Rendering "REAL MODEL" over it would
 * claim a live Gemini call that never happened.
 *
 * So a replayed run never shows the model badge. It shows REPLAYED, naming the
 * stages that came from storage, with the exact provenance in the tooltip.
 */
export function ReplayBadge({ metadata }: { metadata: ReplayMetadata }) {
  if (!metadata.replayed) return null;

  const stages = replayedStages(metadata);
  const captured = [metadata.capturedProvider, metadata.capturedModel]
    .filter(Boolean)
    .join(" / ");

  const detail = [
    `AI output captured previously and revalidated against the current deterministic case state.`,
    stages.length ? `Replayed stages: ${stages.join(", ")}.` : "",
    captured ? `Captured from: ${captured}.` : "",
    metadata.artifactCreatedAt ? `Captured at: ${metadata.artifactCreatedAt}.` : "",
    `No model was called for these stages. Refund, confidence, escalation and the resolution mode were recomputed from the current analysis.`,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <span
      className="mode-badge replayed"
      title={detail}
      aria-label={`Replayed AI output. ${detail}`}
    >
      REPLAYED{stages.length ? ` · ${stages.join(" + ")}` : ""}
    </span>
  );
}

/**
 * A run whose replay was requested and refused. Shown separately from the
 * success badge so a refusal can never be mistaken for a replay that worked.
 */
export function ReplayInvalidBadge({ metadata }: { metadata: ReplayMetadata }) {
  if (metadata.valid) return null;
  return (
    <span
      className="mode-badge replay-invalid"
      title={`Replay was refused: ${metadata.reasonCodes.join(", ")}. No provider call was made.`}
    >
      REPLAY INVALID · {metadata.reasonCodes.join(", ")}
    </span>
  );
}
