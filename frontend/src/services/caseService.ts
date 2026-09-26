import type { CaseAnalysis, CaseSummary, DisputeCase } from "../types/dispute";
import type { AdvocateRunResult } from "../types/advocate";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export class CaseApiError extends Error {
  constructor(message: string, public readonly status?: number) {
    super(message);
    this.name = "CaseApiError";
  }
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
  }
};
