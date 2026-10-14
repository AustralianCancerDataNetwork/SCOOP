"""Create a self-contained SQLite export of a routed synthetic cohort."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from omop_alchemy.maintenance.cli_schema_tables import create_missing_tables
from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session

from ..patients.build import (
    ScheduleBuildOptions,
    attach_case_schedule,
    build_case_bundle,
)
from ..patients.ids import (
    condition_id_seq,
    drug_exposure_id_seq,
    episode_id_seq,
    measurement_id_seq,
    observation_id_seq,
    person_id_seq,
    procedure_occurrence_id_seq,
)
from ..profiles.context import set_active_simulation_context
from ..profiles.manifest import load_routed_profile
from ..protocols.hemonc.gateway import SqlHemOncGateway
from ..schedule.rollout import ScheduleConfig, UnresolvedScheduleError
from ..vocabulary.gateway import DefaultVocabularyGateway
from .omop import OmopExportConfig, UnmappableComponentError, export_case

_BUNDLE_FIELDS = (
    "condition_occurrences",
    "measurements",
    "observations",
    "drug_exposures",
    "procedures",
    "episodes",
    "episode_events",
)
_BATCH_SIZE_DEFAULT = 200


@dataclass(frozen=True)
class SyntheticCohortOptions:
    """Inputs for a reproducible routed-profile SQLite export."""

    database_url: str
    hemonc_snapshot: Path
    output: Path
    profile_id: str = "nsclc"
    count: int = 10_000
    seed: int = 0
    max_cycles: int = 35
    batch_size: int = _BATCH_SIZE_DEFAULT
    connect_options: str | None = "-csearch_path=public,omop"
    profile_root: Path | None = None
    progress_callback: Callable[[int, int], None] | None = None


@dataclass
class _CaseBuildContext:
    runtime: Any
    hemonc_gateway: SqlHemOncGateway
    source_session: Session
    vocabulary_gateway: DefaultVocabularyGateway
    max_cycles: int
    episode_type_override: int | None
    sig_cache: dict[int, dict]


@dataclass
class _GenerationContext:
    source_engine: sa.Engine
    target_engine: sa.Engine
    runtime: Any
    vocabulary_gateway: DefaultVocabularyGateway
    episode_type_override: int | None


@dataclass
class _SampledCase:
    seed: int
    route: Any
    sample: dict
    case: Any


@dataclass
class _SourceEvidence:
    payloads: list[dict]
    drug_events: list[dict]
    procedure_events: list[dict]
    sigs: list[dict]
    sig_by_id: dict[int, dict]
    regimen_cuis: list[int]
    variant_cuis: list[int]
    hemonc_codes: set[str]


@dataclass
class _BundleExport:
    bundle: Any
    status: str
    reason: str | None
    event_rows: list[dict]
    payloads: list[dict]


def _create_export_tables(engine: sa.Engine) -> None:
    """Create cohort provenance tables beside the copied HemOnc and OMOP rows."""

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "create table scoop_generation_run ("
            "run_id integer primary key, profile_id text not null, "
            "requested_cases integer not null, seed_start integer not null, "
            "seed_end integer not null, max_cycles integer not null, "
            "hemonc_snapshot text not null, created_at text not null)"
        )
        connection.exec_driver_sql(
            "create table scoop_case_metadata ("
            "person_id integer primary key, seed integer not null, profile_id text not null, "
            "routed_profile text, regimen_type text, schedule_status text, schedule_reason text, "
            "export_status text not null, export_reason text, schedule_policy text not null, "
            "course_end_reason text, max_cycle_observed integer, course_start_date text, "
            "course_end_date text, source_sig_ids_json text not null, "
            "source_regimen_cuis_json text not null, source_variant_cuis_json text not null, "
            "graph_sample_json text not null, treatment_episode_json text)"
        )
        connection.exec_driver_sql(
            "create table scoop_hemonc_event ("
            "event_table text not null, event_id integer not null, person_id integer not null, "
            "source_sig_id integer, component_hemonc_cui integer, component_source_value text, "
            "phase_id text, cycle_number integer, event_date text, regimen_cui integer, "
            "variant_cui integer, payload_json text not null, "
            "primary key(event_table,event_id))"
        )


def _bundle_rows(bundle: Any) -> list[Any]:
    rows = [bundle.person] if bundle.person is not None else []
    for field in _BUNDLE_FIELDS:
        rows.extend(getattr(bundle, field))
    return rows


def _mapped_concept_ids(row: Any) -> set[int]:
    return {
        value
        for column in sa.inspect(row).mapper.columns
        if column.name.endswith("_concept_id")
        if isinstance((value := getattr(row, column.name, None)), int) and value > 0
    }


def _source_sig(session: Session, sig_id: int | None, cache: dict[int, dict]) -> dict:
    if sig_id is None:
        return {}
    if sig_id not in cache:
        statement = sa.text(
            "select id, component, component_cui, cyclesigs, timing_sequence, "
            "regimen_cui, variant_cui from sigs where id=:id"
        )
        row = session.execute(statement, {"id": sig_id}).mappings().one_or_none()
        cache[sig_id] = dict(row) if row else {}
    return cache[sig_id]


def _course_end(
    events: list[dict], sig_rows: list[dict], max_cycles: int
) -> tuple[int | None, str]:
    cycles = [
        event.get("cycle_number")
        for event in events
        if isinstance(event.get("cycle_number"), int)
    ]
    maximum = max(cycles, default=None)
    repeated = any("(+" in str(row.get("timing_sequence") or "") for row in sig_rows)
    if maximum == max_cycles and repeated:
        return maximum, "cycle_horizon_reached"
    if repeated:
        return maximum, "sampled_repeat_stop"
    if events:
        return maximum, "source_defined_end"
    return maximum, "no_dated_schedule"


def _insert_rows(engine: sa.Engine, rows: list[Any]) -> None:
    if not rows:
        return
    with Session(engine) as session:
        session.add_all(rows)
        session.commit()


def _persist_provenance(
    engine: sa.Engine,
    metadata_rows: list[dict],
    event_rows: list[dict],
) -> None:
    with engine.begin() as connection:
        if metadata_rows:
            connection.execute(
                sa.text(
                    "insert into scoop_case_metadata values ("
                    ":person_id,:seed,:profile_id,:routed_profile,:regimen_type,"
                    ":schedule_status,:schedule_reason,:export_status,:export_reason,"
                    ":schedule_policy,:course_end_reason,:max_cycle_observed,"
                    ":course_start_date,:course_end_date,:source_sig_ids_json,"
                    ":source_regimen_cuis_json,:source_variant_cuis_json,"
                    ":graph_sample_json,:treatment_episode_json)"
                ),
                metadata_rows,
            )
        if event_rows:
            connection.execute(
                sa.text(
                    "insert into scoop_hemonc_event values ("
                    ":event_table,:event_id,:person_id,:source_sig_id,"
                    ":component_hemonc_cui,:component_source_value,:phase_id,"
                    ":cycle_number,:event_date,:regimen_cui,:variant_cui,:payload_json)"
                ),
                event_rows,
            )


def _source_rows(
    connection: sa.Connection,
    table: str,
    key: str,
    ids: set[Any],
    where: str = "",
) -> list[dict]:
    if not ids:
        return []
    statement = sa.text(f'select * from "{table}" where "{key}" in :ids {where}')
    statement = statement.bindparams(sa.bindparam("ids", expanding=True))
    return [
        dict(row)
        for row in connection.execute(statement, {"ids": sorted(ids)}).mappings()
    ]


def _copy_omop_references(
    source_engine: sa.Engine,
    target_engine: sa.Engine,
    concept_ids: set[int],
    hemonc_codes: set[str],
) -> None:
    concepts, relationships = _read_referenced_concepts(
        source_engine,
        concept_ids,
        hemonc_codes,
    )
    metadata = MetaData()
    tables = {
        name: Table(name, metadata, autoload_with=target_engine)
        for name in (
            "concept",
            "concept_relationship",
            "vocabulary",
            "domain",
            "concept_class",
            "relationship",
        )
    }
    with target_engine.begin() as target:
        target.execute(tables["concept"].insert(), concepts)
        if relationships:
            target.execute(tables["concept_relationship"].insert(), relationships)
    _close_metadata_dependencies(source_engine, target_engine, tables)


def _read_referenced_concepts(
    source_engine: sa.Engine,
    concept_ids: set[int],
    hemonc_codes: set[str],
) -> tuple[list[dict], list[dict]]:
    with source_engine.connect() as source:
        source_hemonc = _source_rows(
            source,
            "concept",
            "concept_code",
            hemonc_codes,
            "and vocabulary_id='HemOnc'",
        )
        source_ids = {row["concept_id"] for row in source_hemonc}
        relationships = []
        if source_ids:
            statement = sa.text(
                "select concept_id_1, concept_id_2, relationship_id, valid_start_date, "
                "valid_end_date, invalid_reason from concept_relationship "
                "where concept_id_1 in :ids and relationship_id='Maps to' "
                "and invalid_reason is null"
            ).bindparams(sa.bindparam("ids", expanding=True))
            relationships = [
                dict(row)
                for row in source.execute(
                    statement, {"ids": sorted(source_ids)}
                ).mappings()
            ]
        all_ids = (
            set(concept_ids)
            | source_ids
            | {row["concept_id_2"] for row in relationships}
        )
        concepts = _source_rows(source, "concept", "concept_id", all_ids)
        missing = sorted(all_ids - {row["concept_id"] for row in concepts})
        if missing:
            raise RuntimeError(
                f"Source OMOP database is missing concepts: {missing[:20]}"
            )
        concepts.append(
            dict(
                source.execute(sa.text("select * from concept where concept_id=0"))
                .mappings()
                .one()
            )
        )
    return concepts, relationships


def _close_metadata_dependencies(
    source_engine: sa.Engine,
    target_engine: sa.Engine,
    tables: dict[str, Table],
) -> None:
    with source_engine.connect() as source, target_engine.begin() as target:
        for _ in range(10):
            rows = _fetch_metadata_rows(source, target, tables)
            if not _insert_metadata_rows(source, target, tables, rows):
                return
    raise RuntimeError("OMOP vocabulary metadata closure did not converge")


def _fetch_metadata_rows(
    source: sa.Connection,
    target: sa.Connection,
    tables: dict[str, Table],
) -> dict[str, list[dict]]:
    concepts = list(target.execute(select(tables["concept"])).mappings())
    rows = {
        "vocabulary": _source_rows(
            source,
            "vocabulary",
            "vocabulary_id",
            {row["vocabulary_id"] for row in concepts},
        ),
        "domain": _source_rows(
            source, "domain", "domain_id", {row["domain_id"] for row in concepts}
        ),
        "concept_class": _source_rows(
            source,
            "concept_class",
            "concept_class_id",
            {row["concept_class_id"] for row in concepts},
        ),
    }
    relationship_ids = {
        row["relationship_id"]
        for row in target.execute(select(tables["concept_relationship"])).mappings()
    }
    existing = list(target.execute(select(tables["relationship"])).mappings())
    relationship_ids.update(row["relationship_id"] for row in existing)
    relationship_ids.update(
        row["reverse_relationship_id"]
        for row in existing
        if row["reverse_relationship_id"]
    )
    rows["relationship"] = _source_rows(
        source, "relationship", "relationship_id", relationship_ids
    )
    return rows


def _insert_metadata_rows(
    source: sa.Connection,
    target: sa.Connection,
    tables: dict[str, Table],
    rows: dict[str, list[dict]],
) -> int:
    additions = 0
    for name, values in rows.items():
        if values:
            table = tables[name]
            before = target.execute(
                select(sa.func.count()).select_from(table)
            ).scalar_one()
            target.execute(table.insert().prefix_with("OR IGNORE"), values)
            after = target.execute(
                select(sa.func.count()).select_from(table)
            ).scalar_one()
            additions += after - before

    needed = {0}
    for name, key in (
        ("vocabulary", "vocabulary_concept_id"),
        ("domain", "domain_concept_id"),
        ("concept_class", "concept_class_concept_id"),
        ("relationship", "relationship_concept_id"),
    ):
        needed.update(row[key] for row in rows[name] if row.get(key) is not None)
    for row in target.execute(select(tables["concept_relationship"])).mappings():
        needed.update((row["concept_id_1"], row["concept_id_2"]))
    existing_ids = {
        row[0] for row in target.execute(select(tables["concept"].c.concept_id))
    }
    concepts = _source_rows(source, "concept", "concept_id", needed - existing_ids)
    if concepts:
        before = target.execute(
            select(sa.func.count()).select_from(tables["concept"])
        ).scalar_one()
        target.execute(tables["concept"].insert().prefix_with("OR IGNORE"), concepts)
        after = target.execute(
            select(sa.func.count()).select_from(tables["concept"])
        ).scalar_one()
        additions += after - before
    return additions


def _episode_type_override(source_engine: sa.Engine) -> int | None:
    with source_engine.connect() as connection:
        if connection.execute(
            sa.text("select 1 from concept where concept_id=32545")
        ).first():
            return None
        result = connection.execute(
            sa.text(
                "select concept_id from concept where concept_name='EHR episode record' "
                "and vocabulary_id='Type Concept' and invalid_reason is null "
                "order by concept_id limit 1"
            )
        ).scalar_one_or_none()
    return int(result) if result is not None else None


def _reset_sequences() -> None:
    for sequence in (
        person_id_seq,
        condition_id_seq,
        measurement_id_seq,
        observation_id_seq,
        episode_id_seq,
        drug_exposure_id_seq,
        procedure_occurrence_id_seq,
    ):
        sequence.reset()


def _sample_case(seed: int, context: _CaseBuildContext) -> _SampledCase:
    route = context.runtime.sample_routed_patient(seed=seed)
    active = (
        context.runtime.entry_context
        if route.tail_graph_name is None
        else context.runtime.tail_contexts[route.tail_graph_name]
    )
    set_active_simulation_context(active)
    sample = route.final_sample
    case = build_case_bundle(sample, include_treatment_episode=True)
    treatment = case.treatment_episode
    if treatment is not None:
        try:
            attach_case_schedule(
                treatment,
                case.condition_occurrence,
                case.person,
                ScheduleBuildOptions(
                    gateway=context.hemonc_gateway,
                    seed=seed,
                    config=ScheduleConfig(max_cycles=context.max_cycles),
                ),
            )
        except UnresolvedScheduleError as exc:
            treatment.schedule_status = "unresolved"
            treatment.schedule_reason = exc.reason
    return _SampledCase(seed, route, sample, case)


def _source_evidence(
    sampled: _SampledCase, context: _CaseBuildContext
) -> _SourceEvidence:
    treatment = sampled.case.treatment_episode
    drug_events = list(treatment.drug_exposures) if treatment else []
    procedure_events = list(treatment.procedures) if treatment else []
    payloads = [*drug_events, *procedure_events]
    sigs = [
        _source_sig(
            context.source_session, event.get("source_sig_id"), context.sig_cache
        )
        for event in payloads
    ]
    sigs = [row for row in sigs if row]
    sig_by_id = {row["id"]: row for row in sigs}
    regimen_cuis = sorted(
        {row["regimen_cui"] for row in sigs if row.get("regimen_cui") is not None}
    )
    variant_cuis = sorted(
        {row["variant_cui"] for row in sigs if row.get("variant_cui") is not None}
    )
    codes = {
        str(value)
        for value in (
            [event.get("component_hemonc_cui") for event in payloads]
            + regimen_cuis
            + variant_cuis
        )
        if value is not None
    }
    return _SourceEvidence(
        payloads,
        drug_events,
        procedure_events,
        sigs,
        sig_by_id,
        regimen_cuis,
        variant_cuis,
        codes,
    )


def _export_bundle(
    sampled: _SampledCase, evidence: _SourceEvidence, context: _CaseBuildContext
) -> _BundleExport:
    case = sampled.case
    try:
        bundle = export_case(
            case,
            context.vocabulary_gateway,
            OmopExportConfig(person_id=case.person.person_id),
        )
        status, reason = "exported", None
    except (UnmappableComponentError, ValueError) as exc:
        status, reason = "patient_only", str(exc)
        if sampled.case.treatment_episode is not None:
            sampled.case.treatment_episode.drug_exposures = []
            sampled.case.treatment_episode.procedures = []
        bundle = export_case(
            case,
            context.vocabulary_gateway,
            OmopExportConfig(person_id=case.person.person_id),
        )
        evidence.payloads.clear()
        evidence.drug_events.clear()
        evidence.procedure_events.clear()

    for episode in bundle.episodes:
        if (
            episode.episode_type_concept_id == 32545
            and context.episode_type_override is not None
        ):
            episode.episode_type_concept_id = context.episode_type_override
        if (
            sampled.case.treatment_episode
            and episode.episode_source_value
            == sampled.case.treatment_episode.regimen_type
            and evidence.payloads
        ):
            episode.episode_end_date = max(
                event.get("event_date") or event.get("procedure_date")
                for event in evidence.payloads
            )
            episode.episode_end_datetime = datetime.combine(
                episode.episode_end_date, time.min
            )

    links = _event_links(sampled, evidence, bundle, status)
    return _BundleExport(bundle, status, reason, links, evidence.payloads)


def _event_links(
    sampled: _SampledCase, evidence: _SourceEvidence, bundle: Any, status: str
) -> list[dict]:
    if status != "exported" or not evidence.payloads:
        return []
    person_id = sampled.case.person.person_id
    links = [
        _event_link(
            "drug_exposure",
            row.drug_exposure_id,
            person_id,
            event,
            evidence.sig_by_id.get(event.get("source_sig_id"), {}),
        )
        for event, row in zip(
            evidence.drug_events,
            bundle.drug_exposures,
            strict=True,
        )
    ]
    links.extend(
        _event_link(
            "procedure_occurrence",
            row.procedure_occurrence_id,
            person_id,
            event,
            evidence.sig_by_id.get(event.get("source_sig_id"), {}),
        )
        for event, row in zip(
            evidence.procedure_events,
            bundle.procedures,
            strict=True,
        )
    )
    return links


def _metadata_row(
    sampled: _SampledCase,
    evidence: _SourceEvidence,
    exported: _BundleExport,
    context: _CaseBuildContext,
) -> dict:
    dates = [
        event.get("event_date") or event.get("procedure_date")
        for event in exported.payloads
    ]
    dates = [value for value in dates if value is not None]
    max_cycle, end_reason = _course_end(
        exported.payloads, evidence.sigs, context.max_cycles
    )
    treatment = sampled.case.treatment_episode
    return {
        "person_id": sampled.case.person.person_id,
        "seed": sampled.seed,
        "profile_id": context.runtime.profile_id,
        "routed_profile": sampled.route.tail_graph_name,
        "regimen_type": treatment.regimen_type if treatment else None,
        "schedule_status": treatment.schedule_status if treatment else "no_treatment",
        "schedule_reason": treatment.schedule_reason if treatment else None,
        "export_status": exported.status,
        "export_reason": exported.reason,
        "schedule_policy": f"source-defined events; repeated instructions capped at {context.max_cycles} cycles",
        "course_end_reason": end_reason,
        "max_cycle_observed": max_cycle,
        "course_start_date": str(min(dates)) if dates else None,
        "course_end_date": str(max(dates)) if dates else None,
        "source_sig_ids_json": json.dumps(sorted(evidence.sig_by_id)),
        "source_regimen_cuis_json": json.dumps(evidence.regimen_cuis),
        "source_variant_cuis_json": json.dumps(evidence.variant_cuis),
        "graph_sample_json": json.dumps(sampled.sample, default=str, sort_keys=True),
        "treatment_episode_json": json.dumps(
            treatment.model_dump(), default=str, sort_keys=True
        )
        if treatment
        else None,
    }


def _export_one_case(
    seed: int, context: _CaseBuildContext
) -> tuple[list[Any], dict, list[dict], set[int], set[str]]:
    sampled = _sample_case(seed, context)
    evidence = _source_evidence(sampled, context)
    exported = _export_bundle(sampled, evidence, context)
    rows = _bundle_rows(exported.bundle)
    concepts = set().union(*(_mapped_concept_ids(row) for row in rows))
    metadata = _metadata_row(sampled, evidence, exported, context)
    return rows, metadata, exported.event_rows, concepts, evidence.hemonc_codes


def _event_link(
    table: str, event_id: int, person_id: int, event: dict, sig: dict
) -> dict:
    date = (
        event.get("event_date")
        or event.get("drug_exposure_start_date")
        or event.get("procedure_date")
    )
    cycle = (
        event.get("cycle_number")
        if table == "drug_exposure"
        else event.get("fraction_number")
    )
    return {
        "event_table": table,
        "event_id": event_id,
        "person_id": person_id,
        "source_sig_id": event.get("source_sig_id"),
        "component_hemonc_cui": event.get("component_hemonc_cui"),
        "component_source_value": event.get("component_source_value"),
        "phase_id": event.get("phase_id"),
        "cycle_number": cycle,
        "event_date": str(date) if date is not None else None,
        "regimen_cui": sig.get("regimen_cui"),
        "variant_cui": sig.get("variant_cui"),
        "payload_json": json.dumps(event, default=str, sort_keys=True),
    }


def export_synthetic_cohort(options: SyntheticCohortOptions) -> dict[str, Any]:
    """Build and validate a portable SQLite cohort from a routed SCOOP profile."""

    if options.count < 1:
        raise ValueError("count must be greater than zero")
    if options.seed < 0:
        raise ValueError("seed must be non-negative")
    if options.max_cycles < 1:
        raise ValueError("max_cycles must be greater than zero")
    if options.batch_size < 1:
        raise ValueError("batch_size must be greater than zero")

    snapshot = options.hemonc_snapshot.expanduser().resolve()
    destination = options.output.expanduser().resolve()
    if not snapshot.is_file():
        raise FileNotFoundError(f"HemOnc SQLite snapshot was not found: {snapshot}")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f".{destination.name}.partial")
    if staging.exists():
        raise FileExistsError(f"Refusing to overwrite staging database: {staging}")

    source_args = (
        {"connect_args": {"options": options.connect_options}}
        if options.connect_options
        else {}
    )
    source_engine = sa.create_engine(options.database_url, **source_args)
    target_engine: sa.Engine | None = None
    try:
        _generate_staging_database(
            source_engine,
            options,
            staging,
        )
        target_engine = sa.create_engine(f"sqlite:///{staging}")
        _finish_database(target_engine, options, snapshot)
        target_engine.dispose()
        target_engine = None
        source_engine.dispose()
        staging.replace(destination)
        return _summarize_database(destination)
    except BaseException:
        if target_engine is not None:
            target_engine.dispose()
        source_engine.dispose()
        if staging.exists():
            staging.unlink()
        raise


def _generate_staging_database(
    source_engine: sa.Engine,
    options: SyntheticCohortOptions,
    staging: Path,
) -> None:
    source = sqlite3.connect(f"file:{options.hemonc_snapshot}?mode=ro", uri=True)
    target = sqlite3.connect(staging)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()

    root = options.profile_root or Path(__file__).resolve().parents[1]
    target_engine = sa.create_engine(f"sqlite:///{staging}")
    create_missing_tables(target_engine)
    _create_export_tables(target_engine)
    vocabulary_gateway = DefaultVocabularyGateway(
        root,
        engine_factory=lambda: source_engine,
    )
    runtime = load_routed_profile(
        options.profile_id,
        base_dir=root,
        vocabulary_gateway=vocabulary_gateway,
    )
    context = _GenerationContext(
        source_engine,
        target_engine,
        runtime,
        vocabulary_gateway,
        _episode_type_override(source_engine),
    )
    concept_ids, hemonc_codes = _write_cases(context, options)
    try:
        _copy_omop_references(source_engine, target_engine, concept_ids, hemonc_codes)
    finally:
        target_engine.dispose()


def _write_cases(
    generation: _GenerationContext,
    options: SyntheticCohortOptions,
) -> tuple[set[int], set[str]]:
    metadata_rows: list[dict] = []
    event_rows: list[dict] = []
    omop_rows: list[Any] = []
    concept_ids: set[int] = set()
    hemonc_codes: set[str] = set()
    sig_cache: dict[int, dict] = {}
    _reset_sequences()

    with Session(generation.source_engine) as source_session:
        hemonc_gateway = SqlHemOncGateway(session=source_session)
        context = _CaseBuildContext(
            generation.runtime,
            hemonc_gateway,
            source_session,
            generation.vocabulary_gateway,
            options.max_cycles,
            generation.episode_type_override,
            sig_cache,
        )
        for index in range(options.count):
            rows, metadata_row, links, concepts, codes = _export_one_case(
                options.seed + index,
                context,
            )
            omop_rows.extend(rows)
            metadata_rows.append(metadata_row)
            event_rows.extend(links)
            concept_ids.update(concepts)
            hemonc_codes.update(codes)
            if len(metadata_rows) >= options.batch_size:
                _flush_batch(
                    generation.target_engine, omop_rows, metadata_rows, event_rows
                )
                omop_rows.clear()
                metadata_rows.clear()
                event_rows.clear()
            completed = index + 1
            if options.progress_callback is not None:
                options.progress_callback(completed, options.count)
            elif completed % 1000 == 0 or completed == options.count:
                print(f"Built {completed:,}/{options.count:,} patients")
    _flush_batch(generation.target_engine, omop_rows, metadata_rows, event_rows)
    return concept_ids, hemonc_codes


def _flush_batch(
    engine: sa.Engine,
    rows: list[Any],
    metadata_rows: list[dict],
    event_rows: list[dict],
) -> None:
    _insert_rows(engine, rows)
    _persist_provenance(engine, metadata_rows, event_rows)


def _finish_database(
    target_engine: sa.Engine,
    options: SyntheticCohortOptions,
    snapshot: Path,
) -> None:
    with target_engine.begin() as connection:
        connection.execute(
            sa.text(
                "insert into scoop_generation_run values (1,:profile,:count,:start,:end,:cycles,:snapshot,datetime('now'))"
            ),
            {
                "profile": options.profile_id,
                "count": options.count,
                "start": options.seed,
                "end": options.seed + options.count - 1,
                "cycles": options.max_cycles,
                "snapshot": str(snapshot),
            },
        )
        connection.exec_driver_sql(
            "create table scoop_distribution_counts as select routed_profile,regimen_type,"
            "schedule_status,count(*) as patient_count from scoop_case_metadata "
            "group by routed_profile,regimen_type,schedule_status"
        )
    counts = _summarize_database(target_engine.url.database)
    if any(
        counts["counts"].get(table) != options.count
        for table in ("person", "condition_occurrence", "scoop_case_metadata")
    ):
        raise RuntimeError(
            "The SQLite export does not contain the requested number of patients and conditions"
        )
    if (
        counts["max_cycle_observed"] is not None
        and counts["max_cycle_observed"] > options.max_cycles
    ):
        raise RuntimeError(
            "A generated treatment course exceeded the configured cycle limit"
        )
    if counts["integrity"] != "ok" or counts["foreign_key_errors"]:
        raise RuntimeError(
            f"SQLite verification failed: integrity={counts['integrity']}, "
            f"foreign_keys={counts['foreign_key_errors']}"
        )


def _summarize_database(path: str | Path) -> dict[str, Any]:
    connection = sqlite3.connect(path)
    try:
        table_names = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
        counts = _table_counts(connection, table_names)
        distributions = _distribution_counts(connection)
        integrity = connection.execute("pragma integrity_check").fetchone()[0]
        foreign_key_errors = len(
            connection.execute("pragma foreign_key_check").fetchall()
        )
    finally:
        connection.close()
    return {
        "path": str(path),
        "size_bytes": Path(path).stat().st_size,
        "counts": counts,
        **distributions,
        "integrity": integrity,
        "foreign_key_errors": foreign_key_errors,
    }


def _table_counts(
    connection: sqlite3.Connection, table_names: set[str]
) -> dict[str, int]:
    expected = {
        "person",
        "condition_occurrence",
        "measurement",
        "observation",
        "drug_exposure",
        "procedure_occurrence",
        "episode",
        "episode_event",
        "concept",
        "concept_relationship",
        "sigs",
        "variants",
        "regimens",
        "drugs",
        "scoop_case_metadata",
        "scoop_hemonc_event",
        "scoop_distribution_counts",
    }
    return {
        name: connection.execute(f'select count(*) from "{name}"').fetchone()[0]
        for name in sorted(expected & table_names)
    }


def _distribution_counts(connection: sqlite3.Connection) -> dict[str, Any]:
    route_counts = connection.execute(
        "select routed_profile,count(*) from scoop_case_metadata group by routed_profile order by routed_profile"
    ).fetchall()
    status_counts = connection.execute(
        "select schedule_status,count(*) from scoop_case_metadata group by schedule_status order by schedule_status"
    ).fetchall()
    end_counts = connection.execute(
        "select course_end_reason,count(*) from scoop_case_metadata group by course_end_reason order by course_end_reason"
    ).fetchall()
    max_cycle = connection.execute(
        "select max(max_cycle_observed) from scoop_case_metadata"
    ).fetchone()[0]
    return {
        "route_counts": route_counts,
        "schedule_status_counts": status_counts,
        "course_end_counts": end_counts,
        "max_cycle_observed": max_cycle,
    }
