from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scoop.export.omop import (
    OmopExportBundle,
    OmopExportConfig,
    UnmappableComponentError,
    export,
    export_case,
    export_cohort,
)
from scoop.patients.build import CaseBundle
from scoop.patients.condition_episode import Condition_Episode
from scoop.patients.condition_occurrence import Condition_Occurrence
from scoop.patients.episode_base import Episode_Event
from scoop.patients.person import Person
from scoop.patients.treatment_episode import Treatment_Episode
from scoop.profiles.context import set_active_simulation_context
from scoop.profiles.manifest import load_profile
from scoop.schedule.rollout import (
    Component,
    ProtocolSchedule,
    RadiationAdministration,
    SystemicAdministration,
    UnresolvedScheduleError,
)
from scoop.vocabulary.gateway import DefaultVocabularyGateway, FakeVocabularyGateway


def _gateway():
    return FakeVocabularyGateway(
        concepts={
            ("drug", "cisplatin"): 111,
            ("procedure", "External beam radiotherapy"): 333,
            ("record_type", "drug_exposure"): 32818,
            ("record_type", "procedure_occurrence"): 32817,
        },
        route_concepts={"intravenous": 222},
        component_concepts={
            # HemOnc CUI 9001 -> the standard concept its "Maps to" edge reaches,
            # which must win over the ("drug", "cisplatin") name entry.
            ("drug", 9001): 444,
        },
    )


@contextmanager
def _active_mappers():
    root = Path(__file__).resolve().parents[1] / "scoop"
    context = load_profile(
        "nsclc",
        base_dir=root,
        vocabulary_gateway=DefaultVocabularyGateway(root, engine_factory=lambda: None),
    )
    set_active_simulation_context(context)
    try:
        yield
    finally:
        set_active_simulation_context(None)


def _case_with_drug_event(*, schedule_seed=None):
    person = Person(sex="female", age=65, auto_create_condition=False)
    with patch.object(
        Condition_Occurrence,
        "_create_overarching_condition_episode",
        lambda self: self,
    ):
        condition = Condition_Occurrence(
            person_id=person.person_id,
            condition_concept_id=44500984,
            condition_source_concept_id=44500984,
            condition_source_value="8070/3-C34.1",
            condition_start_date=date(2026, 1, 1),
            auto_assign_regimen=False,
        )
    condition.person = person
    person.condition = condition
    episode = Treatment_Episode(
        person_id=person.person_id,
        condition_occurrence_id=condition.condition_occurrence_id,
        regimen_type="fixture",
        regimen_category_id="fixture",
        schedule_seed=schedule_seed,
        drug_exposures=[
            {
                "drug_exposure_start_date": date(2026, 1, 2),
                "drug_source_value": "cisplatin",
                "route_source_value": "intravenous",
                "dose": 75,
                "dose_unit": "mg",
            }
        ],
    )
    return CaseBundle({}, person, condition, episode)


def test_empty_schedule_exports_to_empty_bundle():
    result = export(ProtocolSchedule(), _gateway())

    assert result.drug_exposures == []
    assert result.procedures == []


def test_populated_schedule_exports_real_omop_fact_models():
    component = Component(name="cisplatin", cui=9001, drug_object=None)
    schedule = ProtocolSchedule(
        administrations=(
            SystemicAdministration(
                date=date(2026, 1, 1),
                component=component,
                phase_id="systemic",
                source_sig_id=7,
                dose=75,
                dose_unit="mg",
                dose_unit_cui=8,
                route="intravenous",
            ),
        )
    )

    result = export(
        schedule,
        _gateway(),
        OmopExportConfig(person_id=10, drug_type_concept_id=38000175),
    )
    row = result.drug_exposures[0]

    assert row.person_id == 10
    # Resolved through the component's HemOnc CUI, not its name.
    assert row.drug_concept_id == 444
    assert row.drug_type_concept_id == 38000175
    assert row.route_concept_id == 222
    assert row.quantity == 75
    assert row.drug_exposure_start_date == date(2026, 1, 1)


def test_drug_falls_back_to_the_name_map_when_hemonc_has_no_mapping():
    # HemOnc leaves some components unmapped (radiotherapy, BCG); those resolve
    # through the declared name map instead.
    component = Component(name="cisplatin", cui=9999, drug_object=None)
    schedule = ProtocolSchedule(
        administrations=(
            SystemicAdministration(
                date=date(2026, 1, 1),
                component=component,
                phase_id="systemic",
                route="intravenous",
            ),
        )
    )

    result = export(schedule, _gateway(), OmopExportConfig(person_id=10))

    assert result.drug_exposures[0].drug_concept_id == 111


