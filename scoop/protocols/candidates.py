from pydantic import BaseModel, Field
from typing import Optional, Dict, List
from collections import defaultdict
import scoop.protocols.descriptions as desc
from scoop.protocols.hemonc.queries import phase_spec_to_sigdict
from scoop.protocols.hemonc.gateway import HemOncGateway
from scoop.protocols.spec import ProtocolSpec

class RegimenCandidate(BaseModel):
    """
    A collection of sigs which can be the basis of a treatment episode.

    This may or may not be equivalent to a variant.
    """
    name: str
    variant_cui: Optional[int]
    sigs: list
    regimen: str
    components: set
    # NOTE: Rarely has the surgery sig been included w/ a hemonc variant
    # surgery component sig will need to be added in majority of cases
    # Options: context-dependent? independent? How much more restrictive is dependency?

    @classmethod
    def from_variant(cls, variant) -> "RegimenCandidate":
        name = variant.regimen + " " + variant.variant
        variant_cui = int(variant.variant_cui)
        sigs = [sig for sig in variant.component_sigs]
        regimen = variant.regimen
        components = {sig.component for sig in variant.component_sigs}
        return cls(name=name, variant_cui = variant_cui, 
                   sigs=sigs, regimen=regimen, components=components)

    def __repr__(self):
        header = f"{self.name} (CUI: {self.variant_cui})"
        sig_lines = "\n".join(
            desc.sig_component_obj(**sig.__dict__)
            for sig in self.sigs
        )
        return f"{header}\n{sig_lines}"

    def _repr_html_(self):
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
    conditions: list
    categories: set | None = None


    @classmethod
    def from_yaml(cls, index: int
                 ) -> "PhaseCandidates":
        options = []
        original_options = []
        ...
        return cls(index=index, conditions = [],
                   options=options, original_options=original_options, 
                   label="")

    @classmethod
    def from_tuple_dict(cls, tuple_dict) -> "PhaseCandidates":
        ...

    def __repr__(self):
        header = f"Phase {self.index}" if not self.label else f"{self.label} (Phase {self.index})"
        options = "\n".join(f"{sb.name} {sb.variant_cui}" for sb in self.options)
        return f"{header}\n{options}"

    def _repr_html_(self):
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


    def _render_regimens(self):
        """Render all regimen groups with hues avoiding red."""
        regimen_groups = defaultdict(list)
        for sb in self.options:
            regimen_groups[sb.regimen].append(sb)
    
        # Note that "cisplatin" is different to "cisplatin," - this is helpful
        first_words = sorted(set(r.split()[0].upper() for r in regimen_groups.keys()))
        hue_map = map_strings_hues(first_words, hue_start=60, hue_end=300) # hues avoiding red
    
        return [self._render_single_regimen(regimen, bundles, hue_map)
                for regimen, bundles in regimen_groups.items()]

    def _render_single_regimen(self, regimen, bundles, hue_map):
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

    @classmethod
    def from_yaml(cls, name: str, 
                 ) -> "ProtocolCandidate":
        ...
        phases = []
        return cls(name=name, phases=phases)

    def __repr__(self):
        return f"Treatment protocol: {self.name} ({len(self.phases)} phase(s))"

    def _repr_html_(self):
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
    def from_yaml(cls, yaml_path: str, prepend="") -> "CandidateSet":
        ...

    def _repr_html_(self):
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
    """Declare candidate assembly without activating the legacy query cascade."""

    raise NotImplementedError(
        "Protocol candidate assembly is intentionally deferred to Phase 4."
    )


def render_collapsible(header, children=None, count=None, color="#fdfdfd",
                       data_attrs=None, open_by_default=True):
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

def map_strings_hues(strings, hue_start, hue_end):
    n = len(strings)
    hue_map = {
        fw: int(hue_start + (hue_end - hue_start) * i / max(n - 1, 1))
        for i, fw in enumerate(strings)
    }
    return hue_map

def bold_before_colon(original_text):
    if ":" in original_text:
        prior, after = original_text.split(":", 1)
        prior_html = f"<strong>{prior}</strong>"
        result_html = f"{prior_html}:{after}"
    else:
        result_html = original_text
    return result_html


