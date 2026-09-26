import { useCallback, useEffect, useRef, useState } from "react";
import type { CaseAnalysis } from "../types/dispute";
import type { IntakeCase, IntakeMessage, Party } from "../types/intake";
import { IntakeApiError, intakeService } from "../services/intakeService";
import { DeterministicAnalysisPanel } from "./DeterministicAnalysis";

const LIFECYCLE_LABELS: Record<string, string> = {
  RIDER_INTERVIEW: "Rider interview",
  DRIVER_INTERVIEW: "Driver interview",
  READY_FOR_ANALYSIS: "Ready for analysis",
  ANALYSING: "Analysing…",
  AUTO_RESOLVED: "Auto resolved",
  HUMAN_REVIEW: "Human review",
};

function ChatBubble({ msg }: { msg: IntakeMessage }) {
  const isUser = msg.sender === "user";
  const time = msg?.timestamp ? new Date(msg.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
  return (
    <div className={`chat-bubble ${isUser ? "chat-user" : "chat-assistant"}`}>
      <p>{msg.content}</p>
      {time && <time>{time}</time>}
    </div>
  );
}

interface PhoneProps {
  party: Party;
  caseData: IntakeCase | null;
  onSend: (content: string) => void;
  loading: boolean;
}

function PhoneMockup({ party, caseData, onSend, loading }: PhoneProps) {
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const messages = caseData?.messages?.filter((m) => m.party === party) ?? [];
  const state = party === "rider" ? caseData?.riderState : caseData?.driverState;

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length]);

  const submit = () => {
    const text = input.trim();
    if (!text || loading) return;
    onSend(text);
    setInput("");
  };

  const label = party === "rider" ? "Rider" : "Driver";

  return (
    <div className="phone-frame">
      <div className="phone-notch" />
      <div className="phone-screen">
        <header className="phone-header">
          <span className="phone-party-badge">{label}</span>
          {state?.interviewComplete && <span className="phone-complete">✓ Complete</span>}
        </header>
        <div className="phone-chat" ref={scrollRef}>
          {messages.length === 0 && (
            <p className="chat-empty">Create an intake case and send a message to start the {label.toLowerCase()} interview.</p>
          )}
          {messages.map((m) => <ChatBubble key={m.id} msg={m} />)}
          {loading && <div className="chat-typing">…</div>}
        </div>
        <footer className="phone-input-bar">
          <input
            type="text"
            value={input}
            placeholder={`Message as ${label.toLowerCase()}…`}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
            disabled={loading}
          />
          <button onClick={submit} disabled={loading || !input.trim()}>Send</button>
        </footer>
      </div>
    </div>
  );
}

interface FactListProps { facts: { key: string; value: string; statedBy: string }[]; missing: string[]; }

