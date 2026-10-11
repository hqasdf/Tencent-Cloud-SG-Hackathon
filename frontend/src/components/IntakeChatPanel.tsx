import { useCallback, useEffect, useRef, useState } from "react";
import type { IntakeCase, IntakeMessage, Party } from "../types/intake";
import { IntakeApiError, intakeService } from "../services/intakeService";

const LIFECYCLE_LABELS: Record<string, string> = {
  RIDER_INTERVIEW: "Rider interview",
  DRIVER_INTERVIEW: "Driver interview",
  RIDER_REBUTTAL: "Rider clarification",
  READY_FOR_ANALYSIS: "Ready for analysis",
  ANALYSING: "Analysing…",
  AUTO_RESOLVED: "Accounts reviewed",
  HUMAN_REVIEW: "Needs human review",
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
  intakeCase: IntakeCase;
  onSend: (content: string) => Promise<boolean>;
  loading: boolean;
  disabled: boolean;
}

function PhoneMockup({ party, intakeCase, onSend, loading, disabled }: PhoneProps) {
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const messages = intakeCase.messages.filter((m) => m.party === party);
  const state = party === "rider" ? intakeCase.riderState : intakeCase.driverState;

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length]);

  const submit = async () => {
    const text = input.trim();
    if (!text || loading || disabled) return;
    if (await onSend(text)) setInput("");
  };

  const label = party === "rider" ? "Rider" : "Driver";
  const allowed = (party === "rider" && (
    intakeCase.lifecycle === "RIDER_INTERVIEW" || intakeCase.lifecycle === "RIDER_REBUTTAL"
  )) || (party === "driver" && intakeCase.lifecycle === "DRIVER_INTERVIEW");
  const isDisabled = loading || disabled || !allowed;

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
            <p className="chat-empty">No messages yet. Send the first message to start the {label.toLowerCase()} interview.</p>
          )}
          {messages.map((m) => <ChatBubble key={m.id} msg={m} />)}
          {loading && <div className="chat-typing">…</div>}
        </div>
        <footer className="phone-input-bar">
          <input
            aria-label={`${label} statement`}
            type="text"
            value={input}
            placeholder={`Message as ${label.toLowerCase()}…`}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
            disabled={isDisabled}
          />
          <button onClick={submit} disabled={isDisabled || !input.trim()}>Send</button>
        </footer>
      </div>
    </div>
  );
}

interface IntakeChatPanelProps {
  sourceCaseId: string;
  onLifecycleChange?: (intakeCase: IntakeCase) => void;
}

/**
 * Embeddable intake chat panel for the case workspace.
 *
 * Fetches or creates an intake case for the given source case ID, renders
 * two phone mockups (rider + driver), and handles sending messages,
 * auto-starting the driver interview, the rebuttal phase, and skipping.
 */
