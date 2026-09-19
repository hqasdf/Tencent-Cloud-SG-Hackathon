export class ValidationError extends Error {
  constructor(message, details = []) {
    super(message);
    this.name = "ValidationError";
    this.details = details;
  }
}

function isNonEmptyString(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function isFiniteNonNegative(value) {
  return Number.isFinite(value) && value >= 0;
}

function validateRoute(route, field, errors) {
  if (!route || typeof route !== "object") {
    errors.push(`${field} is required`);
    return;
  }

  const hasDistance = isFiniteNonNegative(route.distance_km);
  const hasPoints = Array.isArray(route.points) && route.points.length >= 2;
  if (!hasDistance && !hasPoints) {
    errors.push(`${field} requires distance_km or at least two points`);
  }
  if (!isFiniteNonNegative(route.duration_minutes)) {
    errors.push(`${field}.duration_minutes must be a non-negative number`);
  }
}

export function validateDisputeInput(input) {
  const errors = [];
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new ValidationError("Dispute input must be an object");
  }
  if (!isNonEmptyString(input.case_id)) errors.push("case_id is required");
  if (input.dispute_type !== "route_deviation") {
    errors.push("dispute_type must be route_deviation");
  }
  if (!input.trip || typeof input.trip !== "object") {
    errors.push("trip is required");
  } else {
    validateRoute(input.trip.expected_route, "trip.expected_route", errors);
    validateRoute(input.trip.actual_route, "trip.actual_route", errors);
    const fare = input.trip.fare;
    if (!fare || typeof fare !== "object") {
      errors.push("trip.fare is required");
    } else {
      if (!isFiniteNonNegative(fare.expected_amount)) errors.push("trip.fare.expected_amount must be non-negative");
      if (!isFiniteNonNegative(fare.actual_amount)) errors.push("trip.fare.actual_amount must be non-negative");
      if (!isNonEmptyString(fare.currency)) errors.push("trip.fare.currency is required");
    }
    if (input.trip.events !== undefined && !Array.isArray(input.trip.events)) {
      errors.push("trip.events must be an array");
    }
  }
  if (!Array.isArray(input.evidence)) {
    errors.push("evidence must be an array");
  } else {
    input.evidence.forEach((item, index) => {
      if (!item || typeof item !== "object") {
        errors.push(`evidence[${index}] must be an object`);
        return;
      }
      if (!isNonEmptyString(item.id)) errors.push(`evidence[${index}].id is required`);
      if (!isNonEmptyString(item.type)) errors.push(`evidence[${index}].type is required`);
      if (!isNonEmptyString(item.source)) errors.push(`evidence[${index}].source is required`);
      if (typeof item.valid !== "boolean") errors.push(`evidence[${index}].valid must be a boolean`);
      if (item.contradicts !== undefined && !Array.isArray(item.contradicts)) {
        errors.push(`evidence[${index}].contradicts must be an array`);
      }
    });
  }
  if (errors.length > 0) throw new ValidationError("Invalid route deviation dispute", errors);
  return input;
}

export function validateAdvocateCase(advocateCase, expectedActor) {
  const errors = [];
  if (!advocateCase || typeof advocateCase !== "object") errors.push("advocate case must be an object");
  if (advocateCase?.actor !== expectedActor) errors.push(`advocate actor must be ${expectedActor}`);
  if (advocateCase?.output_schema !== "advocate_case_v1") errors.push("unsupported advocate output_schema");
  if (!isNonEmptyString(advocateCase?.summary)) errors.push("advocate summary is required");
  if (!Array.isArray(advocateCase?.claims)) errors.push("advocate claims must be an array");
  if (Array.isArray(advocateCase?.claims)) {
    const ids = new Set();
    for (const claim of advocateCase.claims) {
      if (claim?.actor !== expectedActor) errors.push(`claim ${claim?.id ?? "unknown"} has the wrong actor`);
      if (ids.has(claim?.id)) errors.push(`duplicate claim ID: ${claim.id}`);
      ids.add(claim?.id);
    }
  }
  if (errors.length > 0) throw new ValidationError("Invalid advocate case", errors);
  return advocateCase;
}

export function validateClaimSchema(claim) {
  const errors = [];
  if (!claim || typeof claim !== "object") errors.push("claim must be an object");
  if (!isNonEmptyString(claim?.id)) errors.push("claim.id is required");
  if (!["rider", "driver"].includes(claim?.actor)) errors.push("claim.actor must be rider or driver");
  if (!["supports", "opposes", "neutral"].includes(claim?.position)) {
    errors.push("claim.position must be supports, opposes, or neutral");
  }
  if (!isNonEmptyString(claim?.claim)) errors.push("claim.claim is required");
  if (!Array.isArray(claim?.evidence_ids) || claim.evidence_ids.length === 0) {
    errors.push("claim.evidence_ids must contain at least one evidence ID");
  }
  if (!Array.isArray(claim?.policy_refs) || claim.policy_refs.length === 0) {
    errors.push("claim.policy_refs must contain at least one policy reference");
  }
  if (errors.length > 0) throw new ValidationError("Invalid advocate claim", errors);
  return claim;
}

export function assertResolutionShape(resolution) {
  const required = [
    "case_id",
    "dispute_type",
    "timeline",
    "rider_case",
    "driver_case",
    "verified_claims",
    "rejected_claims",
    "policy_result",
    "ruling",
    "recommended_action",
    "refund_amount",
    "confidence",
    "resolution_mode",
    "explanation",
    "counterfactual_explanation"
  ];
  const missing = required.filter((key) => !(key in resolution));
  if (missing.length > 0) throw new ValidationError("Resolution is missing required fields", missing);
  if (!["AUTO_RESOLVE", "HUMAN_REVIEW"].includes(resolution.resolution_mode)) {
    throw new ValidationError("Invalid resolution_mode");
  }
  if (!Array.isArray(resolution.timeline)) throw new ValidationError("timeline must be an array");
  if (!Array.isArray(resolution.verified_claims)) throw new ValidationError("verified_claims must be an array");
  if (!Array.isArray(resolution.rejected_claims)) throw new ValidationError("rejected_claims must be an array");
  if (!Number.isFinite(resolution.refund_amount) || resolution.refund_amount < 0) {
    throw new ValidationError("refund_amount must be a non-negative number");
  }
  if (!Number.isFinite(resolution.confidence) || resolution.confidence < 0 || resolution.confidence > 1) {
    throw new ValidationError("confidence must be between 0 and 1");
  }
  if (resolution.resolution_mode === "HUMAN_REVIEW"
    && (resolution.ruling !== "PENDING_HUMAN_REVIEW" || resolution.refund_amount !== 0)) {
    throw new ValidationError("Human-review resolutions cannot publish an executable ruling or refund");
  }
  return resolution;
}
