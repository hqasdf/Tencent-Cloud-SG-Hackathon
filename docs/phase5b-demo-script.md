# Three-minute walkthrough script

This is a short pitch/rehearsal, not the handbook's optional 5-8 minute submission video. Use synthetic data; keep MOCK_DEMO visible. No live Gemini actions during this pass.

**0:00-0:20 - Problem / track**
"RydeResolve addresses the Digital Native - Ryde multi-agent dispute resolution challenge. Riders and drivers can describe the same journey differently. Support needs a decision grounded in evidence, not whichever explanation sounds more persuasive."

**0:20-0:40 - Architecture**
Show the architecture diagram. "Code calculates facts. AI argues from verified facts. Code checks AI claims and controls the final resolution. We represent both parties rather than using a single opaque model decision."

**0:40-1:10 - Intake and evidence**
Open DISP-005. Point to Rider/Driver intake and evidence/timeline. "The product supports separate party interviews and attributed statements. This offline demonstration has empty/partial intake; it is not a completed live interview. Structured route conditions explain the deviation. Evidence validation flags missing or contradictory information."

**1:10-1:55 - Agent flow**
Click Show saved demo (replay). "The MOCK_DEMO label means these are simulated saved outputs, revalidated now without calling Gemini. Rider and Driver Advocates argue separately. Our verifier accepts or rejects each claim; rejected claims remain visible. Both Rebuttals and the advisory Judge operate inside the checked context. Saved model text cannot authorize a different refund."

**1:55-2:20 - Final decision / second category**
Point to NO_REFUND, SGD 0.00. "Code applies the prototype policy, not the language model. The verified closure explains this deviation." Open DISP-002. "The same design handles no-show cancellation through measured arrival and waiting thresholds. Here the charge is upheld." Show its compatible replay only if configured and manually verified; otherwise label this a deterministic second-category view.

**2:20-2:40 - Escalation**
Open DISP-003. "Contradictory route evidence produces HUMAN_REVIEW. We refuse an unsafe automatic resolution and surface the reasons. Responsible automation includes knowing when not to decide."

**2:40-3:00 - Innovation / honest close**
"RydeResolve combines party-specific advocacy, deterministic claim verification, bounded rebuttals and resumable/revalidated AI work. We have 887 backend and 17 frontend passing tests and validated container builds. These are prototype engineering results, not proven business savings. Our next step is real policy calibration and a controlled support-team evaluation."

For an optional 5-8 minute video, add a second full-category replay, a longer verified evidence/claim example, limitations, and an authentic CodeBuddy/WorkBuddy development-process reflection with sanitized screenshots. Do not invent tool usage or live interview results.
