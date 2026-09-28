"""Stage 4C benchmark CLI.

Stage 4C characterizes ``gemini-3.8-flash``, the project-selected advocate model.
It is not a model-selection competition: the CLI does not rank models, does not
declare a winner, and computing an aggregate quality score is deliberately not
supported. Its outputs are for regression testing and for measuring provider
reliability, latency, tokens, hallucination and claim verification.

Usage::

    # Plan only. Makes no API calls. This is the default.
    python -m app.benchmarks.advocate_benchmark --profile smoke --models gemini

    # Actually run it.
    python -m app.benchmarks.advocate_benchmark --profile smoke --models gemini --execute

``--models`` still accepts more than one name. That capability is retained so the
transport stays demonstrably vendor-agnostic; it is not a Stage 4C goal, and
``qwen3-ctx16k`` is an unused fallback that is never run.

The plan is always printed before anything runs, and the expected number of API
calls is part of it. With a constrained free tier, the cost of a benchmark has
to be inspectable before it is spent.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.benchmarks.cases import BenchmarkCaseSelectionError
from app.benchmarks.models import (
    BenchmarkConfigurationError,
    resolve_model_configs,
)
from app.benchmarks.reporting import (
    BenchmarkSecretLeakError,
    collect_secret_values,
    write_results,
)
from app.benchmarks.runner import (
    BenchmarkPlan,
    build_plan,
    execute_plan,
    render_plan,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.benchmarks.advocate_benchmark",
        description=(
            "Benchmark advocate models through the real Stage 4 pipeline. "
            "Makes no API calls unless --execute is given."
        ),
    )
    parser.add_argument(
        "--profile",
        choices=("smoke", "full"),
        default="smoke",
        help="smoke: one case per dispute type. full: every qualifying case. Default: smoke.",
    )
    parser.add_argument(
        "--models",
        default="gemini",
        help="Comma-separated model names from the catalogue. Default: gemini.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Repetitions per case. Default: 1. More runs let you measure consistency.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Directory for result files. Default: backend/benchmark_results.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually call the models. Without this, only the plan is printed.",
    )
    parser.add_argument(
        "--require-unique-cases",
        action="store_true",
        help=(
            "Fail if a criterion matches more than one case instead of selecting "
            "deterministically. Useful when auditing fixture changes."
        ),
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Override the catalogue retry count for every selected model.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Running as a CLI bypasses app.main, so load backend/.env here too.
    from app.env import load_local_env

    load_local_env()

    try:
        configs = resolve_model_configs([part for part in args.models.split(",")])
        if args.max_retries is not None:
            from dataclasses import replace

            configs = [replace(config, max_retries=max(0, args.max_retries)) for config in configs]
        plan = build_plan(profile=args.profile, model_configs=configs, runs=args.runs)
    except (BenchmarkConfigurationError, BenchmarkCaseSelectionError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    print(render_plan(plan))
    print()

    if not args.execute:
        print("PLAN ONLY — NO API CALLS MADE")
        print(f"Re-run with --execute to perform the {plan.total_expected_calls} call(s) above.")
        return 0

    if args.require_unique_cases:
        # Selection already happened deterministically; this flag exists to make
        # the ambiguity loud during a fixture audit.
        from app.benchmarks.cases import select_profile_cases

        try:
            select_profile_cases(args.profile)
        except BenchmarkCaseSelectionError as error:
            print(f"Case selection error: {error}", file=sys.stderr)
            return 2

    print(f"Executing {plan.total_expected_calls} call(s)…")
    print()

    try:
        result = execute_plan(plan, allow_live=True)
    except BenchmarkConfigurationError as error:
        print(f"Execution refused: {error}", file=sys.stderr)
        return 2

    try:
        json_path, markdown_path = write_results(
            result,
            output_dir=Path(args.output) if args.output else None,
            secret_values=collect_secret_values(configs),
        )
    except BenchmarkSecretLeakError as error:
        print(f"Result write refused: {error}", file=sys.stderr)
        return 3

    _print_summary(result)
    print(f"Results written to:\n  {json_path}\n  {markdown_path}")
    return 0


def _print_summary(result: object) -> None:
    aggregates = getattr(result, "aggregates", [])
    print("Summary")
    for aggregate in aggregates:
        if aggregate.skipped:
            print(f"  {aggregate.model}: SKIPPED ({aggregate.skip_reason})")
            continue
        print(
            f"  {aggregate.model}: "
            f"{aggregate.completed_calls}/{aggregate.total_calls} completed, "
            f"{aggregate.verified_claims}/{aggregate.total_claims} claims verified, "
            f"provider failures {aggregate.provider_failure_count}, "
            f"model-output failures {aggregate.model_output_failure_count}"
        )
    if aggregates and all(aggregate.skipped for aggregate in aggregates):
        print()
        print("BENCHMARK READY")
        print("ALL SELECTED MODELS UNAVAILABLE — NO RESULTS PRODUCED")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
