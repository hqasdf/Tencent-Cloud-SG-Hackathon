/** Build-time public URL only. Provider credentials never belong in VITE_ variables. */
export function resolveApiBaseUrl(value: string | undefined, production: boolean, fallback: string): string {
  const configured = value?.trim();
  if (!configured || configured === "/") return fallback;
  let parsed: URL;
  try { parsed = new URL(configured); }
  catch { throw new Error("VITE_API_BASE_URL must be an absolute HTTP(S) URL or empty for same-origin routing."); }
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("VITE_API_BASE_URL must not contain credentials, query parameters or fragments.");
  }
  if (production && (parsed.protocol !== "https:" || ["localhost", "127.0.0.1", "[::1]", "0.0.0.0"].includes(parsed.hostname))) {
    throw new Error("Production VITE_API_BASE_URL must use HTTPS and a non-loopback host.");
  }
  return configured.replace(/\/+$/, "");
}

export function validatePublicBuildEnvironment(environment: Record<string, string | undefined>): void {
  if (Object.keys(environment).some(key => key.startsWith("VITE_") && key !== "VITE_API_BASE_URL")) {
    throw new Error("Only VITE_API_BASE_URL is permitted as a public build variable; keep credentials server-side.");
  }
  resolveApiBaseUrl(environment.VITE_API_BASE_URL, true, "");
}
