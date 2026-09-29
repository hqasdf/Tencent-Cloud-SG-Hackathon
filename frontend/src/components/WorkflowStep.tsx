import type { ReactNode } from "react";

/**
 * One numbered step in the case workflow.
 *
 * The case page used to be a single sixteen-section scroll: the answer sat at
 * the bottom, after the timeline, the evidence table and an audit log, and the
 * same numbers appeared in up to three places. This component replaces that
 * with an explicit sequence an operator walks from top to bottom.
 *
 * The design rules it encodes:
 *
 *  - The collapsed state must still be informative. A step that is closed shows
 *    its number, its title and one plain-language line, so an operator can scan
 *    the whole workflow without expanding anything.
 *  - State is never implied by colour alone. Every step carries a text pill
 *    ("Done", "Next", "Not run"), because colour is the first thing lost to
 *    colour-blindness, low-quality projectors and greyscale screenshots.
 *  - Navigation is always available. Back/Next live inside the open step rather
 *    than in a separate control, so the affordance is where the reader already
 *    is. There is no "continue" affordance on a waiting step, because there is
 *    nothing to continue to.
 */

export type StepState = "done" | "ready" | "waiting" | "blocked";

const STATE_LABEL: Record<StepState, string> = {
  done: "Done",
  ready: "Next",
  waiting: "Not run",
  blocked: "Blocked"
};

export interface WorkflowStepProps {
  /** 1-based position in the workflow. */
  index: number;
  total: number;
  title: string;
  /** One line, plain language, no internal stage names. */
  summary: string;
  state: StepState;
  /** Overrides the default pill text for this state. */
  stateLabel?: string;
  open: boolean;
  onToggle: () => void;
  /** Omitted on the first step. */
  onPrev?: () => void;
  /** Omitted on the last step. */
  onNext?: () => void;
  nextLabel?: string;
  children: ReactNode;
}

export function WorkflowStep({
  index,
  total,
  title,
  summary,
  state,
  stateLabel,
  open,
  onToggle,
  onPrev,
  onNext,
  nextLabel,
  children
}: WorkflowStepProps) {
  const bodyId = `workflow-step-body-${index}`;
  return (
    <section className={`wstep ${state} ${open ? "open" : ""}`} id={`workflow-step-${index}`}>
      <button
        type="button"
        className="wstep-head"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={bodyId}
      >
        <span className="wstep-num" aria-hidden="true">
          {state === "done" && !open ? "✓" : index}
        </span>
        <span className="wstep-title">
          <strong>{title}</strong>
          <small>{summary}</small>
        </span>
        <span className={`wstep-pill ${state}`}>{stateLabel ?? STATE_LABEL[state]}</span>
        <span className="wstep-chevron" aria-hidden="true" />
      </button>
      {open && (
        <div className="wstep-body" id={bodyId}>
          {children}
          <div className="wstep-foot">
            <button type="button" onClick={onPrev} disabled={!onPrev}>
              ← Back
            </button>
            <span className="wstep-progress">
              Step {index} of {total}
            </span>
            <button type="button" className="wstep-next" onClick={onNext} disabled={!onNext}>
              {nextLabel ?? "Next step"} →
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

/**
 * The at-a-glance rail above the steps.
 *
 * Rendered separately from the steps themselves so an operator can jump
 * straight to the decision without walking the earlier steps, and so the shape
 * of the whole workflow (eight steps, two of them done) is visible without
 * scrolling.
 */
export interface WorkflowRailProps {
  steps: { title: string; short: string }[];
  stateFor: (index: number) => StepState;
  current: number;
  onSelect: (index: number) => void;
}

export function WorkflowRail({ steps, stateFor, current, onSelect }: WorkflowRailProps) {
  return (
    <nav className="workflow-rail" aria-label="Case workflow steps">
      {steps.map((step, position) => {
        const index = position + 1;
        const state = stateFor(index);
        return (
          <button
            key={step.short}
            type="button"
            className={`rail-chip ${state} ${current === index ? "current" : ""}`}
            onClick={() => onSelect(index)}
            aria-current={current === index ? "step" : undefined}
            title={step.title}
          >
            <i aria-hidden="true">{state === "done" ? "✓" : index}</i>
            {step.short}
          </button>
        );
      })}
    </nav>
  );
}

/** Placeholder shown inside a step that cannot run yet. */
export function StepPending({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="wstep-empty">
      <b>{title}</b>
      <p>{children}</p>
    </div>
  );
}
