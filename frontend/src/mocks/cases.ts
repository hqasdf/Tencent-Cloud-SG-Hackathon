import type { DisputeCase, Evidence, TimelineEvent } from "../types/dispute";

const timeline = (route: boolean, conflict = false): TimelineEvent[] => [
  { id: "E01", timestamp: "09:02", type: "Booking accepted", description: "Driver accepted the booking.", evidenceIds: ["E01"] },
  { id: "E02", timestamp: "09:09", type: "Driver arrived", description: "Driver location recorded at pickup point.", evidenceIds: ["E02"] },
  { id: "E03", timestamp: "09:12", type: "Trip started", description: "Trip started and GPS telemetry began.", evidenceIds: ["E03"] },
  ...(route ? [{ id: "E04", timestamp: "09:26", type: "Route deviation", description: "Actual route diverged from the recommended route.", evidenceIds: ["E04", "E05"], severity: "attention" as const }] : []),
  { id: "E06", timestamp: "09:31", type: "Chat message", description: route ? "Driver reported heavier traffic ahead." : "Driver notified rider that waiting time had started.", evidenceIds: ["E06"] },
  { id: "E07", timestamp: "09:40", type: route ? "Trip completed" : "Cancellation", description: route ? "Trip completed and final fare calculated." : "System recorded cancellation and payment event.", evidenceIds: ["E07", "E08"], severity: conflict ? "conflict" as const : "normal" as const }
];

const evidence = (route: boolean, conflict = false): Evidence[] => [
  { id: "E01", type: "Trip event", timestamp: "09:02", source: "Trip service", summary: "Booking acceptance record.", status: "Verified" },
  { id: "E02", type: "GPS", timestamp: "09:09", source: "Driver telemetry", summary: route ? "Pickup arrival confirmed." : "Arrival within 75 m pickup radius.", status: conflict ? "Conflicting" : "Verified" },
  { id: "E03", type: "GPS", timestamp: "09:12", source: "Trip telemetry", summary: "Trip GPS trace and timestamps.", status: conflict ? "Conflicting" : "Verified" },
  { id: "E04", type: route ? "GPS" : "Trip event", timestamp: "09:26", source: route ? "Route comparison" : "Cancellation service", summary: route ? "Route deviation segment detected." : "Waiting timer started.", status: "Verified" },
  { id: "E05", type: route ? "Fare" : "Policy", timestamp: "09:40", source: route ? "Payment ledger" : "Policy engine", summary: route ? "Fare difference calculated from final trip charge." : "No-show waiting threshold rule.", status: "Verified" },
  { id: "E06", type: "Chat", timestamp: "09:31", source: "In-app messages", summary: route ? "Driver message identifies traffic conditions." : "Driver message states waiting has begun.", status: "Verified" },
  { id: "E07", type: "Trip event", timestamp: "09:40", source: "Trip service", summary: "Final trip or cancellation event.", status: conflict ? "Conflicting" : "Verified" },
  { id: "E08", type: "Policy", timestamp: "09:40", source: "Policy registry", summary: route ? "Route deviation refund policy." : "No-show charge policy.", status: "Verified" }
];

