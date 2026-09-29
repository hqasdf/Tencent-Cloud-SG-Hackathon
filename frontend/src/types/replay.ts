/**
 * Stage 6B replay provenance.
 *
 * The backend attaches `replayMetadata` to every resolution response, including
 * a fully live one, so this layer never has to infer provenance from a missing
 * field. That matters more than it looks: `agentRun.mode` describes the
 * *configured* provider mode, not what happened during this request, so a
 * replayed run can legitimately report `mode: "real"` while no model was called.
 * The UI must read provenance from here, not from there.
 */
export type ReplayMode = "NONE" | "ADVOCATES" | "ADVOCATES_AND_REBUTTALS" | "FULL_AI";

export interface ReplayMetadata {
  mode: ReplayMode;
  /** True when at least one AI stage was served from a stored artefact. */
  replayed: boolean;
  advocatesReplayed: boolean;
  rebuttalsReplayed: boolean;
  judgeReplayed: boolean;
  valid: boolean;
  reasonCodes: string[];
  currentAnalysisHash: string;
  artifactAnalysisHash: string | null;
  artifactCreatedAt: string | null;
  artifactVersion: string | null;
  capturedProvider: string | null;
  capturedModel: string | null;
  note: string;
}

/**
 * Human-readable label for a replayed stage list, e.g. "advocates, rebuttals".
 * Kept here rather than in a component so the wording is defined once.
 */
export function replayedStages(metadata: ReplayMetadata): string[] {
  const stages: string[] = [];
  if (metadata.advocatesReplayed) stages.push("advocates");
  if (metadata.rebuttalsReplayed) stages.push("rebuttals");
  if (metadata.judgeReplayed) stages.push("judge");
  return stages;
}
