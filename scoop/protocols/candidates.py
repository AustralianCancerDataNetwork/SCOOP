from pydantic import BaseModel, Field
from typing import Optional, Dict, List
from collections import defaultdict
import scoop.protocols.descriptions as desc
from scoop.protocols.hemonc.queries import source_phase_to_sigdict
# import scoop.protocols.spec 

class SigBundle(BaseModel):
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
    def from_variant(cls, variant) -> "SigBundle":
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


class TreatmentPhase(BaseModel):
    """
    A collection of sig bundles valid for the same treatment episode

    Note that phase order is driven by order in yaml
    """
    index: int = 1
    label: Optional[str] = None
    options: list[SigBundle]
    # original_options: Optional[tuple[SigBundle, ...]] = None
    # filters_passed: Optional[dict[str, Any]] = Field(default_factory=dict)
    # filters_failed: Optional[dict[str, Any]] = Field(default_factory=dict)
    conditions: list
    categories: set | None = None


    @classmethod
    def from_yaml(cls, index: int
                 ) -> "TreatmentPhase":
        options = []
        original_options = []
        ...
        return cls(index=index, conditions = [],
                   options=options, original_options=original_options, 
                   label="")

    @classmethod
    def from_tuple_dict(cls, tuple_dict) -> "TreatmentPhase":
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
        

