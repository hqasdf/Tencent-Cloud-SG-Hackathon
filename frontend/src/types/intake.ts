export type Party = "rider" | "driver";
export type Sender = "user" | "assistant";
export type IntakeLifecycle =
  | "RIDER_INTERVIEW"
  | "DRIVER_INTERVIEW"
  | "READY_FOR_ANALYSIS"
  | "ANALYSING"
  | "AUTO_RESOLVED"
  | "HUMAN_REVIEW";
export type SuggestedDisputeType = "route_deviation" | "no_show_charge" | "unknown";

export interface IntakeMessage {
  id: string;
  intakeCaseId: string;
  party: Party;
  sender: Sender;
  content: string;
  timestamp: string;
}

export interface PartyFact {
  key: string;
  value: string;
  statedBy: Party;
}

export interface InterviewState {
  intakeCaseId: string;
  party: Party;
  facts: PartyFact[];
  missingDetails: string[];
  suggestedDisputeType: SuggestedDisputeType;
  interviewComplete: boolean;
  updatedAt: string;
}

export interface IntakeCase {
  id: string;
  sourceCaseId: string;
  tripId: string;
  lifecycle: IntakeLifecycle;
  detectedDisputeType: SuggestedDisputeType;
  createdAt: string;
  updatedAt: string;
  finalAnalysis: Record<string, unknown> | null;
  messages: IntakeMessage[];
  riderState: InterviewState | null;
  driverState: InterviewState | null;
}

export interface CreateIntakeCaseRequest {
  tripId: string;
}

export interface SendMessageRequest {
  party: Party;
  content: string;
}

export interface SendMessageResponse {
  case: IntakeCase;
  assistantMessage: IntakeMessage;
}
