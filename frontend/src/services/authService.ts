import { resolveApiBaseUrl } from "../utils/apiConfig";
import { setApiSession } from "./apiSession";
const base = resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL, import.meta.env.PROD,
  import.meta.env.PROD ? "" : "http://localhost:8000");
export interface BackendCapabilities {
  authenticationRequired?: boolean;
  liveAIEnabled?: boolean;
  replayEnabled?: boolean;
}
export async function backendCapabilities(): Promise<BackendCapabilities> {
  const response = await fetch(`${base}/api/health`, { signal: AbortSignal.timeout(15000) });
  if (!response.ok) throw new Error("RydeResolve could not reach its backend. Try again.");
  return response.json() as Promise<BackendCapabilities>;
}
export async function loginDemo(password: string): Promise<void> {
  const response = await fetch(`${base}/api/auth/login`, { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password }),
    signal: AbortSignal.timeout(190000) });
  if (!response.ok) throw new Error(response.status === 401 ? "The demo password was not accepted."
    : response.status === 429 ? "The demo login limit was reached. Please try later."
    : "Demo sign-in is temporarily unavailable.");
  const body = await response.json() as { accessToken?: string };
  if (!body.accessToken) throw new Error("Demo sign-in returned an invalid session.");
  setApiSession(body.accessToken);
}
