"""Benchmark result serialisation.

Two output artefacts are written per run: a machine-readable JSON file and a
human-readable Markdown summary. Both are produced from the same in-memory
result, so they cannot disagree.

Secret handling: the JSON is checked against the live credential values before
it is written, and the write is refused if any of them appear. This is a
belt-and-braces check rather than the primary defence — the metric records are
built from an allow-list and never carry a key — but it turns "we did not store
the key" from a claim into an enforced property.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable

from app.benchmarks.models import ModelAggregate
from app.benchmarks.runner import BenchmarkResult

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "benchmark_results"

# Strings this short are not treated as secrets: a one- or two-character key
# would match everywhere and produce a useless check.
_MIN_SECRET_LENGTH = 8


class BenchmarkSecretLeakError(RuntimeError):
    """Raised when a credential would have been written to a result file."""


def collect_secret_values(configs: Iterable[object]) -> list[str]:
    """Resolved credential values for the models in a run.

    Used only to verify that the output does not contain them.
    """
    secrets: list[str] = []
    for config in configs:
        resolver = getattr(config, "resolve_api_key", None)
        if callable(resolver):
            value = resolver()
            if isinstance(value, str) and len(value) >= _MIN_SECRET_LENGTH:
                secrets.append(value)
    return secrets


def find_secret_leaks(text: str, secrets: Iterable[str]) -> list[str]:
    """Return a redacted description of any credential found in ``text``.

    The credential itself is never included in the returned description.
    """
    found: list[str] = []
    for secret in secrets:
        if secret and len(secret) >= _MIN_SECRET_LENGTH and secret in text:
            found.append(f"<redacted credential of length {len(secret)}>")
    return found


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace any credential in ``text`` with a placeholder."""
    for secret in secrets:
        if secret and len(secret) >= _MIN_SECRET_LENGTH:
            text = text.replace(secret, "<REDACTED>")
    return text


