// Server rendering of the real components, without a browser or HTTP calls.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import ts from 'typescript';
const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const cache = new Map();
function load(file) {
  if (cache.has(file)) return cache.get(file);
  const compiled = ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022
  } }).outputText;
  const mod = { exports: {} }; cache.set(file, mod.exports);
  new Function('require', 'module', 'exports', compiled)(name => {
    if (name.includes('services/intakeService')) return { intakeService: {}, IntakeApiError: class extends Error {} };
    if (!name.startsWith('.')) return require(name);
    const path = resolve(dirname(file), name);
    for (const ext of ['.tsx', '.ts']) { try { return load(path + ext); } catch (e) { if (e.code !== 'ENOENT') throw e; } }
    throw new Error(`Missing dependency ${name}`);
  }, mod, mod.exports);
  cache.set(file, mod.exports); return mod.exports;
}
const root = fileURLToPath(new URL('../src/', import.meta.url));
const { CaseDetail } = load(resolve(root, 'components/CaseDetail.tsx'));
const { AiReviewTimeline } = load(resolve(root, 'components/AiReviewTimeline.tsx'));
const { ReplayBadge } = load(resolve(root, 'components/ReplayBadge.tsx'));
const side = name => ({ side: name, status: 'COMPLETE', summary: `${name} account reviewed`, verifiedClaims: [], rejectedClaims: [{ evidenceIds: ['UNTRUSTED'] }], execution: null });
const props = { caseData: { id: 'DISP-005', title: 'Route dispute', disputeType: 'route_deviation', rider: { name: 'Rider', id: 'R1' }, driver: { name: 'Driver', id: 'D1' }, evidence: [], timeline: [], trip: { pickup: "A", destination: "B" }, fare: { currency: "SGD", quoted: 10, actual: 10 }, metadata: {} }, analysis: null, advocateRun: null, resolutionRun: null, advocateRunning: false, resolutionRunning: false, advocateError: null, resolutionError: null, onRunResolution() {}, onRunAdvocates() {}, onRunDemo() {} };
const render = (component, properties) => renderToStaticMarkup(React.createElement(component, properties));
test('workspace has exactly four accessible sections and one primary analysis action', () => {
  const html = render(CaseDetail, props);
  for (const title of ['Hear Both Sides', 'Check the Evidence', 'AI Review', 'Final Decision']) assert.ok(html.includes(title));
  assert.equal((html.match(/id="workflow-step-\d"/g) ?? []).length, 4);
  assert.equal((html.match(/class="wf-run"/g) ?? []).length, 1);
  assert.ok(html.includes('More options'));
  assert.ok(html.includes('aria-expanded="true"'));
  assert.ok(!html.includes('Resolution pipeline'));
});
test('running and failed analysis have honest loading, retry and preserved-work messages', () => {
  const busy = render(CaseDetail, { ...props, resolutionRunning: true });
  assert.ok(busy.includes('Running analysis')); assert.ok(busy.includes('disabled=""'));
  const failed = render(CaseDetail, { ...props, resolutionError: 'REPLAY_INVALID' });
  assert.ok(failed.includes('role="alert"')); assert.ok(failed.includes('saved work'));
  assert.ok(failed.includes('Error details')); assert.ok(!failed.includes('Review complete'));
});
test('AI timeline preserves five roles, rejected counts and advisory human-review status', () => {
  const resolution = { rebuttals: { rider: { status: 'COMPLETE', overallSummary: 'Rider response', verifiedRebuttals: [], rejectedRebuttals: [] }, driver: { status: 'NOT_RUN', verifiedRebuttals: [], rejectedRebuttals: [] } }, judge: { status: 'PENDING_HUMAN_REVIEW', recommendedOutcome: 'NO_REFUND', reasoningSummary: 'Evidence needs review', evidenceIds: [] } };
  const html = render(AiReviewTimeline, { rider: side('RIDER'), driver: side('DRIVER'), resolution });
  assert.equal((html.match(/<li>/g) ?? []).length, 5);
  assert.ok(html.includes('0 accepted · 1 rejected')); assert.ok(html.includes('Needs human review'));
  assert.ok(html.includes('Advisory:')); assert.ok(!html.includes('UNTRUSTED'));
  assert.ok(html.includes('Not Run'));
});
test('saved mock and live replay labels remain visible and never infer provenance from runtime provider', () => {
  const base = { replayed: true, reusedRoles: [], capturedProvider: 'mock', advocatesReplayed: true, rebuttalsReplayed: true, judgeReplayed: true };
  assert.ok(render(ReplayBadge, { metadata: { ...base, source: 'MOCK_DEMO' } }).includes('MOCK DEMO'));
  assert.ok(render(ReplayBadge, { metadata: { ...base, source: 'REPLAYED_LIVE' } }).includes('REPLAYED LIVE'));
});
function hookComponent(exportName, service, initialIntake) {
  const file = resolve(root, 'components/IntakeChatPanel.tsx');
  const compiled = ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText + '\nexports.PhoneMockup = PhoneMockup;';
  const hooks = []; let cursor = 0;
  const react = { useCallback: fn => fn, useEffect() {}, useRef(initial) { const i = cursor++; hooks[i] ??= { current: initial }; return hooks[i]; },
    useState(initial) { const i = cursor++; if (!(i in hooks)) hooks[i] = initialIntake && i === 0 ? initialIntake : initialIntake && i === 1 ? false : initial;
      return [hooks[i], value => { hooks[i] = typeof value === 'function' ? value(hooks[i]) : value; }]; } };
  const jsx = (type, props) => ({ type, props }); const mod = { exports: {} };
  new Function('require', 'module', 'exports', compiled)(name => name === 'react' ? react : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx, Fragment: 'fragment' } : { intakeService: service, IntakeApiError: class extends Error {} }, mod, mod.exports);
  return properties => { cursor = 0; return mod.exports[exportName](properties); };
}
function nodes(node) { if (!node || typeof node !== 'object') return []; return [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)]; }
test('failed interview sends retain the draft; successful sends clear it', async () => {
  let succeed = false; const phone = hookComponent('PhoneMockup', {});
  const properties = { party: 'rider', intakeCase: { lifecycle: 'RIDER_INTERVIEW', messages: [] }, onSend: async () => succeed, loading: false, disabled: false };
  let tree = phone(properties);
  nodes(tree).find(n => n.type === 'input').props.onChange({ target: { value: 'My route statement' } });
  tree = phone(properties); await nodes(tree).find(n => n.type === 'button').props.onClick();
  assert.equal(nodes(phone(properties)).find(n => n.type === 'input').props.value, 'My route statement');
  succeed = true; await nodes(phone(properties)).find(n => n.type === 'button').props.onClick();
  assert.equal(nodes(phone(properties)).find(n => n.type === 'input').props.value, '');
});
test('rapid intake actions share an immediate guard before React rerenders', async () => {
  let release; let calls = 0;
  const pending = new Promise(resolve => { release = resolve; });
  const intake = { id: 'INTAKE-TEST', lifecycle: 'RIDER_INTERVIEW', messages: [] };
  const panel = hookComponent('IntakeChatPanel', { sendMessage() { calls++; return pending; } }, intake);
  const tree = panel({ sourceCaseId: 'DISP-005' });
  const phones = nodes(tree).filter(n => typeof n.type === 'function' && n.type.name === 'PhoneMockup');
  const first = phones[0].props.onSend('Statement'); const duplicate = phones[0].props.onSend('Statement');
  assert.equal(calls, 1); assert.equal(await duplicate, false);
  release({ case: intake }); assert.equal(await first, true);
});
test('completed review points to the decision instead of triggering another model run', () => {
  const done = { status: 'COMPLETE' }; const pair = { rider: done, driver: done };
  const resolution = { rider: side('RIDER'), driver: side('DRIVER'), rebuttals: { rider: {}, driver: {} }, judge: {},
    workflow: { deterministic: done, advocates: pair, rebuttals: pair, judge: done, finalResolution: done },
    deterministicResolution: { recommendedAction: 'NO_REFUND', refundAmount: 0, currency: 'SGD', resolutionMode: 'AUTO_RESOLVE' },
    replayMetadata: { source: 'MOCK_DEMO', replayed: false, valid: true, reusedRoles: [] } };
  const html = render(CaseDetail, { ...props, resolutionRun: resolution });
  assert.ok(html.includes('View decision')); assert.ok(html.includes('Run analysis again'));
  assert.ok(!html.includes('Advocates only')); assert.ok(html.includes('MOCK DEMO'));
});
test('untrusted model text is escaped instead of rendered as executable markup', () => {
  const payload = '<img src=x onerror="alert(1)"><script>alert(1)</script>';
  const html = render(AiReviewTimeline, { rider: { ...side('RIDER'), summary: payload }, driver: side('DRIVER'), resolution: null });
  assert.ok(!html.includes('<script>')); assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;img')); assert.ok(html.includes('&lt;script&gt;'));
});
test('UI crashes do not disclose private error messages or stacks', () => {
  const { ErrorBoundary } = load(resolve(root, 'components/ErrorBoundary.tsx'));
  const boundary = new ErrorBoundary({ children: null });
  boundary.state = { error: new Error('SYNTHETIC_PRIVATE_ERROR') };
  const html = renderToStaticMarkup(boundary.render());
  assert.ok(!html.includes('SYNTHETIC_PRIVATE_ERROR')); assert.ok(html.includes('Saved interviews'));
  const previous = console.error; const logged = []; console.error = (...args) => logged.push(args.join(' '));
  try { boundary.componentDidCatch(boundary.state.error, { componentStack: 'SYNTHETIC_PRIVATE_STACK' }); }
  finally { console.error = previous; }
  assert.ok(!logged.join(' ').includes('SYNTHETIC_PRIVATE'));
});
