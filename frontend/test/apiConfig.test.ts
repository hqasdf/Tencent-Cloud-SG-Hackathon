import test from "node:test";
import assert from "node:assert/strict";
import { resolveApiBaseUrl, validatePublicBuildEnvironment } from "../src/utils/apiConfig.ts";

test("production defaults to same-origin while local development retains port 8000", () => {
  assert.equal(resolveApiBaseUrl(undefined, true, ""), "");
  assert.equal(resolveApiBaseUrl("", false, "http://localhost:8000"), "http://localhost:8000");
  assert.equal(resolveApiBaseUrl("https://api.example.com/", true, ""), "https://api.example.com");
});

test("production refuses localhost, insecure URLs and credentials without echoing secrets", () => {
  for (const url of ["http://api.example.com", "https://localhost:8000", "https://127.0.0.1", "https://[::1]",
    "https://user:PRIVATE_CREDENTIAL@api.example.com", "https://api.example.com?key=PRIVATE_CREDENTIAL", "not-a-url"]) {
    assert.throws(() => resolveApiBaseUrl(url, true, ""), error => !error.message.includes("PRIVATE_CREDENTIAL"));
  }
});

test("explicit local development API URLs remain supported", () => {
  assert.equal(resolveApiBaseUrl("http://127.0.0.1:8000/", false, ""), "http://127.0.0.1:8000");
});

test("the production build refuses additional public variables before bundling credentials", () => {
  validatePublicBuildEnvironment({ VITE_API_BASE_URL: "" });
  assert.throws(() => validatePublicBuildEnvironment({ VITE_API_KEY: "PRIVATE_CREDENTIAL" }),
    error => !error.message.includes("PRIVATE_CREDENTIAL"));
  assert.throws(() => validatePublicBuildEnvironment({ VITE_API_BASE_URL: "http://localhost:8000" }));
});

// Public framework metadata names verified against Vercel's documentation.
const vercelMetadata = {
  VITE_VERCEL_ENV: "production",
  VITE_VERCEL_TARGET_ENV: "production",
  VITE_VERCEL_URL: "ryderesolve-deployment.vercel.app",
  VITE_VERCEL_BRANCH_URL: "ryderesolve-git-main.vercel.app",
  VITE_VERCEL_PROJECT_PRODUCTION_URL: "ryderesolve.vercel.app",
  VITE_VERCEL_HASH_SALT: "1783933175",
  VITE_VERCEL_GIT_PROVIDER: "github",
  VITE_VERCEL_GIT_REPO_SLUG: "Tencent-Cloud-SG-Hackathon",
  VITE_VERCEL_GIT_REPO_OWNER: "hqasdf",
  VITE_VERCEL_GIT_REPO_ID: "123456789",
  VITE_VERCEL_GIT_COMMIT_REF: "main",
  VITE_VERCEL_GIT_COMMIT_SHA: "3f9f5b18e56156b07b0d5dcbfe17d174bdd4fa53",
  VITE_VERCEL_GIT_COMMIT_MESSAGE: "Final Code-Quality Improvements",
  VITE_VERCEL_GIT_COMMIT_AUTHOR_LOGIN: "synthetic-author",
  VITE_VERCEL_GIT_COMMIT_AUTHOR_NAME: "Synthetic Author",
  VITE_VERCEL_GIT_PULL_REQUEST_ID: ""
};

test("production and preview accept only the documented Vercel metadata names", () => {
  for (const [name, value] of Object.entries(vercelMetadata)) {
    assert.doesNotThrow(() => validatePublicBuildEnvironment({ [name]: value }));
  }
  for (const target of ["production", "preview", "development", "synthetic-custom-environment"]) {
    assert.doesNotThrow(() => validatePublicBuildEnvironment({ ...vercelMetadata,
      VITE_VERCEL_TARGET_ENV: target, VITE_API_BASE_URL: "https://api.example.com" }));
  }
});

test("Vercel metadata never exempts the API URL from strict production validation", () => {
  for (const url of ["http://api.example.com", "https://localhost:8000", "not-a-url",
    "https://user:PRIVATE_CREDENTIAL@api.example.com", "https://api.example.com?token=PRIVATE_CREDENTIAL"]) {
    assert.throws(() => validatePublicBuildEnvironment({ ...vercelMetadata, VITE_API_BASE_URL: url }),
      error => !error.message.includes("PRIVATE_CREDENTIAL"));
  }
});

test("secret-like and unknown Vercel-prefixed variables fail with names only", () => {
  for (const name of ["VITE_GEMINI_API_KEY", "VITE_SECRET", "VITE_TOKEN", "VITE_API_KEY",
    "VITE_VERCEL_SECRET", "VITE_VERCEL_OIDC_TOKEN", "VITE_VERCEL_UNKNOWN_METADATA", "VITE_CUSTOM_FLAG"]) {
    assert.throws(() => validatePublicBuildEnvironment({ ...vercelMetadata, [name]: "PRIVATE_CREDENTIAL" }),
      error => error.message.includes(name) && !error.message.includes("PRIVATE_CREDENTIAL"));
  }
  assert.throws(() => validatePublicBuildEnvironment({ VITE_TOKEN: "PRIVATE_CREDENTIAL", VITE_SECRET: "PRIVATE_CREDENTIAL" }),
    error => error.message.includes("VITE_SECRET, VITE_TOKEN") && !error.message.includes("PRIVATE_CREDENTIAL"));
});

test("an empty or undefined unknown public variable remains forbidden", () => {
  for (const value of ["", undefined]) {
    assert.throws(() => validatePublicBuildEnvironment({ VITE_VERCEL_SECRET: value }),
      error => error.message.includes("VITE_VERCEL_SECRET"));
  }
});

test("unprefixed server variables do not enter the public-variable allowlist", () => {
  assert.doesNotThrow(() => validatePublicBuildEnvironment({ ...vercelMetadata,
    VERCEL: "1", VERCEL_OIDC_TOKEN: "SYNTHETIC_SERVER_ONLY", AGENT_API_KEY: "SYNTHETIC_SERVER_ONLY" }));
});
