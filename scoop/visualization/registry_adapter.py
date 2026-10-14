"""Visualization-facing views over the typed SCOOP protocol model."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..protocols.spec import RegistrySpec, component_term_label
from ..selection.interventions import (
    Choice,
    InterventionCatalog,
    NonHemOnc,
    PhaseRef,
    ProtocolRef,
    Recipe,
)


@dataclass(frozen=True)
class ResolverView:
    type: str
    group_id: str | None = None
    members: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "group_id": self.group_id,
            "members": list(self.members),
            **self.metadata,
        }


class ComponentQueryResolver(ResolverView):
    pass


@dataclass(frozen=True)
class BundlePhaseView:
    phase_id: str
    title: str | None = None
    source_type: str = "phase"
    source_entry_id: str | None = None
    source_group_id: str | None = None
    component_terms: tuple[str, ...] = ()
    notes: str | None = None


@dataclass(frozen=True)
class BundleRecipeResolver(ResolverView):
    phases: tuple[BundlePhaseView, ...] = ()
    sequence_template: str | None = None


class CompositeResolver(ResolverView):
    pass


@dataclass(frozen=True)
class RegistryEntryView:
    public: bool = True
    clinical_group: str = ""
    stage_scope: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    graph_sources: tuple[str, ...] = ()
    required_modifiers: tuple[str, ...] = ()
    resolver: ResolverView = field(default_factory=lambda: ResolverView("none"))
    notes: str | None = None


@dataclass(frozen=True)
class GroupView:
    condition_set: Any = None
    default_condition_cuis: tuple[int, ...] = ()
    component_lists: tuple[tuple[str, ...], ...] = ()
    category_restrictions: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None


@dataclass(frozen=True)
class BundleView:
    sequence_template: str | None = None
    condition_set: Any = None
    default_condition_cuis: tuple[int, ...] = ()
    notes: str | None = None
    phases: tuple[BundlePhaseView, ...] = ()


@dataclass(frozen=True)
class VisualizationRegistry:
    label: str
    entries: dict[str, RegistryEntryView] = field(default_factory=dict)
    hemonc_groups: dict[str, GroupView] = field(default_factory=dict)
    hemonc_bundle_groups: dict[str, BundleView] = field(default_factory=dict)


def _condition_data(phase: Any) -> tuple[Any, tuple[int, ...]]:
    conditions = phase.conditions
    return conditions.to_value(), tuple(conditions.cuis or ())


def _group_from_phase(phase: Any) -> GroupView:
    condition_set, condition_cuis = _condition_data(phase)
    requirements = phase.component_requirements
    return GroupView(
        condition_set=condition_set,
        default_condition_cuis=condition_cuis,
        component_lists=tuple(
            tuple(component_term_label(item) for item in items)
            for items in requirements.component_lists
        ),
        category_restrictions={
            str(category): restriction.model_dump()
            for category, restriction in requirements.category_restrictions.items()
        },
    )


def _resolver_for_intervention(intervention_id: str, resolution: Any) -> ResolverView:
    if isinstance(resolution, PhaseRef):
        return ComponentQueryResolver(
            type="phase",
            group_id=resolution.phase_id,
            metadata={"technique": resolution.technique},
        )
    if isinstance(resolution, ProtocolRef):
        return ComponentQueryResolver(type="protocol", group_id=resolution.protocol_id)
    if isinstance(resolution, Choice):
        return CompositeResolver(type="composite", members=resolution.intervention_ids)
    if isinstance(resolution, Recipe):
        phases = tuple(
            BundlePhaseView(
                phase_id=getattr(step.reference, "phase_id", getattr(step.reference, "protocol_id", "")),
                source_type=type(step.reference).__name__.lower(),
                source_entry_id=intervention_id,
                notes=step.timing.value if step.timing is not None else None,
            )
            for step in resolution.steps
        )
        return BundleRecipeResolver(
            type="bundle_recipe",
            group_id=intervention_id,
            phases=phases,
            sequence_template=resolution.sequence_template.value if resolution.sequence_template else None,
        )
    if isinstance(resolution, NonHemOnc):
        return ResolverView(
            type="non_hemonc",
            metadata={"label": resolution.label, "concept_domain": resolution.concept_domain},
        )
    return ResolverView(type="none")


def load_visual_registry(
    profile_id: str,
    registry_path: Path,
    interventions_path: Path | None = None,
) -> VisualizationRegistry:
    """Load typed registry data without importing the removed legacy registry."""

    try:
        registry = RegistrySpec.from_path(registry_path)
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return VisualizationRegistry(label=profile_id)

    catalog = None
    if interventions_path is not None and interventions_path.exists():
        try:
            catalog = InterventionCatalog.from_path(interventions_path, protocols=registry.protocols)
        except (OSError, TypeError, ValueError):
            catalog = None

    entries: dict[str, RegistryEntryView] = {}
    if catalog is not None:
        for intervention_id, spec in catalog.specs.items():
            entries[intervention_id] = RegistryEntryView(
                public=not spec.internal,
                required_modifiers=spec.requires,
                resolver=_resolver_for_intervention(intervention_id, spec.resolution),
            )
    else:
        for phase_id in registry.source_phases:
            entries[phase_id] = RegistryEntryView(
                resolver=ComponentQueryResolver(type="phase", group_id=phase_id),
            )

    groups = {phase_id: _group_from_phase(phase) for phase_id, phase in registry.source_phases.items()}
    bundles: dict[str, BundleView] = {}
    if catalog is not None:
        for intervention_id, spec in catalog.specs.items():
            if isinstance(spec.resolution, Recipe):
                bundles[intervention_id] = BundleView(
                    sequence_template=spec.resolution.sequence_template.value if spec.resolution.sequence_template else None,
                    phases=tuple(
                        BundlePhaseView(
                            phase_id=getattr(step.reference, "phase_id", getattr(step.reference, "protocol_id", "")),
                            source_type=type(step.reference).__name__.lower(),
                            source_entry_id=intervention_id,
                        )
                        for step in spec.resolution.steps
                    ),
                )
    return VisualizationRegistry(
        label=registry.label,
        entries=entries,
        hemonc_groups=groups,
        hemonc_bundle_groups=bundles,
    )
