import type {
  CaseAnalysis,
} from "../types/dispute";
import type {
  CreateIntakeCaseRequest,
  IntakeCase,
  SendMessageRequest,
  SendMessageResponse,
} from "../types/intake";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export class IntakeApiError extends Error {
  constructor(message: string, public readonly status?: number) {
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
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new IntakeApiError(body?.detail ?? `Intake API returned ${response.status}.`, response.status);
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
};
