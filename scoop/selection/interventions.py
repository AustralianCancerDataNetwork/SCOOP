from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Mapping, TypeAlias

from yaml import safe_load

from ..protocols.spec import ProtocolSpec


class SequenceTemplate(StrEnum):
    """Observed bundle-level recipe labels from the pre-refactor registry."""

    ADJUVANT = "adjuvant"
    POSTOPERATIVE = "postoperative"
    CONCURRENT = "concurrent"
    SEQUENTIAL = "sequential"


class PhaseTiming(StrEnum):
    """Observed timing value attached to one recipe phase."""

    PRE = "pre"
    POST = "post"
    CONCURRENT = "concurrent"


@dataclass(frozen=True)
class PhaseRef:
    phase_id: str
    technique: str | None = None

    def __post_init__(self) -> None:
        if not self.phase_id:
            raise ValueError("PhaseRef.phase_id cannot be empty")


@dataclass(frozen=True)
class ProtocolRef:
    protocol_id: str

    def __post_init__(self) -> None:
        if not self.protocol_id:
            raise ValueError("ProtocolRef.protocol_id cannot be empty")


@dataclass(frozen=True)
class Choice:
    intervention_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.intervention_ids) < 2:
            raise ValueError("Choice must contain at least two intervention ids")
        if len(set(self.intervention_ids)) != len(self.intervention_ids):
            raise ValueError("Choice members must be distinct")


@dataclass(frozen=True)
class RecipeStep:
    reference: PhaseRef | ProtocolRef
    timing: PhaseTiming | None = None


@dataclass(frozen=True)
class Recipe:
    steps: tuple[RecipeStep, ...]
    sequence_template: SequenceTemplate | None = None

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("Recipe must contain at least one step")


@dataclass(frozen=True)
class NonHemOnc:
    intervention_id: str
    label: str
    concept_domain: str | None = None
    concept_value: str | None = None

    def __post_init__(self) -> None:
        if not self.intervention_id:
            raise ValueError("NonHemOnc.intervention_id cannot be empty")
        if not self.label:
            raise ValueError("NonHemOnc.label cannot be empty")


ResolutionForm: TypeAlias = PhaseRef | ProtocolRef | Choice | Recipe | NonHemOnc


@dataclass(frozen=True)
class InterventionSpec:
    intervention_id: str
    resolution: ResolutionForm
    requires: tuple[str, ...] = ()
    source_profile: str | None = None
    internal: bool = False

    def __post_init__(self) -> None:
        if not self.intervention_id:
            raise ValueError("InterventionSpec.intervention_id cannot be empty")
        if len(set(self.requires)) != len(self.requires):
            raise ValueError(
                f"Intervention {self.intervention_id!r} repeats a required context field"
            )
        if any(not field for field in self.requires):
            raise ValueError(
                f"Intervention {self.intervention_id!r} has an empty context field"
            )


@dataclass(frozen=True)
class ResolvedIntervention:
    intervention_id: str
    protocols: tuple[ProtocolSpec, ...] = ()
    phase_refs: tuple[PhaseRef, ...] = ()
    recipe: Recipe | None = None
    non_hemonc: NonHemOnc | None = None
    alternatives: tuple["ResolvedIntervention", ...] = ()
    technique: str | None = None


