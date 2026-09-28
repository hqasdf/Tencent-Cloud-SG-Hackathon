"""Semantic benchmark case selection.

The benchmark must not hardcode ``DISP-005``. If the fixtures are rewritten, a
hardcoded ID silently benchmarks a different case — or crashes — and the
resulting numbers stop being comparable with earlier runs.

So a case is chosen by *property*: a named criterion describing what makes a
case suitable for benchmarking. Selection is deterministic and always reports
the full candidate list, so a reader can see exactly which fixtures qualified
and why one was picked.

Why not "exactly one, or fail"?

With the current eight synthetic fixtures, each criterion matches **two**
equivalent cases (route: DISP-005 / DISP-008; no-show: DISP-002 / DISP-004).
Requiring uniqueness outright would make the smoke profile unrunnable. The
compromise is deliberate and visible rather than silent:

  * zero matches  -> raise, always
  * two+ matches  -> raise when ``require_unique`` is set; otherwise pick the
                     lowest case id and record the alternatives in the report
  * the narrowing rule is written into the output as ``tie_break``

``require_unique=True`` exists so the ambiguity is detectable and testable
rather than merely tolerated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.data.cases import MOCK_CASES
from app.models.analysis import CaseAnalysisResponse
from app.models.case import DisputeCase
from app.services.dispute_analysis import DisputeAnalysisService

ROUTE_DEVIATION = "route_deviation"
NO_SHOW = "no_show_charge"


class BenchmarkCaseSelectionError(RuntimeError):
    """Raised when a criterion matches zero cases, or when uniqueness was demanded."""


Predicate = Callable[[DisputeCase, CaseAnalysisResponse], bool]


@dataclass(frozen=True)
class CaseCriterion:
    key: str
    dispute_type: str
    description: str
    predicate: Predicate


@dataclass(frozen=True)
class CaseSelection:
    criterion_key: str
    description: str
    dispute_type: str
    candidates: list[str]
    """Every fixture matching the predicate, ordered by case id."""

    selected: list[str]
    tie_break: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "criterion": self.criterion_key,
            "description": self.description,
            "disputeType": self.dispute_type,
            "candidates": list(self.candidates),
            "selected": list(self.selected),
            "tieBreak": self.tie_break,
        }


# ---------------------------------------------------------------------------
# Criteria
# ---------------------------------------------------------------------------


def _route_clean_fully_explained(case: DisputeCase, analysis: CaseAnalysisResponse) -> bool:
    """A route case with no evidence gaps, no contradictions, and a real basis.

    "Fully explained" means every extra kilometre is accounted for by verified
    conditions, so the advocate has something concrete to argue from rather
    than a hole in the record.
    """
    facts = analysis.analysis
    return (
        not facts.missing_evidence_ids
        and not facts.contradictory_evidence
        and facts.explained_deviation_distance_km > 0
        and facts.unexplained_deviation_distance_km == 0
    )


def _no_show_clean_chargeable(case: DisputeCase, analysis: CaseAnalysisResponse) -> bool:
    """A no-show case with no evidence gaps and a non-zero charge and wait.

    Both must be non-zero: a zero wait or a zero charge is a degenerate case
    that tests nothing about the advocate's reasoning.
    """
    facts = analysis.analysis
    return (
        not facts.missing_evidence_ids
        and not facts.contradictory_evidence
        and facts.waiting_duration_seconds > 0
        and facts.cancellation_charge_amount > 0
    )


ROUTE_CRITERION = CaseCriterion(
    key="route_clean_fully_explained",
    dispute_type=ROUTE_DEVIATION,
    description=(
        "route deviation with no missing or contradictory evidence, at least one "
        "verified condition explaining the deviation, and no unexplained distance"
    ),
    predicate=_route_clean_fully_explained,
)

NO_SHOW_CRITERION = CaseCriterion(
    key="no_show_clean_chargeable",
    dispute_type=NO_SHOW,
    description=(
        "no-show charge with no missing or contradictory evidence, a non-zero "
        "waiting duration, and a non-zero cancellation charge"
    ),
    predicate=_no_show_clean_chargeable,
)

CRITERIA_BY_DISPUTE_TYPE: dict[str, CaseCriterion] = {
    ROUTE_DEVIATION: ROUTE_CRITERION,
    NO_SHOW: NO_SHOW_CRITERION,
}


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def evaluate_criterion(
    criterion: CaseCriterion,
    cases: list[DisputeCase] | None = None,
) -> list[str]:
    """Return every case id matching the criterion, ordered by id."""
    service = DisputeAnalysisService()
    matched: list[str] = []
    for case in cases if cases is not None else MOCK_CASES:
        if case.dispute_type != criterion.dispute_type:
            continue
        analysis = service.analyze(case)
        if criterion.predicate(case, analysis):
            matched.append(case.id)
    return sorted(matched)


def select_cases(
    criterion: CaseCriterion,
    *,
    limit: int | None = None,
    require_unique: bool = False,
    cases: list[DisputeCase] | None = None,
) -> CaseSelection:
    """Select benchmark cases for one criterion.

    Raises when nothing matches, because benchmarking an empty category would
    quietly produce a comparison with a missing column.
    """
    candidates = evaluate_criterion(criterion, cases)

    if not candidates:
        raise BenchmarkCaseSelectionError(
            f"No case matches criterion {criterion.key!r} ({criterion.description}). "
            "The fixtures have changed; update the criterion rather than the case id."
        )

    if require_unique and len(candidates) > 1:
        raise BenchmarkCaseSelectionError(
            f"Criterion {criterion.key!r} matched {len(candidates)} cases "
            f"({', '.join(candidates)}); a single case was required. "
            "Narrow the criterion, or pass require_unique=False to pick deterministically."
        )

    tie_break: str | None = None
    if limit is not None and len(candidates) > limit:
        # Deterministic and stated, never silent.
        tie_break = (
            f"{len(candidates)} cases matched; took the {limit} lowest case id(s) "
            f"in ascending order"
        )
        selected = candidates[:limit]
    else:
        selected = list(candidates)

    return CaseSelection(
        criterion_key=criterion.key,
        description=criterion.description,
        dispute_type=criterion.dispute_type,
        candidates=candidates,
        selected=selected,
        tie_break=tie_break,
    )


def select_profile_cases(profile: str) -> list[CaseSelection]:
    """Case selections for a benchmark profile.

    ``smoke`` takes one representative case per dispute type; ``full`` takes
    every fixture that qualifies.
    """
    limit = 1 if profile == "smoke" else None
    return [
        select_cases(ROUTE_CRITERION, limit=limit),
        select_cases(NO_SHOW_CRITERION, limit=limit),
    ]


def case_by_id(case_id: str) -> DisputeCase:
    for case in MOCK_CASES:
        if case.id == case_id:
            return case
    raise BenchmarkCaseSelectionError(f"Unknown case id {case_id!r}.")
