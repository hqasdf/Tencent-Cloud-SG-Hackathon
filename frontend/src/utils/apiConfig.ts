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

// Exact public framework metadata documented by Vercel; never allow the prefix.
// https://vercel.com/docs/environment-variables/framework-environment-variables
const ALLOWED_PUBLIC_BUILD_VARIABLES = new Set([
  "VITE_API_BASE_URL",
  "VITE_VERCEL_ENV",
  "VITE_VERCEL_TARGET_ENV",
  "VITE_VERCEL_URL",
  "VITE_VERCEL_BRANCH_URL",
  "VITE_VERCEL_PROJECT_PRODUCTION_URL",
  "VITE_VERCEL_HASH_SALT",
  // Browser telemetry endpoints: https://vercel.com/docs/analytics/package
  "VITE_VERCEL_OBSERVABILITY_CLIENT_CONFIG",
  "VITE_VERCEL_GIT_PROVIDER",
  "VITE_VERCEL_GIT_REPO_SLUG",
  "VITE_VERCEL_GIT_REPO_OWNER",
  "VITE_VERCEL_GIT_REPO_ID",
  "VITE_VERCEL_GIT_COMMIT_REF",
  "VITE_VERCEL_GIT_COMMIT_SHA",
  "VITE_VERCEL_GIT_COMMIT_MESSAGE",
  "VITE_VERCEL_GIT_COMMIT_AUTHOR_LOGIN",
  "VITE_VERCEL_GIT_COMMIT_AUTHOR_NAME",
  "VITE_VERCEL_GIT_PULL_REQUEST_ID"
]);

export function validatePublicBuildEnvironment(environment: Record<string, string | undefined>): void {
  const unknown = Object.keys(environment)
    .filter(key => key.startsWith("VITE_") && !ALLOWED_PUBLIC_BUILD_VARIABLES.has(key))
    .sort();
  if (unknown.length) {
    throw new Error(`Unsupported public build variable names: ${unknown.join(", ")}. Keep credentials server-side.`);
  }
  resolveApiBaseUrl(environment.VITE_API_BASE_URL, true, "");
}