export function IntakeChatPanel({ sourceCaseId, onLifecycleChange }: IntakeChatPanelProps) {
  const [intakeCase, setIntakeCase] = useState<IntakeCase | null>(null);
  const [loadingCase, setLoadingCase] = useState(true);
  const [sending, setSending] = useState<"rider" | "driver" | null>(null);
  const [skipping, setSkipping] = useState(false);
  const requestActive = useRef(false);
  const [error, setError] = useState<string | null>(null);

  // Load or create intake case when the source case changes
  const loadOrCreate = useCallback(async () => {
    setLoadingCase(true);
    setError(null);
    try {
      let ic: IntakeCase;
      try {
        ic = await intakeService.getBySource(sourceCaseId);
      } catch (e) {
        if (e instanceof IntakeApiError && e.status === 404) {
          // No intake case yet — create one
          ic = await intakeService.createBySource(sourceCaseId);
        } else {
          throw e;
        }
      }
      setIntakeCase(ic);
      onLifecycleChange?.(ic);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load intake case.");
    } finally {
      setLoadingCase(false);
    }
  }, [sourceCaseId, onLifecycleChange]);

  useEffect(() => {
    void loadOrCreate();
  }, [loadOrCreate]);

  const handleSend = useCallback(async (party: Party, content: string) => {
    if (!intakeCase || requestActive.current) return false;
    requestActive.current = true;
    setSending(party);
    setError(null);
    try {
      const resp = await intakeService.sendMessage(intakeCase.id, { party, content });
      setIntakeCase(resp.case);
      onLifecycleChange?.(resp.case);
      return true;
    } catch (e) {
      if (e instanceof IntakeApiError && e.status === 503) {
        try {
          const refreshed = await intakeService.getCase(intakeCase.id);
          setIntakeCase(refreshed);
          onLifecycleChange?.(refreshed);
        } catch { /* ignore */ }
      }
      setError(e instanceof Error ? e.message : "Failed to send message.");
      return false;
    } finally {
      setSending(null);
      requestActive.current = false;
    }
  }, [intakeCase, onLifecycleChange]);

  const handleSkipRebuttal = useCallback(async () => {
    if (!intakeCase || requestActive.current) return;
    requestActive.current = true;
    setSkipping(true);
    setError(null);
    try {
      const refreshed = await intakeService.skipRebuttal(intakeCase.id);
      setIntakeCase(refreshed);
      onLifecycleChange?.(refreshed);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to skip rebuttal.");
    } finally {
      setSkipping(false);
      requestActive.current = false;
    }
  }, [intakeCase, onLifecycleChange]);

  if (loadingCase) {
    return <div className="loading">Loading AI intake…</div>;
  }

  if (error && !intakeCase) {
    return (
      <div className="app-state intake-error">
        <p>{error}</p>
        <button onClick={() => void loadOrCreate()}>Try again</button>
      </div>
    );
  }

  if (!intakeCase) {
    return <StepPendingInline />;
  }

  const inRebuttal = intakeCase.lifecycle === "RIDER_REBUTTAL";
  const intakeDone = intakeCase.lifecycle === "READY_FOR_ANALYSIS" ||
    intakeCase.lifecycle === "AUTO_RESOLVED" ||
    intakeCase.lifecycle === "HUMAN_REVIEW";

  return (
    <div className="intake-chat-panel">
      <div className="intake-chat-status">
        <span className={`mode ${intakeDone ? "auto" : ""}`}>
          {LIFECYCLE_LABELS[intakeCase.lifecycle] ?? intakeCase.lifecycle}
        </span>
      </div>

      {error && <div className="app-state intake-error"><p>{error}</p></div>}

      {inRebuttal && (
        <div className="app-state intake-alert">
          <div className="alert-icon">🔔</div>
          <div>
            <h3>The driver has responded</h3>
            <p>Add a rider clarification, or continue without one.</p>
            <button className="intake-skip-btn" onClick={handleSkipRebuttal} disabled={skipping || sending !== null}>
              {skipping ? "Skipping…" : "Skip clarification"}
            </button>
          </div>
        </div>
      )}

      <details className="intake-transcript" open={!intakeDone}><summary>{intakeDone ? "Saved interviews" : "Continue interview"}</summary>
      <section className="phone-grid">
        <div className="phone-column">
          <PhoneMockup
            party="rider"
            intakeCase={intakeCase}
            onSend={(c) => handleSend("rider", c)}
            loading={sending === "rider"}
            disabled={sending !== null || skipping}
          />
        </div>
        <div className="phone-column">
          <PhoneMockup
            party="driver"
            intakeCase={intakeCase}
            onSend={(c) => handleSend("driver", c)}
            loading={sending === "driver"}
            disabled={sending !== null || skipping}
          />
        </div>
      </section>
      </details>
    </div>
  );
}

function StepPendingInline() {
  return (
    <div className="step-pending-inline">
      <p>No intake case available. Click "Start AI intake" to begin.</p>
    </div>
  );
}
