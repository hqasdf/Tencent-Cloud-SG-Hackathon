import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { RouteDeviationDisputePipeline } from "../src/pipeline.js";
import { RouteDeviationPolicyEngine } from "../src/policy-engine.js";
import { CaseReplay } from "../src/case-replay.js";
import { buildEvidenceCatalog } from "../src/evidence.js";

const here = dirname(fileURLToPath(import.meta.url));
const policy = JSON.parse(await readFile(join(here, "..", "policies", "route-deviation.v1.json"), "utf8"));

function makeCase(overrides = {}) {
  const base = {
    case_id: "CASE_TEST_001",
    dispute_type: "route_deviation",
    trip: {
      expected_route: { distance_km: 10, duration_minutes: 20 },
      actual_route: { distance_km: 13, duration_minutes: 28 },
      fare: { expected_amount: 18, actual_amount: 22.5, currency: "SGD" },
      events: [
        { timestamp: "2026-09-19T10:28:00+08:00", type: "trip_completed", evidence_ids: ["GPS_001", "FARE_001"] },
        { timestamp: "2026-09-19T10:00:00+08:00", type: "trip_started", evidence_ids: ["GPS_001"] }
      ]
    },
    evidence: [
      { id: "GPS_001", type: "actual_route", source: "trip_telemetry", valid: true },
      { id: "ROUTE_BASELINE_001", type: "baseline_route", source: "route_service", valid: true },
      { id: "FARE_001", type: "fare", source: "payment_ledger", valid: true }
    ]
  };
  return {
    ...base,
    ...overrides,
    trip: { ...base.trip, ...(overrides.trip ?? {}) },
    evidence: overrides.evidence ?? base.evidence
  };
}

function pipeline(options = {}) {
  return new RouteDeviationDisputePipeline({ policy, ...options });
}

test("valid route deviation returns the complete structured resolution", async () => {
  const result = await pipeline().resolve(makeCase());
  assert.equal(result.dispute_type, "route_deviation");
  assert.equal(result.case_replay.metrics.distance_deviation_pct, 30);
  assert.equal(result.timeline[0].type, "trip_started");
  assert.ok(Array.isArray(result.verified_claims));
  assert.equal(result.resolution_mode, "AUTO_RESOLVE");
  assert.equal(result.audit.schema_version, "route_deviation_resolution_v1");
});

test("justified deviation upholds the fare", async () => {
  const input = makeCase({
    evidence: [
      ...makeCase().evidence,
      { id: "TRAFFIC_001", type: "traffic", source: "traffic_feed", valid: true }
    ]
  });
  const result = await pipeline().resolve(input);
  assert.equal(result.policy_result.outcome, "JUSTIFIED_DEVIATION");
  assert.equal(result.ruling, "DRIVER_FAVORED");
  assert.equal(result.recommended_action, "UPHOLD_FARE");
  assert.equal(result.refund_amount, 0);
});

test("unjustified deviation produces a deterministic partial refund", async () => {
  const result = await pipeline().resolve(makeCase());
  assert.equal(result.policy_result.outcome, "UNJUSTIFIED_DEVIATION");
  assert.equal(result.ruling, "RIDER_FAVORED");
  assert.equal(result.recommended_action, "PARTIAL_REFUND");
  assert.equal(result.refund_amount, 4.5);
});

test("missing required evidence escalates to human review", async () => {
  const input = makeCase({ evidence: makeCase().evidence.filter((item) => item.type !== "fare") });
  const result = await pipeline().resolve(input);
  assert.equal(result.policy_result.outcome, "INSUFFICIENT_EVIDENCE");
  assert.equal(result.resolution_mode, "HUMAN_REVIEW");
  assert.ok(result.escalation_reasons.includes("MISSING_REQUIRED_EVIDENCE"));
});

test("invalid evidence IDs are rejected before the Judge", async () => {
  const invalidRiderAdvocate = {
    async argue() {
      return {
        actor: "rider",
        output_schema: "advocate_case_v1",
        summary: "Injected malformed model output",
        claims: [{
          id: "HALLUCINATED_CLAIM",
          actor: "rider",
          position: "supports",
          claim: "A nonexistent sensor proves the claim.",
          evidence_ids: ["DOES_NOT_EXIST"],
          policy_refs: ["ROUTE_POLICY_01"]
        }]
      };
    }
  };
  const result = await pipeline({ riderAdvocate: invalidRiderAdvocate }).resolve(makeCase());
  assert.equal(result.rejected_claims[0].reason, "INVALID_EVIDENCE_ID");
  assert.ok(!result.audit.judge_input_claim_ids.includes("HALLUCINATED_CLAIM"));
  assert.equal(result.resolution_mode, "HUMAN_REVIEW");
});

test("contradictory evidence lowers confidence and escalates", async () => {
  const input = makeCase({
    evidence: [
      ...makeCase().evidence,
      { id: "GPS_ALT_001", type: "actual_route", source: "secondary_telemetry", valid: true, contradicts: ["GPS_001"] }
    ]
  });
  const result = await pipeline().resolve(input);
  assert.equal(result.evidence_verification.contradictions.length, 1);
  assert.ok(result.escalation_reasons.includes("CONTRADICTORY_EVIDENCE"));
  assert.equal(result.resolution_mode, "HUMAN_REVIEW");
});

test("low confidence escalates instead of forcing a ruling", async () => {
  const emptyAdvocate = {
    async argue() {
      return { actor: "rider", output_schema: "advocate_case_v1", summary: "No claims", claims: [] };
    }
  };
  const result = await pipeline({ riderAdvocate: emptyAdvocate, driverAdvocate: emptyAdvocate }).resolve(makeCase());
  assert.ok(result.confidence < policy.thresholds.auto_resolve_confidence);
  assert.ok(result.escalation_reasons.includes("LOW_CONFIDENCE"));
  assert.equal(result.resolution_mode, "HUMAN_REVIEW");
});

test("policy evaluation is deterministic and honors exact thresholds", () => {
  const thresholdCase = makeCase({
    trip: {
      expected_route: { distance_km: 10, duration_minutes: 20 },
      actual_route: { distance_km: 11.5, duration_minutes: 20 },
      fare: { expected_amount: 18, actual_amount: 20, currency: "SGD" }
    }
  });
  const replay = new CaseReplay().reconstruct(thresholdCase);
  const evidenceCatalog = buildEvidenceCatalog(thresholdCase.evidence);
  const engine = new RouteDeviationPolicyEngine(policy);
  const first = engine.evaluate({ replay, evidenceCatalog });
  const second = engine.evaluate({ replay, evidenceCatalog });
  assert.deepEqual(first, second);
  assert.equal(first.checks.distance_threshold_exceeded, true);
  assert.equal(first.outcome, "UNJUSTIFIED_DEVIATION");
  assert.equal(first.refund_amount, 2);
});
