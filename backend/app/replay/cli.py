"""Developer CLI for replay.

    python -m app.replay.cli plan
    python -m app.replay.cli capture --case DISP-005 --stage all --execute
    python -m app.replay.cli validate --case DISP-005 --stage advocates
    python -m app.replay.cli inspect --artifact path/to/advocates.json
    python -m app.replay.cli replay --case DISP-005 --mode FULL_AI --execute

**Nothing here makes an external call unless ``--execute`` is passed.** That is
not a convenience default; it is the same rule the rest of the pipeline follows,
applied at the boundary where it matters most. ``capture`` and ``replay`` are the
only two subcommands that could reach a provider, and both refuse to proceed
without the flag, printing what they *would* do and what it would cost instead.

``plan``, ``validate`` and ``inspect`` never call anything. They are the three
you can safely run while reading this file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.agents.context_builder import AdvocateContextBuilder
from app.models.replay import NO_REPLAY, REPLAY_MODES, ReplayMode
from app.replay.plan import build_plan
from app.replay.service import ReplayService
from app.replay.store import ReplayArtifactStore
from app.replay.validation import ReplayValidationService

_CAPTURE_STAGES = ("advocates", "rebuttals", "judge", "all")
_STAGE_BY_ARG: dict[str, str] = {
    "advocates": "ADVOCATES",
    "rebuttals": "REBUTTALS",
    "judge": "JUDGE",
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    return handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.replay.cli",
        description=(
            "Replay stored AI-stage output for a case. Nothing makes an external "
            "call unless --execute is passed."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    plan = sub.add_parser("plan", help="Show the expected provider calls per mode. No network.")
    plan.add_argument(
        "--mode",
        choices=list(REPLAY_MODES),
        default=None,
        help="Show one mode; omit to show every mode.",
    )
    plan.set_defaults(handler=_cmd_plan)

    capture = sub.add_parser(
        "capture", help="Run the pipeline and store AI-stage artefacts."
    )
    capture.add_argument("--case", required=True)
    capture.add_argument("--stage", choices=list(_CAPTURE_STAGES), default="all")
    capture.add_argument(
        "--execute",
        action="store_true",
        help="Required. Without it nothing is run and nothing is written.",
    )
    capture.set_defaults(handler=_cmd_capture)

    validate = sub.add_parser(
        "validate", help="Check a stored artefact against the current case state. No network."
    )
    validate.add_argument("--case", required=True)
    validate.add_argument("--stage", choices=sorted(_STAGE_BY_ARG), required=True)
    validate.set_defaults(handler=_cmd_validate)

    inspect = sub.add_parser("inspect", help="Print an artefact summary. No network.")
    inspect.add_argument("--artifact", required=True)
    inspect.set_defaults(handler=_cmd_inspect)

    replay = sub.add_parser("replay", help="Run the pipeline in a replay mode.")
    replay.add_argument("--case", required=True)
    replay.add_argument("--mode", choices=list(REPLAY_MODES), default=NO_REPLAY)
    replay.add_argument(
        "--execute",
        action="store_true",
        help="Required. Without it nothing is run.",
    )
    replay.set_defaults(handler=_cmd_replay)

    return parser


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_plan(args: argparse.Namespace) -> int:
    modes = [args.mode] if args.mode else list(REPLAY_MODES)
    print("mode                      provider calls   live stages")
    print("-" * 64)
    for mode in modes:
        plan = build_plan(mode)
        live = ", ".join(plan.live_stages) or "(none)"
        print(f"{mode:25s} {plan.expected_provider_calls:>14d}   {live}")
    print()
    print("Deterministic analysis, resolution, confidence and escalation are")
    print("recomputed on every run, in every mode, including FULL_AI.")
    return 0


def _cmd_capture(args: argparse.Namespace) -> int:
    stages = (
        ("ADVOCATES", "REBUTTALS", "JUDGE") if args.stage == "all" else (_STAGE_BY_ARG[args.stage],)
    )
    if not args.execute:
        plan = build_plan(NO_REPLAY)
        print("DRY RUN — nothing executed, nothing written.")
        print(f"  case            {args.case}")
        print(f"  stages          {', '.join(stages)}")
        print(f"  provider calls  {plan.expected_provider_calls} (a full live run)")
        print(f"  artifact dir    {ReplayArtifactStore().root}")
        print()
        print("Pass --execute to run the pipeline and write the artefacts.")
        return 0

    case, analysis, _ = _load_case(args.case)
    from app.services.resolution_orchestrator import ResolutionOrchestratorService

    result = ResolutionOrchestratorService().run(
        case, analysis, capture_stages=frozenset(stages)
    )
    written = [
        event.metadata.get("stage")
        for event in result.audit
        if event.event == "REPLAY_ARTIFACT_WRITTEN"
    ]
    print(f"captured stages: {', '.join(str(item) for item in written) or '(none)'}")
    print(f"artifact dir:    {ReplayArtifactStore().root}")
    print(f"judge status:    {result.judge.status}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    stage = _STAGE_BY_ARG[args.stage]
    case, analysis, context = _load_case(args.case)
    store = ReplayArtifactStore()
    artifact, load_reasons = store.read(case.id, stage)

    if artifact is None:
        print(f"REPLAY_INVALID {load_reasons}")
        return 1

    result = ReplayValidationService().validate(
        artifact, case=case, analysis=analysis, context=context
    )
    print(f"artifact   {store.path_for(case.id, stage)}")
    print(f"created    {artifact.created_at}")
    print(f"provider   {artifact.provider or '(unrecorded)'}  model {artifact.model or '-'}")
    print(f"status     {result.status}")
    if not result.valid:
        for code in result.reason_codes:
            print(f"  - {code}")
        print()
        print("A re-capture is required. No provider call is made automatically.")
        return 1
    print()
    print("Hash check: stored artefact matches the current deterministic case state.")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    path = Path(args.artifact)
    if not path.is_file():
        print(f"no such artefact: {path}")
        return 1
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        print(f"MALFORMED_ARTIFACT: {error}")
        return 1

    print(f"file           {path}")
    for key in (
        "stage",
        "replayVersion",
        "schemaVersion",
        "createdAt",
        "caseId",
        "disputeType",
        "provider",
        "model",
        "deterministicAnalysisHash",
        "trustedContextHash",
    ):
        print(f"{key:22s} {document.get(key, '-')}")

    payload = document.get("payload") or {}
    print()
    print("payload summary (counts only; the raw output is not printed here)")
    for side in ("rider", "driver"):
        entry = payload.get(side)
        if not isinstance(entry, dict):
            continue
        fingerprint = entry.get("capturedVerification") or {}
        print(
            f"  {side:7s} status={entry.get('status', 'COMPLETE')} "
            f"capturedVerified={len(fingerprint.get('verifiedSignatures') or [])} "
            f"capturedRejected={len(fingerprint.get('rejected') or [])}"
        )
    if "output" in payload:
        output = payload["output"] or {}
        print(
            f"  judge   outcome={output.get('recommendedOutcome', '-')} "
            f"status={output.get('status', '-')}"
        )
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    plan = build_plan(args.mode)
    if not args.execute:
        print("DRY RUN — nothing executed.")
        print(f"  case            {args.case}")
        print(f"  mode            {args.mode}")
        print(f"  provider calls  {plan.expected_provider_calls}")
        print(f"  live stages     {', '.join(plan.live_stages) or '(none)'}")
        print()
        print("Pass --execute to run.")
        return 0

    from app.replay.service import ReplayRefused
    from app.services.resolution_orchestrator import ResolutionOrchestratorService

    case, analysis, _ = _load_case(args.case)
    try:
        result = ResolutionOrchestratorService().run(
            case, analysis, replay_mode=args.mode
        )
    except ReplayRefused as refused:
        print(f"REPLAY_INVALID {refused.reason_codes}")
        print("No provider call was made. Re-capture the artefact and try again.")
        return 1

    metadata = result.replay_metadata
    print(f"mode            {metadata.mode}")
    print(f"replayed        {metadata.replayed}  (advocates={metadata.advocates_replayed} "
          f"rebuttals={metadata.rebuttals_replayed} judge={metadata.judge_replayed})")
    print(f"artifact        {metadata.artifact_created_at} v{metadata.artifact_version}")
    print(f"judge           {result.judge.status}  {result.judge.recommended_outcome or '-'}")
    print(f"refund          {result.deterministic_resolution.refund_amount} "
          f"{result.deterministic_resolution.currency}  (recomputed)")
    print(f"mode/confidence {result.deterministic_resolution.resolution_mode}  "
          f"{result.deterministic_resolution.confidence:.4f}  (recomputed)")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_case(case_id: str):
    """Load a case and its deterministic analysis through the canonical path.

    Deliberately the same path the API uses, so a CLI validation cannot disagree
    with what the service would decide.
    """
    from app.repositories.case_repository import MockCaseRepository
    from app.services.case_service import CaseService, CaseNotFoundError

    service = CaseService(MockCaseRepository())
    try:
        case = service._load_case(case_id)
    except CaseNotFoundError:
        print(f"case not found: {case_id}")
        raise SystemExit(2) from None
    analysis = service.get_analysis(case_id)
    context = AdvocateContextBuilder().build(case, analysis)
    return case, analysis, context


if __name__ == "__main__":
    sys.exit(main())
