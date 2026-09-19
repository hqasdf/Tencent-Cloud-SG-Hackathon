import type { CaseAnalysis, CaseSummary, DisputeCase } from "../types/dispute";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export class CaseApiError extends Error {
  constructor(message: string, public readonly status?: number) {
    super(message);
    this.name = "CaseApiError";
  }
}

async function request<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, { headers: { Accept: "application/json" } });
  } catch {
    throw new CaseApiError("RydeResolve could not reach the local case API.");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new CaseApiError(body?.detail ?? `Case API returned ${response.status}.`, response.status);
  }
  return response.json() as Promise<T>;
}

export const caseService = {
  list(): Promise<CaseSummary[]> { return request<CaseSummary[]>("/api/cases"); },
  getById(id: string): Promise<DisputeCase> { return request<DisputeCase>(`/api/cases/${encodeURIComponent(id)}`); },
  getAnalysis(id: string): Promise<CaseAnalysis> { return request<CaseAnalysis>(`/api/cases/${encodeURIComponent(id)}/analysis`); }
};