def test_type_concepts_default_to_the_configured_record_type_map():
    component = Component(name="cisplatin", cui=9001, drug_object=None)
    schedule = ProtocolSchedule(
        administrations=(
            SystemicAdministration(
                date=date(2026, 1, 1),
                component=component,
                phase_id="systemic",
                route="intravenous",
            ),
        )
    )

    result = export(schedule, _gateway(), OmopExportConfig(person_id=10))

    assert result.drug_exposures[0].drug_type_concept_id == 32818


def test_route_ns_is_preserved_as_source_without_concept_lookup():
    schedule = ProtocolSchedule(
        administrations=(
            SystemicAdministration(
                date=date(2026, 1, 1),
                component=Component(name="cisplatin", cui=9001, drug_object=None),
                phase_id="systemic",
                route="NS",
            ),
        )
    )
    gateway = _gateway()
    result = export(schedule, gateway, OmopExportConfig(person_id=10))

    assert result.drug_exposures[0].route_source_value == "NS"
    assert result.drug_exposures[0].route_concept_id is None


def test_radiation_requires_procedure_type_and_maps_procedure_concept():
    component = Component(
        name="External beam radiotherapy",
        cui=9002,
        drug_object=None,
    )
    schedule = ProtocolSchedule(
        administrations=(
            RadiationAdministration(
                date=date(2026, 1, 2),
                component=component,
                phase_id="radiation",
                fraction_dose=2,
                dose_unit="Gy",
            ),
        )
    )

    # With no override the procedure type concept comes from the record_type map.
    defaulted = export(schedule, _gateway(), OmopExportConfig(person_id=10))
    assert defaulted.procedures[0].procedure_type_concept_id == 32817

    result = export(
        schedule,
        _gateway(),
        OmopExportConfig(person_id=10, procedure_type_concept_id=38000275),
    )

    assert result.procedures[0].procedure_concept_id == 333
    assert result.procedures[0].procedure_type_concept_id == 38000275
    assert result.procedures[0].procedure_date == date(2026, 1, 2)


def test_missing_record_type_configuration_is_an_error():
    component = Component(name="cisplatin", cui=9001, drug_object=None)
    schedule = ProtocolSchedule(
        administrations=(
            SystemicAdministration(
                date=date(2026, 1, 1),
                component=component,
                phase_id="systemic",
            ),
        )
    )
    gateway = FakeVocabularyGateway(concepts={("drug", "cisplatin"): 111})

    with pytest.raises(KeyError):
        export(schedule, gateway, OmopExportConfig(person_id=10))


def test_schedule_seed_produces_repeatable_distinct_event_id_ranges():
    schedule = ProtocolSchedule(
        administrations=(
            SystemicAdministration(
                date=date(2026, 1, 1),
                component=Component(name="cisplatin", cui=9001, drug_object=None),
                phase_id="systemic",
            ),
        ),
        seed=17,
    )
    first = export(schedule, _gateway(), OmopExportConfig(person_id=10))
    repeated = export(schedule, _gateway(), OmopExportConfig(person_id=10))
    another_seed = export(
        ProtocolSchedule(administrations=schedule.administrations, seed=18),
        _gateway(),
        OmopExportConfig(person_id=11),
    )

    assert first.drug_exposures[0].drug_exposure_id == 3_400_001
    assert first.drug_exposures[0].drug_exposure_id == repeated.drug_exposures[0].drug_exposure_id
    assert first.drug_exposures[0].drug_exposure_id != another_seed.drug_exposures[0].drug_exposure_id


def test_unmappable_component_is_typed_with_source_identity():
    gateway = FakeVocabularyGateway(concepts={("record_type", "drug_exposure"): 32818})
    schedule = ProtocolSchedule(
        administrations=(
            SystemicAdministration(
                date=date(2026, 1, 1),
                component=Component(name="unknown drug", cui=9999, drug_object=None),
                phase_id="systemic",
            ),
        )
    )

    from scoop.export.omop import UnmappableComponentError

    with pytest.raises(UnmappableComponentError) as exc_info:
        export(schedule, gateway, OmopExportConfig(person_id=10))

    assert exc_info.value.name == "unknown drug"
    assert exc_info.value.hemonc_cui == 9999


def test_cohort_export_records_unresolved_patient_and_continues():
    def build_case(seed):
        if seed == 0:
            raise UnresolvedScheduleError(
                sig_id=22, phase_id="platinum_doublet", reason="cycle gap"
            )
        raise UnmappableComponentError("unknown drug", 888)

    result = export_cohort(2, build_case, _gateway())

    assert len(result.bundles) == 0
    assert [rejection.seed for rejection in result.rejections] == [0, 1]
    assert result.rejections[0].reason.startswith("Unresolved schedule")
    assert result.rejections[1].component_name == "unknown drug"


