from __future__ import annotations
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class CategoryRestriction(BaseModel):
    type: Literal["exact", "min"]
    amount: int


class HemOncGroup(BaseModel):
    group_id: str = ""
    basis: Literal["variant"] = "variant"
    condition_set: str | None = None
    default_condition_cuis: list[int] = Field(default_factory=list)
    component_lists: list[list[str]] = Field(default_factory=list)
    category_restrictions: dict[str, CategoryRestriction] = Field(default_factory=dict)
    notes: str | None = None


class ResolvedHemOncQuery(BaseModel):
    group_id: str | None = None
    basis: Literal["variant"] = "variant"
    default_condition_cuis: list[int] = Field(default_factory=list)
    component_lists: list[list[str]] = Field(default_factory=list)
    category_restrictions: dict[str, CategoryRestriction] = Field(default_factory=dict)


class NullResolver(BaseModel):
    type: Literal["none"] = "none"


class ComponentQueryResolver(BaseModel):
    type: Literal["component_query"] = "component_query"
    group_id: str | None = None
    default_condition_cuis: list[int] = Field(default_factory=list)
    component_lists: list[list[str]] = Field(default_factory=list)
    category_restrictions: dict[str, CategoryRestriction] = Field(default_factory=dict)

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
    type: Literal["composite"] = "composite"
    members: list[str] = Field(default_factory=list)


Resolver = Annotated[
    NullResolver | ComponentQueryResolver | CompositeResolver,
    Field(discriminator="type"),
]


class RegistryEntry(BaseModel):
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
    disease: str
    version: int
    condition_sets: dict[str, list[int]] = Field(default_factory=dict)
    hemonc_groups: dict[str, HemOncGroup] = Field(default_factory=dict)
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
        return self

    def resolve_alias(self, entry_id: str) -> str:
        if entry_id in self.entries:
            return entry_id
        for canonical_id, entry in self.entries.items():
            if entry_id in entry.aliases:
                return canonical_id
        raise KeyError(f"Unknown regimen registry entry or alias: {entry_id}")

    def get_entry(self, entry_id: str) -> RegistryEntry:
        return self.entries[self.resolve_alias(entry_id)]

    def get_hemonc_group(self, group_id: str) -> HemOncGroup:
        try:
            return self.hemonc_groups[group_id]
        except KeyError as exc:
            raise KeyError(f"Unknown HemOnc group: {group_id}") from exc

    def resolve_hemonc_query(self, entry_id: str) -> ResolvedHemOncQuery | None:
        entry = self.get_entry(entry_id)
        resolver = entry.resolver
        if not isinstance(resolver, ComponentQueryResolver):
            return None

        if resolver.group_id is not None:
            group = self.get_hemonc_group(resolver.group_id)
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
            )

        return ResolvedHemOncQuery(
            default_condition_cuis=list(resolver.default_condition_cuis),
            component_lists=[list(component_list) for component_list in resolver.component_lists],
            category_restrictions=resolver.category_restrictions,
        )

    def known_entry_ids(self) -> set[str]:
        known = set(self.entries)
        for entry in self.entries.values():
            known.update(entry.aliases)
        return known

def load_regimen_registry(path: str | Path) -> RegimenRegistry:
    registry_path = Path(path)
    with registry_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return RegimenRegistry.model_validate(raw["registry"])