class ProtocolGenerator(BaseModel):
    sigdict_cache: dict = Field(default_factory=dict)
    phase_specs: dict
    phase_choices: dict

    def get_protocol_from_phase_spec(self, phase_id, session):
        candidate_list = self.phase_spec_to_candidate_list(self.phase_specs[phase_id], session)
        phase = PhaseCandidates(index=1, label=phase_id, options=candidate_list,
                               conditions=self.phase_specs[phase_id].condition_cuis)
        return [ProtocolCandidate(name=phase_id, phases=[phase])]

    def get_protocol_from_phase_choice(self, phase_id, session):
        union_data = self.phase_choices[phase_id]
        candidate_list = []
        conditions = get_union_phase_conditions(phase_id, self.phase_choices, self.phase_specs)
        for source_phase_id in union_data.source_phase_ids:
            candidate_list += self.phase_spec_to_candidate_list(self.phase_specs[source_phase_id], session)
        phase = PhaseCandidates(index=1, label=phase_id, options=candidate_list, conditions=conditions)
        return [ProtocolCandidate(name=phase_id, phases=[phase])]

    def get_protocols_from_protocol_spec(self, protocol_spec, session):
        phase_candidate_lists = self.get_phase_candidate_lists(protocol_spec, session)
        return self.protocols_from_candidate_lists(protocol_spec, phase_candidate_lists)

    def get_phase_candidate_lists(self, protocol_spec, session):
        phase_candidate_lists = dict()
        for phase_id in protocol_spec.source_phase_ids:
            if phase_id in self.phase_choices.keys():
                phase_list = []
                for phase_id in self.phase_choices[phase_id].source_phase_ids:
                    phase_list += self.phase_spec_to_candidate_list(self.phase_specs[phase_id], session)
                phase_candidate_lists[phase_id] = phase_list
            else:
                phase_candidate_lists[phase_id] = self.phase_spec_to_candidate_list(self.phase_specs[phase_id], session)
        return phase_candidate_lists

    def phase_spec_to_candidate_list(self, phase_spec, session):
        cached_sigdict = self.sigdict_cache.get(phase_spec.source_phase_id)
        if cached_sigdict:
            return get_regimen_candidate_list(cached_sigdict)
        else:
            new_sigdict = phase_spec_to_sigdict(phase_spec, session)
            self.sigdict_cache[phase_spec.source_phase_id] = new_sigdict
            return get_regimen_candidate_list(new_sigdict)

    def protocols_from_candidate_lists(self, protocol_spec, phase_candidate_lists):
        if protocol_spec.optional_phases:
            optional_phase = list(protocol_spec.optional_phases.keys())[0]
            requirement_type = protocol_spec.optional_phases[optional_phase]["criteria"]
            is_shared_component_req = requirement_type.shared.lower()=="component"
            if is_shared_component_req:
                req_sharing_phase = requirement_type.as_phase
                optional_phase_sigs = phase_candidate_lists[optional_phase]
                components_in_optional = {component for sb in optional_phase_sigs for component in sb.components }
                pb = create_protocols_by_shared_component(
                    protocol_spec, components_in_optional, phase_candidate_lists,
                    req_sharing_phase, optional_phase, self.phase_specs)
        else:
            phases = create_phase_candidates(protocol_spec, phase_candidate_lists, self.phase_specs, self.phase_choices)
            pb = [ProtocolCandidate(name=protocol_spec.protocol_id, phases=phases)]
        return pb

def get_regimen_candidate_list(sigdict):
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
                                        regimen=regimen, components=components))
    return regimen_candidate_list

def create_protocols_by_shared_component(protocol_spec, components_in_optional: set, candidate_lists_by_phase: Dict[str, list],
                                         req_sharing_phase: str, optional_phase:str, phase_specs):
    mutable_components = components_in_optional.copy()
    protocols = []
    for component in mutable_components.copy():
        component = mutable_components.pop()
        component_restricted_protocol= create_component_restricted_protocol(
            protocol_spec, component, phase_specs, candidate_lists_by_phase, req_sharing_phase, optional_phase
            )
        protocols.append(component_restricted_protocol)

    no_opt_phase_phases = create_phase_candidates(protocol_spec, candidate_lists_by_phase,
                                             phase_specs, exclude={optional_phase})
    no_opt_phase_protocol = ProtocolCandidate(name=protocol_spec.protocol_id, phases=no_opt_phase_phases,
                                    requirement_type="shared component",
                                    requirement_element=None
                                    )
    protocols.append(no_opt_phase_protocol)
    return protocols


def create_component_restricted_protocol(protocol_spec, component: str, phase_specs, candidate_lists_by_phase: Dict[str, list],
                                         req_sharing_phase: str, optional_phase:str):
    req_sharing_phase_w_component = [sb for sb in candidate_lists_by_phase[req_sharing_phase]
                                if component in sb.components]
    optional_phase_w_component = [sb for sb in candidate_lists_by_phase[optional_phase]
                            if component in sb.components]
    with_component_candidate_lists_by_phase = {req_sharing_phase: req_sharing_phase_w_component,
                            optional_phase: optional_phase_w_component}
    with_component_phases = create_phase_candidates(protocol_spec, with_component_candidate_lists_by_phase,
                                            phase_specs, full_candidate_lists=candidate_lists_by_phase)
    component_restricted_protocol = ProtocolCandidate(name=protocol_spec.protocol_id, phases=with_component_phases,
                                        requirement_type="shared component",
                                        requirement_element=component
                                        )
    return component_restricted_protocol


def create_phase_candidates(protocol_spec, restricted_candidate_lists: Dict[str, list],
                       phase_specs, phase_choices={}, full_candidate_lists={}, exclude={}) -> List[PhaseCandidates]:
    """
    Creates a list of phases suitable for populating protocols with.
    
    If creating an unrestricted protocol just pass the full_candidate_lists to
    restricted_candidate_lists and ignore the full_candidate_lists parameter
    """
    phase_index = 0
    phases = []

    for source_phase_id in protocol_spec.source_phase_ids:
        if source_phase_id in exclude:
            continue
        phase_index +=1
        if source_phase_id in restricted_candidate_lists.keys():
            candidate_list = restricted_candidate_lists[source_phase_id]
        else:
            candidate_list = full_candidate_lists[source_phase_id]
        if source_phase_id in phase_choices.keys():
            conditions = get_union_phase_conditions(source_phase_id, phase_choices, phase_specs)
            categories = {category for source_phase_id in phase_choices[source_phase_id].source_phase_ids
                          for category in phase_specs[source_phase_id].categorise()}
        else:
            conditions = phase_specs[source_phase_id].condition_cuis
            categories = phase_specs[source_phase_id].categorise()
        phase_candidates = PhaseCandidates(index = phase_index, label = source_phase_id,
                            options=candidate_list, conditions=conditions, categories=categories)
        phases.append(phase_candidates)
    return phases

def get_union_phase_conditions(phase_id, phase_choices, phase_specs):
    return list({condition for source_phase_id in phase_choices[phase_id].source_phase_ids
                for condition in phase_specs[source_phase_id].condition_cuis})
