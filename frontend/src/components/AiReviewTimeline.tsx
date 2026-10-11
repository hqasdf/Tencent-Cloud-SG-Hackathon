import type { AdvocateSideResult } from "../types/advocate";
import type { ResolutionRunResult } from "../services/caseService";
import { humanize, labelize } from "../utils/format";

export function AiReviewTimeline({ rider, driver, resolution }: {
  rider: AdvocateSideResult; driver: AdvocateSideResult; resolution: ResolutionRunResult | null;
}) {
  const rows: { name: string; status: string; summary: string; counts: string; evidence: string[] }[] = [rider, driver].map(side => ({
    name: `${humanize(side.side)} Advocate`, status: side.status,
    summary: side.status === "FAILED" ? "Could not complete. Retry AI review to recover saved work." : side.summary,
    counts: `${side.verifiedClaims.length} accepted · ${side.rejectedClaims.length} rejected`,
    evidence: [...new Set(side.verifiedClaims.flatMap(claim => claim.evidenceIds))]
  }));
  for (const side of ["rider", "driver"] as const) {
    const result = resolution?.rebuttals[side];
    rows.push({ name: `${labelize(side)} Rebuttal`, status: result?.status ?? "NOT_RUN",
      summary: result?.overallSummary || "Waiting for verified advocate claims.",
      counts: result ? `${result.verifiedRebuttals.length} accepted · ${result.rejectedRebuttals.length} rejected` : "",
      evidence: [...new Set(result?.verifiedRebuttals.flatMap(item => item.evidenceIds) ?? [])] });
  }
  const judge = resolution?.judge;
  rows.push({ name: "AI Judge", status: judge?.status ?? "NOT_RUN",
    summary: judge?.status === "FAILED" ? "No validated recommendation. The code-calculated decision remains available."
      : judge?.recommendedOutcome ? `Advisory: ${humanize(judge.recommendedOutcome)}. ${judge.reasoningSummary}` : "Waiting for verified claims and responses.",
    counts: judge?.status === "PENDING_HUMAN_REVIEW" ? "Recommendation requires human review" : "Advisory only",
    evidence: judge?.evidenceIds ?? [] });
  return <ol className="ai-review-timeline">{rows.map(row => <li key={row.name}>
    <div className="panel-heading"><h3>{row.name}</h3><span className={`wstep-pill ${row.status === "FAILED" ? "failed" : row.status === "NOT_RUN" ? "waiting" : "done"}`}>{row.status === "PENDING_HUMAN_REVIEW" ? "Needs human review" : humanize(row.status)}</span></div>
    <p>{row.summary}</p><small>{row.counts}</small>
    {row.evidence.length > 0 && <details><summary>Supporting evidence</summary><span>{row.evidence.join(", ")}</span></details>}
  </li>)}</ol>;
}
