from __future__ import annotations
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class CategoryRestriction(BaseModel):
    type: Literal["exact", "min"]
    amount: int


class NullResolver(BaseModel):
    type: Literal["none"] = "none"


class ComponentQueryResolver(BaseModel):
    type: Literal["component_query"] = "component_query"
    default_condition_cuis: list[int] = Field(default_factory=list)
    component_lists: list[list[str]] = Field(default_factory=list)
    category_restrictions: dict[str, CategoryRestriction] = Field(default_factory=dict)


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
    entries: dict[str, RegistryEntry]

    @model_validator(mode="after")
    def _populate_entry_ids(self) -> "RegimenRegistry":
        for entry_id, entry in self.entries.items():
            entry.entry_id = entry_id
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
