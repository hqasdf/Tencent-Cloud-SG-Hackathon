import test from "node:test";
import assert from "node:assert/strict";

import {
  aiReviewLabel,
  businessRuleStatusLabel,
  counterfactualChangeLabel,
  deterministicResultLabel,
  intakeProgressLabel,
  namespaceClaimId,
  resolutionProgress
} from "../src/utils/uiContracts.ts";

test("counterfactual labels preserve inclusive and exclusive boundaries", () => {
  assert.equal(counterfactualChangeLabel("INCREASE_TO_OR_ABOVE"), "Would need to rise to at least");
  assert.equal(counterfactualChangeLabel("DECREASE_BELOW"), "Would need to fall below");
  assert.equal(counterfactualChangeLabel("DECREASE_TO_OR_BELOW"), "Would need to fall to at most");
  assert.equal(counterfactualChangeLabel("INCREASE_ABOVE"), "Would need to rise above");
});

test("resolution progress excludes AI intake", () => {
  assert.deepEqual(
    resolutionProgress(["ready", "done", "done", "done", "done", "done", "done", "done", "done"]),
    { done: 8, total: 8, failed: 0, blocked: 0 }
  );
  assert.deepEqual(
    resolutionProgress(["done", "done", "failed", "blocked"]),
    { done: 1, total: 3, failed: 1, blocked: 1 }
  );
});

test("AI intake has its own lifecycle progress", () => {
  assert.equal(intakeProgressLabel(null), "Not started");
  assert.equal(intakeProgressLabel("DRIVER_INTERVIEW"), "In progress");
  assert.equal(intakeProgressLabel("READY_FOR_ANALYSIS"), "Complete");
  assert.equal(intakeProgressLabel("AUTO_RESOLVED"), "Complete");
  assert.equal(intakeProgressLabel("HUMAN_REVIEW"), "Complete");
});

test("unmet business rules are conditions, not technical failures", () => {
  assert.equal(businessRuleStatusLabel(true), "Condition met");
  assert.equal(businessRuleStatusLabel(false), "Condition not met");
});

test("claim IDs are displayed with stable party namespaces", () => {
  assert.equal(namespaceClaimId("RIDER", "CLM-001"), "RIDER-CLM-001");
  assert.equal(namespaceClaimId("driver", "C02"), "DRIVER-C02");
  assert.equal(namespaceClaimId("RIDER", "RIDER-C01"), "RIDER-C01");
});

test("deterministic availability and AI review completion remain separate", () => {
  assert.equal(deterministicResultLabel(true), "Deterministic result available");
  assert.equal(aiReviewLabel(false, false), "AI review not complete");
  assert.equal(aiReviewLabel(false, true), "AI review in progress");
  assert.equal(aiReviewLabel(true, false), "AI review complete");
});
