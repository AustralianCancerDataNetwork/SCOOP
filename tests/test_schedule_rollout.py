from datetime import date

import pytest

from scoop.patients.build import assign_regimen
from scoop.patients.condition_occurrence import Condition_Occurrence
from scoop.patients.treatment_episode import Treatment_Episode
from scoop.protocols.candidates import RegimenCandidate
from scoop.schedule import UnresolvedScheduleError
from scoop.schedule.rollout import (
    ProtocolSchedule,
    RadiationAdministration,
    ScheduleConfig,
    SystemicAdministration,
    schedule,
)
from scoop.schedule.systemic import Component
from scoop.selection.choose import (
    SelectedProtocol,
    SelectedRegimen,
    apply_recipe_timing,
)
from scoop.selection.interventions import (
    PhaseRef,
    PhaseTiming,
    Recipe,
    RecipeStep,
    SequenceTemplate,
)


class StubSignature:
    def __init__(self, component: str = "cisplatin"):
        self.id = 42
        self.component = component
        self.component_cui = 1234
        self.drug_object = None
        self.timing_sequence = "1"
        self.alldays = "1"
        self.cycle_length_lb = 21
        self.cycle_length_ub = 21
        self.cycle_length_unit = "day"
        self.cyclesigs = "21 day cycles for 1 cycle"
        self.phase = None
        self.phase_step = None
        self.portion = ""
        self.step_number = "1 of 1"
        self.route = "intravenous"
        self.route_cui = 777
        self.frequency = "once"
        self.frequency_cui = 778
        self.component_role = ""
        # The new engine classifies by HemOnc's class, not component spelling.
        self.class_field = (
            "rad sig"
            if component == "External beam radiotherapy"
            else "iv intermittent canonical sig"
        )
        self.doseminnum = 10
        self.dosemaxnum = 20
        self.doseunit = "mg"
        self.doseunit_cui = 779
        self.divided = False
        self.dosecapnum = None
        self.dosecapunit = None
        self.dosecapunit_cui = None
        self.durationminnum = None
        self.durationmaxnum = None
        self.durationunit = None
        self.durationunit_cui = None


def _selected(signature):
    regimen = RegimenCandidate(
        name="fixture",
        variant_cui=1,
        sigs=[signature],
        regimen="fixture",
        components={signature.component},
        source_phase_id="phase",
    )
    return SelectedProtocol(
        protocol_id="protocol",
        phases=(SelectedRegimen(phase_id="phase", regimen=regimen),),
    )


def _schedule_signature(signature, *, seed=11, config=None):
    return schedule(
        _selected(signature), date(2026, 1, 1), seed=seed, config=config
    ).administrations


def test_schedule_rolls_signature_into_typed_systemic_administration():
    result = schedule(
        _selected(StubSignature()),
        date(2026, 1, 1),
        seed=11,
        config=ScheduleConfig(max_cycles=1),
    )

    assert len(result.administrations) == 1
    administration = result.administrations[0]
    assert isinstance(administration, SystemicAdministration)
    assert administration.date == date(2026, 1, 1)
    assert administration.component.name == "cisplatin"
    assert 10 <= administration.dose <= 20
    assert administration.source_sig_id == 42


def test_schedule_classifies_radiation_components_without_placeholder_rows():
    result = schedule(
        _selected(StubSignature("External beam radiotherapy")),
        date(2026, 1, 1),
        seed=11,
        config=ScheduleConfig(max_cycles=1),
    )

    assert isinstance(result.administrations[0], RadiationAdministration)
    assert result.administrations[0].fraction_dose is not None


def test_schedule_rejects_unschedulable_placeholder_signatures():
    regimen = RegimenCandidate(
        name="fixture",
        variant_cui=1,
        sigs=["not-a-signature"],
        regimen="fixture",
        components={"cisplatin"},
    )
    selected = SelectedProtocol(
        protocol_id="protocol",
        phases=(SelectedRegimen(phase_id="phase", regimen=regimen),),
    )

    with pytest.raises(ValueError, match="without the released scheduling fields"):
        schedule(selected, date(2026, 1, 1), seed=11)


def test_schedule_config_validates_probabilities():
    with pytest.raises(ValueError, match="between 0 and 1"):
        ScheduleConfig(optional_cycle_probability=1.1)