def test_cohort_export_uses_case_seed_for_component_and_row_rejections(monkeypatch):
    from omop_alchemy.cdm.model import Drug_Exposure

    import scoop.export.omop as omop_export

    cases = {
        index: SimpleNamespace(
            seed=73 + index,
            treatment_episode=SimpleNamespace(
                regimen_type="fixture", schedule_seed=73 + index
            ),
        )
        for index in range(3)
    }

    def export_or_fail(case, _gateway):
        if case.seed == 73:
            raise UnmappableComponentError("unknown drug", 888)
        if case.seed == 74:
            omop_export.require_populated_columns(Drug_Exposure())
        return OmopExportBundle()

    monkeypatch.setattr(omop_export, "export_case", export_or_fail)
    result = export_cohort(3, cases.__getitem__, _gateway())

    assert len(result.bundles) == 1
    assert [rejection.seed for rejection in result.rejections] == [73, 74]
    assert result.rejections[0].component_name == "unknown drug"
    assert "Drug_Exposure" in result.rejections[1].reason
    assert result.rejections[1].component_name is None


def test_seeded_case_exports_repeat_event_ids():
    case = _case_with_drug_event(schedule_seed=73)
    with (
        _active_mappers(),
        patch.object(Person, "_collect_emitted_outputs", return_value=([], [], [], [])),
        patch.object(
            Condition_Occurrence,
            "_collect_emitted_outputs",
            return_value=([], [], [], []),
        ),
    ):
        first = export_case(case, _gateway())
        second = export_case(case, _gateway())

    assert first.drug_exposures[0].drug_exposure_id == 14_600_001
    assert second.drug_exposures[0].drug_exposure_id == 14_600_001
    assert {
        column.name: getattr(first.drug_exposures[0], column.name)
        for column in first.drug_exposures[0].__table__.columns
    } == {
        column.name: getattr(second.drug_exposures[0], column.name)
        for column in second.drug_exposures[0].__table__.columns
    }


def test_export_case_composes_patient_condition_and_treatment_rows():
    case = _case_with_drug_event()
    person = case.person

    with (
        _active_mappers(),
        patch.object(Person, "_collect_emitted_outputs", return_value=([], [], [], [])),
        patch.object(
            Condition_Occurrence,
            "_collect_emitted_outputs",
            return_value=([], [], [], []),
        ),
    ):
        result = export_case(
            case,
            _gateway(),
            OmopExportConfig(
                person_id=person.person_id,
                drug_type_concept_id=38000175,
            ),
        )

    assert result.person.person_id == person.person_id
    assert len(result.condition_occurrences) == 1
    assert len(result.drug_exposures) == 1
    assert result.drug_exposures[0].person_id == person.person_id
    assert len(result.episodes) == 1
    assert len(result.episode_events) == 1
    assert result.episode_events[0].event_id == result.drug_exposures[0].drug_exposure_id


def test_export_case_empty_treatment_needs_no_event_configuration():
    person = Person(sex="female", age=65, auto_create_condition=False)
    with patch.object(
        Condition_Occurrence,
        "_create_overarching_condition_episode",
        lambda self: self,
    ):
        condition = Condition_Occurrence(
            person_id=person.person_id,
            condition_concept_id=44500984,
            condition_source_concept_id=44500984,
            condition_source_value="8070/3-C34.1",
            condition_start_date=date(2026, 1, 1),
            auto_assign_regimen=False,
        )
    condition.person = person
    person.condition = condition
    episode = Treatment_Episode(
        person_id=person.person_id,
        condition_occurrence_id=condition.condition_occurrence_id,
        regimen_type="unresolved",
        regimen_category_id="unresolved",
    )

    with (
        _active_mappers(),
        patch.object(Person, "_collect_emitted_outputs", return_value=([], [], [], [])),
        patch.object(
            Condition_Occurrence,
            "_collect_emitted_outputs",
            return_value=([], [], [], []),
        ),
    ):
        result = export_case(
            CaseBundle({}, person, condition, episode),
            _gateway(),
        )

    assert result.person.person_id == person.person_id
    assert result.drug_exposures == []
    assert result.procedures == []
    assert result.episodes == []
    assert result.episode_events == []


def test_condition_episode_export_keeps_its_existing_nullable_fields():
    with _active_mappers():
        episode = Condition_Episode(
            person_id=4,
            condition_occurrence_id=12,
            episode_object_concept_id=23,
            episode_events=[
                Episode_Event(
                    episode_id=7,
                    event_id=12,
                    episode_event_field_concept_id=1147127,
                )
            ],
        )
        episode.episode_concept_id = None
        episode.episode_type_concept_id = None
        row = episode.to_omop_row()

    assert row.episode_object_concept_id == 23
    assert row.episode_concept_id is None
    assert row.episode_type_concept_id is None
