export type DisputeType = "route_deviation" | "no_show_charge";
export type CaseStatus = "Pending" | "Investigating" | "Auto Resolved" | "Human Review";
export type EvidenceStatus = "Verified" | "Pending" | "Conflicting" | "Missing";
export type ClaimStatus = "Verified" | "Rejected" | "Pending";
export type ResolutionMode = "AUTO_RESOLVE" | "HUMAN_REVIEW";

export interface Person { name: string; id: string; }
export interface TripInfo { tripId: string; pickup: string; destination: string; bookedAt: string; distance: string; duration: string; }
export interface FareInfo { currency: string; quoted: number; actual: number; difference: number; }
export interface CaseMetadata { submittedAt: string; lastUpdated: string; priority: "Standard" | "High"; policyVersion: string; }
export interface TimelineEvent { id: string; timestamp: string; type: string; description: string; evidenceIds: string[]; severity?: "normal" | "attention" | "conflict"; }
export interface Evidence { id: string; type: "GPS" | "Chat" | "Fare" | "Trip event" | "Policy" | "Traffic" | "System"; timestamp: string; source: string; summary: string; status: EvidenceStatus; }
export interface AdvocateClaim { id: string; claim: string; evidenceIds: string[]; policyRefs: string[]; status: ClaimStatus; reason?: string; }
export interface AdvocateCase { party: "Rider" | "Driver"; summary: string; claims: AdvocateClaim[]; }
export interface PolicyResult { policyId: string; name: string; ruleSummary: string; outcome: string; }
export interface ConfidenceBreakdown { evidenceCompleteness: number; contradictoryEvidence: number; policyClarity: number; missingInformation: number; advocateDisagreement: number; overall: number; }
export interface Resolution { ruling: string; recommendedAction: string; refundAmount: number; currency: string; acceptedClaimIds: string[]; rejectedClaimIds: string[]; mode: ResolutionMode; explanation: string; counterfactualExplanation: string; escalationReason?: string; }
export interface AgentActivity { id: string; label: string; state: "complete" | "active" | "queued"; detail: string; }
export interface DisputeCase { id: string; disputeType: DisputeType; title: string; status: CaseStatus; description: string; rider: Person; driver: Person; trip: TripInfo; fare: FareInfo; riderComplaint: string; driverResponse: string; metadata: CaseMetadata; timeline: TimelineEvent[]; evidence: Evidence[]; riderCase: AdvocateCase; driverCase: AdvocateCase; activity: AgentActivity[]; policyResult: PolicyResult; confidence: ConfidenceBreakdown; resolution: Resolution; humanReviewSummary?: string; }
export interface CaseSummary { id: string; disputeType: DisputeType; title: string; status: CaseStatus; rider: Person; driver: Person; confidence: ConfidenceBreakdown; resolution: Resolution; }
export interface DeterministicAnalysis { analysisType: DisputeType; [key: string]: string | number | boolean | string[] | Condition[] | undefined; }
export interface Condition { type: string; explainedDistanceKm: number; evidenceIds: string[]; }
export interface RuleEvaluation { ruleId: string; description: string; passed: boolean; actualValue: string | number | boolean; requiredValue: string | number | boolean; evidenceIds: string[]; }
export interface DeterministicPolicyEvaluation { policyId: string; policyVersion: string; evaluatedRules: RuleEvaluation[]; passedRules: string[]; failedRules: string[]; supportingEvidenceIds: string[]; overallOutcome: string; }
export interface ResolutionCalculation { fareDifference: number; explainedFareImpact: number; unexplainedFareImpact: number; ratioApplied: number; }
export interface ResolutionRecommendation { ruling: string; recommendedAction: string; refundAmount: number; currency: string; calculation: ResolutionCalculation; explanation: string; counterfactualExplanation: string; }
export interface DeterministicConfidence { evidenceCompleteness: number; policyClarity: number; factualConsistency: number; invalidEvidencePenalty: number; contradictionPenalty: number; missingDataPenalty: number; overallConfidence: number; prototypeNotice: string; }
export interface CaseAnalysis { caseId: string; disputeType: DisputeType; analysis: DeterministicAnalysis; policyEvaluation: DeterministicPolicyEvaluation; resolutionRecommendation: ResolutionRecommendation; confidence: DeterministicConfidence; resolutionMode: ResolutionMode; escalationReasons: string[]; }
