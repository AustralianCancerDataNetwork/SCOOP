from pathlib import Path
from types import SimpleNamespace

import yaml

from scoop.protocols.candidates import (
    CandidateSet,
    NoCandidates,
    OptionalPhaseOmitted,
    PhaseCandidates,
    RegimenCandidate,
    SharedComponent,
    assemble,
)
from scoop.protocols.hemonc.gateway import FakeHemOncGateway
from scoop.protocols.spec import (
    OptionalPhaseCriteria,
    OptionalPhaseSpec,
    PhaseChoiceSpec,
    PhaseSpec,
    ProtocolSpec,
)


def candidate(
    name: str,
    *,
    phase: str,
    variant: str = "v1",
    study: str = "study-1",
    variant_cui: int = 1,
    components: set[str] | None = None,
    sigs: list[str] | None = None,
) -> RegimenCandidate:
    return RegimenCandidate(
        name=name,
        variant_cui=variant_cui,
        sigs=sigs or [name],
        regimen="regimen",
        components=components or {"cisplatin"},
        phase=phase,
        study=study,
        variant=variant,
    )


def phase(phase_id: str) -> PhaseSpec:
    return PhaseSpec(source_phase_id=phase_id)


def _fake_sig(**overrides) -> SimpleNamespace:
    """A minimal stand-in for a real HemOnc sig row.

    Omits `seqrelwhat` on purpose -- real sig rows never carry it either.
    """

    fields = dict(
        doseminnum=1, dosemaxnum=1, doseunit="mg",
        dosecapnum=None, dosecapunit=None,
        durationunit=None, durationmaxnum=None, durationminnum=None,
        route="oral", frequency=None, alldays=[1], timing_sequence=None,
        component="cisplatin",
        seqrel_items=[], seqrelwhen_items=[], seqrelwhenunit_items=[],
        sequence=None, id=1, phase="phase-1", divided=False,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_regimen_candidate_repr_does_not_require_legacy_seqrelwhat_field():
    regimen_candidate = candidate("test", phase="phase-1", sigs=[_fake_sig()])

    assert "cisplatin" in repr(regimen_candidate)
    assert "cisplatin" in regimen_candidate._repr_html_()


def test_assembly_groups_rows_by_candidate_identity():
    spec = ProtocolSpec(
        protocol_id="protocol",
        source_phase_ids=["phase"],
        source_phases={"phase": phase("phase")},
    )
    gateway = FakeHemOncGateway(
        {
            "phase": [
                candidate("first", phase="phase", sigs=["sig-1"]),
                candidate("first", phase="phase", sigs=["sig-2"]),
                candidate("second", phase="phase", variant="v2", variant_cui=2),
            ]
        }
    )

    options = assemble(spec, gateway)[0].phases[0].options

    assert len(options) == 2
    assert options[0].sigs == ["sig-1", "sig-2"]


def test_empty_gateway_result_is_an_explicit_phase_failure():
    spec = ProtocolSpec(
        protocol_id="protocol",
        source_phase_ids=["phase"],
        source_phases={"phase": phase("phase")},
    )

    result = assemble(spec, FakeHemOncGateway())

    assert result[0].phases[0].options == []
    assert result[0].phases[0].no_candidates == NoCandidates(phase_id="phase")


def test_choice_keeps_member_provenance_and_choice_key():
    spec = ProtocolSpec(
        protocol_id="protocol",
        source_phase_ids=["choice"],
        source_phases={"a": phase("a"), "b": phase("b")},
        phase_choices={
            "choice": PhaseChoiceSpec(
                phase_id="choice",
                source_phase_ids=["a", "b"],
            )
        },
    )
    gateway = FakeHemOncGateway(
        {
            "a": [candidate("a", phase="a")],
            "b": [candidate("b", phase="b", variant_cui=2)],
        }
    )

    assembled = assemble(spec, gateway)[0]

    assert assembled.phases[0].label == "choice"
    assert {option.source_phase_id for option in assembled.phases[0].options} == {
        "a",
        "b",
    }


def test_shared_component_optional_phase_fan_out_is_typed():
    spec = ProtocolSpec(
        protocol_id="protocol",
        source_phase_ids=["required", "optional"],
        source_phases={"required": phase("required"), "optional": phase("optional")},
        optional_phases={
            "optional": OptionalPhaseSpec(
                criteria=OptionalPhaseCriteria(shared="component", as_phase="required")
            )
        },
    )
    gateway = FakeHemOncGateway(
        {
            "required": [candidate("required", phase="required")],
            "optional": [candidate("optional", phase="optional")],
        }
    )

    assembled = assemble(spec, gateway)

    assert len(assembled) == 2
    assert assembled[0].fan_out_reason == SharedComponent("cisplatin")
    assert assembled[0].phases[0].options
    assert assembled[0].phases[1].options
    assert assembled[1].fan_out_reason == OptionalPhaseOmitted("optional")
    assert [phase.label for phase in assembled[1].phases] == ["required"]


def test_fake_gateway_caches_repeated_phase_queries():
    spec = ProtocolSpec(
        protocol_id="protocol",
        source_phase_ids=["phase"],
        source_phases={"phase": phase("phase")},
    )
    gateway = FakeHemOncGateway({"phase": [candidate("candidate", phase="phase")]})

    first = assemble(spec, gateway)
    second = assemble(spec, gateway)

    assert first == second
    assert gateway.calls == ["phase"]


def test_candidate_layer_has_no_sqlalchemy_import():
    source = Path(__file__).parents[1] / "scoop" / "protocols" / "candidates.py"
    assert "sqlalchemy" not in source.read_text(encoding="utf-8").lower()


def test_portable_candidate_loaders_preserve_typed_shape(tmp_path):
    payload = {
        "name": "fixture",
        "treatment_scenarios": {
            "scenario": {
                "phases": [
                    {
                        "label": "phase",
                        "conditions": ["Lung cancer"],
                        "options": [
                            {
                                "name": "regimen v1",
                                "variant_cui": 7,
                                "sigs": ["sig-1"],
                                "regimen": "regimen",
                                "components": ["cisplatin"],
                            }
                        ],
                    }
                ]
            }
        },
    }
    path = tmp_path / "candidates.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    loaded = CandidateSet.from_yaml(str(path))

    assert loaded.name == "fixture"
    option = loaded.treatment_scenarios["scenario"].phases[0].options[0]
    assert isinstance(option, RegimenCandidate)
    assert option.components == {"cisplatin"}


def test_tuple_candidate_loader_normalizes_identity_and_order():
    phase = PhaseCandidates.from_tuple_dict(
        {
            ("phase", "2", "study", "regimen", "v2"): ["b"],
            ("phase", "1", "study", "regimen", "v1"): ["a"],
        }
    )

    assert [option.variant_cui for option in phase.options] == [1, 2]
    assert phase.options[0].components == {"a"}