def test_treatment_episode_attaches_schedule_with_event_provenance():
    selected = _selected(StubSignature())
    protocol_schedule = schedule(
        selected,
        date(2026, 1, 1),
        seed=11,
        config=ScheduleConfig(max_cycles=1),
    )
    episode = Treatment_Episode(
        person_id=1,
        condition_occurrence_id=2,
        regimen_type="fixture",
        regimen_category_id="fixture",
    )

    assert episode.attach_schedule(protocol_schedule) is episode
    drugs, procedures = episode.roll_events()
    assert len(drugs) == 1
    assert procedures == []
    assert drugs[0]["phase_id"] == "phase"
    assert drugs[0]["source_sig_id"] == 42
    # The HemOnc CUI travels as a CUI: it is not an OMOP concept_id, so no
    # *_concept_id key is emitted here at all.
    assert drugs[0]["component_hemonc_cui"] == 1234
    assert "drug_concept_id" not in drugs[0]


def test_assign_regimen_accepts_an_explicit_schedule_without_legacy_lookup():
    protocol_schedule = schedule(
        _selected(StubSignature()),
        date(2026, 1, 1),
        seed=11,
        config=ScheduleConfig(max_cycles=1),
    )
    condition = Condition_Occurrence(
        person_id=1,
        auto_assign_regimen=False,
    )

    episode = assign_regimen(condition, protocol_schedule=protocol_schedule)

    assert episode.registry_resolver_type == "none"
    assert len(episode.drug_exposures) == 1


def test_recipe_timing_sequences_ordered_phases():
    first = _selected(StubSignature("cisplatin")).phases[0]
    second_regimen = RegimenCandidate(
        name="radiation",
        variant_cui=2,
        sigs=[StubSignature("External beam radiotherapy")],
        regimen="radiation",
        components={"External beam radiotherapy"},
    )
    selected = SelectedProtocol(
        protocol_id="protocol",
        phases=(
            SelectedRegimen(phase_id="pre", regimen=first.regimen),
            SelectedRegimen(phase_id="post", regimen=second_regimen),
        ),
    )
    recipe = Recipe(
        steps=(
            # The first signature is a systemic phase and the second is a
            # procedure phase; timing is intentionally declared here.
            RecipeStep(PhaseRef("pre"), timing=PhaseTiming.PRE),
            RecipeStep(PhaseRef("post"), timing=PhaseTiming.POST),
        ),
        sequence_template=SequenceTemplate.POSTOPERATIVE,
    )

    annotated = apply_recipe_timing(selected, recipe)
    result = schedule(annotated, date(2026, 1, 1), seed=11)

    assert result.phase_start_dates == {
        "pre": date(2026, 1, 1),
        "post": date(2026, 1, 2),
    }
    assert [event.date for event in result.administrations] == [
        date(2026, 1, 1),
        date(2026, 1, 2),
    ]


def test_recipe_timing_keeps_concurrent_phases_on_one_anchor_date():
    first = _selected(StubSignature("cisplatin")).phases[0]
    second_regimen = RegimenCandidate(
        name="radiation",
        variant_cui=2,
        sigs=[StubSignature("External beam radiotherapy")],
        regimen="radiation",
        components={"External beam radiotherapy"},
    )
    selected = SelectedProtocol(
        protocol_id="protocol",
        phases=(
            SelectedRegimen(phase_id="systemic", regimen=first.regimen),
            SelectedRegimen(phase_id="radiation", regimen=second_regimen),
        ),
    )
    annotated = apply_recipe_timing(
        selected,
        Recipe(
            steps=(
                RecipeStep(PhaseRef("systemic"), timing=PhaseTiming.CONCURRENT),
                RecipeStep(PhaseRef("radiation"), timing=PhaseTiming.CONCURRENT),
            ),
            sequence_template=SequenceTemplate.CONCURRENT,
        ),
    )
    result = schedule(annotated, date(2026, 1, 1), seed=11)

    assert result.phase_start_dates == {
        "systemic": date(2026, 1, 1),
        "radiation": date(2026, 1, 1),
    }


