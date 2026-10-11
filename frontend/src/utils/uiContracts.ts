export type UiStepState = "done" | "ready" | "waiting" | "failed" | "blocked";

export type CounterfactualChangeDirection =
  | "INCREASE_TO_OR_ABOVE"
  | "DECREASE_BELOW"
  | "DECREASE_TO_OR_BELOW"
  | "INCREASE_ABOVE";

/** Human wording for the backend's explicit, equality-aware change contract. */
export function counterfactualChangeLabel(direction: CounterfactualChangeDirection): string {
  switch (direction) {
    case "INCREASE_TO_OR_ABOVE":
      return "Would need to rise to at least";
    case "DECREASE_BELOW":
      return "Would need to fall below";
    case "DECREASE_TO_OR_BELOW":
      return "Would need to fall to at most";
    case "INCREASE_ABOVE":
      return "Would need to rise above";
  }
}

/** Intake is step 1 in the rail but is not part of resolution progress. */
export function resolutionProgress(allStepStates: readonly UiStepState[]): {
  done: number;
  total: number;
  failed: number;
  blocked: number;
} {
  const resolutionStates = allStepStates.slice(1);
  return {
    done: resolutionStates.filter((state) => state === "done").length,
    total: resolutionStates.length,
    failed: resolutionStates.filter((state) => state === "failed").length,
    blocked: resolutionStates.filter((state) => state === "blocked").length
  };
}

export function intakeProgressLabel(lifecycle: string | null | undefined): string {
  if (!lifecycle) return "Not started";
  if (["READY_FOR_ANALYSIS", "AUTO_RESOLVED", "HUMAN_REVIEW"].includes(lifecycle)) {
    return "Complete";
  }
  return "In progress";
}

export function businessRuleStatusLabel(passed: boolean): string {
  return passed ? "Condition met" : "Condition not met";
}

/** Claim IDs shown to people use the same namespace used by rebuttals and Judge. */
export function namespaceClaimId(side: string, claimId: string): string {
  const namespace = side.toUpperCase();
  return claimId.startsWith(`${namespace}-`) ? claimId : `${namespace}-${claimId}`;
}

export function deterministicResultLabel(available: boolean): string {
  return available ? "Deterministic result available" : "Deterministic result unavailable";
}

export function aiReviewLabel(complete: boolean, running: boolean): string {
  if (running) return "AI review in progress";
  return complete ? "AI review complete" : "AI review not complete";
}