def render_markdown(result: BenchmarkResult) -> str:
    """Human-readable summary of a run."""
    lines: list[str] = [
        "# Stage 4C — advocate model benchmark",
        "",
        f"- **Run id**: `{result.benchmark_run_id}`",
        f"- **Profile**: `{result.profile}`",
        f"- **Runs per case**: {result.runs}",
        f"- **Started**: {result.started_at}",
        f"- **Finished**: {result.finished_at}",
        "",
        "## Cases benchmarked",
        "",
    ]

    selections = result.plan.get("caseSelections", [])
    for selection in selections:  # type: ignore[union-attr]
        lines.append(
            f"- `{selection['disputeType']}` → **{', '.join(selection['selected'])}** "
            f"(candidates: {', '.join(selection['candidates']) or 'none'})"
        )
        lines.append(f"  - criterion: {selection['description']}")
        if selection.get("tieBreak"):
            lines.append(f"  - tie break: {selection['tieBreak']}")
    lines.append("")

    if not result.calls:
        lines.extend(
            [
                "## No calls were made",
                "",
                "Every selected model was skipped. This is not a result about model",
                "quality; see the skip reasons below.",
                "",
            ]
        )

    lines.extend(["## Per-model summary", ""])
    lines.append(
        "| Model | Calls | Completed | Failed | Skipped | Completion rate | "
        "Claims | Verified | Rejected | Verification rate |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for aggregate in result.aggregates:
        lines.append(
            f"| `{aggregate.model}` | {aggregate.total_calls} | {aggregate.completed_calls} | "
            f"{aggregate.failed_calls} | {_yes_no(aggregate.skipped)} | "
            f"{_percent(aggregate.completion_rate)} | {aggregate.total_claims} | "
            f"{aggregate.verified_claims} | {aggregate.rejected_claims} | "
            f"{_percent(aggregate.verification_rate)} |"
        )
    lines.append("")

    lines.extend(["## Failure separation", ""])
    lines.append(
        "Provider failures and model-output failures are counted separately. "
        "An outage is not a quality signal, and a rejected claim is not downtime."
    )
    lines.append("")
    lines.append("| Model | Provider failures | Model-output failures | Internal errors |")
    lines.append("|---|---|---|---|")
    for aggregate in result.aggregates:
        lines.append(
            f"| `{aggregate.model}` | {aggregate.provider_failure_count} | "
            f"{aggregate.model_output_failure_count} | {aggregate.internal_error_count} |"
        )
    lines.append("")

    _append_error_counts(lines, result.aggregates)

    lines.extend(["## Grounding and verification", ""])
    lines.append(
        "| Model | Invalid evidence refs | Invalid policy refs | Fact contradictions | "
        "Unknown facts | Schema failures | Malformed output | Zero-claim calls |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for aggregate in result.aggregates:
        lines.append(
            f"| `{aggregate.model}` | {aggregate.invalid_evidence_refs} | "
            f"{aggregate.invalid_policy_refs} | {aggregate.fact_contradictions} | "
            f"{aggregate.unknown_facts} | {aggregate.schema_failure_count} | "
            f"{aggregate.malformed_output_count} | {aggregate.calls_with_zero_claims} |"
        )
    lines.append("")

    lines.extend(["## Cost and latency", ""])
    lines.append(
        "Latency is reported separately for completed and failed calls. A request "
        "that fails in 900 ms is not evidence of a fast model."
    )
    lines.append("")
    lines.append(
        "| Model | Mean latency (completed) | n | Median | Mean latency (failed) | n | "
        "Input tokens | Output tokens | Total tokens | Unattributed tokens | "
        "Attempts | Retries | Estimated cost |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for aggregate in result.aggregates:
        completed = aggregate.completed_latency
        failed = aggregate.failed_latency
        lines.append(
            f"| `{aggregate.model}` | {_ms(completed.mean_ms)} | {completed.sample_size} | "
            f"{_ms(completed.median_ms)} | {_ms(failed.mean_ms)} | {failed.sample_size} | "
            f"{_num(aggregate.input_tokens)} | {_num(aggregate.output_tokens)} | "
            f"{_num(aggregate.total_tokens)} | {_num(aggregate.unattributed_tokens)} | "
            f"{aggregate.total_attempts} | {aggregate.total_retries} | "
            f"{_cost(aggregate.estimated_cost)} |"
        )
    lines.append("")

    lines.extend(["## Recommended outcomes", ""])
    lines.append(
        "Rider and Driver outcomes are recorded independently. Disagreement is not "
        "an error: the two advocates are supposed to argue their own side."
    )
    lines.append("")
    for aggregate in result.aggregates:
        outcomes = aggregate.recommended_outcomes
        rendered = ", ".join(f"`{key}` × {value}" for key, value in sorted(outcomes.items()))
        lines.append(f"- `{aggregate.model}`: {rendered or 'none'}")
    lines.append("")

    lines.extend(["## Per-call detail", ""])
    lines.append(
        "| Case | Side | Status | Failure code | Category | Latency ms | "
        "Attempts | Claims | Verified | Rejected | Outcome |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for call in result.calls:
        lines.append(
            f"| `{call.case_id}` | {call.side} | {call.status} | {call.failure_code or '—'} | "
            f"{call.failure_category} | {_num(call.latency_ms)} | "
            f"{_num(call.attempt_count)} | {call.generated_claim_count} | "
            f"{call.verified_claim_count} | {call.rejected_claim_count} | "
            f"{call.recommended_outcome or '—'} |"
        )
    lines.append("")

    lines.extend(
        [
            "## Limitations",
            "",
            "- No aggregate quality score is computed. Individual metrics are the "
            "result; collapsing them into one number would hide which axis moved.",
            "- No model is ranked. A ranking needs comparable runs of every model "
            "through the same code revision.",
            "- A skipped model is not a worse model. See skip reasons above.",
            "- Token totals are provider-reported. `unattributedTokens` is the "
            "provider's own total minus input and output; the provider does not say "
            "what those tokens were.",
            "",
        ]
    )
    return "\n".join(lines)


def _append_error_counts(lines: list[str], aggregates: list[ModelAggregate]) -> None:
    lines.extend(["### Error codes by category", ""])
    for aggregate in aggregates:
        lines.append(f"- `{aggregate.model}`")
        lines.append(f"  - provider: {_counts(aggregate.provider_error_counts)}")
        lines.append(f"  - model output: {_counts(aggregate.model_output_error_counts)}")
        lines.append(f"  - internal: {_counts(aggregate.internal_error_counts)}")
        if aggregate.skipped:
            lines.append(f"  - SKIPPED: {aggregate.skip_reason}")
    lines.append("")


def _counts(values: dict[str, int]) -> str:
    if not values:
        return "none"
    return ", ".join(f"`{key}` × {value}" for key, value in sorted(values.items()))


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}"


def _num(value: int | None) -> str:
    return "n/a" if value is None else str(value)


def _cost(value: float | None) -> str:
    return "n/a (no pricing configured)" if value is None else f"{value:.6f}"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def write_results(
    result: BenchmarkResult,
    *,
    output_dir: Path | str | None = None,
    secret_values: Iterable[str] = (),
) -> tuple[Path, Path]:
    """Write the JSON and Markdown artefacts, refusing to leak a credential."""
    import json

    directory = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    directory.mkdir(parents=True, exist_ok=True)

    stamp = _stamp(result.started_at)
    json_path = directory / f"benchmark_{stamp}.json"
    markdown_path = directory / f"benchmark_{stamp}.md"

    payload = result.to_dict()
    serialized = json.dumps(payload, indent=2, ensure_ascii=False)
    markdown = render_markdown(result)

    secrets = list(secret_values)
    leaks = find_secret_leaks(serialized, secrets) + find_secret_leaks(markdown, secrets)
    if leaks:
        raise BenchmarkSecretLeakError(
            "Refusing to write benchmark results: a credential was present in the "
            f"output ({len(leaks)} occurrence(s)). This is a bug in the benchmark, "
            "not something to work around."
        )

    json_path.write_text(serialized, encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def _stamp(started_at: str) -> str:
    try:
        moment = datetime.fromisoformat(started_at)
    except ValueError:
        moment = datetime.now()
    return moment.strftime("%Y%m%d_%H%M%S")


__all__ = [
    "BenchmarkSecretLeakError",
    "collect_secret_values",
    "find_secret_leaks",
    "redact",
    "render_markdown",
    "write_results",
]