def test_phase_blocks_use_full_prior_cycles_and_preserve_same_day_components():

    first = StubSignature("first")
    first.timing_sequence = "1,2"
    first.cycle_length_lb = first.cycle_length_ub = 14
    second = StubSignature("second")
    second.id = 43
    second.timing_sequence = "3,4"
    peer = StubSignature("peer")
    peer.id = 44
    peer.timing_sequence = "3,4"
    selected = _selected(first)
    selected.phases[0].regimen.sigs.extend([second, peer])
    result = schedule(selected, date(2026, 1, 1), seed=11)

    # Blocks start after two FULL 14-day cycles, not two 21-day cycles.
    assert [
        (a.component.name, a.cycle_number, a.date) for a in result.administrations
    ] == [
        ("first", 1, date(2026, 1, 1)),
        ("first", 2, date(2026, 1, 15)),
        ("peer", 3, date(2026, 1, 29)),
        ("second", 3, date(2026, 1, 29)),
        ("peer", 4, date(2026, 2, 19)),
        ("second", 4, date(2026, 2, 19)),
    ]
    capped = schedule(
        selected, date(2026, 1, 1), seed=11, config=ScheduleConfig(max_cycles=3)
    )
    assert max(a.cycle_number for a in capped.administrations) == 3


def test_two_drug_components_are_retained_as_same_day_episode_events():
    cisplatin = StubSignature("cisplatin")
    pemetrexed = StubSignature("pemetrexed")
    pemetrexed.id = cisplatin.id + 1
    selected = _selected(cisplatin)
    selected.phases[0].regimen.sigs.append(pemetrexed)

    protocol_schedule = schedule(selected, date(2026, 1, 1), seed=11)
    episode = Treatment_Episode(
        person_id=1,
        condition_occurrence_id=1,
        regimen_type="test",
        regimen_category_id="test",
    ).attach_schedule(protocol_schedule)
    drugs, procedures = episode.roll_events()

    assert not procedures
    assert {event["drug_source_value"] for event in drugs} == {
        "cisplatin",
        "pemetrexed",
    }
    assert len({event["drug_exposure_start_date"] for event in drugs}) == 1
    assert len({event["cycle_number"] for event in drugs}) == 1
    assert all(
        not key.endswith("_concept_id")
        for event in drugs + procedures
        for key in event
    )


def test_overlapping_blocks_inherit_the_shared_cycle_anchor():
    first = StubSignature("first")
    first.timing_sequence = "1,2,3"
    second = StubSignature("second")
    second.id = 43
    second.timing_sequence = "2,3"
    selected = _selected(first)
    selected.phases[0].regimen.sigs.append(second)
    result = schedule(selected, date(2026, 1, 1), seed=11)
    assert [a.date for a in result.administrations if a.component.name == "second"] == [
        date(2026, 1, 22),
        date(2026, 2, 12),
    ]


def test_negative_days_do_not_receive_the_positive_day_offset():
    sig = StubSignature()
    sig.alldays = "-5,-3,1"
    result = schedule(_selected(sig), date(2026, 1, 10), seed=11)
    # Corrects the legacy engine's extra subtraction for negative day numbers.
    assert [a.date for a in result.administrations] == [
        date(2026, 1, 5),
        date(2026, 1, 7),
        date(2026, 1, 10),
    ]


@pytest.mark.parametrize(
    "component", ["cisplatin", "External beam radiotherapy", "131I-tositumomab"]
)
@pytest.mark.parametrize(
    "days,expected_count,choice_groups",
    [
        ("1,8|9", 2, ((8, 9),)),
        ("1,8|9,12|13", 3, ((8, 9), (12, 13))),
        ("7|8|9|10|11|12|13|14|15", 1, (tuple(range(7, 16)),)),
    ],
)
def test_choice_days_are_picked_reproducibly_for_fresh_signatures(
    component, days, expected_count, choice_groups
):
    sig = StubSignature(component)
    sig.alldays = days
    result = _schedule_signature(sig)
    fresh_sig = StubSignature(component)
    fresh_sig.alldays = days
    assert result == _schedule_signature(fresh_sig)
    assert len(result) == expected_count
    assert all(administration.date is not None for administration in result)
    selected_days = [administration.date.day for administration in result]
    for choices in choice_groups:
        assert sum(day in choices for day in selected_days) == 1
    assert all(
        not administration.timing_status.startswith("unresolved:")
        for administration in result
    )


