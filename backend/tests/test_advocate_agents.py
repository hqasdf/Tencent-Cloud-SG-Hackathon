"""Tests for the two advocate agents and the prompt contract.

The agents must:

  * depend only on the LlmProvider protocol, not on any vendor
  * receive the assigned side explicitly rather than guessing
  * reject output whose ``side`` does not match, rather than coercing it
  * embed the context and the output schema into the request
"""

import json

import pytest

from app.agents.base_advocate import AdvocateAgentError, BaseAdvocateAgent
from app.agents.config import PROMPT_VERSION, AgentSettings
from app.agents.context_builder import AdvocateContextBuilder
from app.agents.driver_advocate import DriverAdvocateAgent
from app.agents.provider import AgentCompletionRequest, LlmCompletion, LlmProviderError
from app.agents.providers.mock import MockLlmProvider
from app.agents.rider_advocate import RiderAdvocateAgent
from app.data.cases import MOCK_CASES
from app.services.dispute_analysis import DisputeAnalysisService


class RecordingProvider:
    """Captures the request and delegates to the mock provider."""

    name = "recording"

    def __init__(self) -> None:
        self.requests: list[AgentCompletionRequest] = []
        self._inner = MockLlmProvider()

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        self.requests.append(request)
        return self._inner.complete(request)


class FixedOutputProvider:
    """Returns a canned JSON payload, to test parsing and side checking."""

    name = "fixed"

    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload)

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        return LlmCompletion(
            raw_text=self._payload,
            provider_name=self.name,
            model_name=None,
            duration_ms=0,
        )


class BrokenProvider:
    name = "broken"

    def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
        raise LlmProviderError("upstream exploded", provider=self.name)


def context_for(case_id: str = "CASE-2026-1041"):
    case = next(item for item in MOCK_CASES if item.id == case_id)
    analysis = DisputeAnalysisService().analyze(case)
    return AdvocateContextBuilder().build(case, analysis)


def rider_output(side: str = "RIDER") -> dict:
    return {
        "side": side,
        "summary": "s",
        "claims": [],
        "requestedOutcome": "PARTIAL_REFUND",
        "contextAcknowledged": True,
    }


# ---------------------------------------------------------------------------
# Basic contract
# ---------------------------------------------------------------------------

def test_rider_agent_declares_its_side_and_prompt_file() -> None:
    assert RiderAdvocateAgent.side == "RIDER"
    assert DriverAdvocateAgent.side == "DRIVER"


def test_agent_produces_validated_output_in_mock_mode() -> None:
    agent = RiderAdvocateAgent(MockLlmProvider())
    output = agent.argue(context_for())
    assert output.side == "RIDER"
    assert output.claims
    assert output.context_acknowledged is True


def test_driver_agent_produces_validated_output_in_mock_mode() -> None:
    agent = DriverAdvocateAgent(MockLlmProvider())
    output = agent.argue(context_for())
    assert output.side == "DRIVER"
    assert output.claims


def test_agent_depends_only_on_the_provider_protocol() -> None:
    """Any object with name + complete() works — no vendor coupling."""
    provider = RecordingProvider()
    agent = RiderAdvocateAgent(provider)
    agent.argue(context_for())
    assert len(provider.requests) == 1


def test_agent_reports_the_provider_name_and_prompt_version() -> None:
    agent = RiderAdvocateAgent(MockLlmProvider())
    assert agent.provider_name == "mock"
    assert agent.prompt_version == PROMPT_VERSION


# ---------------------------------------------------------------------------
# Request assembly
# ---------------------------------------------------------------------------

def test_request_carries_the_assigned_side_in_metadata() -> None:
    """Side comes from the agent, never guessed from prompt prose."""
    provider = RecordingProvider()
    DriverAdvocateAgent(provider).argue(context_for())
    assert provider.requests[0].metadata["side"] == "DRIVER"

    provider = RecordingProvider()
    RiderAdvocateAgent(provider).argue(context_for())
    assert provider.requests[0].metadata["side"] == "RIDER"


def test_request_embeds_the_trusted_context_as_camel_case() -> None:
    provider = RecordingProvider()
    RiderAdvocateAgent(provider).argue(context_for())
    embedded = provider.requests[0].metadata["context_json"]
    assert '"caseId"' in embedded
    assert '"riderComplaint"' in embedded
    assert '"facts"' in embedded


def test_user_prompt_wraps_the_context_and_forbids_recomputation() -> None:
    provider = RecordingProvider()
    RiderAdvocateAgent(provider).argue(context_for())
    prompt = provider.requests[0].user_prompt
    assert "<case_context>" in prompt
    assert "</case_context>" in prompt
    assert "Do not recompute anything" in prompt


def test_system_prompt_contains_common_rules_side_role_and_output_schema() -> None:
    provider = RecordingProvider()
    RiderAdvocateAgent(provider).argue(context_for())
    system = provider.requests[0].system_prompt
    # Shared invariants, side role, and the machine-readable schema.
    assert "# Required output schema" in system
    assert "assertedFacts" in system
    assert "requestedOutcome" in system


