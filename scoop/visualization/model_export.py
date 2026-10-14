from __future__ import annotations

"""Build a lightweight visualization view-model for the model explorer app.

This module intentionally exports a frontend-friendly JSON structure rather than
runtime SCOOP objects. The goal is to support teaching and communication tools
without coupling the UI to internal Python classes.
"""

from dataclasses import asdict, dataclass, field
import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from ..profiles.paths import (
    PROFILES_DIRNAME,
    profile_artifact_relpath,
    profile_manifest_path,
    resolve_config_root,
)
from .registry_adapter import (
    BundleRecipeResolver,
    ComponentQueryResolver,
    CompositeResolver,
    VisualizationRegistry,
    load_visual_registry,
)
from ..profiles.manifest import load_profile_manifest


@dataclass(frozen=True)
class ProfileRecord:
    id: str
    label: str
    default_graph_id: str
    entry_graph_id: str | None
    graph_ids: list[str] = field(default_factory=list)
    pathway_labels: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GraphRecord:
    id: str
    profile_id: str
    name: str
    label: str
    kind: str
    source_path: str


@dataclass(frozen=True)
class GraphNodeRecord:
    id: str
    graph_id: str
    label: str
    kind: str
    node_type: str
    scope: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EdgeRecord:
    id: str
    source: str
    target: str
    kind: str
    label: str | None = None
    graph_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RegistryEntryRecord:
    id: str
    profile_id: str
    entry_id: str
    label: str
    clinical_group: str
    public: bool
    resolver_type: str
    stage_scope: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    graph_sources: list[str] = field(default_factory=list)
    notes: str | None = None


@dataclass(frozen=True)
class ResolverRecord:
    id: str
    profile_id: str
    registry_entry_id: str
    resolver_type: str
    label: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GroupRecord:
    id: str
    profile_id: str
    group_id: str
    label: str
    kind: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BundleRecord:
    id: str
    profile_id: str
    bundle_id: str
    label: str
    phase_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SampleStepRecord:
    id: str
    label: str
    target_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SampleRecord:
    id: str
    profile_id: str
    label: str
    resolved_entry_id: str | None = None
    graph_id: str | None = None
    steps: list[SampleStepRecord] = field(default_factory=list)


