from dataclasses import dataclass
from pydantic import BaseModel
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional
from collections import defaultdict
from yaml import safe_load
import scoop.protocols.descriptions as desc
from scoop.protocols.hemonc.gateway import HemOncGateway
from scoop.protocols.spec import ConditionRef, PhaseChoiceSpec, PhaseSpec, ProtocolSpec


@dataclass(frozen=True)
class SharedComponent:
    """Reason for a candidate created by an optional shared-component phase."""

    component: str


@dataclass(frozen=True)
class OptionalPhaseOmitted:
    """Reason for retaining the protocol without its optional phase."""

    phase_id: str


FanOutReason = SharedComponent | OptionalPhaseOmitted


@dataclass(frozen=True)
class NoCandidates:
    """Typed evidence that one phase query returned no operational candidates."""

    phase_id: str
    query: str = "HemOncGateway.candidates_for_phase"

class RegimenCandidate(BaseModel):
    """
    A collection of sigs which can be the basis of a treatment episode.

    This may or may not be equivalent to a variant.
    """
    name: str
    variant_cui: Optional[int]
    sigs: list[Any]
    regimen: str
    components: set[str]
    phase: str | None = None
    study: str | None = None
    variant: str | None = None
    source_phase_id: str | None = None
    # NOTE: Rarely has the surgery sig been included w/ a hemonc variant
    # surgery component sig will need to be added in majority of cases
    # Options: context-dependent? independent? How much more restrictive is dependency?

    @classmethod
    def from_variant(cls, variant: Any) -> "RegimenCandidate":
        name = variant.regimen + " " + variant.variant
        variant_cui = int(variant.variant_cui)
        sigs = [sig for sig in variant.component_sigs]
        regimen = variant.regimen
        components = {sig.component for sig in variant.component_sigs}
        return cls(name=name, variant_cui = variant_cui, 
                   sigs=sigs, regimen=regimen, components=components)

    def __repr__(self) -> str:
        header = f"{self.name} (CUI: {self.variant_cui})"
        sig_lines = "\n".join(
            desc.sig_component_obj(**sig.__dict__)
            for sig in self.sigs
        )
        return f"{header}\n{sig_lines}"

    def _repr_html_(self) -> str:
        # Compromise between cluttering original print w/ markup and not formatting
        children_html = "<ul style='margin-left:15px; padding-left:0;'>"
        for sig in self.sigs:
            original_text = desc.sig_component_obj(**sig.__dict__)
            html_w_bold_component = bold_before_colon(original_text)
            children_html += f"<li>{html_w_bold_component}</li>"
        children_html += "</ul>"

        return render_collapsible(
            header=f"{self.name} (CUI: {self.variant_cui})",
            children=[children_html],
            count=len(self.sigs),
            color="#fafafa",
            data_attrs={"regimen": self.regimen},
            open_by_default=False
        )