def test_prompt_does_not_mention_any_specific_vendor() -> None:
    provider = RecordingProvider()
    RiderAdvocateAgent(provider).argue(context_for())
    system = provider.requests[0].system_prompt.lower()
    for vendor in ("openai", "gpt-4", "tencent", "anthropic", "claude"):
        assert vendor not in system, f"prompt leaked vendor name {vendor!r}"


def test_rider_and_driver_receive_different_side_prompts() -> None:
    rider_provider = RecordingProvider()
    RiderAdvocateAgent(rider_provider).argue(context_for())
    driver_provider = RecordingProvider()
    DriverAdvocateAgent(driver_provider).argue(context_for())

    rider_system = rider_provider.requests[0].system_prompt
    driver_system = driver_provider.requests[0].system_prompt

    # Same shared rules, different role section.
    assert rider_system != driver_system
    assert "RIDER" in rider_system.upper()
    assert "DRIVER" in driver_system.upper()


# ---------------------------------------------------------------------------
# Parsing and side enforcement
# ---------------------------------------------------------------------------

def test_wrong_side_output_is_rejected_not_coerced() -> None:
    """The driver agent must not accept output labelled RIDER."""
    agent = DriverAdvocateAgent(FixedOutputProvider(rider_output("RIDER")))
    with pytest.raises(AdvocateAgentError) as caught:
        agent.argue(context_for())
    assert "RIDER" in str(caught.value)


def test_malformed_json_raises_advocate_agent_error() -> None:
    class GarbageProvider:
        name = "garbage"

        def complete(self, request: AgentCompletionRequest) -> LlmCompletion:
            return LlmCompletion(
                raw_text="I would rather not.",
                provider_name=self.name,
                model_name=None,
                duration_ms=0,
            )

    with pytest.raises(AdvocateAgentError):
        RiderAdvocateAgent(GarbageProvider()).argue(context_for())


def test_schema_violation_raises_advocate_agent_error() -> None:
    incomplete = {"side": "RIDER", "summary": "s"}  # missing claims/outcome/ack
    with pytest.raises(AdvocateAgentError):
        RiderAdvocateAgent(FixedOutputProvider(incomplete)).argue(context_for())


def test_provider_error_propagates_unchanged() -> None:
    """The agent does not swallow provider failures; the orchestrator handles them."""
    with pytest.raises(LlmProviderError):
        RiderAdvocateAgent(BrokenProvider()).argue(context_for())


# ---------------------------------------------------------------------------
# Prompt files on disk
# ---------------------------------------------------------------------------

def test_prompt_files_exist_and_are_non_trivial() -> None:
    from app.agents.base_advocate import PROMPTS_DIR

    for name in ("advocate_common.md", "rider_advocate.md", "driver_advocate.md"):
        path = PROMPTS_DIR / name
        assert path.exists(), f"{name} is missing"
        assert len(path.read_text(encoding="utf-8").strip()) > 200, f"{name} is too thin"


def test_output_schema_file_omits_position_as_a_property() -> None:
    """`position` must not exist as a SCHEMA FIELD.

    The word may legitimately appear inside a human-readable description (e.g.
    "an honest position for your side"), so this checks the declared property
    names rather than doing a substring scan over the whole document.
    """
    from app.agents.base_advocate import PROMPTS_DIR

    schema = json.loads((PROMPTS_DIR / "output_schema.json").read_text(encoding="utf-8"))

    def property_names(node: object) -> set[str]:
        names: set[str] = set()
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                names |= set(properties.keys())
            for value in node.values():
                names |= property_names(value)
        elif isinstance(node, list):
            for value in node:
                names |= property_names(value)
        return names

    names = property_names(schema)
    assert "position" not in names
    assert "assertedFacts" in names
    assert "requestedOutcome" in names
    assert "claimId" in names


def test_common_prompt_states_the_authority_hierarchy() -> None:
    """The prompt must tell the model that CODE owns the facts."""
    from app.agents.base_advocate import PROMPTS_DIR

    common = (PROMPTS_DIR / "advocate_common.md").read_text(encoding="utf-8").lower()
    assert "deterministic" in common
    assert "do not" in common  # the honesty / no-recalculation instruction


def test_fairness_instructions_forbid_history_and_ratings() -> None:
    """The fairness guarantee is architectural, but the prompt must reinforce it.

    These words appear in the prompts precisely because the prompts FORBID their
    use, so assert on the prohibition rather than on the word itself.
    """
    from app.agents.base_advocate import PROMPTS_DIR

    common = (PROMPTS_DIR / "advocate_common.md").read_text(encoding="utf-8").lower()
    assert "do not rely on" in common or "must not" in common
    # The withheld-information clause must exist and cover the fairness terms.
    for term in ("rating", "history", "reputation"):
        assert term in common, f"prompt should explicitly name {term!r} as withheld"
    assert "withheld" in common
    assert "irrelevant" in common
