from __future__ import annotations

"""Profile and graph loading utilities.

This module is the main entry point for:
- reading profile manifests from ``profiles/*/profile.yaml``
- turning manifest entries into concrete graph/registry config objects
- loading a single graph context for sampling
- loading a routed entry-plus-tail runtime for multi-graph disease profiles
"""

import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

from ..vocabulary.gateway import (
    DefaultVocabularyGateway,
    VocabularyGateway,
    VocabularyMappers,
)
from ..vocabulary.concept_map import ConceptMapper
from ..vocabulary.fields import MeasurementMapper, ObservationMapper
from ..vocabulary.modifier_map import ModifierMapper
from .paths import (
    DEFAULT_PROFILE_CONCEPT_MAP_RELPATH,
    DEFAULT_PROFILE_MEASUREMENT_MAP_RELPATH,
    DEFAULT_PROFILE_OBSERVATION_MAP_RELPATH,
    profile_artifact_relpath,
    profile_manifest_path,
    PROFILES_DIRNAME,
    resolve_config_root,
)
from .context import set_active_simulation_context
# from .regimen_registry import RegimenRegistry, load_regimen_registry
from ..guidelines.graph import TransformationGraph
from ..guidelines.sampling import GuidelineSample, sample
from ..protocols.spec import RegistrySpec
from ..selection.interventions import InterventionCatalog

if TYPE_CHECKING:
    from ..patients.treatment_episode import GraphOutcome


@dataclass(frozen=True)
class ProfileConfig:
    """Resolved file locations for one named graph within a profile."""

    profile_id: str
    profile_name: str
    graph_relpath: Path
    regimen_registry_relpath: Path
    concept_map_relpath: Path
    measurement_map_relpath: Path
    observation_map_relpath: Path
    interventions_relpath: Path | None = None


@dataclass(frozen=True)
class ProfileManifest:
    """Parsed ``profile.yaml`` content for one disease profile."""

    profile_id: str
    default_graph: str
    graphs: dict[str, Path]
    regimen_registry: Path
    concept_map: Path
    measurement_map: Path
    observation_map: Path
    entry_graph: str | None = None
    pathways: dict[str, str] | None = None
    interventions: Path | None = None


