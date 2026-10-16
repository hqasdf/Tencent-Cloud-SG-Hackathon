// Execute the actual App with deferred services and a small hook harness.
// No DOM, browser automation, HTTP or extra test dependencies are involved.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022
} }).outputText;

function deferred() {
  let resolve; let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function harness(service) {
  const hooks = []; let cursor = 0; let effects = []; let dirty = false; let tree;
  const CaseDetail = () => null;
  const react = {
    useState(initial) {
      const i = cursor++;
      if (!hooks[i]) hooks[i] = { value: initial };
      return [hooks[i].value, value => { hooks[i].value = value; dirty = true; }];
    },
    useRef(initial) {
      const i = cursor++;
      if (!hooks[i]) hooks[i] = { current: initial };
      return hooks[i];
    },
    useEffect(run, deps) {
      const i = cursor++; const previous = hooks[i];
      if (!previous || deps.some((d, n) => d !== previous.deps[n])) {
        effects.push(() => { previous?.cleanup?.(); hooks[i] = { deps, cleanup: run() }; });
      }
    }
  };
  const jsx = (type, props, key) => ({ type, props, key });
  const mod = { exports: {} };
  new Function("require", "module", "exports", compiled)(name => {
    if (name === "react") return react;
    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx, Fragment: "fragment" };
    if (name.includes("CaseDetail")) return { CaseDetail };
    if (name.includes("DisputeDashboard")) return {};
    if (name.includes("caseService")) return { caseService: service, CaseApiError: class extends Error {} };
    throw new Error(`Unexpected dependency ${name}`);
  }, mod, mod.exports);
  function render() {
    dirty = false; cursor = 0; effects = []; tree = mod.exports.default();
    effects.forEach(run => run());
  }
  function nodes(node) {
    if (!node || typeof node !== "object") return [];
    const children = [node.props?.children].flat(Infinity);
    return [node, ...children.flatMap(child => nodes(child))];
  }
  async function flush() {
    for (let i = 0; i < 12; i++) { await Promise.resolve(); if (dirty) render(); }
  }
  render();
  return {
    flush,
    panel: () => nodes(tree).find(node => node.type === CaseDetail)?.props,
    nodes: () => nodes(tree),
    select(id) {
      nodes(tree).find(node => node.type === "button" && node.key === id).props.onClick();
      render();
    }
  };
}

const cases = ["DISP-002", "DISP-005"].map(id => ({ id, title: id, status: "Pending",
  disputeType: "route_deviation", rider: { name: "Test rider" }, driver: { name: "Test driver" }, confidence: { overall: 95 } }));
const result = id => ({ caseId: id, rider: {}, driver: {}, agentRun: {}, pipeline: [] });
const baseService = () => ({ list: async () => cases, getById: async id => cases.find(c => c.id === id),
  getAnalysis: async id => ({ caseId: id }) });

test("late case loads cannot replace the newly selected case", async () => {
  const old = deferred(); const service = baseService();
  service.getById = id => id === "DISP-002" ? old.promise : Promise.resolve(cases[1]);
  const app = harness(service); await app.flush(); app.select("DISP-005"); await app.flush();
  assert.equal(app.panel().caseData.id, "DISP-005");
  old.resolve(cases[0]); await app.flush();
  assert.equal(app.panel().caseData.id, "DISP-005");
  assert.equal(app.panel().analysis.caseId, "DISP-005");
});

test("late full analysis never appears under another case, including A-B-A navigation", async () => {
  const pending = deferred(); const service = { ...baseService(), runResolution: () => pending.promise };
  const app = harness(service); await app.flush(); app.panel().onRunResolution(); await app.flush();
  app.select("DISP-005"); await app.flush(); app.select("DISP-002"); await app.flush();
  pending.resolve(result("DISP-002")); await app.flush();
  assert.equal(app.panel().resolutionRun, null);
  assert.equal(app.panel().advocateRun, null);
  assert.equal(app.panel().resolutionRunning, false);
});

test("late advocate errors do not contaminate another case", async () => {
  const pending = deferred(); const service = { ...baseService(), runAdvocates: () => pending.promise };
  const app = harness(service); await app.flush(); app.panel().onRunAdvocates(); await app.flush();
  app.select("DISP-005"); await app.flush(); pending.reject(new Error("Old case error")); await app.flush();
  assert.equal(app.panel().advocateError, null);
  assert.equal(app.panel().advocateRunning, false);
});

test("duplicate or overlapping AI actions make one request and explicit replay keeps FULL_AI", async () => {
  const pending = deferred(); const calls = [];
  const service = { ...baseService(), runResolution: (id, mode) => { calls.push([id, mode]); return pending.promise; },
    runAdvocates: () => { throw new Error("Overlapping advocate call"); } };
  const app = harness(service); await app.flush(); const panel = app.panel();
  panel.onRunDemo(); panel.onRunDemo(); panel.onRunAdvocates(); await app.flush();
  assert.deepEqual(calls, [["DISP-002", "FULL_AI"]]);
  pending.resolve(result("DISP-002")); await app.flush();
  assert.equal(app.panel().resolutionRun.caseId, "DISP-002");
});


test("all eight returned cases are selectable and finish loading their detail and analysis", async () => {
  const eight = Array.from({ length: 8 }, (_, n) => ({ ...cases[0], id: `DISP-${String(n + 2).padStart(3, "0")}` }));
  const h = harness({ ...baseService(), list: async () => eight, getById: async id => eight.find(c => c.id === id) });
  await h.flush();
  assert.equal(h.nodes().filter(node => node.type === "button" && eight.some(c => c.id === node.key)).length, 8);
  for (const item of eight) { h.select(item.id); await h.flush(); assert.equal(h.panel().caseData.id, item.id); assert.equal(h.panel().analysis.caseId, item.id); }
});

test("a failed case list ends loading and retains the actual error without starting detail requests", async () => {
  const pending = deferred(); let details = 0;
  const h = harness({ ...baseService(), list: () => pending.promise, getById: async () => { details++; throw new Error("Unexpected detail request"); } });
  assert.ok(h.nodes().some(node => typeof node.props?.children === "string" && node.props.children.includes("Loading RydeResolve")));
  pending.reject(new Error("Demo authentication required")); await h.flush();
  assert.ok(h.nodes().some(node => node.type === "h1" && node.props.children === "Case API unavailable"));
  assert.ok(h.nodes().some(node => node.type === "p" && node.props.children === "Demo authentication required"));
  assert.equal(details, 0); assert.equal(h.panel(), undefined);
});

test("failed case details finish loading and display the error instead of an empty case", async () => {
  const h = harness({ ...baseService(), getById: async () => { throw new Error("Case detail unavailable"); } });
  await h.flush(); assert.equal(h.panel(), undefined);
  assert.ok(h.nodes().some(node => node.type === "p" && node.props.children === "Case detail unavailable"));
});
