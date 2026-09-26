"""Stage 4 advocate agents.

Architecture rule for this package:

    CODE calculates facts.  AI argues from facts.
    CODE checks AI claims.  Judge later decides using only verified information.

Agents in this package may only reason from a trusted AgentCaseContext. They
never recompute, mutate, or contradict deterministic values.
"""
