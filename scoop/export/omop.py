from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, TypeVar

import sqlalchemy as sa

from ..schedule.rollout import (
    ProtocolSchedule,
    RadiationAdministration,
    SystemicAdministration,
)
from ..vocabulary.gateway import VocabularyGateway


def as_datetime(value: date | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min)


_OmopRow = TypeVar("_OmopRow")


def require_populated_columns(row: _OmopRow) -> _OmopRow:
    """Raise if any NOT-NULL column on this omop_alchemy ORM row is unset.

    The requirement is read straight off the row's own SQLAlchemy mapper --
    the same constraint the real CDM schema enforces at insert time -- so
    there is no hand-maintained list of "required fields" to keep in sync as
    tables or columns change. Returns the row unchanged, so a `to_omop_row()`
    implementation can wrap its return value:
    `return require_populated_columns(omop_model.X(...))`.
    """

    mapper = sa.inspect(row).mapper
    missing = [
        column.name
        for column in mapper.columns
        if not column.nullable and getattr(row, column.name, None) is None
    ]
    if missing:
        raise ValueError(
            f"{type(row).__name__} row is missing required (NOT NULL) "
            f"column(s): {', '.join(missing)}. Check the profile and "
            "vocabulary configuration that produced this case."
        )
    return row


@dataclass
class OmopExportBundle:
    person: Any | None = None
    condition_occurrences: list[Any] = field(default_factory=list)
    episodes: list[Any] = field(default_factory=list)
    episode_events: list[Any] = field(default_factory=list)
    measurements: list[Any] = field(default_factory=list)
    observations: list[Any] = field(default_factory=list)
    drug_exposures: list[Any] = field(default_factory=list)
    procedures: list[Any] = field(default_factory=list)

    def merge(self, other: "OmopExportBundle") -> "OmopExportBundle":
        if self.person is None:
            self.person = other.person
        self.condition_occurrences.extend(other.condition_occurrences)
        self.episodes.extend(other.episodes)
        self.episode_events.extend(other.episode_events)
        self.measurements.extend(other.measurements)
        self.observations.extend(other.observations)
        self.drug_exposures.extend(other.drug_exposures)
        self.procedures.extend(other.procedures)
        return self


# concept_maps.yaml domain holding one provenance concept per CDM event table.
RECORD_TYPE_DOMAIN = "record_type"

# CDM event table -> the OmopExportConfig field that overrides its type concept.
_TYPE_CONCEPT_OVERRIDES = {
    "drug_exposure": "drug_type_concept_id",
    "procedure_occurrence": "procedure_type_concept_id",
}


@dataclass(frozen=True)
class OmopExportConfig:
    """Identifiers required to create standalone synthetic OMOP fact rows.

    The two type concepts are left unset by default and resolved from the
    profile's ``record_type`` concept map at export time, so provenance is a
    configuration decision rather than something every caller has to restate.
    Setting either here overrides the configured value for this export.
    """

    person_id: int
    drug_type_concept_id: int | None = None
    procedure_type_concept_id: int | None = None
    first_drug_exposure_id: int = 1
    first_procedure_occurrence_id: int = 1

    def __post_init__(self) -> None:
        if self.person_id < 1:
            raise ValueError("person_id must be positive")
        if self.first_drug_exposure_id < 1:
            raise ValueError("first_drug_exposure_id must be positive")
        if self.first_procedure_occurrence_id < 1:
            raise ValueError("first_procedure_occurrence_id must be positive")


def _record_type_concept_id(
    config: OmopExportConfig,
    gateway: VocabularyGateway,
    *,
    event_table: str,
) -> int:
    """The *_type_concept_id for one event table, from config or the mapper."""

    override = getattr(config, _TYPE_CONCEPT_OVERRIDES[event_table])
    if override:
        return int(override)

    resolved = gateway.resolve_concept(RECORD_TYPE_DOMAIN, event_table)
    if not resolved:
        raise ValueError(
            f"No type concept for {event_table} rows: set it on OmopExportConfig "
            f"or add a '{RECORD_TYPE_DOMAIN}.{event_table}' entry to the "
            "profile's concept map."
        )
    return int(resolved)