class PhaseCandidates(BaseModel):
    """
    A collection of sig bundles valid for the same treatment episode

    Note that phase order is driven by order in yaml
    """
    index: int = 1
    label: Optional[str] = None
    options: list[RegimenCandidate]
    # original_options: Optional[tuple[RegimenCandidate, ...]] = None
    # filters_passed: Optional[dict[str, Any]] = Field(default_factory=dict)
    # filters_failed: Optional[dict[str, Any]] = Field(default_factory=dict)
    conditions: list[Any]
    categories: set[str] | None = None
    no_candidates: NoCandidates | None = None


    @classmethod
    def from_yaml(
        cls,
        index: int,
        payload: Mapping[str, Any] | None = None,
    ) -> "PhaseCandidates":
        """Load one serialized phase candidate block.

        Candidate assembly itself is gateway-backed and therefore does not read
        YAML.  This loader is intentionally limited to the portable display /
        fixture representation used by SCOOP; it is not a second protocol
        registry format.
        """

        data = dict(payload or {})
        options = [RegimenCandidate.model_validate(option) for option in data.get("options", [])]
        no_candidates = data.get("no_candidates")
        if no_candidates is not None and not isinstance(no_candidates, NoCandidates):
            if not isinstance(no_candidates, Mapping):
                raise TypeError("no_candidates must be a mapping")
            no_candidates = NoCandidates(**no_candidates)
        return cls(
            index=index,
            label=data.get("label"),
            options=options,
            conditions=list(data.get("conditions", [])),
            categories=set(data["categories"]) if data.get("categories") is not None else None,
            no_candidates=no_candidates,
        )

    @classmethod
    def from_tuple_dict(
        cls,
        tuple_dict: Mapping[tuple[Any, ...], list[Any]],
        *,
        index: int = 1,
        label: str | None = None,
        conditions: list[Any] | None = None,
    ) -> "PhaseCandidates":
        """Convert legacy grouped query rows into typed phase candidates.

        The tuple-key form is retained as a narrow ingestion helper for old
        query fixtures.  It does not participate in runtime protocol loading.
        Keys follow ``(phase, variant_cui, study, regimen, variant)``.
        """

        options: list[RegimenCandidate] = []
        for key, sigs in tuple_dict.items():
            if len(key) != 5:
                raise ValueError(
                    "candidate tuple keys must contain phase, variant CUI, "
                    "study, regimen, and variant"
                )
            phase, variant_cui, study, regimen, variant = key
            try:
                normalized_cui = int(variant_cui) if variant_cui is not None else None
            except (TypeError, ValueError):
                normalized_cui = None
            normalized_variant = None if variant in (None, "None") else str(variant)
            normalized_study = None if study in (None, "None") else str(study)
            normalized_regimen = str(regimen)
            options.append(
                RegimenCandidate(
                    name=(
                        f"{normalized_regimen} {normalized_variant}"
                        if normalized_variant
                        else normalized_regimen
                    ),
                    variant_cui=normalized_cui,
                    sigs=list(sigs),
                    regimen=normalized_regimen,
                    components={getattr(sig, "component", str(sig)) for sig in sigs},
                    phase=None if phase in (None, "None") else str(phase),
                    study=normalized_study,
                    variant=normalized_variant,
                )
            )
        return cls(
            index=index,
            label=label,
            options=_sort_candidates(options),
            conditions=list(conditions or []),
        )

    def __repr__(self) -> str:
        header = f"Phase {self.index}" if not self.label else f"{self.label} (Phase {self.index})"
        options = "\n".join(f"{sb.name} {sb.variant_cui}" for sb in self.options)
        return f"{header}\n{options}"

    def _repr_html_(self) -> str:
        children_html = self._render_regimens()

        header = self.label if self.label else f"Phase {self.index}"

        return render_collapsible(
            header=header,
            children=children_html,
            count=len(children_html),
            color="#fdfdfd",
            data_attrs={"phase": self.index},
            open_by_default=False,
        )


    def _render_regimens(self) -> list[str]:
        """Render all regimen groups with hues avoiding red."""
        regimen_groups: dict[str, list[RegimenCandidate]] = defaultdict(list)
        for sb in self.options:
            regimen_groups[sb.regimen].append(sb)

        # Note that "cisplatin" is different to "cisplatin," - this is helpful
        first_words = sorted(set(r.split()[0].upper() for r in regimen_groups.keys()))
        hue_map = map_strings_hues(first_words, hue_start=60, hue_end=300) # hues avoiding red

        return [self._render_single_regimen(regimen, bundles, hue_map)
                for regimen, bundles in regimen_groups.items()]

    def _render_single_regimen(
        self,
        regimen: str,
        bundles: list["RegimenCandidate"],
        hue_map: dict[str, int],
    ) -> str:
        """Render a single regimen block as HTML."""
        bundle_count = len(bundles)
        children = [sb._repr_html_() for sb in bundles]

        first_word = regimen.split()[0].upper()
        hue = hue_map[first_word]
        color = f"hsl({hue}, 60%, 90%)"

        return render_collapsible(
            header=regimen,
            children=children,
            count=bundle_count,
            color=color,
            data_attrs={"regimen": regimen},
            open_by_default=False
        )
        

