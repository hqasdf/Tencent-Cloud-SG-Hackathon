import type {
  CaseAnalysis,
} from "../types/dispute";
import type {
  CreateIntakeCaseRequest,
  IntakeCase,
  SendMessageRequest,
  SendMessageResponse,
} from "../types/intake";
import { resolveApiBaseUrl } from "../utils/apiConfig";

const apiBaseUrl = resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL, import.meta.env.PROD,
  import.meta.env.PROD ? "" : "http://localhost:8000");

export class IntakeApiError extends Error {
  constructor(message: string, public readonly status?: number, public readonly code?: string) {
    super(message);
    this.name = "IntakeApiError";
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...options,
      headers: { "Content-Type": "application/json", Accept: "application/json", ...options?.headers },
    });
  } catch {
    throw new IntakeApiError("Could not reach the intake API.");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null) as {
      detail?: string | { code?: string; message?: string };
    } | null;
    const detail = body?.detail;
    const message = typeof detail === "string"
      ? detail
      : detail?.message ?? `Intake API returned ${response.status}.`;
    const code = typeof detail === "object" ? detail?.code : undefined;
    throw new IntakeApiError(message, response.status, code);
  }
  return response.json() as Promise<T>;
}

export const intakeService = {
  listTrips(): Promise<string[]> {
    return request<string[]>("/api/intake/trips");
  },
  createCase(body: CreateIntakeCaseRequest): Promise<IntakeCase> {
    return request<IntakeCase>("/api/intake/cases", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  getCase(id: string): Promise<IntakeCase> {
    return request<IntakeCase>(`/api/intake/cases/${encodeURIComponent(id)}`);
  },
  getBySource(sourceCaseId: string): Promise<IntakeCase> {
    return request<IntakeCase>(`/api/intake/by-source/${encodeURIComponent(sourceCaseId)}`);
  },
  createBySource(sourceCaseId: string): Promise<IntakeCase> {
    return request<IntakeCase>(`/api/intake/by-source/${encodeURIComponent(sourceCaseId)}`, {
      method: "POST",
    });
  },
  sendMessage(id: string, body: SendMessageRequest): Promise<SendMessageResponse> {
    return request<SendMessageResponse>(`/api/intake/cases/${encodeURIComponent(id)}/messages`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  analyse(id: string): Promise<CaseAnalysis> {
    return request<CaseAnalysis>(`/api/intake/cases/${encodeURIComponent(id)}/analyse`, {
      method: "POST",
    });
  },
  skipRebuttal(id: string): Promise<IntakeCase> {
    return request<IntakeCase>(`/api/intake/cases/${encodeURIComponent(id)}/skip-rebuttal`, {
      method: "POST",
    });
  },
};