@dataclass(frozen=True)
class ViewModel:
    generated_at: str
    profiles: list[ProfileRecord] = field(default_factory=list)
    graphs: list[GraphRecord] = field(default_factory=list)
    nodes: list[GraphNodeRecord] = field(default_factory=list)
    edges: list[EdgeRecord] = field(default_factory=list)
    registry_entries: list[RegistryEntryRecord] = field(default_factory=list)
    resolvers: list[ResolverRecord] = field(default_factory=list)
    groups: list[GroupRecord] = field(default_factory=list)
    bundles: list[BundleRecord] = field(default_factory=list)
    samples: list[SampleRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExportProfileManifest:
    profile_id: str
    default_graph: str
    graphs: dict[str, Path]
    regimen_registry: Path
    interventions: Path | None = None
    entry_graph: str | None = None
    pathways: dict[str, str] = field(default_factory=dict)


def _slug_label(value: str) -> str:
    return value.replace("_", " ").replace(".", " ").title()


def _graph_record_id(profile_id: str, graph_name: str) -> str:
    return f"graph:{profile_id}:{graph_name}"


def _node_record_id(profile_id: str, graph_name: str, node_name: str) -> str:
    return f"node:{profile_id}:{graph_name}:{node_name}"


def _registry_record_id(profile_id: str, entry_id: str) -> str:
    return f"registry:{profile_id}:{entry_id}"


def _resolver_record_id(profile_id: str, entry_id: str) -> str:
    return f"resolver:{profile_id}:{entry_id}"


def _group_record_id(profile_id: str, group_id: str) -> str:
    return f"group:{profile_id}:{group_id}"


def _bundle_record_id(profile_id: str, bundle_id: str) -> str:
    return f"bundle:{profile_id}:{bundle_id}"


def _read_graph_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return raw.get("graph", raw)


def _list_profiles(config_root: Path) -> list[str]:
    manifests = (config_root / PROFILES_DIRNAME).glob("*/profile.yaml")
    return sorted(manifest.parent.name for manifest in manifests)


def _load_manifest(profile_id: str, config_root: Path) -> ExportProfileManifest:
    manifest = load_profile_manifest(profile_id, base_dir=config_root)
    return ExportProfileManifest(
        profile_id=manifest.profile_id,
        default_graph=manifest.default_graph,
        graphs=manifest.graphs,
        regimen_registry=manifest.regimen_registry,
        interventions=manifest.interventions,
        entry_graph=manifest.entry_graph,
        pathways=dict(manifest.pathways or {}),
    )


def _graph_kind(manifest_entry_graph: str | None, graph_name: str, pathways: dict[str, str]) -> str:
    if manifest_entry_graph == graph_name:
        return "entry"
    if graph_name in pathways.values():
        return "pathway"
    return "standalone"


def _node_kind(node_name: str) -> str:
    if node_name.startswith("treatment_episode."):
        return "treatment_endpoint"
    if node_name.startswith("condition_episode."):
        return "routing"
    return "state"


def _node_scope(node_name: str) -> str:
    if "." not in node_name:
        return "graph"
    return node_name.split(".", 1)[0]


def _export_registry(
    profile_id: str,
    registry: VisualizationRegistry,
) -> tuple[list[RegistryEntryRecord], list[ResolverRecord], list[GroupRecord], list[BundleRecord], list[EdgeRecord]]:
    registry_entries: list[RegistryEntryRecord] = []
    resolvers: list[ResolverRecord] = []
    groups: list[GroupRecord] = []
    bundles: list[BundleRecord] = []
    edges: list[EdgeRecord] = []

    for group_id, group in registry.hemonc_groups.items():
        groups.append(
            GroupRecord(
                id=_group_record_id(profile_id, group_id),
                profile_id=profile_id,
                group_id=group_id,
                label=_slug_label(group_id),
                kind="hemonc_group",
                metadata={
                    "condition_set": group.condition_set,
                    "default_condition_cuis": list(group.default_condition_cuis),
                    "component_lists": [list(component_list) for component_list in group.component_lists],
                    "category_restrictions": {
                        key: (
                            restriction.model_dump()
                            if hasattr(restriction, "model_dump")
                            else restriction
                        )
                        for key, restriction in group.category_restrictions.items()
                    },
                    "notes": group.notes,
                },
            )
        )

    for bundle_id, bundle in registry.hemonc_bundle_groups.items():
        bundle_record_id = _bundle_record_id(profile_id, bundle_id)
        phase_ids = [phase.phase_id for phase in bundle.phases]
        bundles.append(
            BundleRecord(
                id=bundle_record_id,
                profile_id=profile_id,
                bundle_id=bundle_id,
                label=_slug_label(bundle_id),
                phase_ids=phase_ids,
                metadata={
                    "condition_set": bundle.condition_set,
                    "default_condition_cuis": list(bundle.default_condition_cuis),
                    "sequence_template": bundle.sequence_template,
                    "notes": bundle.notes,
                },
            )
        )
        for phase in bundle.phases:
            phase_node_id = f"{bundle_record_id}:phase:{phase.phase_id}"
            edges.append(
                EdgeRecord(
                    id=f"edge:{phase_node_id}",
                    source=bundle_record_id,
                    target=phase_node_id,
                    kind="bundle_phase",
                    label=phase.phase_id,
                    metadata={
                        "source_type": phase.source_type,
                        "source_entry_id": phase.source_entry_id,
                        "source_group_id": phase.source_group_id,
                        "component_terms": list(phase.component_terms),
                    },
                )
            )
            if phase.source_type == "entry" and phase.source_entry_id is not None:
                edges.append(
                    EdgeRecord(
                        id=f"edge:{phase_node_id}:entry",
                        source=phase_node_id,
                        target=_registry_record_id(profile_id, phase.source_entry_id),
                        kind="bundle_source",
                        label="entry source",
                    )
                )
            if phase.source_type == "group" and phase.source_group_id is not None:
                edges.append(
                    EdgeRecord(
                        id=f"edge:{phase_node_id}:group",
                        source=phase_node_id,
                        target=_group_record_id(profile_id, phase.source_group_id),
                        kind="bundle_source",
                        label="group source",
                    )
                )

    for entry_id, entry in registry.entries.items():
        registry_id = _registry_record_id(profile_id, entry_id)
        resolver_id = _resolver_record_id(profile_id, entry_id)
        registry_entries.append(
            RegistryEntryRecord(
                id=registry_id,
                profile_id=profile_id,
                entry_id=entry_id,
                label=_slug_label(entry_id),
                clinical_group=entry.clinical_group,
                public=entry.public,
                resolver_type=entry.resolver.type,
                stage_scope=list(entry.stage_scope),
                aliases=list(entry.aliases),
                graph_sources=list(entry.graph_sources),
                notes=entry.notes,
            )
        )
        resolvers.append(
            ResolverRecord(
                id=resolver_id,
                profile_id=profile_id,
                registry_entry_id=registry_id,
                resolver_type=entry.resolver.type,
                label=_slug_label(entry.resolver.type),
                metadata=entry.resolver.model_dump(),
            )
        )
        edges.append(
            EdgeRecord(
                id=f"edge:{registry_id}:resolver",
                source=registry_id,
                target=resolver_id,
                kind="resolver",
                label="resolved by",
            )
        )

        if isinstance(entry.resolver, ComponentQueryResolver) and entry.resolver.group_id is not None:
            edges.append(
                EdgeRecord(
                    id=f"edge:{resolver_id}:group",
                    source=resolver_id,
                    target=_group_record_id(profile_id, entry.resolver.group_id),
                    kind="resolver_target",
                    label="query group",
                )
            )
        if isinstance(entry.resolver, BundleRecipeResolver):
            edges.append(
                EdgeRecord(
                    id=f"edge:{resolver_id}:bundle",
                    source=resolver_id,
                    target=_bundle_record_id(profile_id, entry.resolver.group_id),
                    kind="resolver_target",
                    label="bundle group",
                )
            )
        if isinstance(entry.resolver, CompositeResolver):
            for member in entry.resolver.members:
                edges.append(
                    EdgeRecord(
                        id=f"edge:{registry_id}:member:{member}",
                        source=registry_id,
                        target=_registry_record_id(profile_id, member),
                        kind="composite_member",
                        label="composite member",
                    )
                )

    return registry_entries, resolvers, groups, bundles, edges


def build_view_model(base_dir: Path | None = None) -> ViewModel:
    """Export available profile and registry structure into one JSON-safe object."""

    config_root = resolve_config_root(base_dir)
    profiles: list[ProfileRecord] = []
    graphs: list[GraphRecord] = []
    nodes: list[GraphNodeRecord] = []
    edges: list[EdgeRecord] = []
    registry_entries: list[RegistryEntryRecord] = []
    resolvers: list[ResolverRecord] = []
    groups: list[GroupRecord] = []
    bundles: list[BundleRecord] = []
    samples: list[SampleRecord] = []

    for profile_id in _list_profiles(config_root):
        manifest = _load_manifest(profile_id, config_root)
        pathways = manifest.pathways
        profile_graph_ids = [
            _graph_record_id(profile_id, graph_name)
            for graph_name in manifest.graphs
        ]
        profiles.append(
            ProfileRecord(
                id=profile_id,
                label=_slug_label(profile_id),
                default_graph_id=_graph_record_id(profile_id, manifest.default_graph),
                entry_graph_id=(
                    _graph_record_id(profile_id, manifest.entry_graph)
                    if manifest.entry_graph is not None
                    else None
                ),
                graph_ids=profile_graph_ids,
                pathway_labels=sorted(pathways),
            )
        )

        for graph_name, graph_relpath in manifest.graphs.items():
            graph_id = _graph_record_id(profile_id, graph_name)
            graph_path = config_root / profile_artifact_relpath(profile_id, graph_relpath)
            graph_yaml = _read_graph_yaml(graph_path)
            graphs.append(
                GraphRecord(
                    id=graph_id,
                    profile_id=profile_id,
                    name=graph_name,
                    label=_slug_label(graph_name),
                    kind=_graph_kind(manifest.entry_graph, graph_name, pathways),
                    source_path=str(graph_path.relative_to(config_root)),
                )
            )

            for node_name, node_data in graph_yaml.get("nodes", {}).items():
                nodes.append(
                    GraphNodeRecord(
                        id=_node_record_id(profile_id, graph_name, node_name),
                        graph_id=graph_id,
                        label=_slug_label(node_name),
                        kind=_node_kind(node_name),
                        node_type=node_data.get("type", "unknown"),
                        scope=_node_scope(node_name),
                        metadata=node_data,
                    )
                )

            for edge_index, edge_data in enumerate(graph_yaml.get("edges", []), start=1):
                src = edge_data["from"]
                dst = edge_data["to"]
                edges.append(
                    EdgeRecord(
                        id=f"edge:{graph_id}:{edge_index}",
                        source=_node_record_id(profile_id, graph_name, src),
                        target=_node_record_id(profile_id, graph_name, dst),
                        kind="graph",
                        label=edge_data.get("using"),
                        graph_id=graph_id,
                        metadata={
                            key: value
                            for key, value in edge_data.items()
                            if key not in {"from", "to"}
                        },
                    )
                )

        if manifest.entry_graph is not None:
            for pathway_label, target_graph in pathways.items():
                edges.append(
                    EdgeRecord(
                        id=f"edge:pathway:{profile_id}:{pathway_label}",
                        source=_graph_record_id(profile_id, manifest.entry_graph),
                        target=_graph_record_id(profile_id, target_graph),
                        kind="pathway",
                        label=pathway_label,
                    )
                )

        registry_path = config_root / profile_artifact_relpath(
            profile_id,
            manifest.regimen_registry,
        )
        interventions_path = (
            config_root / profile_artifact_relpath(profile_id, manifest.interventions)
            if manifest.interventions is not None
            else None
        )
        registry = load_visual_registry(
            profile_id,
            registry_path,
            interventions_path,
        )
        (
            exported_registry_entries,
            exported_resolvers,
            exported_groups,
            exported_bundles,
            exported_edges,
        ) = _export_registry(profile_id, registry)
        registry_entries.extend(exported_registry_entries)
        resolvers.extend(exported_resolvers)
        groups.extend(exported_groups)
        bundles.extend(exported_bundles)
        edges.extend(exported_edges)

        samples.append(
            SampleRecord(
                id=f"sample:{profile_id}:placeholder",
                profile_id=profile_id,
                label=f"{_slug_label(profile_id)} placeholder sample",
                graph_id=(
                    _graph_record_id(profile_id, manifest.entry_graph)
                    if manifest.entry_graph is not None
                    else _graph_record_id(profile_id, manifest.default_graph)
                ),
                steps=[
                    SampleStepRecord(
                        id=f"sample:{profile_id}:placeholder:1",
                        label="Sample path export not wired yet",
                        metadata={
                            "todo": "Attach routed patient traces here when the teaching scenarios are defined.",
                        },
                    )
                ],
            )
        )

    return ViewModel(
        generated_at="2026-07-21",
        profiles=profiles,
        graphs=graphs,
        nodes=nodes,
        edges=edges,
        registry_entries=registry_entries,
        resolvers=resolvers,
        groups=groups,
        bundles=bundles,
        samples=samples,
    )


def _write_output(payload: dict[str, Any], output_path: Path | None, pretty: bool) -> None:
    text = json.dumps(payload, indent=2 if pretty else None, sort_keys=pretty)
    if output_path is None:
        print(text)
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text + ("\n" if pretty else ""), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export SCOOP visualization view-model JSON.")
    parser.add_argument(
        "--config-root",
        type=Path,
        default=None,
        help="Optional SCOOP configuration root override.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output path. Defaults to stdout.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    args = parser.parse_args()

    view_model = build_view_model(base_dir=args.config_root)
    _write_output(view_model.to_dict(), args.output, args.pretty)


if __name__ == "__main__":
    main()
