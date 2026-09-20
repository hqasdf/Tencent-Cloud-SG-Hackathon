from __future__ import annotations

from app.agents.base_advocate import BaseAdvocateAgent


class DriverAdvocateAgent(BaseAdvocateAgent):
    """Builds the strongest DRIVER argument supported by trusted evidence.

    Uses the identical schema and the identical restrictions as the Rider
    Advocate. The two agents reason independently: the Rider output is never
    passed to the Driver agent, and vice versa. Cross-examination is not part
    of Milestone 4.
    """

    side = "DRIVER"
    prompt_file = "driver_advocate.md"
