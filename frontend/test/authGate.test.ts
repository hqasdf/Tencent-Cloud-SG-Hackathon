import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";
import { authenticatedHeaders, clearApiSession, hasApiSession, setApiSession } from "../src/services/apiSession.ts";

const compiled = ts.transpileModule(readFileSync(new URL("../src/AuthenticatedApp.tsx", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 }
}).outputText;

function harness(capabilities, login = async () => {}) {
  const hooks = []; const listeners = new Map(); let cursor = 0; let effects = []; let tree;
  const workspace = () => null;
  const jsx = (type, props) => ({ type, props });
  const react = {
    useState(initial) { const i = cursor++; if (!hooks[i]) hooks[i] = { value: initial }; return [hooks[i].value, value => { hooks[i].value = typeof value === "function" ? value(hooks[i].value) : value; }]; },
    useEffect(run, deps) { const i = cursor++; if (!hooks[i]) { hooks[i] = { deps }; effects.push(run); } }
  };
  const mod = { exports: {} };
  const fakeWindow = { addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) };
  new Function("require", "module", "exports", "window", compiled)(name => {
    if (name === "react") return react;
    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx, Fragment: "fragment" };
    if (name === "./App") return { default: workspace };
    if (name.includes("authService")) return { backendCapabilities: async () => capabilities, loginDemo: login };
    if (name.includes("apiSession")) return {
      hasApiSession: () => hasApiSession(),
      clearApiSession: () => { clearApiSession(); listeners.get("ryde-auth-expired")?.(); }
    };
    throw new Error(name);
  }, mod, mod.exports, fakeWindow);
  function render() { cursor = 0; effects = []; tree = mod.exports.default(); effects.forEach(fn => fn()); }
  function nodes(node) { if (!node || typeof node !== "object") return []; return [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)]; }
  render();
  return { workspace, nodes: () => nodes(tree), render, async flush() { for (let i = 0; i < 10; i++) await Promise.resolve(); render(); } };
}

test("cloud workspace stays inaccessible until successful sign-in and signs out", async () => {
  let received;
  const h = harness({ authenticationRequired: true, liveAIEnabled: false }, async value => { received = value; });
  await h.flush();
  assert.equal(h.nodes().some(node => node.type === h.workspace), false);
  h.nodes().find(node => node.type === "input").props.onChange({ target: { value: "synthetic-password" } });
  h.render();
  h.nodes().find(node => node.type === "form").props.onSubmit({ preventDefault() {} });
  await h.flush();
  assert.equal(received, "synthetic-password");
  assert.equal(h.nodes().find(node => node.type === h.workspace).props.cloudDemo, true);
  h.nodes().find(node => node.props?.className === "demo-signout").props.onClick();
  h.render();
  assert.equal(h.nodes().some(node => node.type === h.workspace), false);
  assert.equal(h.nodes().find(node => node.type === "input").props.value, "");
});

test("failed login clears password and leaves cloud workspace protected", async () => {
  const h = harness({ authenticationRequired: true, liveAIEnabled: false }, async () => { throw new Error("Invalid demo credentials"); });
  await h.flush();
  h.nodes().find(node => node.type === "form").props.onSubmit({ preventDefault() {} });
  await h.flush();
  assert.equal(h.nodes().some(node => node.type === h.workspace), false);
  assert.equal(h.nodes().find(node => node.props?.role === "alert").props.children, "Invalid demo credentials");
  assert.equal(h.nodes().find(node => node.type === "input").props.value, "");
});

test("SQLite development retains the existing workspace without cloud sign-in", async () => {
  const h = harness({}); await h.flush();
  assert.equal(h.nodes().find(node => node.type === h.workspace).props.cloudDemo, false);
  assert.equal(h.nodes().some(node => node.type === "form"), false);
});

test("authenticated sign-out is in the header before the desktop workspace and clears the real session", async () => {
  setApiSession("synthetic-header-session");
  try {
    const h = harness({ authenticationRequired: true, liveAIEnabled: false });
    await h.flush();
    const nodes = h.nodes();
    const header = nodes.find(node => node.type === "header");
    assert.equal(header.props["aria-label"], "Demo session");
    const button = [header.props.children].flat().find(node => node.type === "button");
    assert.equal(button.props.children, "Sign out");
    assert.equal(button.props.type, "button");
    assert.equal(button.props.disabled, undefined);
    assert.ok(nodes.indexOf(header) < nodes.findIndex(node => node.type === h.workspace));
    button.props.onClick();
    h.render();
    assert.equal(hasApiSession(), false);
    assert.deepEqual(authenticatedHeaders(), {});
    assert.equal(h.nodes().some(node => node.type === h.workspace), false);
    assert.equal(h.nodes().some(node => node.type === "header"), false);
    assert.equal(h.nodes().find(node => node.type === "input").props.value, "");
    assert.ok(h.nodes().some(node => node.type === "form"));
  } finally { clearApiSession(); }
});


test("approved live cloud restores intake while retaining authenticated workspace and sign-out", async () => {
  setApiSession("synthetic-live-cloud-session");
  try {
    const h = harness({ authenticationRequired: true, liveAIEnabled: true });
    await h.flush();
    const workspace = h.nodes().find(node => node.type === h.workspace);
    assert.equal(workspace.props.cloudDemo, false);
    assert.equal(workspace.props.cloudHosted, true);
    assert.ok(h.nodes().some(node => node.props?.className === "demo-signout"));
  } finally { clearApiSession(); }
});
