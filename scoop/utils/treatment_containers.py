from pydantic import BaseModel, Field
from typing import Optional, Any
from collections import defaultdict
import scoop.utils.descriptions as desc
# import scoop.configuration.protocol_registry 

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