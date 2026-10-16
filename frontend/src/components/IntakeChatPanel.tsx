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
    <div className={`flex flex-col ${isUser ? "items-end" : "items-start"} mb-3`}>
      <div className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-sm ${isUser ? "bg-[#f03b65] text-white" : "bg-white text-[#131b2b] border border-gray-100"}`}>
        <p>{msg.content}</p>
      </div>
      {time && <time className="text-[10px] text-gray-400 mt-1 px-1">{time}</time>}
    </div>
  );
}

function PartyAvatar({ party }: { party: Party }) {
  const initial = party === "rider" ? "M" : "L";
  return (
    <span className={`w-8 h-8 rounded-full flex items-center justify-center text-white text-sm font-bold ${
      party === "rider" ? "bg-[#f03b65]" : "bg-slate-700"
    }`}>
      {initial}
    </span>
  );
}

interface PhoneProps {
  party: Party;
  displayName: string;
  intakeCase: IntakeCase;
  onSend: (content: string) => Promise<boolean>;
  loading: boolean;
  disabled: boolean;
}

function PhoneMockup({ party, displayName, intakeCase, onSend, loading, disabled }: PhoneProps) {
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

  const label = party === "rider" ? "RIDER" : "DRIVER";
  const accent = party === "rider" ? "text-[#f03b65]" : "text-slate-700";
  const accentBg = party === "rider" ? "bg-[#f03b65]" : "bg-slate-700";
  const allowed = (party === "rider" && (
    intakeCase.lifecycle === "RIDER_INTERVIEW" || intakeCase.lifecycle === "RIDER_REBUTTAL"
  )) || (party === "driver" && intakeCase.lifecycle === "DRIVER_INTERVIEW");
  const isDisabled = loading || disabled || !allowed;

  return (
    <div className="relative mx-auto w-full max-w-[280px] aspect-[9/19] rounded-[2.25rem] border-[8px] border-slate-800 bg-slate-800 shadow-[0_25px_50px_-12px_rgba(19,27,43,0.45)] overflow-hidden">
      {/* side button */}
      <span className="absolute left-[-10px] top-24 w-[2px] h-10 bg-slate-700 rounded-l-sm" />
      <span className="absolute right-[-10px] top-20 w-[2px] h-14 bg-slate-700 rounded-r-sm" />
      <span className="absolute right-[-10px] top-36 w-[2px] h-8 bg-slate-700 rounded-r-sm" />

      <div className="relative w-full h-full rounded-[1.5rem] overflow-hidden bg-white flex flex-col">
        {/* status bar + notch */}
        <div className="relative h-7 bg-white flex items-center justify-between px-5 text-[10px] font-semibold text-slate-800 shrink-0">
          <span>9:41</span>
          <span className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-20 h-4 bg-slate-800 rounded-full" />
          <span className="flex items-center gap-1">
            <svg viewBox="0 0 16 12" className="w-3 h-2" fill="currentColor"><path d="M1 9h2v2H1zM5 7h2v4H5zM9 5h2v6H9zM13 3h2v8h-2z"/></svg>
            <svg viewBox="0 0 16 12" className="w-3" fill="currentColor"><path d="M8 2.5C5 2.5 2.4 3.6.3 5.7l1.4 1.4C3.4 5.4 5.6 4.5 8 4.5s4.6.9 6.3 2.6l1.4-1.4C13.6 3.6 11 2.5 8 2.5zm0 3C6 5.5 4.2 6.3 2.7 7.7l1.4 1.4C5.2 8.1 6.5 7.5 8 7.5s2.8.6 3.9 1.6l1.4-1.4C11.8 6.3 10 5.5 8 5.5zm0 3c-1 0-2 .4-2.7 1.1L8 12.2l2.7-2.6C10 8.9 9 8.5 8 8.5z"/></svg>
            <span className="ml-0.5 inline-block w-5 h-2.5 border border-slate-800 rounded-sm relative"><span className="absolute inset-0.5 bg-slate-800 rounded-sm" /></span>
          </span>
        </div>

        {/* chat header */}
        <div className="flex items-center gap-2.5 px-3 py-2.5 border-b border-gray-100 shrink-0">
          <span className="w-7 h-7 rounded-full bg-gray-100 flex items-center justify-center text-gray-500">
            <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4"><path d="M15 18l-6-6 6-6" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round"/></svg>
          </span>
          <PartyAvatar party={party} />
          <span className="text-xs leading-tight">
            <b className={`font-bold block ${accent}`}>{label}</b>
            <span className="text-gray-500">{displayName}</span>
          </span>
          {state?.interviewComplete && (
            <span className="ml-auto text-[9px] font-bold uppercase tracking-wider text-green-600">✓</span>
          )}
        </div>

        {/* messages area */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-4 flex flex-col items-center justify-center bg-white">
          {messages.length === 0 ? (
            <div className="flex flex-col items-center text-center text-gray-400 px-2">
              <svg className="w-10 h-10 text-gray-300 mb-2" viewBox="0 0 64 64" fill="none">
                <path d="M12 12h40v32H28l-8 8V44H12V12z" stroke="currentColor" strokeWidth={2.5} fill="none" strokeLinejoin="round" />
                <circle cx={32} cy={28} r={2.5} fill="currentColor" />
                <circle cx={22} cy={28} r={2.5} fill="currentColor" />
                <circle cx={42} cy={28} r={2.5} fill="currentColor" />
              </svg>
              <p className="text-[11px] leading-snug">No messages yet.<br/>Start the {party} interview.</p>
            </div>
          ) : (
            <div className="w-full">{messages.map((m) => <ChatBubble key={m.id} msg={m} />)}</div>
          )}
          {loading && <div className="text-gray-400 text-xs self-start mt-2">… typing</div>}
        </div>

        {/* input row */}
        <div className="flex items-center gap-1.5 px-2.5 py-2.5 border-t border-gray-100 bg-white shrink-0">
          <input
            aria-label={`${label} statement`}
            type="text"
            value={input}
            placeholder={`Message as ${party}…`}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
            disabled={isDisabled}
            className="flex-1 min-w-0 rounded-full border border-gray-200 px-3 py-1.5 text-xs focus:outline-none focus:border-[#f03b65] disabled:bg-gray-50 disabled:text-gray-400"
          />
          <button
            onClick={submit}
            disabled={isDisabled || !input.trim()}
            className={`${accentBg} rounded-full w-8 h-8 flex items-center justify-center text-white disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors shrink-0`}
            aria-label="Send"
          >
            <svg viewBox="0 0 24 24" fill="none" className="w-4 h-4"><path d="M5 12l14-7-7 14-2-5-5-2z" stroke="currentColor" strokeWidth={2} strokeLinejoin="round"/></svg>
          </button>
        </div>
      </div>
    </div>
  );
}

interface IntakeChatPanelProps {
  sourceCaseId: string;
  onLifecycleChange?: (intakeCase: IntakeCase) => void;
  riderName?: string;
  driverName?: string;
}

/**
 * Embeddable intake chat panel for the case workspace.
 *
 * Renders two phone mockups (rider + driver) that show the ongoing intake
 * interview, and wires sending messages, auto-starting the driver interview,
 * the rebuttal phase, and skipping the rebuttal.
 */
export function IntakeChatPanel({ sourceCaseId, onLifecycleChange, riderName = "Michael Wong", driverName = "Lim Wei Ming" }: IntakeChatPanelProps) {
  const [intakeCase, setIntakeCase] = useState<IntakeCase | null>(null);
  const [loadingCase, setLoadingCase] = useState(true);
  const [sending, setSending] = useState<"rider" | "driver" | null>(null);
  const [skipping, setSkipping] = useState(false);
  const requestActive = useRef(false);
  const [error, setError] = useState<string | null>(null);

  const loadOrCreate = useCallback(async () => {
    setLoadingCase(true);
    setError(null);
    try {
      let ic: IntakeCase;
      try {
        ic = await intakeService.getBySource(sourceCaseId);
      } catch (e) {
        if (e instanceof IntakeApiError && e.status === 404) {
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
    return <div className="flex items-center justify-center h-40 text-gray-500 text-sm">Loading AI intake…</div>;
  }

  if (error && !intakeCase) {
    return (
      <div className="rounded-xl bg-red-50 border-l-4 border-red-400 px-4 py-3">
        <p className="text-sm text-red-600">{error}</p>
        <button onClick={() => void loadOrCreate()} className="mt-2 rounded-full bg-[#f03b65] px-4 py-1.5 text-xs font-semibold text-white">Try again</button>
      </div>
    );
  }

  if (!intakeCase) {
    return (
      <div className="rounded-xl border border-dashed border-gray-200 px-4 py-6 text-center text-sm text-gray-500">
        No intake case available. Click "Start AI intake" to begin.
      </div>
    );
  }

  const inRebuttal = intakeCase.lifecycle === "RIDER_REBUTTAL";
  const intakeDone = intakeCase.lifecycle === "READY_FOR_ANALYSIS" ||
    intakeCase.lifecycle === "AUTO_RESOLVED" ||
    intakeCase.lifecycle === "HUMAN_REVIEW";

  return (
    <div className="space-y-3">
      {inRebuttal && (
        <div className="flex items-start gap-3 rounded-lg bg-amber-50 border border-amber-200 px-4 py-3">
          <span className="text-amber-600 flex-shrink-0">🔔</span>
          <div className="flex-1">
            <h3 className="font-bold text-sm text-amber-900">The driver has responded</h3>
            <p className="text-xs text-amber-800">Add a rider clarification, or continue without one.</p>
            <button className="mt-2 rounded-full bg-amber-600 hover:bg-amber-700 px-4 py-1.5 text-xs font-semibold text-white" onClick={handleSkipRebuttal} disabled={skipping || sending !== null}>
              {skipping ? "Skipping…" : "Skip clarification"}
            </button>
          </div>
        </div>
      )}

      <details open={!intakeDone}>
        <summary className="cursor-pointer text-sm font-semibold text-[#131b2b] py-2 list-none flex items-center gap-1.5">
          <span className="text-[#f03b65]">▼</span> Continue interview
        </summary>
        <div className="grid grid-cols-2 gap-8 mt-6 items-start">
          <PhoneMockup
            party="rider"
            displayName={riderName}
            intakeCase={intakeCase}
            onSend={(c) => handleSend("rider", c)}
            loading={sending === "rider"}
            disabled={sending !== null || skipping}
          />
          <PhoneMockup
            party="driver"
            displayName={driverName}
            intakeCase={intakeCase}
            onSend={(c) => handleSend("driver", c)}
            loading={sending === "driver"}
            disabled={sending !== null || skipping}
          />
        </div>
        {error && <p className="text-xs text-red-600 mt-2">{error}</p>}
      </details>
    </div>
  );
}
