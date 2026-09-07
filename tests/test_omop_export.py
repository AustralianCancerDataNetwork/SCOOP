from datetime import date
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scoop.export.omop import OmopExportConfig, export, export_case
from scoop.patients.build import CaseBundle
from scoop.patients.condition_occurrence import Condition_Occurrence
from scoop.patients.person import Person
from scoop.patients.treatment_episode import Treatment_Episode
from scoop.profiles.context import set_active_simulation_context
from scoop.vocabulary.gateway import DefaultVocabularyGateway
from scoop.schedule.rollout import (
    Component,
    ProtocolSchedule,
    RadiationAdministration,
    SystemicAdministration,
)
from scoop.vocabulary.gateway import FakeVocabularyGateway


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
    # TEST-ONLY VOCABULARY MAPPER PLACEHOLDER:
    # This supplies packaged mapper structure without pretending to solve
    # profile-specific clinical mappings. Replace with profile-scoped fixtures
    # when case export coverage is promoted to an end-to-end clinical test.
    root = Path(__file__).resolve().parents[1] / "scoop"
    mappers = DefaultVocabularyGateway(
        root,
        engine_factory=lambda: None,
    ).get_mappers()
    set_active_simulation_context(SimpleNamespace(vocabulary_mappers=mappers))
    try:
        yield
    finally:
        set_active_simulation_context(None)


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


def test_export_case_composes_patient_condition_and_treatment_rows():
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
    case = CaseBundle(
        graph_sample={},
        person=person,
        condition_occurrence=condition,
        treatment_episode=episode,
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
