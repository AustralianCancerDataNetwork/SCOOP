from pathlib import Path
from types import SimpleNamespace

from scoop.protocols.candidates import RegimenCandidate
from scoop.protocols.hemonc.component_lint import lint_profile_components
from scoop.protocols.hemonc.gateway import FakeHemOncGateway
from scoop.protocols.spec import RegistrySpec


def test_profile_lint_reports_unmappable_component_and_its_phase():
    registry_path = (
        Path(__file__).resolve().parents[1]
        / "scoop/profiles/nsclc/nsclc_protocol_registry.yaml"
    )
    registry = RegistrySpec.from_path(registry_path)
    phase_id = next(iter(registry.source_phases))
    candidate = RegimenCandidate(
        name="fixture",
        variant_cui=1,
        sigs=[SimpleNamespace(component="Unmapped drug", component_cui=876543, class_field=None)],
        regimen="fixture",
        components={"Unmapped drug"},
    )
    hemonc = FakeHemOncGateway(candidates={phase_id: [candidate]})

    class Vocabulary:
        def resolve_component_concept(self, domain, value, cui):
            raise LookupError(f"no {domain} mapping for {value} ({cui})")

    issues = lint_profile_components(registry, hemonc, Vocabulary())

    assert len(issues) == 1
    assert issues[0].name == "Unmapped drug"
    assert issues[0].hemonc_cui == 876543
    assert issues[0].phases == (phase_id,)
