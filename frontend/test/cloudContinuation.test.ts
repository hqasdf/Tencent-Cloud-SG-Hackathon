import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../src/services/caseService.ts", import.meta.url), "utf8")
  .replaceAll("import.meta.env", '({ VITE_API_BASE_URL: "https://synthetic.example", PROD: true })');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
function harness(responses) {
  const requests = []; let token = "synthetic-session"; let key = 0;
  const mod = { exports: {} };
  new Function("require", "module", "exports", "fetch", compiled)(name => {
    if (name.includes("apiConfig")) return { resolveApiBaseUrl: value => value };
    if (name.includes("apiSession")) return {
      authenticatedHeaders: () => token ? { Authorization: `Bearer ${token}`, "Idempotency-Key": `synthetic-action-${++key}` } : {},
      clearApiSession: () => { token = ""; }
    };
    throw new Error(name);
  }, mod, mod.exports, async (url, init) => {
    requests.push({ url, init });
    const item = responses.shift();
    if (item instanceof Error) throw item;
    assert.ok(item, "No extra request is permitted");
    return { ok: item.status < 400, status: item.status, json: async () => { item.onRead?.(); return item.body; } };
  });
  return { service: mod.exports.caseService, requests, token: () => token };
}
const roles = ["RIDER_ADVOCATE", "DRIVER_ADVOCATE", "RIDER_REBUTTAL", "DRIVER_REBUTTAL"];

test("live analysis follows at most five progressive checkpoint requests with distinct action keys", async () => {
  const final = { caseId: "DISP-005", marker: "synthetic-final" };
  const h = harness([...roles.map((_, index) => ({ status: 202, body: { resumeRequired: true, savedRoles: roles.slice(0, index + 1) } })), { status: 200, body: final }]);
  assert.deepEqual(await h.service.runResolution("DISP-005"), final);
  assert.equal(h.requests.length, 5);
  assert.equal(new Set(h.requests.map(item => item.init.headers["Idempotency-Key"])).size, 5);
});

test("provider failure and network uncertainty never trigger automatic continuation", async () => {
  for (const response of [{ status: 429, body: { detail: "Provider cooldown" } }, new Error("synthetic network uncertainty")]) {
    const h = harness([response]);
    await assert.rejects(h.service.runResolution("DISP-005"));
    assert.equal(h.requests.length, 1);
  }
});

test("invalid, duplicate or non-progressing checkpoint responses stop before another call", async () => {
  for (const savedRoles of [[], ["UNKNOWN"], ["RIDER_ADVOCATE", "RIDER_ADVOCATE"]]) {
    const h = harness([{ status: 202, body: { resumeRequired: true, savedRoles } }]);
    await assert.rejects(h.service.runResolution("DISP-005"));
    assert.equal(h.requests.length, 1);
  }
  const h = harness([1, 2].map(() => ({ status: 202, body: { resumeRequired: true, savedRoles: ["RIDER_ADVOCATE"] } })));
  await assert.rejects(h.service.runResolution("DISP-005"));
  assert.equal(h.requests.length, 2);
});

test("session expiry during continuation clears authentication and stops", async () => {
  const h = harness([{ status: 202, body: { resumeRequired: true, savedRoles: ["RIDER_ADVOCATE"] } }, { status: 401, body: { detail: "Sign in again" } }]);
  await assert.rejects(h.service.runResolution("DISP-005"));
  assert.equal(h.token(), "");
  assert.equal(h.requests.length, 2);
});

test("saved mock demo uses one replay request and cannot silently continue live", async () => {
  const h = harness([{ status: 202, body: { resumeRequired: true, savedRoles: ["RIDER_ADVOCATE"] } }]);
  await assert.rejects(h.service.runResolution("DISP-005", "FULL_AI"));
  assert.equal(h.requests.length, 1);
  assert.equal(JSON.parse(h.requests[0].init.body).replayMode, "FULL_AI");
});


test("leaving a workflow stops before the next AI role without retrying the in-flight request", async () => {
  const controller = new AbortController();
  const h = harness([{ status: 202, body: { resumeRequired: true, savedRoles: ["RIDER_ADVOCATE"] }, onRead: () => controller.abort() }]);
  await assert.rejects(h.service.runResolution("DISP-005", "NONE", controller.signal));
  assert.equal(h.requests.length, 1);
});
