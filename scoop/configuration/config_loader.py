from __future__ import annotations

"""Profile and graph loading utilities.

This module is the main entry point for:
- reading profile manifests from ``configuration/profiles/*/profile.yaml``
- turning manifest entries into concrete graph/registry config objects
- loading a single graph context for sampling
- loading a routed entry-plus-tail runtime for multi-graph disease profiles
"""

import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from ..utils import ConceptMapper, MeasurementMapper, ObservationMapper
from ..utils.modifier_map import ModifierMapper
from .paths import (
    DEFAULT_PROFILE_CONCEPT_MAP_RELPATH,
    DEFAULT_PROFILE_MEASUREMENT_MAP_RELPATH,
    DEFAULT_PROFILE_OBSERVATION_MAP_RELPATH,
    MAPPERS_DIRNAME,
    MODIFIER_MAP_FILENAME,
    profile_artifact_relpath,
    profile_manifest_path,
    PROFILES_DIRNAME,
    resolve_config_root,
)
from .database import get_omop_engine
from .profile_manager import set_active_simulation_context
from .protocol_registry import RegistryYaml, load_protocol_registry
from .transformation_graph import TransformationGraph

if TYPE_CHECKING:
    from ..schema.treatment_episode import GraphOutcome


@dataclass(frozen=True)
class ProfileConfig:
    """Resolved file locations for one named graph within a profile."""

    profile_id: str
    profile_name: str
    graph_relpath: Path
    protocol_registry_relpath: Path | None
    concept_map_relpath: Path
    measurement_map_relpath: Path
    observation_map_relpath: Path

    @property
    def regimen_registry_relpath(self) -> Path | None:
        """Deprecated compatibility name for the protocol registry path."""

        return self.protocol_registry_relpath


@dataclass(frozen=True)
class ProfileManifest:
    """Parsed ``profile.yaml`` content for one disease profile."""

    profile_id: str
    default_graph: str
    graphs: dict[str, Path]
    protocol_registry: Path | None
    concept_map: Path
    measurement_map: Path
    observation_map: Path
    entry_graph: str | None = None
    pathways: dict[str, str] | None = None
    legacy_regimen_registry: Path | None = None

    @property
    def regimen_registry(self) -> Path | None:
        """Deprecated compatibility view of the registry artifact path."""

        return self.protocol_registry or self.legacy_regimen_registry


