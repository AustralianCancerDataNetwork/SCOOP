import unittest
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from live_db import LiveDatabaseTestCase

from scoop.export.omop import (
    OmopExportConfig,
    UnmappableComponentError,
    export,
    require_populated_columns,
)
from scoop.patients.treatment_episode import Treatment_Episode
from scoop.profiles.manifest import load_profile
from scoop.protocols.spec import RegistrySpec
from scoop.schedule.bridge import resolve_schedule_for_treatment
from scoop.schedule.rollout import UnresolvedScheduleError

REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / "scoop/profiles/nsclc/nsclc_protocol_registry.yaml"
)
_KNOWN_GAPS = {
    "egfr_intervention": "Anlotinib (HemOnc CUI 121127)",
}


@dataclass(frozen=True)
class ScheduleTrial:
    status: str
    rejection_reason: str | None = None
    letter_markers: int = 0
    exported_rows: int = 0
    phase_id: str | None = None


def _letter_marker_count(schedule) -> int:
    return sum(
        any(
            marker in str(getattr(sig, field, "") or "")
            for field in ("alldays", "timing_sequence")
            for marker in ("(+c)", "(+n")
        )
        for phase in schedule.selected_protocol.phases
        for sig in phase.regimen.sigs
    )


def _schedule_trial(phase_id, seed, registry, gateway, profile) -> ScheduleTrial:
    treatment = Treatment_Episode(
        person_id=seed + 1,
        condition_occurrence_id=seed + 1,
        regimen_type=phase_id,
        regimen_category_id=phase_id,
        registry_resolver_type="phaseref",
        bundle_phase_summary=[{"phase_id": phase_id}],
    )
    try:
        result = resolve_schedule_for_treatment(
            treatment,
            registry,
            gateway,
            start_date=date(2026, 1, 1),
            seed=seed,
        )
    except UnresolvedScheduleError as exc:
        return ScheduleTrial("rejected", rejection_reason=exc.reason)
    if result.schedule is None:
        return ScheduleTrial(result.status)
    if not result.schedule.administrations:
        raise AssertionError("a schedule must contain administrations")
    if not all(event.date is not None for event in result.schedule.administrations):
        raise AssertionError("scheduled administrations must have dates")
    try:
        bundle = export(
            result.schedule,
            profile.vocabulary_gateway,
            OmopExportConfig(person_id=seed + 1),
        )
    except UnmappableComponentError as exc:
        return ScheduleTrial(
            "rejected",
            rejection_reason=f"unmappable {exc.name} (HemOnc CUI {exc.hemonc_cui})",
            phase_id=phase_id,
        )
    for row in [*bundle.drug_exposures, *bundle.procedures]:
        require_populated_columns(row)
    return ScheduleTrial(
        "scheduled",
        letter_markers=_letter_marker_count(result.schedule),
        exported_rows=len(bundle.drug_exposures) + len(bundle.procedures),
    )


class LiveTreatmentScheduleCompletenessTests(LiveDatabaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            cls.registry = RegistrySpec.from_path(REGISTRY_PATH)
            cls.profile = load_profile("nsclc")
        except Exception as exc:  # noqa: BLE001 - preserve the environment reason in the skip
            cls.tearDownClass()
            raise unittest.SkipTest(
                f"Live OMOP vocabulary unavailable: {type(exc).__name__}: {exc}"
            )

    def test_all_nsclc_phases_have_measured_schedule_outcomes(self):
        outcomes = Counter()
        rejected = Counter()
        trials = []
        letter_marker_occurrences = exported_rows = 0
        for phase_id in self.registry.source_phases:
            for seed in range(20):
                trial = _schedule_trial(
                    phase_id, seed, self.registry, self.gateway, self.profile
                )
                trials.append(trial)
                outcomes[trial.status] += 1
                if trial.rejection_reason is not None:
                    rejected[trial.rejection_reason] += 1
                letter_marker_occurrences += trial.letter_markers
                exported_rows += trial.exported_rows
        self.assertLessEqual(
            set(outcomes),
            {"scheduled", "no_candidates", "no_selection", "non_hemonc", "rejected"},
        )
        self.assertGreater(sum(outcomes.values()), 0)
        self.assertGreater(outcomes["scheduled"], 0)
        self.assertGreater(exported_rows, 0)
        unmappable = {
            trial.phase_id: trial.rejection_reason
            for trial in trials
            if trial.rejection_reason
            and trial.rejection_reason.startswith("unmappable ")
        }
        self.assertLessEqual(set(unmappable), set(_KNOWN_GAPS))
        for phase_id, gap in _KNOWN_GAPS.items():
            if phase_id in unmappable:
                self.assertIn(gap, unmappable[phase_id])
        print(
            "NSCLC schedule outcomes across 20 seeds per phase: "
            f"{dict(outcomes)}; rejected reasons: {dict(rejected)}; "
            f"letter marker occurrences: {letter_marker_occurrences}; "
            f"exported event rows: {exported_rows}"
        )

    def test_registry_does_not_invent_patient_selection_requirements(self):
        requirements = {
            protocol.protocol_id: protocol.selection_requires
            for protocol in self.registry.protocols.values()
            if protocol.selection_requires
        }
        self.assertFalse(
            requirements,
            f"Patient context requirements need a real graph-sample test: {requirements}",
        )
