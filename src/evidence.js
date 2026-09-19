import { ValidationError, validateClaimSchema } from "./contracts.js";

export function buildEvidenceCatalog(evidence) {
  const catalog = new Map();
  for (const item of evidence) {
    if (catalog.has(item.id)) {
      throw new ValidationError(`Duplicate evidence ID: ${item.id}`);
    }
    catalog.set(item.id, Object.freeze({ valid: true, ...item }));
  }
  return catalog;
}

export function findEvidenceByType(catalog, type) {
  return [...catalog.values()].find((item) => item.type === type && item.valid !== false);
}

export function detectContradictions(catalog) {
  const contradictions = [];
  const seen = new Set();
  for (const item of catalog.values()) {
    for (const otherId of item.contradicts ?? []) {
      if (!catalog.has(otherId)) continue;
      const pair = [item.id, otherId].sort();
      const key = pair.join("::");
      if (!seen.has(key)) {
        seen.add(key);
        contradictions.push({ evidence_ids: pair, reason: "DECLARED_CONTRADICTION" });
      }
    }
  }
  return contradictions;
}

export function verifyClaims(claims, catalog, validPolicyRefs) {
  const verified_claims = [];
  const rejected_claims = [];

  for (const rawClaim of claims) {
    let claim;
    try {
      claim = validateClaimSchema(rawClaim);
    } catch (error) {
      rejected_claims.push({
        claim: rawClaim,
        reason: "INVALID_CLAIM_SCHEMA",
        details: error.details ?? [error.message]
      });
      continue;
    }

    const missingEvidenceIds = claim.evidence_ids.filter((id) => !catalog.has(id));
    const invalidEvidenceIds = claim.evidence_ids.filter((id) => catalog.get(id)?.valid === false);
    const invalidPolicyRefs = claim.policy_refs.filter((id) => !validPolicyRefs.has(id));

    if (missingEvidenceIds.length > 0 || invalidEvidenceIds.length > 0 || invalidPolicyRefs.length > 0) {
      rejected_claims.push({
        claim,
        reason: missingEvidenceIds.length > 0
          ? "INVALID_EVIDENCE_ID"
          : invalidEvidenceIds.length > 0
            ? "INVALID_EVIDENCE"
            : "INVALID_POLICY_REF",
        missing_evidence_ids: missingEvidenceIds,
        invalid_evidence_ids: invalidEvidenceIds,
        invalid_policy_refs: invalidPolicyRefs
      });
      continue;
    }

    verified_claims.push({ ...claim, verified: true });
  }

  return { verified_claims, rejected_claims };
}
