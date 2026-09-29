import type { CaseAnalysis, CaseSummary, DisputeCase } from "../types/dispute";
import type { AdvocateRunResult } from "../types/advocate";
import type { AuditEvent, DeterministicResolution, JudgeResult } from "../types/judge";
import type { ReplayMetadata } from "../types/replay";
import type {
  DecisionCounterfactual,
  DecisionExplanation,
  RebuttalRunResult
} from "../types/rebuttal";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export class CaseApiError extends Error {
  constructor(message: string, public readonly status?: number) {
    super(message);
    this.name = "CaseApiError";
  }
}

/**
 * The Stage 6 result.
 *
 * Four separate layers, kept as separate fields on purpose:
 *   rider / driver        — the initial arguments
 *   rebuttals             — the bounded cross-examination
 *   judge                 — the AI's advisory recommendation
 *   deterministicResolution — what code is actually willing to do
 *
 * `explanation` and `counterfactual` are siblings of `judge` rather than
 * children of it, because they are computed without reference to the Judge.
 */
export interface ResolutionRunResult {
  caseId: string;
  disputeType: "route_deviation" | "no_show_charge";
  rider: AdvocateRunResult["rider"];
  driver: AdvocateRunResult["driver"];
  rebuttals: RebuttalRunResult;
  agentRun: AdvocateRunResult["agentRun"];
  verificationSummary: AdvocateRunResult["verificationSummary"];
  judge: JudgeResult;
  deterministicResolution: DeterministicResolution;
  explanation: DecisionExplanation;
  counterfactual: DecisionCounterfactual;
  /**
   * Where this run's AI material came from. Present on every response, so the
   * UI never has to treat an absent field as "live". `agentRun.mode` describes
   * the configured provider, not whether a model was actually called — a
   * replayed run can report `real` while spending nothing.
   */
  replayMetadata: ReplayMetadata;
  pipeline: AdvocateRunResult["pipeline"];
  audit: AuditEvent[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...(init?.headers ?? {}) }
    });
  } catch {
    throw new CaseApiError("RydeResolve could not reach the local case API.");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    const detail = typeof body?.detail === "string" ? body.detail : `Case API returned ${response.status}.`;
    throw new CaseApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

export const caseService = {
  list(): Promise<CaseSummary[]> { return request<CaseSummary[]>("/api/cases"); },
  getById(id: string): Promise<DisputeCase> { return request<DisputeCase>(`/api/cases/${encodeURIComponent(id)}`); },
  getAnalysis(id: string): Promise<CaseAnalysis> { return request<CaseAnalysis>(`/api/cases/${encodeURIComponent(id)}/analysis`); },

  /**
   * Run the Rider and Driver advocates.
   *
   * POST rather than GET: running advocates may trigger external model calls and
   * incurs latency and token cost, so it is an explicit operator action. In mock
   * mode it is deterministic and free.
   */
  runAdvocates(id: string): Promise<AdvocateRunResult> {
    return request<AdvocateRunResult>(`/api/cases/${encodeURIComponent(id)}/advocates/run`, {
      method: "POST"
    });
  },

  /**
   * Run the full Stage 6 pipeline: advocates, verification, one rebuttal round,
   * rebuttal verification, Judge, deterministic remedy, explanation.
   *
   * Kept separate from runAdvocates so the Stage 4 endpoint and its response
   * contract are untouched. A complete live run costs five model calls.
   */
  runResolution(id: string): Promise<ResolutionRunResult> {
    return request<ResolutionRunResult>(`/api/cases/${encodeURIComponent(id)}/resolution/run`, {
      method: "POST"
    });
  }
};
