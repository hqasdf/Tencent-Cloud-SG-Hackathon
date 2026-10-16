import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";
import { authenticatedHeaders, clearApiSession, hasApiSession, setApiSession } from "../src/services/apiSession.ts";

function client(file: string, name: string, responses: { status: number; body: unknown }[]) {
  const source = readFileSync(new URL(`../src/services/${file}.ts`, import.meta.url), "utf8")
    .replaceAll("import.meta.env", '({ VITE_API_BASE_URL: "https://synthetic.example", PROD: true })');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const calls: { url: string; init: RequestInit }[] = [];
  const mod = { exports: {} as Record<string, any> };
  new Function("require", "module", "exports", "fetch", compiled)((dependency: string) => {
    if (dependency.includes("apiConfig")) return { resolveApiBaseUrl: (value: string) => value };
    if (dependency.includes("apiSession")) return { authenticatedHeaders, clearApiSession };
    throw new Error(dependency);
  }, mod, mod.exports, async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    const response = responses.shift(); assert.ok(response, "No unexpected request is allowed");
    return { ok: response.status < 400, status: response.status, json: async () => response.body };
  });
  return { service: mod.exports[name], calls };
}

test("signed-in list, detail and analysis requests use the real in-memory bearer session", async () => {
  setApiSession("synthetic-case-session");
  try {
    const cases = Array.from({ length: 8 }, (_, n) => ({ id: `DISP-${String(n + 2).padStart(3, "0")}` }));
    const h = client("caseService", "caseService", [{ status: 200, body: cases }, { status: 200, body: cases[3] }, { status: 200, body: { caseId: "DISP-005" } }]);
    assert.deepEqual(await h.service.list(), cases);
    assert.equal((await h.service.getById("DISP-005")).id, "DISP-005");
    assert.equal((await h.service.getAnalysis("DISP-005")).caseId, "DISP-005");
    assert.deepEqual(h.calls.map(c => new URL(c.url).pathname), ["/api/cases", "/api/cases/DISP-005", "/api/cases/DISP-005/analysis"]);
    for (const call of h.calls) {
      assert.equal(new Headers(call.init.headers).get("Authorization"), "Bearer synthetic-case-session");
      assert.ok(call.init.signal);
    }
  } finally { clearApiSession(); }
});

test("case-list 401 clears the session, preserves the server error and does not retry", async () => {
  setApiSession("synthetic-expired-session");
  const h = client("caseService", "caseService", [{ status: 401, body: { detail: "Demo authentication required" } }]);
  await assert.rejects(h.service.list(), { message: "Demo authentication required", status: 401 });
  assert.equal(hasApiSession(), false); assert.equal(h.calls.length, 1);
});

test("intake uses the same session and distinct idempotency keys; sign-out removes authorization", async () => {
  setApiSession("synthetic-intake-session");
  try {
    const h = client("intakeService", "intakeService", Array.from({ length: 4 }, () => ({ status: 200, body: {} })));
    await h.service.getBySource("DISP-005");
    await h.service.createBySource("DISP-005");
    await h.service.createBySource("DISP-006");
    for (const call of h.calls) assert.equal(new Headers(call.init.headers).get("Authorization"), "Bearer synthetic-intake-session");
    const keys = h.calls.slice(1).map(call => new Headers(call.init.headers).get("Idempotency-Key"));
    assert.ok(keys.every(Boolean)); assert.notEqual(keys[0], keys[1]);
    clearApiSession(); await h.service.getBySource("DISP-005");
    assert.equal(new Headers(h.calls[3].init.headers).get("Authorization"), null);
  } finally { clearApiSession(); }
});