class ProtocolCandidate(BaseModel):
    """
    A protocol may comprise one or more treatment phases.
    """
    name: str
    phases: list[PhaseCandidates]
    requirement_type: str | None = None
    requirement_element: str | None = None
    fan_out_reason: FanOutReason | None = None
    selection_requires: tuple[str, ...] = ()

    @classmethod
    def from_yaml(
        cls,
        name: str,
        payload: Mapping[str, Any] | None = None,
    ) -> "ProtocolCandidate":
        """Load a portable serialized protocol candidate."""

        data = dict(payload or {})
        raw_phases = data.get("phases", [])
        if not isinstance(raw_phases, list):
            raise TypeError("protocol candidate phases must be a list")
        phases = [
            PhaseCandidates.from_yaml(index, phase)
            for index, phase in enumerate(raw_phases, start=1)
        ]
        return cls(
            name=name,
            phases=phases,
            requirement_type=data.get("requirement_type"),
            requirement_element=data.get("requirement_element"),
            selection_requires=tuple(data.get("selection_requires", ())),
        )

    def __repr__(self) -> str:
        return f"Treatment protocol: {self.name} ({len(self.phases)} phase(s))"

    def _repr_html_(self) -> str:
        if len(self.phases) == 1:
            phase = self.phases[0]
            regimen_children = phase._render_regimens()
            
            return render_collapsible(
                header=f"Treatment protocol: {self.name}",
                children=regimen_children,
                count=len(regimen_children), 
                color="#fdfdfd",
                data_attrs={"treatment-protocol": self.name},
                open_by_default=False,
            )
        else:
            # Multi-phase: scenario header + phase headers
            children = [phase._repr_html_() for phase in self.phases]
    
            return render_collapsible(
                header=f"Treatment Scenario: {self.name}",
                children=children,
                count=len(children),
                color="#fdfdfd",
                data_attrs={"clinical-scenario": self.name},
                open_by_default=False,
            )


        
class CandidateSet(BaseModel):
    """
    A collection of Protocols valid for a disease state grouping
    e.g. nsclc stage 2-3 resectable
    """
    name: str
    # conditions: list
    treatment_scenarios: dict[str, ProtocolCandidate]

    @classmethod
    def from_yaml(cls, yaml_path: str, prepend: str = "") -> "CandidateSet":
        path = Path(yaml_path)
        with path.open(encoding="utf-8") as handle:
            payload = safe_load(handle) or {}
        if not isinstance(payload, Mapping):
            raise TypeError("candidate set YAML must contain a mapping")

        name = str(payload.get("name") or prepend or path.stem)
        raw_scenarios = payload.get("treatment_scenarios", payload.get("scenarios", {}))
        if not isinstance(raw_scenarios, Mapping):
            raise TypeError("candidate set scenarios must be a mapping")
        scenarios = {
            str(scenario_name): ProtocolCandidate.from_yaml(
                str(scenario_name), scenario_payload
            )
            for scenario_name, scenario_payload in raw_scenarios.items()
        }
        return cls(name=name, treatment_scenarios=scenarios)

    def _repr_html_(self) -> str:
        children = []
        for scenario in self.treatment_scenarios.values():
            children.append(scenario._repr_html_())

        return render_collapsible(
            header=f"Treatment group: {self.name}",
            children=children,
            count=len(self.treatment_scenarios.keys()),
            color="#eef6ff",
            data_attrs={"group": self.name},
            open_by_default=False
        )


