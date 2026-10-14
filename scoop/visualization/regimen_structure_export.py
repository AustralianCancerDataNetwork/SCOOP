from __future__ import annotations

"""Export a regimen-focused structure payload for the plain JS explorer."""

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


def _slug_label(value: str) -> str:
    return value.replace("_", " ").replace(".", " ").title()


def _list_profiles(config_root: Path) -> list[str]:
    manifests = (config_root / PROFILES_DIRNAME).glob("*/profile.yaml")
    return sorted(manifest.parent.name for manifest in manifests)


def _load_manifest(profile_id: str, config_root: Path) -> dict[str, Any]:
    manifest_path = profile_manifest_path(profile_id, config_root)
    with manifest_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return raw.get("profile", raw)


def _protocol_registry_path(profile_id: str, config_root: Path) -> Path | None:
    profile_root = config_root / PROFILES_DIRNAME / profile_id
    matches = sorted(profile_root.glob("*_protocol_registry.yaml"))
    return matches[0] if matches else None


def _raw_protocol_registry(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None

    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    if not raw:
        return None

    if len(raw) == 1:
        return next(iter(raw.values()))
    return raw


def _export_registry_entry(profile_id: str, entry_id: str, entry: Any) -> dict[str, Any]:
    return {
        "id": f"entry:{profile_id}:{entry_id}",
        "entry_id": entry_id,
        "label": _slug_label(entry_id),
        "public": entry.public,
        "clinical_group": entry.clinical_group,
        "stage_scope": list(entry.stage_scope),
        "aliases": list(entry.aliases),
        "graph_sources": list(entry.graph_sources),
        "required_modifiers": list(entry.required_modifiers),
        "resolver_type": entry.resolver.type,
        "notes": entry.notes,
    }


def _export_registry_resolver(profile_id: str, entry_id: str, entry: Any) -> dict[str, Any]:
    resolver = entry.resolver
    target_kind = "none"
    target_id: str | None = None

    if isinstance(resolver, ComponentQueryResolver):
        target_kind = "group"
        target_id = resolver.group_id
    elif isinstance(resolver, BundleRecipeResolver):
        target_kind = "bundle"
        target_id = resolver.group_id
    elif isinstance(resolver, CompositeResolver):
        target_kind = "composite"
        target_id = entry_id

    return {
        "id": f"resolver:{profile_id}:{entry_id}",
        "entry_id": entry_id,
        "label": _slug_label(resolver.type),
        "resolver_type": resolver.type,
        "target_kind": target_kind,
        "target_id": target_id,
        "metadata": resolver.model_dump(),
    }


def _export_registry_group(profile_id: str, group_id: str, group: Any) -> dict[str, Any]:
    return {
        "id": f"group:{profile_id}:{group_id}",
        "group_id": group_id,
        "label": _slug_label(group_id),
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
    }


def _export_registry_bundle(profile_id: str, bundle_id: str, bundle: Any) -> dict[str, Any]:
    return {
        "id": f"bundle:{profile_id}:{bundle_id}",
        "bundle_id": bundle_id,
        "label": _slug_label(bundle_id),
        "sequence_template": bundle.sequence_template,
        "condition_set": bundle.condition_set,
        "default_condition_cuis": list(bundle.default_condition_cuis),
        "notes": bundle.notes,
        "phases": [
            {
                "phase_id": phase.phase_id,
                "title": phase.title,
                "source_type": phase.source_type,
                "source_entry_id": phase.source_entry_id,
                "source_group_id": phase.source_group_id,
                "component_terms": list(phase.component_terms),
                "notes": phase.notes,
            }
            for phase in bundle.phases
        ],
    }


def _export_registry(profile_id: str, registry: VisualizationRegistry) -> dict[str, Any]:
    entries = [
        _export_registry_entry(profile_id, entry_id, entry)
        for entry_id, entry in registry.entries.items()
    ]
    resolvers = [
        _export_registry_resolver(profile_id, entry_id, entry)
        for entry_id, entry in registry.entries.items()
    ]
    groups = [
        _export_registry_group(profile_id, group_id, group)
        for group_id, group in registry.hemonc_groups.items()
    ]
    bundles = [
        _export_registry_bundle(profile_id, bundle_id, bundle)
        for bundle_id, bundle in registry.hemonc_bundle_groups.items()
    ]
    composites = [
        {
            "id": f"composite:{profile_id}:{entry_id}",
            "entry_id": entry_id,
            "label": _slug_label(entry_id),
            "members": list(entry.resolver.members),
        }
        for entry_id, entry in registry.entries.items()
        if isinstance(entry.resolver, CompositeResolver)
    ]

    return {
        "entries": entries,
        "resolvers": resolvers,
        "groups": groups,
        "bundles": bundles,
        "composites": composites,
    }


def _export_protocol(profile_id: str, raw_protocol: dict[str, Any] | None) -> dict[str, Any] | None:
    if raw_protocol is None:
        return None

    source_phases = []
    for phase_id, phase_data in (raw_protocol.get("source_phases") or {}).items():
        component_requirements = phase_data.get("component_requirements", {})
        source_phases.append(
            {
                "id": f"source_phase:{profile_id}:{phase_id}",
                "phase_id": phase_id,
                "label": _slug_label(phase_id),
                "phase_type": phase_data.get("type"),
                "regimens": phase_data.get("regimens"),
                "curative_intent": phase_data.get("curative_intent", True),
                "component_lists": component_requirements.get("component_lists", []),
                "category_restrictions": component_requirements.get("category_restrictions", {}),
            }
        )

    protocols = []
    for protocol_id, protocol_data in (raw_protocol.get("protocols") or {}).items():
        protocols.append(
            {
                "id": f"protocol:{profile_id}:{protocol_id}",
                "protocol_id": protocol_id,
                "label": _slug_label(protocol_id),
                "phases": list(protocol_data.get("phases", [])),
                "remove_source_phases": protocol_data.get("remove_source_phases"),
                "optional_phases": protocol_data.get("optional_phases"),
            }
        )

    entries = []
    for entry_id, entry_data in (raw_protocol.get("entries") or {}).items():
        resolver = entry_data.get("resolver", {})
        entries.append(
            {
                "id": f"protocol_entry:{profile_id}:{entry_id}",
                "entry_id": entry_id,
                "label": _slug_label(entry_id),
                "public": entry_data.get("public"),
                "clinical_group": entry_data.get("clinical_group"),
                "stage_scope": list(entry_data.get("stage_scope", [])),
                "graph_sources": list(entry_data.get("graph_sources", [])),
                "required_modifiers": list(entry_data.get("required_modifiers", [])),
                "resolver_type": resolver.get("type"),
                "resolver_group_id": resolver.get("group_id"),
            }
        )

    return {
        "source_phases": source_phases,
        "protocols": protocols,
        "entries": entries,
    }


def build_regimen_structure(base_dir: Path | None = None) -> dict[str, Any]:
    config_root = resolve_config_root(base_dir)
    profiles = []

    for profile_id in _list_profiles(config_root):
        manifest = _load_manifest(profile_id, config_root)
        registry_path = config_root / profile_artifact_relpath(
            profile_id, Path(manifest["regimen_registry"])
        )
        interventions_path = (
            config_root
            / profile_artifact_relpath(profile_id, Path(manifest["interventions"]))
            if manifest.get("interventions") is not None
            else None
        )
        registry = load_visual_registry(
            profile_id,
            registry_path,
            interventions_path,
        )
        protocol = _export_protocol(
            profile_id,
            _raw_protocol_registry(_protocol_registry_path(profile_id, config_root)),
        )

        profiles.append(
            {
                "id": profile_id,
                "label": _slug_label(profile_id),
                "registry": _export_registry(profile_id, registry),
                "protocol": protocol,
            }
        )

    return {
        "generated_at": "2026-07-21",
        "profiles": profiles,
    }


def _render_payload(payload: dict[str, Any], as_js: bool) -> str:
    raw_json = json.dumps(payload, indent=2, sort_keys=True)
    if not as_js:
        return raw_json + "\n"
    return f"window.REGIMEN_STRUCTURE_DATA = {raw_json};\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export regimen structure data for the plain JS explorer.")
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
        "--format",
        choices=("json", "js"),
        default="js",
        help="Output format. 'js' writes window.REGIMEN_STRUCTURE_DATA = ...",
    )
    args = parser.parse_args()

    payload = build_regimen_structure(base_dir=args.config_root)
    text = _render_payload(payload, as_js=args.format == "js")

    if args.output is None:
        print(text, end="")
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