def test_choice_resolution_deduplicates_a_selected_existing_day():
    sig = StubSignature()
    sig.alldays = "1,1|8"

    result = _schedule_signature(sig)

    assert len(result) == 2
    assert len({administration.date for administration in result}) == 2


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("alldays", None, "no explicit dosing days"),
        ("timing_sequence", "1,3", "non-contiguous"),
        ("alldays", "1,(+n)", "day continuation has no numeric repeat interval"),
    ],
)
def test_unresolved_source_timing_raises_a_typed_schedule_error(field, value, reason):
    sig = StubSignature()
    setattr(sig, field, value)
    with pytest.raises(UnresolvedScheduleError, match=reason) as exc:
        _schedule_signature(sig)

    assert exc.value.sig_id == 42
    assert exc.value.phase_id == "phase"
    assert reason in exc.value.reason


def test_radiation_classification_uses_the_class_not_the_name():
    sig = StubSignature("External beam radiotherapy")
    sig.class_field = "drug sig"
    result = schedule(_selected(sig), date(2026, 1, 1), seed=11)
    assert isinstance(result.administrations[0], SystemicAdministration)
    sig.component = "unusual abbreviation"
    sig.class_field = "rad sig"
    assert isinstance(
        schedule(_selected(sig), date(2026, 1, 1), seed=11).administrations[0],
        RadiationAdministration,
    )


@pytest.mark.parametrize(
    "component_prob,block_prob,count", [(1, 1, 3), (0, 1, 1), (1, 0, 1)]
)
def test_numeric_cycle_continuation_respects_interval_cap_and_zero_probabilities(
    component_prob, block_prob, count
):
    sig = StubSignature()
    sig.timing_sequence = "1,(+2)"
    config = ScheduleConfig(
        max_cycles=5,
        within_component_continue_probability=component_prob,
        within_cycleset_continue_probability=block_prob,
    )
    result = schedule(_selected(sig), date(2026, 1, 1), seed=11, config=config)
    assert len(result.administrations) == count
    assert [a.cycle_number for a in result.administrations] == [1, 3, 5][:count]
    assert [a.date for a in result.administrations] == [
        date(2026, 1, 1),
        date(2026, 2, 12),
        date(2026, 3, 26),
    ][:count]
    assert sig.timing_sequence == "1,(+2)"


@pytest.mark.parametrize("probability,count", [(0, 1), (1, 3)])
def test_optional_cycles_are_sampled_once_for_all_doses(probability, count):
    sig = StubSignature()
    sig.timing_sequence = "1,(2),(3)"
    sig.alldays = "1,8"
    result = schedule(
        _selected(sig),
        date(2026, 1, 1),
        seed=11,
        config=ScheduleConfig(optional_cycle_probability=probability),
    )
    assert [a.cycle_number for a in result.administrations] == [
        n for n in range(1, count + 1) for _ in range(2)
    ]


@pytest.mark.parametrize("probability,days", [(0, [1]), (1, [1, 3, 5])])
def test_numeric_day_continuation_is_bounded_by_cycle_end(probability, days):
    sig = StubSignature()
    sig.alldays = "1,(+2)"
    sig.cycle_length_lb = sig.cycle_length_ub = 6
    result = schedule(
        _selected(sig),
        date(2026, 1, 1),
        seed=11,
        config=ScheduleConfig(within_component_continue_probability=probability),
    )
    assert [a.date.day for a in result.administrations] == days
    assert [a.cycle_number for a in result.administrations] == [1] * len(days)


def test_certain_unbounded_continuation_requires_a_limit():
    sig = StubSignature()
    sig.timing_sequence = "1,(+1)"
    with pytest.raises(ValueError, match="max_cycles is required"):
        schedule(
            _selected(sig),
            date(2026, 1, 1),
            seed=11,
            config=ScheduleConfig(
                within_component_continue_probability=1,
                within_cycleset_continue_probability=1,
            ),
        )


def test_continuation_and_doses_are_reproducible_for_fresh_signatures():
    def run():
        first = StubSignature("first")
        second = StubSignature("second")
        second.id = 43
        first.timing_sequence = second.timing_sequence = "1,(+1)"
        selected = _selected(first)
        selected.phases[0].regimen.sigs.append(second)
        return schedule(
            selected, date(2026, 1, 1), seed=42, config=ScheduleConfig(max_cycles=20)
        ).administrations

    assert run() == run()


