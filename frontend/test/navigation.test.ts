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
    select(id) {
      nodes(tree).find(node => node.type === "button" && node.props.className?.includes("case-row")
        && node.props.children[0].props.children[0].props.children === id).props.onClick();
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
