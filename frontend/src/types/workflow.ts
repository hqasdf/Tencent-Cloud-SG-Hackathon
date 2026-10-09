/**
 * The backend's workflow state machine, mirrored.
 *
 * The frontend renders this and nothing else. It must never infer that a step
 * is finished from the mere presence of a result object — that inference is
 * exactly what allowed the UI to report "8 of 8 steps ready" for a run in which
 * the advocates failed and the Judge never ran.
 *
 * The distinction that matters most is `BLOCKED` versus `FAILED`:
 *
 *   FAILED   the stage ran and did not produce a usable result
 *   BLOCKED  the stage was never attempted because something it depends on
 *            failed, so retrying it alone cannot help
 *
 * They are shown differently, because they call for different actions.
 */
export type StageStatus =
  | "NOT_STARTED"
  | "RUNNING"
  | "COMPLETE"
  | "FAILED"
  | "BLOCKED"
  | "SKIPPED"
  | "PENDING_HUMAN_REVIEW";

export interface StageState {
  status: StageStatus;
  /** Machine-readable cause, e.g. `ADVOCATE_INPUT_INCOMPLETE`. */
  reason: string | null;
  /** A sentence written by code for an operator. Never model-generated. */
  detail: string | null;
  failureCode: string | null;
}

export interface SideStages {
  rider: StageState;
  driver: StageState;
}

export interface WorkflowState {
  deterministic: StageState;
  advocates: SideStages;
  rebuttals: SideStages;
  judge: StageState;
  finalResolution: StageState;
  explanation: StageState;
}

/** A stage that produced a usable result, including a human-review routing. */
export function isSettledOk(state: StageState | undefined): boolean {
  return state?.status === "COMPLETE" || state?.status === "PENDING_HUMAN_REVIEW";
}

/** A stage that will not produce a result in this run, and why. */
export function isSettledNotOk(state: StageState | undefined): boolean {
  return (
    state?.status === "FAILED" ||
    state?.status === "BLOCKED" ||
    state?.status === "SKIPPED"
  );
}

export function bothSettledOk(sides: SideStages | undefined): boolean {
  return isSettledOk(sides?.rider) && isSettledOk(sides?.driver);
}
