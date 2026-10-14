import type { CaseAnalysis, CaseSummary, DisputeCase, IntakeContext } from "../types/dispute";
import type { AdvocateRunResult } from "../types/advocate";
import type { AuditEvent, DeterministicResolution, JudgeResult } from "../types/judge";
import type { ReplayMetadata } from "../types/replay";
import type {
  DecisionCounterfactual,
  DecisionExplanation,
  RebuttalRunResult
} from "../types/rebuttal";
import type { WorkflowState } from "../types/workflow";
import { resolveApiBaseUrl } from "../utils/apiConfig";
import { authenticatedHeaders, clearApiSession } from "./apiSession";

const apiBaseUrl = resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL, import.meta.env.PROD,
  import.meta.env.PROD ? "" : "http://localhost:8000");

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
  intakeContext?: IntakeContext | null;
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
  /**
   * The authoritative workflow state machine.
   *
   * `null` only for a response stored before the state machine existed. When it
   * is present the UI derives every step's state from it and does not look at
   * `pipeline` at all.
   */
  workflow: WorkflowState | null;
  pipeline: AdvocateRunResult["pipeline"];
  audit: AuditEvent[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...init,
      signal: init?.signal ?? AbortSignal.timeout(190000),
      headers: { Accept: "application/json", ...authenticatedHeaders(init?.method), ...(init?.headers ?? {}) }
    });
  } catch {
    throw new CaseApiError("RydeResolve could not complete the API request. Saved analysis remains on the backend; requests are not automatically retried.");
  }
  if (!response.ok) {
    if (response.status === 401) clearApiSession();
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    const structured = body?.detail as { message?: unknown } | null;
    const detail = typeof body?.detail === "string" ? body.detail
      : typeof structured?.message === "string" ? structured.message : `Case API returned ${response.status}.`;
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
  runResolution(id: string, replayMode: "NONE" | "FULL_AI" = "NONE"): Promise<ResolutionRunResult> {
    return request<ResolutionRunResult>(`/api/cases/${encodeURIComponent(id)}/resolution/run`, {
      method: "POST",
      ...(replayMode === "FULL_AI" ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify({ replayMode }) } : {})
    });
  }
};