def assemble(spec: ProtocolSpec, gateway: HemOncGateway) -> list[ProtocolCandidate]:
    """Assemble protocol candidates using only the injected HemOnc gateway.

    The function deliberately owns no SQL knowledge. It resolves source phases once,
    preserves choice provenance, and makes optional-phase fan-out explicit.
    """

    if spec is None:
        raise NotImplementedError(
            "Protocol candidate assembly requires a ProtocolSpec in Phase 4C."
        )

    phase_specs = getattr(spec, "source_phases", {}) or {}
    phase_choices = getattr(spec, "phase_choices", {}) or {}
    resolved: dict[str, PhaseCandidates] = {}

    def source_phase(phase_id: str) -> PhaseSpec:
        return phase_specs.get(phase_id, PhaseSpec(source_phase_id=phase_id))

    def candidates_for(phase_id: str) -> list[RegimenCandidate]:
        phase = source_phase(phase_id)
        candidates = list(gateway.candidates_for_phase(phase))
        return _group_candidates(candidates, source_phase_id=phase_id)

    def phase_for(phase_id: str) -> PhaseCandidates:
        if phase_id in resolved:
            return resolved[phase_id].model_copy(deep=True)
        options = candidates_for(phase_id)
        phase = source_phase(phase_id)
        result = PhaseCandidates(
            index=1,
            label=phase_id,
            options=options,
            conditions=phase.conditions.to_value(),
            categories=phase.delivered_categories(),
            no_candidates=NoCandidates(phase_id=phase_id) if not options else None,
        )
        resolved[phase_id] = result
        return result.model_copy(deep=True)

    def choice_phase(choice_id: str, choice: PhaseChoiceSpec) -> PhaseCandidates:
        member_phases = [phase_for(member_id) for member_id in choice.source_phase_ids]
        options = [
            candidate.model_copy(update={"source_phase_id": member_id})
            for member_id, member_phase in zip(choice.source_phase_ids, member_phases)
            for candidate in member_phase.options
        ]
        conditions = _union_conditions(
            source_phase(member_id).conditions for member_id in choice.source_phase_ids
        )
        categories = {
            category
            for member_id in choice.source_phase_ids
            for category in (source_phase(member_id).delivered_categories() or set())
        }
        return PhaseCandidates(
            index=1,
            label=choice_id,
            options=options,
            conditions=conditions,
            categories=categories or None,
            no_candidates=NoCandidates(phase_id=choice_id) if not options else None,
        )

    def phase_for_protocol(phase_id: str) -> PhaseCandidates:
        choice = phase_choices.get(phase_id)
        return choice_phase(phase_id, choice) if choice is not None else phase_for(phase_id)

    base_phases = {
        phase_id: phase_for_protocol(phase_id)
        for phase_id in spec.source_phase_ids
    }

    optional = spec.optional_phases or {}
    if not optional:
        return [
            ProtocolCandidate(
                name=spec.protocol_id,
                phases=_ordered_phases(spec.source_phase_ids, base_phases),
                selection_requires=spec.selection_requires,
            )
        ]

    optional_id, optional_spec = next(iter(optional.items()))
    required_id = optional_spec.criteria.as_phase
    optional_phase = base_phases.get(optional_id)
    if optional_phase is None:
        optional_phase = phase_for_protocol(optional_id)
    shared_components = sorted(
        {
            component
            for candidate in optional_phase.options
            for component in candidate.components
        }
    )

    fan_out: list[ProtocolCandidate] = []
    for component in shared_components:
        restricted = {
            phase_id: _restrict_phase(phase, component)
            if phase_id in {required_id, optional_id}
            else phase.model_copy(deep=True)
            for phase_id, phase in base_phases.items()
        }
        fan_out.append(
            ProtocolCandidate(
                name=spec.protocol_id,
                phases=_ordered_phases(spec.source_phase_ids, restricted),
                fan_out_reason=SharedComponent(component),
                selection_requires=spec.selection_requires,
            )
        )

    omitted = {
        phase_id: phase.model_copy(deep=True)
        for phase_id, phase in base_phases.items()
        if phase_id != optional_id
    }
    fan_out.append(
        ProtocolCandidate(
            name=spec.protocol_id,
            phases=_ordered_phases(
                [phase_id for phase_id in spec.source_phase_ids if phase_id != optional_id],
                omitted,
            ),
            fan_out_reason=OptionalPhaseOmitted(optional_id),
            selection_requires=spec.selection_requires,
        )
    )
    return fan_out


def _candidate_identity(candidate: RegimenCandidate) -> tuple[Any, ...]:
    """Return the stable grouping key for one candidate row."""

    return (
        candidate.phase or candidate.source_phase_id,
        candidate.variant_cui,
        candidate.study,
        candidate.regimen,
        candidate.variant,
    )


def _group_candidates(
    candidates: list[RegimenCandidate],
    *,
    source_phase_id: str,
) -> list[RegimenCandidate]:
    grouped: dict[tuple[Any, ...], RegimenCandidate] = {}
    for candidate in candidates:
        candidate = candidate.model_copy(
            update={
                "source_phase_id": candidate.source_phase_id or source_phase_id,
                "components": set(candidate.components),
                "sigs": list(candidate.sigs),
            },
            deep=True,
        )
        key = _candidate_identity(candidate)
        if key not in grouped:
            grouped[key] = candidate
            continue
        existing = grouped[key]
        grouped[key] = existing.model_copy(
            update={
                "sigs": [*existing.sigs, *candidate.sigs],
                "components": existing.components | candidate.components,
            },
            deep=True,
        )
    return _sort_candidates(list(grouped.values()))


