/** Session token stays in memory; service credentials never enter this module. */
let token = "";
export function setApiSession(value: string): void { token = value; }
export function hasApiSession(): boolean { return token.length > 0; }
export function clearApiSession(): void {
  token = "";
  if (typeof window !== "undefined") window.dispatchEvent(new Event("ryde-auth-expired"));
}
export function authenticatedHeaders(method = "GET"): Record<string, string> {
  if (!token) return {};
  return { Authorization: `Bearer ${token}`,
    ...(method.toUpperCase() === "POST" ? { "Idempotency-Key": crypto.randomUUID() } : {}) };
}