def export(
    schedule: ProtocolSchedule,
    gateway: VocabularyGateway,
    config: OmopExportConfig | None = None,
) -> OmopExportBundle:
    """Export schedule administrations into OMOP clinical fact models."""

    if not isinstance(schedule, ProtocolSchedule):
        raise TypeError("export() requires a ProtocolSchedule")
    if not schedule.administrations:
        return OmopExportBundle()
    if config is None:
        raise ValueError(
            "config is required when exporting populated schedules; provide "
            "person and OMOP type concept identifiers"
        )

    from omop_alchemy.cdm import model as omop_model

    bundle = OmopExportBundle()
    next_drug_id = config.first_drug_exposure_id
    next_procedure_id = config.first_procedure_occurrence_id
    for administration in schedule.administrations:
        if isinstance(administration, SystemicAdministration):
            drug_type_concept_id = _record_type_concept_id(
                config, gateway, event_table="drug_exposure"
            )
            drug_concept_id = gateway.resolve_component_concept(
                "drug",
                administration.component.name,
                administration.component.cui,
            )
            route_concept_id = (
                gateway.resolve_route_concept(administration.route)
                if administration.route
                else None
            )
            bundle.drug_exposures.append(
                omop_model.Drug_Exposure(
                    drug_exposure_id=next_drug_id,
                    person_id=config.person_id,
                    drug_exposure_start_date=administration.date,
                    drug_exposure_end_date=administration.date,
                    drug_exposure_start_datetime=as_datetime(administration.date),
                    drug_exposure_end_datetime=as_datetime(administration.date),
                    drug_concept_id=drug_concept_id,
                    drug_type_concept_id=drug_type_concept_id,
                    route_concept_id=route_concept_id,
                    drug_source_value=administration.component.name,
                    route_source_value=administration.route,
                    dose_unit_source_value=administration.dose_unit,
                    quantity=administration.dose,
                )
            )
            next_drug_id += 1
            continue
        if isinstance(administration, RadiationAdministration):
            procedure_type_concept_id = _record_type_concept_id(
                config, gateway, event_table="procedure_occurrence"
            )
            procedure_concept_id = gateway.resolve_component_concept(
                "procedure",
                administration.component.name,
                administration.component.cui,
            )
            bundle.procedures.append(
                omop_model.Procedure_Occurrence(
                    procedure_occurrence_id=next_procedure_id,
                    person_id=config.person_id,
                    procedure_concept_id=procedure_concept_id,
                    procedure_date=administration.date,
                    procedure_datetime=as_datetime(administration.date),
                    procedure_type_concept_id=procedure_type_concept_id,
                    procedure_source_value=administration.component.name,
                )
            )
            next_procedure_id += 1
            continue
        raise TypeError(
            "Unsupported administration for OMOP export: "
            f"{type(administration).__name__}"
        )
    return bundle


def export_case(
    case: Any,
    gateway: VocabularyGateway,
    config: OmopExportConfig | None = None,
) -> OmopExportBundle:
    """Compose patient, condition, episode, and treatment rows for one case."""

    if not hasattr(case, "person") or not hasattr(case, "condition_occurrence"):
        raise TypeError(
            "export_case() requires a CaseBundle-like object with person and "
            "condition_occurrence"
        )
    person = case.person
    condition = case.condition_occurrence
    # TODO(vocabulary-mappers): case-level clinical-field serialization still
    # relies on the active profile mapper context. Do not add fallback concept
    # IDs here; callers must install profile-scoped mappers or inject an
    # explicit export gateway.
    bundle = person.to_omop_bundle(include_related=True)
    treatment_episode = case.treatment_episode
    if treatment_episode is None:
        return bundle
    if treatment_episode.person_id != person.person_id:
        raise ValueError("treatment episode person_id does not match the case person")
    if treatment_episode.condition_occurrence_id != condition.condition_occurrence_id:
        raise ValueError(
            "treatment episode condition_occurrence_id does not match the case"
        )

    drug_events, procedure_events = treatment_episode.roll_events()
    if not drug_events and not procedure_events:
        return bundle
    if config is None:
        config = OmopExportConfig(person_id=person.person_id)
    if config.person_id != person.person_id:
        raise ValueError(
            "export configuration person_id does not match the case person"
        )
    event_bundle = _export_event_payloads(
        drug_events,
        procedure_events,
        gateway=gateway,
        config=config,
    )
    bundle.merge(event_bundle)
    return bundle


def _export_event_payloads(
    drug_events: list[dict[str, Any]],
    procedure_events: list[dict[str, Any]],
    *,
    gateway: VocabularyGateway,
    config: OmopExportConfig,
) -> OmopExportBundle:
    from omop_alchemy.cdm import model as omop_model

    drug_type_concept_id = (
        _record_type_concept_id(config, gateway, event_table="drug_exposure")
        if drug_events
        else None
    )
    procedure_type_concept_id = (
        _record_type_concept_id(config, gateway, event_table="procedure_occurrence")
        if procedure_events
        else None
    )

    bundle = OmopExportBundle()
    for offset, event in enumerate(drug_events):
        start_date = event["drug_exposure_start_date"]
        route = event.get("route_source_value")
        bundle.drug_exposures.append(
            omop_model.Drug_Exposure(
                drug_exposure_id=config.first_drug_exposure_id + offset,
                person_id=config.person_id,
                drug_exposure_start_date=start_date,
                drug_exposure_end_date=start_date,
                drug_exposure_start_datetime=as_datetime(start_date),
                drug_exposure_end_datetime=as_datetime(start_date),
                drug_concept_id=gateway.resolve_component_concept(
                    "drug",
                    event["drug_source_value"],
                    event.get("component_hemonc_cui"),
                ),
                drug_type_concept_id=drug_type_concept_id,
                route_concept_id=(
                    gateway.resolve_route_concept(route) if route else None
                ),
                drug_source_value=event["drug_source_value"],
                route_source_value=route,
                dose_unit_source_value=event.get("dose_unit"),
                quantity=event.get("dose"),
            )
        )
    for offset, event in enumerate(procedure_events):
        procedure_date = event["procedure_date"]
        bundle.procedures.append(
            omop_model.Procedure_Occurrence(
                procedure_occurrence_id=config.first_procedure_occurrence_id + offset,
                person_id=config.person_id,
                procedure_concept_id=gateway.resolve_component_concept(
                    "procedure",
                    event["procedure_source_value"],
                    event.get("component_hemonc_cui"),
                ),
                procedure_date=procedure_date,
                procedure_datetime=as_datetime(procedure_date),
                procedure_type_concept_id=procedure_type_concept_id,
                procedure_source_value=event["procedure_source_value"],
            )
        )
    return bundle
