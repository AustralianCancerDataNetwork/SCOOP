from __future__ import annotations
"""Typed regimen-registry models and resolution helpers.

This module turns YAML registry configuration into validated Pydantic models and
provides the resolution layer used by the HemOnc query helpers.

There are two main resolution targets:
- variant-backed HemOnc component queries
- non-variant bundle recipes assembled from query-backed or sig-backed phases
"""

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


BroadCategory = Literal[
    "chemotherapy",
    "radiotherapy",
    "immunotherapy",
    "surgery",
    "supportive",
]

QueryBasis = Literal["variant"]
BundleBasis = Literal["sig_bundle"]
AssemblyMode = Literal[
    "rt_only",
    "variant_only",
    "variant_plus_rt",
    "variant_plus_surgery",
    "sig_only",
    "multi_sig_only",
]
PhaseType = Literal["systemic", "radiotherapy", "surgery", "supportive"]
PhaseTiming = Literal["standalone", "pre", "concurrent", "post"]
BundlePhaseSourceType = Literal["entry", "group", "sig_component"]


class CategoryRestriction(BaseModel):
    """Cardinality rule applied to a broad regimen category."""

    type: Literal["exact", "min"]
    amount: int


class HemOncGroup(BaseModel):
    """Reusable named variant-query definition backed by HemOnc components."""

    group_id: str = ""
    basis: QueryBasis = "variant"
    condition_set: str | None = None
    default_condition_cuis: list[int] = Field(default_factory=list)
    component_lists: list[list[str]] = Field(default_factory=list)
    category_restrictions: dict[BroadCategory, CategoryRestriction] = Field(default_factory=dict)
    category_restrictions_provided: bool = Field(default=False, exclude=True)
    notes: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _mark_category_restrictions_presence(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            data["category_restrictions_provided"] = "category_restrictions" in data
        return data


class ResolvedHemOncQuery(BaseModel):
    """Concrete variant query ready for SQL generation or execution."""

    group_id: str | None = None
    basis: QueryBasis = "variant"
    default_condition_cuis: list[int] = Field(default_factory=list)
    component_lists: list[list[str]] = Field(default_factory=list)
    category_restrictions: dict[BroadCategory, CategoryRestriction] = Field(default_factory=dict)
    category_restrictions_provided: bool = False


class NullResolver(BaseModel):
    """Marker resolver for registry entries without executable HemOnc logic."""

    type: Literal["none"] = "none"


class BundlePhase(BaseModel):
    """Declarative phase within a non-variant bundle recipe."""

    phase_id: str = ""
    title: str | None = None
    phase_type: PhaseType
    timing: PhaseTiming = "standalone"
    source_type: BundlePhaseSourceType
    source_entry_id: str | None = None
    source_group_id: str | None = None
    component_terms: list[str] = Field(default_factory=list)
    condition_set: str | None = None
    default_condition_cuis: list[int] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def _ensure_valid_source_fields(self) -> "BundlePhase":
        if self.source_type == "entry":
            if self.source_entry_id is None:
                raise ValueError("Bundle phase with source_type='entry' requires source_entry_id.")
            if self.source_group_id is not None or self.component_terms:
                raise ValueError(
                    "Bundle phase with source_type='entry' cannot declare source_group_id or component_terms."
                )
        elif self.source_type == "group":
            if self.source_group_id is None:
                raise ValueError("Bundle phase with source_type='group' requires source_group_id.")
            if self.source_entry_id is not None or self.component_terms:
                raise ValueError(
                    "Bundle phase with source_type='group' cannot declare source_entry_id or component_terms."
                )
        elif self.source_type == "sig_component":
            if not self.component_terms:
                raise ValueError(
                    "Bundle phase with source_type='sig_component' requires component_terms."
                )
            if self.source_entry_id is not None or self.source_group_id is not None:
                raise ValueError(
                    "Bundle phase with source_type='sig_component' cannot declare source_entry_id or source_group_id."
                )
        return self


class HemOncBundleGroup(BaseModel):
    """Reusable named bundle recipe composed of ordered treatment phases."""

    group_id: str = ""
    basis: BundleBasis = "sig_bundle"
    assembly_mode: AssemblyMode
    sequence_template: str | None = None
    condition_set: str | None = None
    default_condition_cuis: list[int] = Field(default_factory=list)
    phases: list[BundlePhase] = Field(default_factory=list)
    notes: str | None = None


class ResolvedBundlePhase(BaseModel):
    """Bundle phase after condition sets and query references have been resolved."""

    phase_id: str
    title: str | None = None
    phase_type: PhaseType
    timing: PhaseTiming
    source_type: BundlePhaseSourceType
    source_entry_id: str | None = None
    source_group_id: str | None = None
    component_terms: list[str] = Field(default_factory=list)
    resolved_condition_cuis: list[int] = Field(default_factory=list)
    resolved_query: ResolvedHemOncQuery | None = None
    notes: str | None = None


class ResolvedHemOncBundle(BaseModel):
    """Concrete bundle recipe ready for SQL compilation and bundle assembly."""

    group_id: str
    basis: BundleBasis = "sig_bundle"
    assembly_mode: AssemblyMode
    sequence_template: str | None = None
    resolved_condition_cuis: list[int] = Field(default_factory=list)
    phases: list[ResolvedBundlePhase] = Field(default_factory=list)
    notes: str | None = None


class ComponentQueryResolver(BaseModel):
    """Entry resolver that points to either an inline or named variant query."""

    type: Literal["component_query"] = "component_query"
    group_id: str | None = None
    default_condition_cuis: list[int] = Field(default_factory=list)
    component_lists: list[list[str]] = Field(default_factory=list)
    category_restrictions: dict[BroadCategory, CategoryRestriction] = Field(default_factory=dict)
    category_restrictions_provided: bool = Field(default=False, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _mark_category_restrictions_presence(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            data["category_restrictions_provided"] = "category_restrictions" in data
        return data

    @model_validator(mode="after")
    def _ensure_group_or_inline_query(self) -> "ComponentQueryResolver":
        if self.group_id is not None:
            return self
        if self.default_condition_cuis or self.component_lists or self.category_restrictions:
            return self
        raise ValueError(
            "component_query resolver requires either group_id or inline query fields."
        )


class CompositeResolver(BaseModel):
    """Resolver that represents a union of other registry entries."""

    type: Literal["composite"] = "composite"
    members: list[str] = Field(default_factory=list)


class BundleRecipeResolver(BaseModel):
    """Entry resolver that points to a named non-variant bundle recipe."""

    type: Literal["bundle_recipe"] = "bundle_recipe"
    group_id: str


Resolver = Annotated[
    NullResolver | ComponentQueryResolver | CompositeResolver | BundleRecipeResolver,
    Field(discriminator="type"),
]


class RegistryEntry(BaseModel):
    """Single canonical regimen endpoint exposed to the transformation graph."""

    entry_id: str = ""
    public: bool
    stage_scope: list[str] = Field(default_factory=list)
    clinical_group: str
    aliases: list[str] = Field(default_factory=list)
    graph_sources: list[str] = Field(default_factory=list)
    exploratory_sources: list[str] = Field(default_factory=list)
    required_modifiers: list[str] = Field(default_factory=list)
    resolver: Resolver
    notes: str | None = None


class RegimenRegistry(BaseModel):
    """Top-level regimen registry loaded from YAML.

    The registry owns reusable condition sets, named HemOnc query groups,
    named bundle groups, and the public/internal entry catalogue used by the
    graph-driven regimen assignment flow.
    """

    disease: str
    version: int
    condition_sets: dict[str, list[int]] = Field(default_factory=dict)
    hemonc_groups: dict[str, HemOncGroup] = Field(default_factory=dict)
    hemonc_bundle_groups: dict[str, HemOncBundleGroup] = Field(default_factory=dict)
    entries: dict[str, RegistryEntry]

    @model_validator(mode="after")
    def _populate_entry_ids(self) -> "RegimenRegistry":
        for entry_id, entry in self.entries.items():
            entry.entry_id = entry_id
        for group_id, group in self.hemonc_groups.items():
            group.group_id = group_id
            if group.condition_set is not None and group.condition_set not in self.condition_sets:
                raise ValueError(
                    f"HemOnc group '{group_id}' references unknown condition_set '{group.condition_set}'."
                )
        for group_id, group in self.hemonc_bundle_groups.items():
            group.group_id = group_id
            if group.condition_set is not None and group.condition_set not in self.condition_sets:
                raise ValueError(
                    f"HemOnc bundle group '{group_id}' references unknown condition_set '{group.condition_set}'."
                )
            for phase_index, phase in enumerate(group.phases):
                phase.phase_id = phase.phase_id or f"{group_id}_phase_{phase_index + 1}"
                if phase.condition_set is not None and phase.condition_set not in self.condition_sets:
                    raise ValueError(
                        f"HemOnc bundle group '{group_id}' phase '{phase.phase_id}' "
                        f"references unknown condition_set '{phase.condition_set}'."
                    )
                if phase.source_type == "group" and phase.source_group_id not in self.hemonc_groups:
                    raise ValueError(
                        f"HemOnc bundle group '{group_id}' phase '{phase.phase_id}' "
                        f"references unknown HemOnc group '{phase.source_group_id}'."
                    )
                if phase.source_type == "entry" and phase.source_entry_id not in self.entries:
                    raise ValueError(
                        f"HemOnc bundle group '{group_id}' phase '{phase.phase_id}' "
                        f"references unknown registry entry '{phase.source_entry_id}'."
                    )
        for entry_id, entry in self.entries.items():
            resolver = entry.resolver
            if (
                isinstance(resolver, ComponentQueryResolver)
                and resolver.group_id is not None
                and resolver.group_id not in self.hemonc_groups
            ):
                raise ValueError(
                    f"Registry entry '{entry_id}' references unknown HemOnc group '{resolver.group_id}'."
                )
            if (
                isinstance(resolver, BundleRecipeResolver)
                and resolver.group_id not in self.hemonc_bundle_groups
            ):
                raise ValueError(
                    f"Registry entry '{entry_id}' references unknown HemOnc bundle group '{resolver.group_id}'."
                )
        return self

    def resolve_alias(self, entry_id: str) -> str:
        """Return the canonical entry id for either an entry id or alias."""

        if entry_id in self.entries:
            return entry_id
        for canonical_id, entry in self.entries.items():
            if entry_id in entry.aliases:
                return canonical_id
        raise KeyError(f"Unknown regimen registry entry or alias: {entry_id}")

    def get_entry(self, entry_id: str) -> RegistryEntry:
        """Return a registry entry by canonical id or accepted alias."""

        return self.entries[self.resolve_alias(entry_id)]

    def get_hemonc_group(self, group_id: str) -> HemOncGroup:
        """Return a named variant-query group."""

        try:
            return self.hemonc_groups[group_id]
        except KeyError as exc:
            raise KeyError(f"Unknown HemOnc group: {group_id}") from exc

    def get_hemonc_bundle_group(self, group_id: str) -> HemOncBundleGroup:
        """Return a named non-variant bundle group."""

        try:
            return self.hemonc_bundle_groups[group_id]
        except KeyError as exc:
            raise KeyError(f"Unknown HemOnc bundle group: {group_id}") from exc

    def resolve_group_query(self, group_id: str) -> ResolvedHemOncQuery:
        """Resolve a named HemOnc query group into a concrete query object."""

        group = self.get_hemonc_group(group_id)
        default_condition_cuis = (
            list(group.default_condition_cuis)
            if group.default_condition_cuis
            else list(self.condition_sets.get(group.condition_set or "", ()))
        )
        return ResolvedHemOncQuery(
            group_id=group.group_id,
            basis=group.basis,
            default_condition_cuis=default_condition_cuis,
            component_lists=[list(component_list) for component_list in group.component_lists],
            category_restrictions=group.category_restrictions,
            category_restrictions_provided=group.category_restrictions_provided,
        )

    def resolve_hemonc_query(self, entry_id: str) -> ResolvedHemOncQuery | None:
        """Resolve an entry into a concrete variant-backed HemOnc query.

        Returns `None` for non-query entries such as composites or bundle-only
        recipes.
        """

        entry = self.get_entry(entry_id)
        resolver = entry.resolver
        if not isinstance(resolver, ComponentQueryResolver):
            return None

        if resolver.group_id is not None:
            return self.resolve_group_query(resolver.group_id)

        return ResolvedHemOncQuery(
            default_condition_cuis=list(resolver.default_condition_cuis),
            component_lists=[list(component_list) for component_list in resolver.component_lists],
            category_restrictions=resolver.category_restrictions,
            category_restrictions_provided=resolver.category_restrictions_provided,
        )

    def resolve_hemonc_bundle(self, entry_id: str) -> ResolvedHemOncBundle | None:
        """Resolve an entry into a concrete bundle recipe with resolved phases.

        Returns `None` for entries that do not use the `bundle_recipe` resolver.
        """

        entry = self.get_entry(entry_id)
        resolver = entry.resolver
        if not isinstance(resolver, BundleRecipeResolver):
            return None

        group = self.get_hemonc_bundle_group(resolver.group_id)
        resolved_condition_cuis = (
            list(group.default_condition_cuis)
            if group.default_condition_cuis
            else list(self.condition_sets.get(group.condition_set or "", ()))
        )
        resolved_phases: list[ResolvedBundlePhase] = []
        for phase in group.phases:
            phase_condition_cuis = (
                list(phase.default_condition_cuis)
                if phase.default_condition_cuis
                else list(self.condition_sets.get(phase.condition_set or "", ()))
            ) or list(resolved_condition_cuis)
            resolved_query: ResolvedHemOncQuery | None = None
            if phase.source_type == "entry":
                resolved_query = self.resolve_hemonc_query(phase.source_entry_id or "")
                if resolved_query is None:
                    raise ValueError(
                        f"Bundle phase '{phase.phase_id}' references non-queryable entry "
                        f"'{phase.source_entry_id}'."
                    )
                if not phase_condition_cuis:
                    phase_condition_cuis = list(resolved_query.default_condition_cuis)
            elif phase.source_type == "group":
                resolved_query = self.resolve_group_query(phase.source_group_id or "")
                if not phase_condition_cuis:
                    phase_condition_cuis = list(resolved_query.default_condition_cuis)

            resolved_phases.append(
                ResolvedBundlePhase(
                    phase_id=phase.phase_id,
                    title=phase.title,
                    phase_type=phase.phase_type,
                    timing=phase.timing,
                    source_type=phase.source_type,
                    source_entry_id=phase.source_entry_id,
                    source_group_id=phase.source_group_id,
                    component_terms=list(phase.component_terms),
                    resolved_condition_cuis=phase_condition_cuis,
                    resolved_query=resolved_query,
                    notes=phase.notes,
                )
            )

        return ResolvedHemOncBundle(
            group_id=group.group_id,
            basis=group.basis,
            assembly_mode=group.assembly_mode,
            sequence_template=group.sequence_template,
            resolved_condition_cuis=resolved_condition_cuis,
            phases=resolved_phases,
            notes=group.notes,
        )

    def known_entry_ids(self) -> set[str]:
        """Return all canonical entry ids plus any configured aliases."""

        known = set(self.entries)
        for entry in self.entries.values():
            known.update(entry.aliases)
        return known

def load_regimen_registry(path: str | Path) -> RegimenRegistry:
    """Load and validate a regimen registry YAML file."""

    registry_path = Path(path)
    with registry_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return RegimenRegistry.model_validate(raw["registry"])