@dataclass(frozen=True)
class InterventionCatalog:
    """Declared intervention mappings and their profile-level validation metadata."""

    profile_id: str
    specs: Mapping[str, InterventionSpec]
    state_node_exemptions: frozenset[str] = frozenset()
    protocols: Mapping[str, ProtocolSpec] = field(default_factory=dict)
    max_choice_depth: int = 8

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        protocols: Mapping[str, ProtocolSpec] | None = None,
    ) -> "InterventionCatalog":
        path = Path(path)
        with path.open("r", encoding="utf-8") as handle:
            raw = safe_load(handle) or {}
        if not isinstance(raw, dict):
            raise ValueError("intervention YAML must contain a mapping")
        allowed = {"profile_id", "version", "state_node_exemptions", "interventions"}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(
                "intervention catalog has unknown field(s): "
                f"{sorted(unknown)}"
            )
        missing = {"profile_id", "version", "interventions"} - set(raw)
        if missing:
            raise ValueError(f"intervention catalog is missing: {sorted(missing)}")
        profile_id = raw["profile_id"]
        declarations = raw["interventions"]
        if not isinstance(declarations, list):
            raise ValueError("interventions must be a list of identified objects")

        declared_ids = [data.get("intervention_id") for data in declarations]
        duplicates = sorted({iid for iid in declared_ids if declared_ids.count(iid) > 1})
        if duplicates:
            raise ValueError(
                f"intervention catalog {profile_id!r} has duplicate "
                f"intervention_id(s): {duplicates}"
            )

        specs = {
            data["intervention_id"]: _parse_intervention(
                data["intervention_id"],
                data,
                source_profile=profile_id,
            )
            for data in declarations
        }
        catalog = cls(
            profile_id=profile_id,
            specs=specs,
            state_node_exemptions=frozenset(
                raw.get("state_node_exemptions", ())
            ),
            protocols=protocols or {},
        )
        catalog.validate_references()
        return catalog

    def validate_references(self) -> None:
        """Validate phase/protocol references and recursive choice structure."""

        referenced_internal: set[str] = set()
        for spec in self.specs.values():
            for member in _choice_members(spec.resolution):
                if member not in self.specs:
                    raise ValueError(
                        f"Intervention {spec.intervention_id!r} references undeclared "
                        f"choice member {member!r}"
                    )
                referenced_internal.add(member)
            for reference in _recipe_references(spec.resolution):
                self._validate_reference(reference, spec.intervention_id)

            if isinstance(spec.resolution, PhaseRef | ProtocolRef):
                self._validate_reference(spec.resolution, spec.intervention_id)

        visiting: list[str] = []
        visited: set[str] = set()

        def visit(intervention_id: str, depth: int) -> None:
            if depth > self.max_choice_depth:
                raise ValueError(
                    "Intervention choice depth exceeds the configured bound "
                    f"({self.max_choice_depth}) at {intervention_id!r}"
                )
            if intervention_id in visiting:
                cycle = " -> ".join([*visiting, intervention_id])
                raise ValueError(f"Intervention choice cycle: {cycle}")
            if intervention_id in visited:
                return
            visiting.append(intervention_id)
            spec = self.specs[intervention_id]
            for member in _choice_members(spec.resolution):
                visit(member, depth + 1)
            visiting.pop()
            visited.add(intervention_id)

        for intervention_id in self.specs:
            visit(intervention_id, 0)

        unused_internal = {
            spec.intervention_id
            for spec in self.specs.values()
            if spec.internal and spec.intervention_id not in referenced_internal
        }
        if unused_internal:
            raise ValueError(
                "Internal interventions are not referenced by a Choice: "
                f"{sorted(unused_internal)}"
            )

    def validate_coverage(self, *, graph_nodes: set[str]) -> None:
        """Validate endpoint coverage and context against profile graph nodes."""

        treatment_nodes = {
            node.split(".", 1)[1]
            for node in graph_nodes
            if node.startswith("treatment_episode.")
        }
        endpoints = treatment_nodes - self.state_node_exemptions
        missing = endpoints - set(self.specs)
        if missing:
            raise ValueError(
                f"Profile {self.profile_id!r} has undeclared intervention endpoint(s): "
                f"{sorted(missing)}"
            )

        undeclared = {
            spec.intervention_id
            for spec in self.specs.values()
            if not spec.internal and spec.intervention_id not in endpoints
        }
        if undeclared:
            raise ValueError(
                f"Profile {self.profile_id!r} declares intervention(s) with no graph "
                f"endpoint: {sorted(undeclared)}"
            )

        missing_context = {
            field
            for spec in self.specs.values()
            for field in spec.requires
            if field not in graph_nodes
        }
        if missing_context:
            raise ValueError(
                f"Profile {self.profile_id!r} intervention context field(s) are not "
                f"produced by any graph node: {sorted(missing_context)}"
            )

        missing_exemptions = {
            exemption
            for exemption in self.state_node_exemptions
            if f"treatment_episode.{exemption}" not in graph_nodes
        }
        if missing_exemptions:
            raise ValueError(
                f"Profile {self.profile_id!r} names state-node exemption(s) that do "
                f"not exist: {sorted(missing_exemptions)}"
            )

    def _validate_reference(
        self,
        reference: PhaseRef | ProtocolRef,
        intervention_id: str,
    ) -> None:
        if isinstance(reference, PhaseRef):
            if not self.protocols:
                return
            known_phases = {
                phase_id
                for protocol in self.protocols.values()
                for phase_id in protocol.source_phases
            }
            if reference.phase_id not in known_phases:
                raise ValueError(
                    f"Intervention {intervention_id!r} references unknown phase "
                    f"{reference.phase_id!r}"
                )
        elif reference.protocol_id not in self.protocols:
            if self.protocols:
                raise ValueError(
                    f"Intervention {intervention_id!r} references unknown protocol "
                    f"{reference.protocol_id!r}"
                )