def test_choice_resolution_does_not_consume_dose_or_cycle_continuation_draws():
    fixed_sig = StubSignature()
    fixed_sig.timing_sequence = "1,(+1)"
    fixed = _schedule_signature(
        fixed_sig, seed=37, config=ScheduleConfig(max_cycles=8)
    )
    chosen_sig = StubSignature()
    chosen_sig.alldays = "1,8|9"
    chosen_sig.timing_sequence = "1,(+1)"
    chosen = _schedule_signature(
        chosen_sig, seed=37, config=ScheduleConfig(max_cycles=8)
    )
    assert {a.cycle_number for a in fixed} == {a.cycle_number for a in chosen}
    fixed_day_one = next(a for a in fixed if a.date == date(2026, 1, 1))
    chosen_day_one = next(a for a in chosen if a.date == date(2026, 1, 1))
    assert fixed_day_one.dose == chosen_day_one.dose


def test_unknown_phase_end_raises_before_returning_a_partial_schedule():
    first = StubSignature()
    first.alldays = None
    selected = SelectedProtocol(
        protocol_id="test",
        phases=(
            SelectedRegimen(
                phase_id="pre",
                regimen=_selected(first).phases[0].regimen,
                timing=PhaseTiming.PRE,
            ),
            SelectedRegimen(
                phase_id="post",
                regimen=_selected(StubSignature()).phases[0].regimen,
                timing=PhaseTiming.POST,
            ),
        ),
    )
    with pytest.raises(UnresolvedScheduleError) as exc:
        schedule(selected, date(2026, 1, 1), seed=11)

    assert exc.value.sig_id == 42
    assert exc.value.phase_id == "pre"
    assert "no explicit dosing days" in exc.value.reason


@pytest.mark.parametrize("marker", ["(+c)", "(+n21)"])
def test_letter_cycle_marker_repeats_geometrically_and_respects_max_cycles(marker):
    sig = StubSignature()
    sig.timing_sequence = f"1,{marker}"
    sampled = _schedule_signature(sig, seed=31, config=ScheduleConfig(max_cycles=20))
    fresh_sig = StubSignature()
    fresh_sig.timing_sequence = f"1,{marker}"
    assert sampled == _schedule_signature(
        fresh_sig, seed=31, config=ScheduleConfig(max_cycles=20)
    )
    assert 1 <= len(sampled) <= 20
    assert [administration.cycle_number for administration in sampled] == list(
        range(1, len(sampled) + 1)
    )

    capped_sig = StubSignature()
    capped_sig.timing_sequence = f"1,{marker}"
    capped = _schedule_signature(
        capped_sig,
        seed=31,
        config=ScheduleConfig(
            max_cycles=4,
            within_component_continue_probability=1,
            within_cycleset_continue_probability=1,
        ),
    )
    assert [administration.cycle_number for administration in capped] == [
        1,
        2,
        3,
        4,
    ]


def test_day_continuation_uses_full_calendar_month_boundaries():
    sig = StubSignature()
    sig.alldays = "1,(+7)"
    sig.timing_sequence = "1,2"
    sig.cycle_length_lb = sig.cycle_length_ub = "1"
    sig.cycle_length_unit = "month"
    result = schedule(
        _selected(sig),
        date(2026, 1, 31),
        seed=11,
        config=ScheduleConfig(within_component_continue_probability=1),
    )
    # February's cycle runs until March 31 (the original month anchor), not March 28.
    assert [a.date for a in result.administrations if a.cycle_number == 2] == [
        date(2026, 2, 28),
        date(2026, 3, 7),
        date(2026, 3, 14),
        date(2026, 3, 21),
        date(2026, 3, 28),
    ]


def test_declining_all_optional_cycles_produces_an_empty_schedule():
    sig = StubSignature()
    sig.timing_sequence = "(1)"
    result = schedule(
        _selected(sig),
        date(2026, 1, 1),
        seed=11,
        config=ScheduleConfig(optional_cycle_probability=0),
    )
    assert result.administrations == ()
    assert result.skipped_phases == ()
    episode = Treatment_Episode(
        person_id=1,
        condition_occurrence_id=1,
        regimen_type="test",
        regimen_category_id="test",
    ).attach_schedule(result)
    assert episode.roll_events() == ([], [])