function FactList({ facts, missing }: FactListProps) {
  const safeFacts = facts ?? [];
  const safeMissing = missing ?? [];
  if (safeFacts.length === 0 && safeMissing.length === 0) return null;
  return (
    <div className="fact-strip">
      {safeFacts.length > 0 && (
        <div className="fact-group">
          <span className="eyebrow">Extracted facts</span>
          {safeFacts.map((f, i) => <div className="fact-row" key={i}><code>{f.key}</code><span>{f.value}</span></div>)}
        </div>
      )}
      {safeMissing.length > 0 && (
        <div className="fact-group">
          <span className="eyebrow">Missing details</span>
          <ul>{safeMissing.map((m, i) => <li key={i}>{m}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

export function IntakeWorkspace() {
  const [trips, setTrips] = useState<string[]>([]);
  const [selectedTrip, setSelectedTrip] = useState("");
  const [intakeCase, setIntakeCase] = useState<IntakeCase | null>(null);
  const [analysis, setAnalysis] = useState<CaseAnalysis | null>(null);
  const [loadingTrips, setLoadingTrips] = useState(true);
  const [sending, setSending] = useState<"rider" | "driver" | null>(null);
  const [analysing, setAnalysing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void intakeService.listTrips().then((items) => {
      setTrips(items);
      if (items.length) setSelectedTrip(items[0]);
    }).catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load trips."))
      .finally(() => setLoadingTrips(false));
  }, []);

  const handleCreate = useCallback(async () => {
    if (!selectedTrip) return;
    setError(null);
    setAnalysis(null);
    try {
      const c = await intakeService.createCase({ tripId: selectedTrip });
      setIntakeCase(c);
    } catch (e) {
      setError(e instanceof IntakeApiError ? e.message : "Failed to create intake case.");
    }
  }, [selectedTrip]);

  const handleSend = useCallback(async (party: Party, content: string) => {
    if (!intakeCase) return;
    setSending(party);
    setError(null);
    try {
      const resp = await intakeService.sendMessage(intakeCase.id, { party, content });
      setIntakeCase(resp.case);
    } catch (e) {
      // On 503 the user message was still saved — refresh the case
      if (e instanceof IntakeApiError && e.status === 503) {
        try {
          const refreshed = await intakeService.getCase(intakeCase.id);
          setIntakeCase(refreshed);
        } catch { /* ignore refresh failure */ }
      }
      setError(e instanceof IntakeApiError ? e.message : "Failed to send message.");
    } finally {
      setSending(null);
    }
  }, [intakeCase]);

  const handleAnalyse = useCallback(async () => {
    if (!intakeCase) return;
    setAnalysing(true);
    setError(null);
    try {
      const result = await intakeService.analyse(intakeCase.id);
      setAnalysis(result);
      const refreshed = await intakeService.getCase(intakeCase.id);
      setIntakeCase(refreshed);
    } catch (e) {
      setError(e instanceof IntakeApiError ? e.message : "Analysis failed.");
    } finally {
      setAnalysing(false);
    }
  }, [intakeCase]);

  const bothComplete = !!(intakeCase?.riderState?.interviewComplete && intakeCase?.driverState?.interviewComplete);
  const canAnalyse = bothComplete && intakeCase?.lifecycle !== "ANALYSING";

  return (
    <main className="content intake-content">
      <header className="case-header">
        <div>
          <div className="breadcrumb">Intake / AI-guided interview</div>
          <h1>Dispute Intake</h1>
          <p>Two-party LLM interview before deterministic analysis.</p>
        </div>
        {intakeCase && (
          <div className={`mode ${intakeCase.lifecycle === "AUTO_RESOLVED" ? "auto" : intakeCase.lifecycle === "HUMAN_REVIEW" ? "review" : ""}`}>
            {LIFECYCLE_LABELS[intakeCase.lifecycle] ?? intakeCase.lifecycle}
          </div>
        )}
      </header>

      <section className="panel intake-controls">
        <div className="intake-trip-select">
          <label htmlFor="trip-select" className="eyebrow">Select trip</label>
          <select id="trip-select" value={selectedTrip} onChange={(e) => setSelectedTrip(e.target.value)} disabled={loadingTrips}>
            {loadingTrips && <option>Loading…</option>}
            {trips.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <button className="intake-create-btn" onClick={handleCreate} disabled={!selectedTrip || sending !== null || analysing}>
          {intakeCase ? "Restart interview" : "Start interview"}
        </button>
        {intakeCase && (
          <button className="intake-analyse-btn" onClick={handleAnalyse} disabled={!canAnalyse || analysing}>
            {analysing ? "Analysing…" : "Run deterministic analysis"}
          </button>
        )}
      </section>

      {error && <div className="app-state intake-error"><p>{error}</p></div>}

      <section className="phone-grid">
        <div className="phone-column">
          <PhoneMockup party="rider" caseData={intakeCase} onSend={(c) => handleSend("rider", c)} loading={sending === "rider"} />
          {intakeCase?.riderState && (
            <FactList facts={intakeCase.riderState.facts ?? []} missing={intakeCase.riderState.missingDetails ?? []} />
          )}
        </div>
        <div className="phone-column">
          <PhoneMockup party="driver" caseData={intakeCase} onSend={(c) => handleSend("driver", c)} loading={sending === "driver"} />
          {intakeCase?.driverState && (
            <FactList facts={intakeCase.driverState.facts ?? []} missing={intakeCase.driverState.missingDetails ?? []} />
          )}
        </div>
      </section>

      {analysis && <DeterministicAnalysisPanel value={analysis} />}

      {!intakeCase && !loadingTrips && (
        <div className="app-state"><h1>No intake case yet</h1><p>Select a trip and click "Start interview" to begin.</p></div>
      )}
    </main>
  );
}
