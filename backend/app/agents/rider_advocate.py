from __future__ import annotations

from app.agents.base_advocate import BaseAdvocateAgent


class RiderAdvocateAgent(BaseAdvocateAgent):
    """Builds the strongest RIDER argument supported by trusted evidence.

    May: identify rider-supporting evidence, explain trusted facts, cite
    PolicyTwin rules, request an outcome.

    May not: fabricate evidence or policy, recalculate or change deterministic
    facts, calculate refunds, use history, access other cases, or issue a
    final ruling.
    """

    side = "RIDER"
    prompt_file = "rider_advocate.md"
