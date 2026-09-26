/**
 * ⚠️ LEGACY IMPLEMENTATION — NOT USED BY THE RUNNING REACT + FASTAPI APPLICATION.
 * THE ACTIVE BACKEND IS `backend/`.
 *
 * The Stage 4 advocates live in `backend/app/agents/`. This pipeline is frozen
 * for reference and must not be extended.
 */
import { assertResolutionShape, validateDisputeInput } from "./contracts.js";
import { CaseReplay } from "./case-replay.js";
import { buildEvidenceCatalog, detectContradictions, verifyClaims } from "./evidence.js";
import { DriverAdvocateAgent, RiderAdvocateAgent } from "./advocates.js";
import { RouteDeviationPolicyEngine } from "./policy-engine.js";
import { JudgeAgent } from "./judge.js";
import { evaluateConfidence } from "./confidence.js";

export class RouteDeviationDisputePipeline {
  constructor({
    policy,
    caseReplay = new CaseReplay(),
    riderAdvocate = new RiderAdvocateAgent(),
    driverAdvocate = new DriverAdvocateAgent(),
    judge = new JudgeAgent()
  }) {
    this.policy = policy;
    this.caseReplay = caseReplay;
    this.riderAdvocate = riderAdvocate;
    this.driverAdvocate = driverAdvocate;
    this.judge = judge;
    this.policyEngine = new RouteDeviationPolicyEngine(policy);
  }

  async resolve(rawInput) {
    const dispute = validateDisputeInput(rawInput);
    const replay = this.caseReplay.reconstruct(dispute);
    const evidenceCatalog = buildEvidenceCatalog(dispute.evidence);

    const commonAgentInput = Object.freeze({
      dispute,
      replay,
      evidenceCatalog,
      policy: this.policy
    });
    const [riderCase, driverCase] = await Promise.all([
      this.riderAdvocate.argue(commonAgentInput),
      this.driverAdvocate.argue(commonAgentInput)
    ]);

    const verification = verifyClaims(
      [...riderCase.claims, ...driverCase.claims],
      evidenceCatalog,
      this.policyEngine.policyRefs
    );
    const contradictions = detectContradictions(evidenceCatalog);
    const policyResult = this.policyEngine.evaluate({ replay, evidenceCatalog });

    // The Judge receives only claims that passed evidence and policy-reference validation.
    const judgment = this.judge.decide({
      verifiedClaims: verification.verified_claims,
      policyResult,
      replay
    });
    const confidence = evaluateConfidence({
      policy: this.policy,
      policyResult,
      verifiedClaims: verification.verified_claims,
      rejectedClaims: verification.rejected_claims,
      contradictions
    });
    const executable = confidence.resolution_mode === "AUTO_RESOLVE";

    return assertResolutionShape({
      case_id: dispute.case_id,
      dispute_type: "route_deviation",
      timeline: replay.timeline,
      case_replay: {
        metrics: replay.metrics,
        calculation_method: replay.calculation_method
      },
      rider_case: riderCase,
      driver_case: driverCase,
      verified_claims: verification.verified_claims,
      rejected_claims: verification.rejected_claims,
      evidence_verification: {
        contradictions,
        evidence_count: evidenceCatalog.size
      },
      policy_result: policyResult,
      ruling: executable ? judgment.ruling : "PENDING_HUMAN_REVIEW",
      recommended_action: executable
        ? judgment.recommended_action
        : "ESCALATE_TO_HUMAN_REVIEW",
      refund_amount: executable ? judgment.refund_amount : 0,
      confidence: confidence.score,
      confidence_details: confidence.inputs,
      escalation_reasons: confidence.escalation_reasons,
      resolution_mode: confidence.resolution_mode,
      explanation: executable
        ? judgment.explanation
        : "The available evidence does not support an executable automated ruling. A human reviewer must resolve this dispute.",
      counterfactual_explanation: judgment.counterfactual_explanation,
      audit: {
        schema_version: "route_deviation_resolution_v1",
        policy_version: this.policy.version,
        judge_input_claim_ids: judgment.input_claim_ids,
        judge_evidence_ids: judgment.evidence_ids
      }
    });
  }
}