@dataclass
class SimulationContext:
    """Loaded runtime objects for sampling a single graph within a profile."""

    profile_id: str
    profile_name: str
    graph: TransformationGraph
    concept_mapper: ConceptMapper
    modifier_mapper: ModifierMapper
    measurement_mapper: MeasurementMapper
    observation_mapper: ObservationMapper
    protocol_registry: RegistryYaml | None = None

    def sample_patient(
        self,
        initial_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Sample one graph realization, optionally from inherited upstream state."""

        return self.graph.sample_one(initial_state=initial_state)

    def sample_regimen_outcome(self) -> "GraphOutcome":
        """Sample one patient and normalize active treatment endpoints to one outcome."""

        from ..schema.treatment_episode import build_graph_outcome

        sampled = self.sample_patient()
        return build_graph_outcome(
            sampled,
            registry=self.protocol_registry,
            source_profile=self.profile_name,
        )


@dataclass(frozen=True)
class RoutedPatientSample:
    """Result of running an entry graph and, when available, its routed tail graph."""

    profile_id: str
    entry_graph_name: str
    pathway: str
    tail_graph_name: str | None
    entry_sample: dict[str, Any]
    final_sample: dict[str, Any]
    graph_outcome: "GraphOutcome | None"
    outcome_error: str | None


@dataclass
class RoutedProfileRuntime:
    """Runtime helper for profiles that use an entry graph plus pathway tail graphs."""

    profile_id: str
    manifest: ProfileManifest
    entry_context: SimulationContext
    tail_contexts: dict[str, SimulationContext]

    @property
    def entry_graph_name(self) -> str:
        """Return the manifest-declared entry graph name for this routed profile."""

        if self.manifest.entry_graph is None:
            raise ValueError(
                f"Profile '{self.profile_id}' does not declare an entry_graph."
            )
        return self.manifest.entry_graph

    @property
    def pathway_to_graph(self) -> dict[str, str]:
        """Return a copy of the manifest pathway-to-tail-graph mapping."""

        return dict(self.manifest.pathways or {})

    def sample_routed_patient(
        self,
        initial_state: dict[str, Any] | None = None,
    ) -> RoutedPatientSample:
        """Sample the entry graph, route by pathway, then sample the selected tail graph."""

        from ..schema.treatment_episode import build_graph_outcome

        entry_sample = self.entry_context.sample_patient(initial_state=initial_state)
        pathway = entry_sample.get("condition_episode.pathway")
        if pathway is None:
            raise ValueError(
                f"Entry graph '{self.entry_graph_name}' did not produce "
                "'condition_episode.pathway'."
            )

        tail_graph_name = self.pathway_to_graph.get(pathway)
        if tail_graph_name is None:
            return RoutedPatientSample(
                profile_id=self.profile_id,
                entry_graph_name=self.entry_graph_name,
                pathway=pathway,
                tail_graph_name=None,
                entry_sample=entry_sample,
                final_sample=dict(entry_sample),
                graph_outcome=None,
                outcome_error=(
                    f"No tail graph mapping declared for pathway '{pathway}' "
                    f"in profile '{self.profile_id}'."
                ),
            )

        tail_context = self.tail_contexts[tail_graph_name]
        final_sample = tail_context.sample_patient(initial_state=entry_sample)
        try:
            graph_outcome = build_graph_outcome(
                final_sample,
                registry=tail_context.protocol_registry,
                source_profile=tail_context.profile_name,
            )
            outcome_error = None
        except Exception as exc:
            graph_outcome = None
            outcome_error = str(exc)

        return RoutedPatientSample(
            profile_id=self.profile_id,
            entry_graph_name=self.entry_graph_name,
            pathway=pathway,
            tail_graph_name=tail_graph_name,
            entry_sample=entry_sample,
            final_sample=final_sample,
            graph_outcome=graph_outcome,
            outcome_error=outcome_error,
        )


class ProfileLoader:
    """Loader that materializes mappers, graphs, and registries under one config root."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def load(self, profile: ProfileConfig) -> SimulationContext:
        """Load one graph context and register it as the active simulation context."""

        concept_mapper = ConceptMapper(
            self.base_dir / profile.concept_map_relpath
        )
        modifier_mapper = ModifierMapper(
            self.base_dir / MAPPERS_DIRNAME / MODIFIER_MAP_FILENAME,
            engine=get_omop_engine(),
        )

        measurement_mapper = MeasurementMapper(
            self.base_dir / profile.measurement_map_relpath,
            concept_mapper,
            modifier_mapper,
        )

        observation_mapper = ObservationMapper(
            self.base_dir / profile.observation_map_relpath,
            concept_mapper,
        )

        graph = load_transformation_graph(
            f'{self.base_dir / profile.graph_relpath}'
        )

        protocol_registry = None
        if profile.protocol_registry_relpath is not None:
            protocol_registry = load_protocol_registry(
                self.base_dir / profile.protocol_registry_relpath
            )

        context = SimulationContext(
            profile_id=profile.profile_id,
            profile_name=profile.profile_name,
            graph=graph,
            concept_mapper=concept_mapper,
            modifier_mapper=modifier_mapper,
            measurement_mapper=measurement_mapper,
            observation_mapper=observation_mapper,
            protocol_registry=protocol_registry,
        )
        set_active_simulation_context(context)
        return context


def list_profiles(base_dir: Path | None = None) -> list[str]:
    """List available profile ids discovered under the profiles directory."""

    config_root = resolve_config_root(base_dir)
    manifests = (config_root / PROFILES_DIRNAME).glob("*/profile.yaml")
    return sorted(manifest.parent.name for manifest in manifests)


def load_profile_manifest(
    profile_id: str,
    base_dir: Path | None = None,
) -> ProfileManifest:
    """Load and validate the manifest for one profile id."""

    config_root = resolve_config_root(base_dir)
    manifest_path = profile_manifest_path(profile_id, config_root)
    if not manifest_path.exists():
        available = ", ".join(list_profiles(config_root)) or "none"
        raise FileNotFoundError(
            f"Profile manifest not found for '{profile_id}' at {manifest_path}. "
            f"Available profiles: {available}"
        )

    with manifest_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    manifest = raw.get("profile", raw)
    graphs = {
        graph_name: Path(graph_path)
        for graph_name, graph_path in manifest["graphs"].items()
    }
    if manifest["default_graph"] not in graphs:
        raise ValueError(
            f"Profile '{profile_id}' default graph '{manifest['default_graph']}' "
            "is not declared in profile.yaml"
        )

    mappers = manifest.get("mappers", {})
    protocol_registry_value = manifest.get("protocol_registry")
    legacy_regimen_registry_value = manifest.get("regimen_registry")
    if protocol_registry_value is None and legacy_regimen_registry_value is None:
        raise ValueError(
            f"Profile '{profile_id}' must declare 'protocol_registry'."
        )

    loaded = ProfileManifest(
        profile_id=manifest["profile_id"],
        default_graph=manifest["default_graph"],
        graphs=graphs,
        protocol_registry=(
            Path(protocol_registry_value)
            if protocol_registry_value is not None
            else None
        ),
        concept_map=Path(
            mappers.get("concept_map", DEFAULT_PROFILE_CONCEPT_MAP_RELPATH)
        ),
        measurement_map=Path(
            mappers.get("measurement_map", DEFAULT_PROFILE_MEASUREMENT_MAP_RELPATH)
        ),
        observation_map=Path(
            mappers.get("observation_map", DEFAULT_PROFILE_OBSERVATION_MAP_RELPATH)
        ),
        entry_graph=manifest.get("entry_graph"),
        pathways=dict(manifest.get("pathways", {})),
        legacy_regimen_registry=(
            Path(legacy_regimen_registry_value)
            if legacy_regimen_registry_value is not None
            else None
        ),
    )
    if loaded.profile_id != profile_id:
        raise ValueError(
            f"Profile manifest id mismatch: expected '{profile_id}', "
            f"found '{loaded.profile_id}'"
        )
    if loaded.entry_graph is not None and loaded.entry_graph not in loaded.graphs:
        raise ValueError(
            f"Profile '{profile_id}' entry_graph '{loaded.entry_graph}' "
            "is not declared in graphs."
        )
    for pathway, graph_name in (loaded.pathways or {}).items():
        if graph_name not in loaded.graphs:
            raise ValueError(
                f"Profile '{profile_id}' pathway '{pathway}' points to unknown "
                f"graph '{graph_name}'."
            )
    return loaded


def get_profile_config(
    profile_id: str,
    graph_name: str | None = None,
    base_dir: Path | None = None,
) -> ProfileConfig:
    """Resolve one graph name within a profile to concrete relative artifact paths."""

    manifest = load_profile_manifest(profile_id, base_dir=base_dir)
    selected_graph = graph_name or manifest.default_graph
    if selected_graph not in manifest.graphs:
        available = ", ".join(sorted(manifest.graphs))
        raise KeyError(
            f"Unknown graph '{selected_graph}' for profile '{profile_id}'. "
            f"Available graphs: {available}"
        )

    return ProfileConfig(
        profile_id=profile_id,
        profile_name=selected_graph,
        graph_relpath=profile_artifact_relpath(
            profile_id, manifest.graphs[selected_graph]
        ),
        protocol_registry_relpath=(
            profile_artifact_relpath(profile_id, manifest.protocol_registry)
            if manifest.protocol_registry is not None
            else None
        ),
        concept_map_relpath=profile_artifact_relpath(
            profile_id, manifest.concept_map
        ),
        measurement_map_relpath=profile_artifact_relpath(
            profile_id, manifest.measurement_map
        ),
        observation_map_relpath=profile_artifact_relpath(
            profile_id, manifest.observation_map
        ),
    )


def load_profile(
    profile_id: str,
    graph_name: str | None = None,
    *,
    base_dir: Path | None = None,
) -> SimulationContext:
    """Load a single graph context for a profile and optional graph name."""

    config_root = resolve_config_root(base_dir)
    loader = ProfileLoader(config_root)
    return loader.load(
        get_profile_config(profile_id, graph_name=graph_name, base_dir=config_root)
    )


def load_routed_profile(
    profile_id: str,
    *,
    base_dir: Path | None = None,
) -> RoutedProfileRuntime:
    """Load a routed runtime for profiles that declare an entry graph and pathways."""

    config_root = resolve_config_root(base_dir)
    manifest = load_profile_manifest(profile_id, base_dir=config_root)
    if not manifest.entry_graph:
        raise ValueError(
            f"Profile '{profile_id}' does not define an entry_graph in profile.yaml."
        )
    if not manifest.pathways:
        raise ValueError(
            f"Profile '{profile_id}' does not define any pathways in profile.yaml."
        )

    entry_context = load_profile(
        profile_id,
        manifest.entry_graph,
        base_dir=config_root,
    )
    tail_contexts = {
        graph_name: load_profile(profile_id, graph_name, base_dir=config_root)
        for graph_name in sorted(set(manifest.pathways.values()))
    }
    return RoutedProfileRuntime(
        profile_id=profile_id,
        manifest=manifest,
        entry_context=entry_context,
        tail_contexts=tail_contexts,
    )

def load_transformation_graph(yaml_path: str) -> TransformationGraph:
    """
    Load the YAML graph into a TransformationGraph,
    build a DAG, validate, and return a GraphEngine instance.
    """
    with open(yaml_path, 'r') as f:
        raw = yaml.safe_load(f)

    tg = TransformationGraph.model_validate(raw['graph'])#(**raw['graph'])
    return tg
