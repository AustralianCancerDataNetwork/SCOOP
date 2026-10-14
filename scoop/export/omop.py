from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from typing import Any

import sqlalchemy as sa

from ..patients.ids import drug_exposure_id_seq, procedure_occurrence_id_seq
from ..schedule.rollout import (
    ProtocolSchedule,
    RadiationAdministration,
    SystemicAdministration,
    UnresolvedScheduleError,
)
from ..vocabulary.gateway import VocabularyGateway

_EVENT_IDS_PER_SEED = 100_000


def as_datetime(value: date | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min)


def require_populated_columns[OmopRow](row: OmopRow) -> OmopRow:
    """Raise if any NOT-NULL column on this ORM row is unset.

    Reads the requirement straight off the row's own mapper, so it stays in
    sync automatically as the CDM schema changes. Returns the row unchanged,
    so a `to_omop_row()` can wrap its return value with this.
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

    def merge(self, other: OmopExportBundle) -> OmopExportBundle:
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


@dataclass(frozen=True)
class ExportRejection:
    case_index: int
    seed: int
    regimen: str | None
    reason: str
    component_name: str | None = None
    hemonc_cui: int | None = None


@dataclass
class CohortExportResult:
    bundles: list[OmopExportBundle] = field(default_factory=list)
    rejections: list[ExportRejection] = field(default_factory=list)


def export_cohort(
    count: int,
    build_case: Callable[[int], Any],
    gateway: VocabularyGateway,
) -> CohortExportResult:
    """Build and export seeded cases, retaining per-case schedule and mapping failures."""

    if count < 0:
        raise ValueError("count must be non-negative")
    result = CohortExportResult()
    for case_index in range(count):
        case = None
        regimen = None
        try:
            case = build_case(case_index)
            regimen = getattr(case.treatment_episode, "regimen_type", None)
            result.bundles.append(export_case(case, gateway))
        except UnresolvedScheduleError as exc:
            result.rejections.append(
                ExportRejection(
                    case_index,
                    exc.case_seed if exc.case_seed is not None else case_index,
                    exc.regimen or regimen,
                    str(exc),
                )
            )
        except (UnmappableComponentError, ValueError) as exc:
            treatment = getattr(case, "treatment_episode", None)
            case_seed = getattr(treatment, "schedule_seed", None)
            if case_seed is None:
                case_seed = getattr(case, "seed", case_index)
            result.rejections.append(
                ExportRejection(
                    case_index,
                    case_seed,
                    regimen,
                    str(exc),
                    component_name=getattr(exc, "name", None),
                    hemonc_cui=getattr(exc, "hemonc_cui", None),
                )
            )
    return result


def _treatment_episode_rows(
    treatment_episode: Any,
    event_bundle: OmopExportBundle,
) -> OmopExportBundle:
    from ..patients.episode_base import Episode_Event
    from ..patients.ids import episode_id_seq
    from ..patients.treatment_episode import TreatmentEpisodeRecord
    from ..vocabulary.gateway import get_active_vocabulary_mappers

    mapper = get_active_vocabulary_mappers().episode_mapper("treatment_episode")
    episode_id = episode_id_seq.next()
    event_rows = (
        (row.drug_exposure_id, "drug_exposure")
        for row in event_bundle.drug_exposures
    )
    event_rows = list(event_rows) + [
        (row.procedure_occurrence_id, "procedure_occurrence")
        for row in event_bundle.procedures
    ]
    events = [
        Episode_Event(
            episode_id=episode_id,
            event_id=event_id,
            episode_event_field_concept_id=mapper.event_field_concept_id(table),
        )
        for event_id, table in event_rows
    ]
    if any(event.episode_event_field_concept_id is None for event in events):
        raise ValueError(
            "The treatment_episode mapper must declare event field concepts "
            "for exported event tables"
        )
    dates = [
        row.drug_exposure_start_date for row in event_bundle.drug_exposures
    ] + [row.procedure_date for row in event_bundle.procedures]
    if not dates:
        return OmopExportBundle()
    record = TreatmentEpisodeRecord(
        person_id=treatment_episode.person_id,
        episode_id=episode_id,
        episode_object=treatment_episode,
        episode_object_concept_id=mapper.episode_concept_id,
        episode_start_date=min(dates),
        episode_source_value=treatment_episode.regimen_type,
        episode_events=events,
    )
    bundle = record.to_omop_bundle()
    bundle.episodes = [require_populated_columns(row) for row in bundle.episodes]
    bundle.episode_events = [
        require_populated_columns(row) for row in bundle.episode_events
    ]
    return bundle


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
    first_drug_exposure_id: int | None = None
    first_procedure_occurrence_id: int | None = None

    def __post_init__(self) -> None:
        if self.person_id < 1:
            raise ValueError("person_id must be positive")
        if (
            self.first_drug_exposure_id is not None
            and self.first_drug_exposure_id < 1
        ):
            raise ValueError("first_drug_exposure_id must be positive")
        if (
            self.first_procedure_occurrence_id is not None
            and self.first_procedure_occurrence_id < 1
        ):
            raise ValueError("first_procedure_occurrence_id must be positive")


class UnmappableComponentError(LookupError):
    def __init__(self, name: str, hemonc_cui: int | None):
        self.name = name
        self.hemonc_cui = hemonc_cui
        super().__init__(f"Cannot map component {name!r} (HemOnc CUI {hemonc_cui})")


def _resolve_component(
    gateway: VocabularyGateway,
    domain: str,
    name: str,
    cui: int | None,
) -> int:
    try:
        return gateway.resolve_component_concept(domain, name, cui)
    except LookupError as exc:
        raise UnmappableComponentError(name, cui) from exc


def _next_id(first: int | None, offset: int, sequence: Any) -> int:
    return first + offset if first is not None else sequence.next()


def _seeded_event_id_start(seed: int) -> int:
    encoded_seed = seed * 2 if seed >= 0 else -seed * 2 - 1
    start = encoded_seed * _EVENT_IDS_PER_SEED + 1
    if start + _EVENT_IDS_PER_SEED > 2_147_483_647:
        raise ValueError("case seed is too large for deterministic OMOP event IDs")
    return start


def _seed_event_ids(config: OmopExportConfig, seed: int | None) -> OmopExportConfig:
    if seed is None:
        return config
    start = _seeded_event_id_start(seed)
    return replace(
        config,
        first_drug_exposure_id=config.first_drug_exposure_id or start,
        first_procedure_occurrence_id=config.first_procedure_occurrence_id or start,
    )


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


def _drug_exposure_row(
    event: dict[str, Any],
    offset: int,
    type_concept_id: int,
    gateway: VocabularyGateway,
    config: OmopExportConfig,
) -> Any:
    from omop_alchemy.cdm import model as omop_model

    start_date = event["drug_exposure_start_date"]
    route = event.get("route_source_value")
    return require_populated_columns(
        omop_model.Drug_Exposure(
            drug_exposure_id=_next_id(
                config.first_drug_exposure_id, offset, drug_exposure_id_seq
            ),
            person_id=config.person_id,
            drug_exposure_start_date=start_date,
            drug_exposure_end_date=start_date,
            drug_exposure_start_datetime=as_datetime(start_date),
            drug_exposure_end_datetime=as_datetime(start_date),
            drug_concept_id=_resolve_component(
                gateway,
                "drug",
                event["drug_source_value"],
                event.get("component_hemonc_cui"),
            ),
            drug_type_concept_id=type_concept_id,
            route_concept_id=gateway.resolve_route_concept(route),
            drug_source_value=event["drug_source_value"],
            route_source_value=route,
            dose_unit_source_value=event.get("dose_unit"),
            quantity=event.get("dose"),
        )
    )


def _procedure_occurrence_row(
    event: dict[str, Any],
    offset: int,
    type_concept_id: int,
    gateway: VocabularyGateway,
    config: OmopExportConfig,
) -> Any:
    from omop_alchemy.cdm import model as omop_model

    procedure_date = event["procedure_date"]
    return require_populated_columns(
        omop_model.Procedure_Occurrence(
            procedure_occurrence_id=_next_id(
                config.first_procedure_occurrence_id,
                offset,
                procedure_occurrence_id_seq,
            ),
            person_id=config.person_id,
            procedure_concept_id=_resolve_component(
                gateway,
                "procedure",
                event["procedure_source_value"],
                event.get("component_hemonc_cui"),
            ),
            procedure_date=procedure_date,
            procedure_datetime=as_datetime(procedure_date),
            procedure_type_concept_id=type_concept_id,
            procedure_source_value=event["procedure_source_value"],
        )
    )


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
    config = _seed_event_ids(config, schedule.seed)

    drug_events: list[dict[str, Any]] = []
    procedure_events: list[dict[str, Any]] = []
    for administration in schedule.administrations:
        if administration.date is None or administration.timing_status.startswith("unresolved:"):
            raise ValueError(
                f"Cannot export unresolved administration for sig {administration.source_sig_id}: "
                f"{administration.timing_status}"
            )
        if isinstance(administration, SystemicAdministration):
            drug_events.append(
                {
                    "drug_exposure_start_date": administration.date,
                    "drug_source_value": administration.component.name,
                    "component_hemonc_cui": administration.component.cui,
                    "route_source_value": administration.route,
                    "dose_unit": administration.dose_unit,
                    "dose": administration.dose,
                }
            )
            continue
        if isinstance(administration, RadiationAdministration):
            procedure_events.append(
                {
                    "procedure_date": administration.date,
                    "procedure_source_value": administration.component.name,
                    "component_hemonc_cui": administration.component.cui,
                }
            )
            continue
        raise TypeError(
            "Unsupported administration for OMOP export: "
            f"{type(administration).__name__}"
        )
    return _export_event_payloads(
        drug_events, procedure_events, gateway=gateway, config=config
    )


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
    config = _seed_event_ids(
        config, getattr(treatment_episode, "schedule_seed", None)
    )
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
    bundle.merge(_treatment_episode_rows(treatment_episode, event_bundle))
    return bundle


def _export_event_payloads(
    drug_events: list[dict[str, Any]],
    procedure_events: list[dict[str, Any]],
    *,
    gateway: VocabularyGateway,
    config: OmopExportConfig,
) -> OmopExportBundle:
    for event in [*drug_events, *procedure_events]:
        if event.get("timing_status", "").startswith("unresolved:"):
            raise ValueError(f"Cannot export unresolved administration: {event['timing_status']}")

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

    return OmopExportBundle(
        drug_exposures=[
            _drug_exposure_row(event, offset, drug_type_concept_id, gateway, config)
            for offset, event in enumerate(drug_events)
        ],
        procedures=[
            _procedure_occurrence_row(
                event, offset, procedure_type_concept_id, gateway, config
            )
            for offset, event in enumerate(procedure_events)
        ],
    )