def _candidate_sort_value(value: Any) -> tuple[int, str]:
    """Make mixed nullable candidate metadata safely sortable."""

    return (0, "") if value is None else (1, str(value))


def _candidate_sort_key(candidate: RegimenCandidate) -> tuple[Any, ...]:
    identity = _candidate_identity(candidate)
    return (
        *(_candidate_sort_value(value) for value in identity),
        candidate.name,
        tuple(sorted(candidate.components)),
        tuple(sorted(repr(sig) for sig in candidate.sigs)),
    )


def _sort_candidates(candidates: list[RegimenCandidate]) -> list[RegimenCandidate]:
    """Return a deterministic order independent of database row ordering."""

    return sorted(candidates, key=_candidate_sort_key)


def _restrict_phase(phase: PhaseCandidates, component: str) -> PhaseCandidates:
    options = [
        candidate
        for candidate in phase.options
        if component in candidate.components
    ]
    return phase.model_copy(
        update={
            "options": options,
            "no_candidates": NoCandidates(phase_id=phase.label or "")
            if not options
            else None,
        },
        deep=True,
    )


def _ordered_phases(
    phase_ids: list[str], phases: dict[str, PhaseCandidates]
) -> list[PhaseCandidates]:
    return [
        phases[phase_id].model_copy(update={"index": index}, deep=True)
        for index, phase_id in enumerate(phase_ids, start=1)
    ]


def _union_conditions(condition_refs: Any) -> list[Any]:
    values: list[Any] = []
    for condition_ref in condition_refs:
        for value in condition_ref.to_value():
            if value not in values:
                values.append(value)
    return values


def render_collapsible(
    header: str,
    children: list[str] | None = None,
    count: int | None = None,
    color: str = "#fdfdfd",
    data_attrs: dict[str, Any] | None = None,
    open_by_default: bool = True,
) -> str:
    """Renders a collapsible HTML block."""
    children = children or []
    data_attrs = data_attrs or {}
    count_str = f" — {count}" if count is not None else ""
    open_attr = "open" if open_by_default else ""
    data_attrs_html = " ".join(f'data-{k}="{v}"' for k, v in data_attrs.items())
    children_html = "".join(children)

    html = f"""
    <details {open_attr} style="margin:6px 0; padding:6px; border:1px dashed #666; border-radius:4px; background:{color};"
             {data_attrs_html}>
        <summary style="font-size:1.1em; font-weight:bold; cursor:pointer;">
            {header}{count_str} child(ren)
        </summary>
        {children_html}
    </details>
    """
    return html

def map_strings_hues(strings: list[str], hue_start: int, hue_end: int) -> dict[str, int]:
    n = len(strings)
    hue_map = {
        fw: int(hue_start + (hue_end - hue_start) * i / max(n - 1, 1))
        for i, fw in enumerate(strings)
    }
    return hue_map

def bold_before_colon(original_text: str) -> str:
    if ":" in original_text:
        prior, after = original_text.split(":", 1)
        prior_html = f"<strong>{prior}</strong>"
        result_html = f"{prior_html}:{after}"
    else:
        result_html = original_text
    return result_html


def get_regimen_candidate_list(
    sigdict: dict[tuple[Any, ...], list[Any]],
) -> list[RegimenCandidate]:
    regimen_candidate_list = []
    for (phase, variant_cui, study, regimen, variant_name), siglist in sigdict.items():
        try:
            variant_cui = int(variant_cui)
        except ValueError:
            variant_cui=None

        if variant_name:
            name = regimen + " " + str(variant_name)
        else:
            name = regimen
        components = {sig.component for sig in siglist}
        regimen_candidate_list.append(RegimenCandidate(name=name, sigs=siglist,
                                        variant_cui=variant_cui,
                                        regimen=regimen, components=components,
                                        phase=_none_if_string(phase),
                                        study=_none_if_string(study),
                                        variant=_none_if_string(variant_name)))
    return regimen_candidate_list


def _none_if_string(value: Any) -> Any:
    return None if value in (None, "None") else value