def _choice_members(resolution: ResolutionForm) -> tuple[str, ...]:
    return resolution.intervention_ids if isinstance(resolution, Choice) else ()


def _recipe_references(resolution: ResolutionForm) -> tuple[PhaseRef | ProtocolRef, ...]:
    if not isinstance(resolution, Recipe):
        return ()
    return tuple(step.reference for step in resolution.steps)


def _parse_intervention(
    intervention_id: str,
    raw: object,
    *,
    source_profile: str,
) -> InterventionSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"Intervention {intervention_id!r} must be a mapping")
    allowed = {"intervention_id", "requires", "resolution", "internal"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(
            f"Intervention {intervention_id!r} has unknown field(s): {sorted(unknown)}"
        )
    requires = tuple(raw.get("requires", ()))
    if not all(isinstance(value, str) for value in requires):
        raise ValueError(f"Intervention {intervention_id!r} requires must be strings")
    return InterventionSpec(
        intervention_id=intervention_id,
        resolution=_parse_resolution(intervention_id, raw.get("resolution")),
        requires=requires,
        source_profile=source_profile,
        internal=bool(raw.get("internal", False)),
    )


def _parse_resolution(intervention_id: str, raw: object) -> ResolutionForm:
    if not isinstance(raw, dict) or len(raw) != 1:
        raise ValueError(
            f"Intervention {intervention_id!r} must declare exactly one resolution form"
        )
    kind, value = next(iter(raw.items()))
    if kind == "phase":
        if isinstance(value, dict) and set(value) <= {"id", "technique"}:
            return PhaseRef(value["id"], value.get("technique"))
        raise ValueError(f"Invalid phase resolution for {intervention_id!r}")
    if kind == "protocol":
        if not isinstance(value, str):
            raise ValueError(f"Invalid protocol resolution for {intervention_id!r}")
        return ProtocolRef(value)
    if kind == "choice":
        if (
            not isinstance(value, dict)
            or set(value) != {"intervention_ids"}
            or not isinstance(value["intervention_ids"], list)
            or not all(isinstance(member, str) for member in value["intervention_ids"])
        ):
            raise ValueError(f"Invalid choice resolution for {intervention_id!r}")
        return Choice(tuple(value["intervention_ids"]))
    if kind == "recipe":
        if not isinstance(value, dict):
            raise ValueError(f"Invalid recipe resolution for {intervention_id!r}")
        allowed = {"sequence_template", "steps"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(
                f"Recipe {intervention_id!r} has unknown field(s): {sorted(unknown)}"
            )
        steps = tuple(
            _parse_recipe_step(intervention_id, step) for step in value["steps"]
        )
        sequence = value.get("sequence_template")
        return Recipe(
            steps=steps,
            sequence_template=SequenceTemplate(sequence) if sequence is not None else None,
        )
    if kind == "non_hemonc":
        if not isinstance(value, dict):
            raise ValueError(f"Invalid non_hemonc resolution for {intervention_id!r}")
        allowed = {"label", "concept_domain", "concept_value"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(
                f"NonHemOnc {intervention_id!r} has unknown field(s): {sorted(unknown)}"
            )
        return NonHemOnc(
            intervention_id=intervention_id,
            label=value.get("label", ""),
            concept_domain=value.get("concept_domain"),
            concept_value=value.get("concept_value"),
        )
    raise ValueError(
        f"Intervention {intervention_id!r} has unknown resolution form {kind!r}"
    )


def _parse_recipe_step(intervention_id: str, raw: object) -> RecipeStep:
    if not isinstance(raw, dict) or set(raw) - {"ref", "timing"}:
        raise ValueError(f"Invalid recipe step for {intervention_id!r}")
    ref = raw.get("ref")
    if not isinstance(ref, dict) or len(ref) != 1:
        raise ValueError(f"Recipe step in {intervention_id!r} needs one ref")
    kind, value = next(iter(ref.items()))
    if kind == "phase":
        if not isinstance(value, dict) or set(value) - {"id", "technique"}:
            raise ValueError(f"Invalid phase recipe reference in {intervention_id!r}")
        reference = PhaseRef(value["id"], value.get("technique"))
    elif kind == "protocol":
        reference = ProtocolRef(value)
    else:
        raise ValueError(f"Invalid recipe reference {kind!r} in {intervention_id!r}")
    timing = raw.get("timing")
    return RecipeStep(
        reference=reference,
        timing=PhaseTiming(timing) if timing is not None else None,
    )
