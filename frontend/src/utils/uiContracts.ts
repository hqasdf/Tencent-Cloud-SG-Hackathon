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

export function intakeProgressLabel(lifecycle: string | null | undefined, interviewsComplete?: boolean): string {
  if (!lifecycle) return "Not started";
  if (interviewsComplete === false) return "In progress";
  if (["READY_FOR_ANALYSIS", "ANALYSING", "AUTO_RESOLVED", "HUMAN_REVIEW"].includes(lifecycle)) {
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

export function resolutionIntakeLabel(context: {
  comparisonAllowed: boolean;
  riderComplete: boolean;
  driverComplete: boolean;
  partyStatements: readonly { facts: readonly unknown[] }[];
} | null | undefined): string {
  if (!context || !context.partyStatements.some(s => s.facts.length)) {
    return "This resolution used case evidence only; no interview allegations were available.";
  }
  return context.comparisonAllowed
    ? "This resolution used completed intake accounts as attributed allegations. Only evidence-backed claims reach the advisory Judge."
    : "This resolution used partial intake. Interviews or rider clarification remain incomplete; opposing interview allegations were withheld from each advocate.";
}

export function rateLimitMessage(executions: readonly ({ failureCode?: string | null; retryAfterSeconds?: number | null } | null | undefined)[]): string | null {
  const limited = executions.filter(e => e?.failureCode === "RATE_LIMITED");
  if (!limited.length) return null;
  const delays = limited.flatMap(e => typeof e?.retryAfterSeconds === "number" && Number.isFinite(e.retryAfterSeconds)
    ? [e.retryAfterSeconds] : []);
  const timing = delays.length
    ? `Provider recommends waiting at least ${Math.ceil(Math.max(...delays))} seconds after the error before retrying.`
    : "Retry time is unknown; check quota availability before retrying.";
  return `Gemini is temporarily unavailable due to rate limiting. ${timing} Eligible completed AI stages remain saved for compatible retries. The code-calculated final decision remains available; AI analysis did not complete.`;
}
