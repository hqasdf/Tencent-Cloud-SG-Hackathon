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