@dataclass
class SimulationContext:
    """Loaded runtime objects for sampling a single graph within a profile."""

    profile_id: str
    profile_name: str
    graph: TransformationGraph
    vocabulary_gateway: VocabularyGateway
    vocabulary_mappers: VocabularyMappers
    registry_spec: RegistrySpec | None
    concept_mapper: ConceptMapper
    modifier_mapper: ModifierMapper
    measurement_mapper: MeasurementMapper
    observation_mapper: ObservationMapper
    intervention_catalog: InterventionCatalog | None = None

    def sample_patient(
        self,
        initial_state: dict[str, Any] | None = None,
        *,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """Compatibility adapter returning the legacy mapping sample shape."""

        if seed is None:
            return dict(self.graph.sample_one(initial_state=initial_state))
        return self.sample_guideline(
            seed=seed,
            initial_state=initial_state,
        ).as_dict

    def sample_guideline(
        self,
        seed: int,
        initial_state: Mapping[str, object] | None = None,
    ) -> GuidelineSample:
        """Sample one typed guideline state with explicit seed and profile provenance."""

        from ..guidelines.sampling import sample

        return sample(
            self.graph,
            seed,
            source_profile=self.profile_name,
            initial_state=initial_state,
            state_node_exemptions=(
                self.intervention_catalog.state_node_exemptions
                if self.intervention_catalog is not None
                else frozenset()
            ),
        )

    def sample_regimen_outcome(self) -> "GraphOutcome":
        """Sample one patient and normalize active treatment endpoints to one outcome."""

        from ..patients.treatment_episode import build_graph_outcome

        sampled = self.sample_patient()
        return build_graph_outcome(
            sampled,
            intervention_catalog=self.intervention_catalog,
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
    entry_guideline: GuidelineSample | None = None
    final_guideline: GuidelineSample | None = None


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
        *,
        seed: int | None = None,
    ) -> RoutedPatientSample:
        """Sample and route graphs, retaining typed samples plus legacy mappings."""

        from ..patients.treatment_episode import build_graph_outcome
        import random

        rng = random.Random(seed) if seed is not None else None
        entry_values = self.entry_context.graph.sample_one(
            initial_state=initial_state,
            rng=rng,
        )
        entry_guideline = GuidelineSample(
            values=dict(entry_values),
            source_profile=self.entry_context.profile_name,
            seed=seed,
            state_node_exemptions=(
                self.entry_context.intervention_catalog.state_node_exemptions
                if self.entry_context.intervention_catalog is not None
                else frozenset()
            ),
        )
        pathway = entry_guideline.values.get("condition_episode.pathway")
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
                entry_sample=entry_guideline.as_dict,
                final_sample=entry_guideline.as_dict,
                graph_outcome=None,
                outcome_error=(
                    f"No tail graph mapping declared for pathway '{pathway}' "
                    f"in profile '{self.profile_id}'."
                ),
                entry_guideline=entry_guideline,
                final_guideline=entry_guideline,
            )

        tail_context = self.tail_contexts[tail_graph_name]
        final_values = tail_context.graph.sample_one(
            initial_state=entry_guideline.as_dict,
            rng=rng,
        )
        final_guideline = GuidelineSample(
            values=dict(final_values),
            source_profile=tail_context.profile_name,
            seed=seed,
            state_node_exemptions=(
                tail_context.intervention_catalog.state_node_exemptions
                if tail_context.intervention_catalog is not None
                else frozenset()
            ),
        )
        try:
            graph_outcome = build_graph_outcome(
                final_guideline.as_dict,
                intervention_catalog=tail_context.intervention_catalog,
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
            entry_sample=entry_guideline.as_dict,
            final_sample=final_guideline.as_dict,
            graph_outcome=graph_outcome,
            outcome_error=outcome_error,
            entry_guideline=entry_guideline,
            final_guideline=final_guideline,
        )


