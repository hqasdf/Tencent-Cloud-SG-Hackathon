import { calculateRouteMetrics } from "./route-metrics.js";

export class CaseReplay {
  reconstruct(dispute) {
    const metrics = calculateRouteMetrics(dispute.trip);
    const timeline = [...(dispute.trip.events ?? [])]
      .map((event, index) => ({
        sequence: index + 1,
        timestamp: event.timestamp ?? null,
        type: event.type ?? "trip_event",
        description: event.description ?? "",
        evidence_ids: [...(event.evidence_ids ?? [])]
      }))
      .sort((a, b) => {
        if (!a.timestamp && !b.timestamp) return a.sequence - b.sequence;
        if (!a.timestamp) return 1;
        if (!b.timestamp) return -1;
        return new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime();
      })
      .map((event, index) => ({ ...event, sequence: index + 1 }));

    return {
      case_id: dispute.case_id,
      metrics,
      timeline,
      reconstructed_at: new Date().toISOString(),
      calculation_method: "deterministic_route_metrics_v1"
    };
  }
}