class Protocol(BaseModel):
    """
    A protocol may comprise one or more treatment phases.
    """
    name: str
    phases: list[TreatmentPhase]
    requirement_type: str | None = None
    requirement_element: str | None = None

    @classmethod
    def from_yaml(cls, name: str, 
                 ) -> "Protocol":
        ...
        phases = []
        return cls(name=name, phases=phases)

    def __repr__(self):
        return f"Treatment Protocol: {self.name} ({len(self.phases)} phase(s))"

    def _repr_html_(self):
        if len(self.phases) == 1:
            phase = self.phases[0]
            regimen_children = phase._render_regimens()
            
            return render_collapsible(
                header=f"Treatment Protocol: {self.name}",
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


        
class TreatmentGroup(BaseModel):
    """
    A collection of Protocols valid for a disease state grouping
    e.g. nsclc stage 2-3 resectable
    """
    name: str
    # conditions: list
    treatment_scenarios: dict[str, Protocol]

    @classmethod
    def from_yaml(cls, yaml_path: str, prepend="") -> "TreatmentGroup":
        ...

    def _repr_html_(self):
        children = []
        for scenario in self.treatment_scenarios.values():
            children.append(scenario._repr_html_())

        return render_collapsible(
            header=f"Treatment Group: {self.name}",
            children=children,
            count=len(self.treatment_scenarios.keys()),
            color="#eef6ff",
            data_attrs={"group": self.name},
            open_by_default=False
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
    source_phases: dict
    phase_unions: dict

    def get_protocol_from_source_phase(self, phase_id, session):
        sb_list = self.source_phase_to_sb_list(self.source_phases[phase_id], session)
        phase = TreatmentPhase(index=1, label=phase_id, options=sb_list, 
                               conditions=self.source_phases[phase_id].condition_cuis)
        return [Protocol(name=phase_id, phases=[phase])]

    def get_protocol_from_phase_union(self, phase_id, session):
        union_data = self.phase_unions[phase_id]
        sb_list = []
        conditions = get_union_phase_conditions(phase_id, self.phase_unions, self.source_phases)
        for source_phase_id in union_data.source_phase_ids:
            sb_list += self.source_phase_to_sb_list(self.source_phases[source_phase_id], session)
        phase = TreatmentPhase(index=1, label=phase_id, options=sb_list, conditions=conditions)
        return [Protocol(name=phase_id, phases=[phase])]

    def get_protocols_from_protocol_yaml(self, protocol_yaml, session):
        source_phases_sb_list = self.get_source_phases_sb_list(protocol_yaml, session)
        return self.protocols_from_sb_list(protocol_yaml, source_phases_sb_list)

    def get_source_phases_sb_list(self, protocol_yaml, session):
        source_phases_sb_list = dict()
        for phase_id in protocol_yaml.source_phase_ids:
            if phase_id in self.phase_unions.keys():
                phase_list = []
                for phase_id in self.phase_unions[phase_id].source_phase_ids:
                    phase_list += self.source_phase_to_sb_list(self.source_phases[phase_id], session)
                source_phases_sb_list[phase_id] = phase_list
            else:
                source_phases_sb_list[phase_id] = self.source_phase_to_sb_list(self.source_phases[phase_id], session)
        return source_phases_sb_list

    def source_phase_to_sb_list(self, source_phase, session):
        cached_sigdict = self.sigdict_cache.get(source_phase.source_phase_id)
        if cached_sigdict:
            return get_sigbundle_list(cached_sigdict)
        else:
            new_sigdict = source_phase_to_sigdict(source_phase, session)
            self.sigdict_cache[source_phase.source_phase_id] = new_sigdict
            return get_sigbundle_list(new_sigdict)

    def protocols_from_sb_list(self, protocol_yaml, source_phases_sb_list):
        if protocol_yaml.optional_phases:
            optional_phase = list(protocol_yaml.optional_phases.keys())[0]
            requirement_type = protocol_yaml.optional_phases[optional_phase]["criteria"]
            is_shared_component_req = requirement_type.shared.lower()=="component"
            if is_shared_component_req:
                req_sharing_phase = requirement_type.as_phase
                optional_phase_sigs = source_phases_sb_list[optional_phase]
                components_in_optional = {component for sb in optional_phase_sigs for component in sb.components }
                pb = create_protocols_by_shared_component(
                    protocol_yaml, components_in_optional, source_phases_sb_list, 
                    req_sharing_phase, optional_phase, self.source_phases)
        else:
            phases = create_phases_list(protocol_yaml, source_phases_sb_list, self.source_phases, self.phase_unions)
            pb = [Protocol(name=protocol_yaml.protocol_id, phases=phases)]
        return pb

def get_sigbundle_list(sigdict):
    sigbundle_list = []
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
        sigbundle_list.append(SigBundle(name=name, sigs=siglist, 
                                        variant_cui=variant_cui,
                                        regimen=regimen, components=components))
    return sigbundle_list

def create_protocols_by_shared_component(protocol_yaml, components_in_optional: set, sb_lists_by_phase: Dict[str, list], 
                                         req_sharing_phase: str, optional_phase:str, source_phases):
    mutable_components = components_in_optional.copy()
    protocols = []
    for component in mutable_components.copy():
        component = mutable_components.pop()
        component_restricted_protocol= create_component_restricted_protocol(
            protocol_yaml, component, source_phases, sb_lists_by_phase, req_sharing_phase, optional_phase
            )
        protocols.append(component_restricted_protocol)

    no_opt_phase_phases = create_phases_list(protocol_yaml, sb_lists_by_phase, 
                                             source_phases, exclude={optional_phase})
    no_opt_phase_protocol = Protocol(name=protocol_yaml.protocol_id, phases=no_opt_phase_phases, 
                                    requirement_type="shared component",
                                    requirement_element=None
                                    )
    protocols.append(no_opt_phase_protocol)
    return protocols


def create_component_restricted_protocol(protocol_yaml, component: str, source_phases, sb_lists_by_phase: Dict[str, list], 
                                         req_sharing_phase: str, optional_phase:str):
    req_sharing_phase_w_component = [sb for sb in sb_lists_by_phase[req_sharing_phase] 
                                if component in sb.components]
    optional_phase_w_component = [sb for sb in sb_lists_by_phase[optional_phase] 
                            if component in sb.components]
    with_components_sb_lists_by_phase = {req_sharing_phase: req_sharing_phase_w_component, 
                            optional_phase: optional_phase_w_component}
    with_component_phases = create_phases_list(protocol_yaml, with_components_sb_lists_by_phase, 
                                            source_phases, full_sb_list=sb_lists_by_phase)
    component_restricted_protocol = Protocol(name=protocol_yaml.protocol_id, phases=with_component_phases, 
                                        requirement_type="shared component",
                                        requirement_element=component
                                        )
    return component_restricted_protocol


def create_phases_list(protocol_yaml, restricted_sb_list: Dict[str, list], 
                       source_phases, phase_unions={}, full_sb_list={}, exclude={}) -> List[TreatmentPhase]:
    """
    Creates a list of phases suitable for populating protocols with.
    
    If creating an unrestricted protocol just pass the full_sb_list to 
    restricted_sb_list and ignore the full_sb_list parameter
    """
    phase_index = 0
    phases = []

    for source_phase_id in protocol_yaml.source_phase_ids:
        if source_phase_id in exclude:
            continue
        phase_index +=1
        if source_phase_id in restricted_sb_list.keys():
            sb_list = restricted_sb_list[source_phase_id]
        else:
            sb_list = full_sb_list[source_phase_id]
        if source_phase_id in phase_unions.keys():
            conditions = get_union_phase_conditions(source_phase_id, phase_unions, source_phases)
            categories = {category for source_phase_id in phase_unions[source_phase_id].source_phase_ids 
                          for category in source_phases[source_phase_id].categorise()}
        else:
            conditions = source_phases[source_phase_id].condition_cuis
            categories = source_phases[source_phase_id].categorise()
        treatment_phase = TreatmentPhase(index = phase_index, label = source_phase_id, 
                            options=sb_list, conditions=conditions, categories=categories)
        phases.append(treatment_phase)
    return phases

def get_union_phase_conditions(phase_id, phase_unions, source_phases):
    return list({condition for source_phase_id in phase_unions[phase_id].source_phase_ids 
                for condition in source_phases[source_phase_id].condition_cuis})