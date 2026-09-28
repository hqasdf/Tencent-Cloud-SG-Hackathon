import type { CaseAnalysis, CaseSummary, DisputeCase } from "../types/dispute";
import type { AdvocateRunResult } from "../types/advocate";
import type { AuditEvent, DeterministicResolution, JudgeResult } from "../types/judge";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export class CaseApiError extends Error {
  constructor(message: string, public readonly status?: number) {
    super(message);
    this.name = "CaseApiError";
  }
}

/** The Stage 5 result: an advisory Judge block plus the authoritative block. */
export interface ResolutionRunResult {
  caseId: string;
  disputeType: "route_deviation" | "no_show_charge";
  rider: AdvocateRunResult["rider"];
  driver: AdvocateRunResult["driver"];
  agentRun: AdvocateRunResult["agentRun"];
  verificationSummary: AdvocateRunResult["verificationSummary"];
  judge: JudgeResult;
  deterministicResolution: DeterministicResolution;
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
   * Run the full Stage 5 pipeline: advocates, verification, Judge, remedy.
   *
   * Kept separate from runAdvocates so the Stage 4 endpoint and its response
   * contract are untouched. This returns both halves of the result: the Judge's
   * advisory recommendation and the authoritative deterministic resolution.
   */
  runResolution(id: string): Promise<ResolutionRunResult> {
    return request<ResolutionRunResult>(`/api/cases/${encodeURIComponent(id)}/resolution/run`, {
      method: "POST"
    });
  }
};