function createCase(input: Partial<DisputeCase> & Pick<DisputeCase, "id" | "disputeType" | "title" | "status">): DisputeCase {
  const { id, disputeType, title, status, ...overrides } = input;
  const route = disputeType === "route_deviation";
  const conflict = status === "Human Review";
  const defaultResolution = input.status === "Human Review"
    ? { ruling: "Pending human review", recommendedAction: "Assign to specialist", refundAmount: 0, currency: "SGD", acceptedClaimIds: ["C-R-01"], rejectedClaimIds: ["C-D-02"], mode: "HUMAN_REVIEW" as const, explanation: "Conflicting GPS and timestamp evidence prevents an automated decision.", counterfactualExplanation: "A reconciled arrival timestamp and location trace would allow the waiting-time rule to be applied.", escalationReason: "GPS evidence and trip timestamps conflict." }
    : { ruling: route ? "Partial refund approved" : "Charge upheld", recommendedAction: route ? "Refund fare difference" : "No action", refundAmount: route ? 2.3 : 0, currency: "SGD", acceptedClaimIds: ["C-R-01"], rejectedClaimIds: ["C-D-02"], mode: "AUTO_RESOLVE" as const, explanation: route ? "Verified GPS and fare evidence show an unexplained portion of the route caused a fare difference." : "Verified arrival radius and waiting time meet the no-show policy threshold.", counterfactualExplanation: route ? "Verified road closure evidence covering the remaining deviation would have removed the refund." : "A verified wait time below the policy threshold would have reversed the charge." };
  return {
    id, disputeType, title, status,
    description: route ? "Rider disputes a fare after a route longer than the estimated journey." : "Rider disputes a no-show cancellation charge.",
    rider: { name: "Amelia Tan", id: "R-1048" }, driver: { name: "Marcus Lim", id: "D-2891" },
    trip: { tripId: `TRP-${input.id.slice(-4)}`, pickup: "Tiong Bahru MRT", destination: "Raffles Place", bookedAt: "19 Sep 2026, 09:00", distance: route ? "Expected 5.8 km / actual 7.1 km" : "Pickup radius 75 m", duration: route ? "Expected 18 min / actual 26 min" : "Driver waited 6 min 12 sec" },
    fare: { currency: "SGD", quoted: route ? 11.2 : 6, actual: route ? 13.5 : 6, difference: route ? 2.3 : 0 },
    riderComplaint: route ? "The driver took a much longer route and I was overcharged." : "I was charged even though the driver did not arrive correctly.",
    driverResponse: route ? "Traffic near the planned road required an alternate route." : "I arrived at the pickup point and waited beyond the required period.",
    metadata: { submittedAt: "19 Sep 2026, 10:05", lastUpdated: "19 Sep 2026, 10:12", priority: conflict ? "High" : "Standard", policyVersion: "2026.09" },
    timeline: timeline(route, conflict), evidence: evidence(route, conflict),
    riderCase: { party: "Rider", summary: route ? "Measured route and fare evidence support a partial adjustment." : "Rider questions the arrival and waiting-time record.", claims: [{ id: "C-R-01", claim: route ? "The route was significantly longer than expected." : "The no-show charge should not apply.", evidenceIds: route ? ["E04", "E05"] : ["E02", "E07"], policyRefs: [route ? "P_ROUTE_01" : "P_NOSHOW_01"], status: "Verified" }] },
    driverCase: { party: "Driver", summary: route ? "Traffic evidence explains part of the deviation." : "Arrival and wait records support the cancellation charge.", claims: [{ id: "C-D-01", claim: route ? "Traffic conditions justified the alternate route segment." : "The driver arrived within the pickup radius and waited long enough.", evidenceIds: ["E02", "E06"], policyRefs: [route ? "P_ROUTE_01" : "P_NOSHOW_01"], status: "Verified" }, { id: "C-D-02", claim: "Unverified statement should determine the ruling.", evidenceIds: ["E99"], policyRefs: [route ? "P_ROUTE_01" : "P_NOSHOW_01"], status: "Rejected", reason: "Evidence ID is not in the case record." }] },
    activity: ["Case received", "Evidence collected", "Rider Advocate analysing", "Driver Advocate analysing", "Claims verified", "Policy evaluated", "Judge reviewing", "Resolution generated"].map((label, index) => ({ id: `A${index + 1}`, label, state: index < 6 ? "complete" : (index === 6 && status === "Investigating") || (index === 7 && status === "Human Review") ? "active" : status === "Pending" ? "queued" : "complete", detail: index < 6 ? "Completed with traceable outputs." : status === "Human Review" && index === 7 ? "Awaiting a human reviewer." : "Decision workflow status." })),
    policyResult: { policyId: route ? "P_ROUTE_01" : "P_NOSHOW_01", name: route ? "Route deviation adjustment" : "No-show charge", ruleSummary: route ? "Refund only the documented, unjustified fare difference." : "Uphold the charge when arrival radius and waiting threshold are verified.", outcome: route ? "Partial deviation justified" : "Threshold conditions satisfied" },
    confidence: { evidenceCompleteness: conflict ? 70 : 96, contradictoryEvidence: conflict ? 35 : 98, policyClarity: 95, missingInformation: conflict ? 55 : 92, advocateDisagreement: route ? 76 : 95, overall: conflict ? 58 : route ? 87 : 94 },
    resolution: defaultResolution,
    humanReviewSummary: conflict ? "The driver GPS trace places arrival within the pickup radius, while the rider-facing trip timestamp was recorded after cancellation. Verify source clock order and the definitive location trace before applying the no-show rule." : undefined,
    ...overrides
  };
}

export const mockCases: DisputeCase[] = [
  createCase({ id: "CASE-2026-1041", disputeType: "route_deviation", title: "Route deviation with partially unexplained fare", status: "Auto Resolved" }),
  createCase({ id: "CASE-2026-1042", disputeType: "route_deviation", title: "Route deviation fully supported by traffic records", status: "Auto Resolved", evidence: evidence(true).map((item) => item.id === "E06" ? { ...item, type: "Traffic", source: "City traffic feed", summary: "Verified congestion event covers the alternate route segment." } : item), resolution: { ruling: "No refund", recommendedAction: "Uphold fare", refundAmount: 0, currency: "SGD", acceptedClaimIds: ["C-D-01"], rejectedClaimIds: ["C-D-02"], mode: "AUTO_RESOLVE", explanation: "Traffic and route records explain the full deviation.", counterfactualExplanation: "Without the verified traffic record, the unexplained fare difference would be refundable." } }),
  createCase({ id: "CASE-2026-1043", disputeType: "no_show_charge", title: "No-show charge supported by arrival and wait evidence", status: "Auto Resolved" }),
  createCase({ id: "CASE-2026-1044", disputeType: "no_show_charge", title: "No-show charge with conflicting arrival evidence", status: "Human Review" })
];
