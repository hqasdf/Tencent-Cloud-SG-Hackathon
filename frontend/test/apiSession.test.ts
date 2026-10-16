import test from "node:test";
import assert from "node:assert/strict";
import { authenticatedHeaders, clearApiSession, hasApiSession, setApiSession } from "../src/services/apiSession.ts";
test("local requests do not receive authentication or cloud idempotency headers", () => {
  clearApiSession();
  assert.equal(hasApiSession(), false);
  assert.deepEqual(authenticatedHeaders("POST"), {});
});
test("authenticated requests send a session and distinct mutation action keys", () => {
  setApiSession("synthetic-session-only");
  const first = authenticatedHeaders("POST"), second = authenticatedHeaders("POST");
  assert.equal(first.Authorization, "Bearer synthetic-session-only");
  assert.match(first["Idempotency-Key"], /^[a-zA-Z0-9_-]{16,128}$/);
  assert.notEqual(first["Idempotency-Key"], second["Idempotency-Key"]);
  assert.equal(authenticatedHeaders("GET")["Idempotency-Key"], undefined);
  clearApiSession();
  assert.deepEqual(authenticatedHeaders(), {});
});
test("session implementation never persists credentials to browser storage", async () => {
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(new URL("../src/services/apiSession.ts", import.meta.url), "utf8");
  assert.doesNotMatch(source, /localStorage|sessionStorage|document\.cookie/);
});