def test_nonnumeric_dose_is_retained_without_inventing_a_quantity():
    sig = StubSignature()
    sig.doseminnum = sig.dosemaxnum = "NS"
    result = schedule(_selected(sig), date(2026, 1, 1), seed=11)
    assert len(result.administrations) == 1
    assert result.administrations[0].dose is None


@pytest.mark.parametrize("component", ["cisplatin", "External beam radiotherapy"])
def test_omop_export_rejects_manually_constructed_undated_schedule_and_episode_payloads(
    component,
):
    from scoop.export.omop import OmopExportConfig, _export_event_payloads, export
    from scoop.vocabulary.gateway import FakeVocabularyGateway

    result = ProtocolSchedule(
        administrations=(
            SystemicAdministration(
                date=None,
                component=Component.from_sig(StubSignature(component)),
                phase_id="phase",
                source_sig_id=42,
                timing_status="unresolved: fixture timing",
            ),
        )
    )
    gateway = FakeVocabularyGateway()
    config = OmopExportConfig(person_id=1)
    with pytest.raises(ValueError, match="Cannot export unresolved administration"):
        export(result, gateway, config)
    episode = Treatment_Episode(
        person_id=1,
        condition_occurrence_id=1,
        regimen_type="test",
        regimen_category_id="test",
    )
    drugs, procedures = episode.attach_schedule(result).roll_events()
    with pytest.raises(ValueError, match="Cannot export unresolved administration"):
        _export_event_payloads(drugs, procedures, gateway=gateway, config=config)


def test_continuing_later_block_keeps_its_predecessor_anchor():
    first = StubSignature("first")
    first.timing_sequence = "1,2"
    first.cycle_length_lb = first.cycle_length_ub = 14
    later = StubSignature("later")
    later.id = 43
    later.timing_sequence = "3,(+1)"
    selected = _selected(first)
    selected.phases[0].regimen.sigs.append(later)
    config = ScheduleConfig(
        max_cycles=5,
        within_component_continue_probability=1,
        within_cycleset_continue_probability=1,
    )
    result = schedule(selected, date(2026, 1, 1), seed=11, config=config)
    assert [
        (a.cycle_number, a.date)
        for a in result.administrations
        if a.component.name == "later"
    ] == [
        (3, date(2026, 1, 29)),
        (4, date(2026, 2, 19)),
        (5, date(2026, 3, 12)),
    ]


def test_unknown_cycle_anchor_raises_a_typed_schedule_error():
    first = StubSignature("first")
    first.timing_sequence = "1,(+1)"
    later = StubSignature("later")
    later.id = 43
    later.timing_sequence = "2"
    selected = _selected(first)
    selected.phases[0].regimen.sigs.append(later)
    with pytest.raises(UnresolvedScheduleError) as exc:
        schedule(
            selected,
            date(2026, 1, 1),
            seed=11,
            config=ScheduleConfig(
                max_cycles=3,
                within_component_continue_probability=1,
                within_cycleset_continue_probability=1,
            ),
        )

    assert exc.value.sig_id == 43
    assert exc.value.phase_id == "phase"


def test_day_continuation_uses_the_seeded_choice_as_its_anchor():
    sig = StubSignature()
    sig.alldays = "1,8|9,(+2)"
    result = schedule(
        _selected(sig),
        date(2026, 1, 1),
        seed=11,
        config=ScheduleConfig(within_component_continue_probability=1),
    )
    days = [administration.date.day for administration in result.administrations]
    assert days[0] == 1
    assert days[1] in (8, 9)
    assert all(right - left == 2 for left, right in zip(days[1:], days[2:]))
    assert all(
        not administration.timing_status.startswith("unresolved:")
        for administration in result.administrations
    )


def test_route_group_does_not_replace_the_actual_route():
    sig = StubSignature()
    sig.route = "intrathecal"
    result = schedule(_selected(sig), date(2026, 1, 1), seed=11)
    administration = result.administrations[0]
    assert administration.route == "intrathecal"
    assert administration.route_group == "IV"
    episode = Treatment_Episode(
        person_id=1,
        condition_occurrence_id=1,
        regimen_type="test",
        regimen_category_id="test",
    )
    drugs, _ = episode.attach_schedule(result).roll_events()
    assert drugs[0]["route_source_value"] == "intrathecal"
    assert drugs[0]["route_group"] == "IV"