class ProfileLoader:
    """Loader that materializes mappers, graphs, and registries under one config root."""

    def __init__(
        self,
        base_dir: Path,
        vocabulary_gateway: VocabularyGateway | None = None,
    ):
        self.base_dir = base_dir
        self.vocabulary_gateway = vocabulary_gateway or DefaultVocabularyGateway(
            base_dir
        )

    def load(self, profile: ProfileConfig) -> SimulationContext:
        """Load one graph context and register it as the active simulation context."""

        vocabulary_mappers = self.vocabulary_gateway.get_mappers(
            concept_path=self.base_dir / profile.concept_map_relpath,
            measurement_path=self.base_dir / profile.measurement_map_relpath,
            observation_path=self.base_dir / profile.observation_map_relpath,
        )

        graph = load_transformation_graph(
            f'{self.base_dir / profile.graph_relpath}'
        )
        # regimen_registry = load_regimen_registry(
        #     self.base_dir / profile.regimen_registry_relpath
        # )

        registry_spec = None
        intervention_catalog = None
        if profile.interventions_relpath is not None:
            registry_spec = RegistrySpec.from_path(
                self.base_dir / profile.regimen_registry_relpath
            )
            intervention_catalog = InterventionCatalog.from_path(
                self.base_dir / profile.interventions_relpath,
                protocols=registry_spec.protocols,
            )

        context = SimulationContext(
            profile_id=profile.profile_id,
            profile_name=profile.profile_name,
            graph=graph,
            vocabulary_gateway=self.vocabulary_gateway,
            vocabulary_mappers=vocabulary_mappers,
            registry_spec=registry_spec,
            concept_mapper=vocabulary_mappers.concept_mapper,
            modifier_mapper=vocabulary_mappers.modifier_mapper,
            measurement_mapper=vocabulary_mappers.measurement_mapper,
            observation_mapper=vocabulary_mappers.observation_mapper,
            intervention_catalog=intervention_catalog,
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

    if not isinstance(raw, dict) or set(raw) != {"profile"}:
        raise ValueError("profile manifest must contain exactly one profile field")
    manifest = raw["profile"]
    if not isinstance(manifest, dict):
        raise ValueError("profile manifest profile must be a mapping")
    if profile_id == "nsclc":
        graphs = {
            graph["graph_id"]: Path(graph["path"])
            for graph in manifest["graphs"]
        }
        pathways = {
            pathway["pathway_id"]: pathway["graph_id"]
            for pathway in manifest.get("pathways", [])
        }
    else:
        # Bladder and SCLC remain on their pre-cutover manifest spelling until
        # their own profile migrations. Keep this boundary explicit and local.
        graphs = {
            graph_name: Path(graph_path)
            for graph_name, graph_path in manifest["graphs"].items()
        }
        pathways = dict(manifest.get("pathways", {}))
    if manifest["default_graph"] not in graphs:
        raise ValueError(
            f"Profile '{profile_id}' default graph '{manifest['default_graph']}' "
            "is not declared in profile.yaml"
        )

    mappers = manifest.get("mappers", {})
    loaded = ProfileManifest(
        profile_id=manifest["profile_id"],
        default_graph=manifest["default_graph"],
        graphs=graphs,
        regimen_registry=Path(manifest["regimen_registry"]),
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
        pathways=pathways,
        interventions=(
            Path(manifest["interventions"])
            if manifest.get("interventions") is not None
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
    _validate_declared_interventions(loaded, config_root)
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
        regimen_registry_relpath=profile_artifact_relpath(
            profile_id, manifest.regimen_registry
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
        interventions_relpath=(
            profile_artifact_relpath(profile_id, manifest.interventions)
            if manifest.interventions is not None
            else None
        ),
    )


def load_profile(
    profile_id: str,
    graph_name: str | None = None,
    *,
    base_dir: Path | None = None,
    vocabulary_gateway: VocabularyGateway | None = None,
) -> SimulationContext:
    """Load a single graph context for a profile and optional graph name."""

    config_root = resolve_config_root(base_dir)
    loader = ProfileLoader(config_root, vocabulary_gateway=vocabulary_gateway)
    return loader.load(
        get_profile_config(profile_id, graph_name=graph_name, base_dir=config_root)
    )


def load_routed_profile(
    profile_id: str,
    *,
    base_dir: Path | None = None,
    vocabulary_gateway: VocabularyGateway | None = None,
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
        vocabulary_gateway=vocabulary_gateway,
    )
    tail_contexts = {
        graph_name: load_profile(
            profile_id,
            graph_name,
            base_dir=config_root,
            vocabulary_gateway=vocabulary_gateway,
        )
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


def _validate_declared_interventions(
    manifest: ProfileManifest,
    config_root: Path,
) -> None:
    if manifest.interventions is None:
        return
    registry_path = profile_artifact_relpath(
        manifest.profile_id,
        manifest.regimen_registry,
    )
    catalog = InterventionCatalog.from_path(
        config_root / profile_artifact_relpath(
            manifest.profile_id,
            manifest.interventions,
        ),
        protocols=RegistrySpec.from_path(config_root / registry_path).protocols,
    )
    if catalog.profile_id != manifest.profile_id:
        raise ValueError(
            f"Intervention profile id mismatch: expected {manifest.profile_id!r}, "
            f"found {catalog.profile_id!r}"
        )

    graph_nodes: set[str] = set()
    for graph_path in manifest.graphs.values():
        path = config_root / profile_artifact_relpath(
            manifest.profile_id,
            graph_path,
        )
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        graph_nodes.update((raw.get("graph", {}).get("nodes", {}) or {}).keys())
    catalog.validate_coverage(graph_nodes=graph_nodes)
