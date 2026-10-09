"""Patient continuation decisions around HemOnc's deterministic phase rollout.

Only numeric (+k) markers supply a repeat cadence. Letter markers and missing
source timing remain visible as unresolved instructions. No source sig is mutated.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import TYPE_CHECKING

from hemonc_alchemy.toolkit.analytics.treatment.scheduling import (
    TimedEvent,
    roll_out_phase,
)
from hemonc_alchemy.toolkit.analytics.treatment.scheduling.tokens import Day

from .systemic import (
    OPTIONAL_CYCLE_PROB,
    WITHIN_COMPONENT_CONTINUE_PROB,
    WITHIN_CYCLESET_CONTINUE_PROB,
    BaseRandomSampler,
)

if TYPE_CHECKING:
    from hemonc_alchemy.toolkit.analytics.treatment.scheduling import (
        AnchoredBlock,
        CycleBlock,
    )

    from .rollout import ScheduleConfig


def _probability(value: float | None, default: float) -> float:
    return default if value is None else value


def _continuing_cycles(
    block: CycleBlock,
    *,
    sampler: BaseRandomSampler,
    config: ScheduleConfig,
) -> dict[int, list[int]]:
    """Sample a block's and each component's survival at each repeat opportunity."""
    marker = block.timing_indefinite
    if marker is None or marker.interval is None or not block.cycle_numbers:
        return {}
    component_prob = _probability(
        config.within_component_continue_probability, WITHIN_COMPONENT_CONTINUE_PROB
    )
    block_prob = _probability(
        config.within_cycleset_continue_probability, WITHIN_CYCLESET_CONTINUE_PROB
    )
    if config.max_cycles is None and component_prob == block_prob == 1:
        raise ValueError(
            "max_cycles is required when both continuation probabilities are 1"
        )
    active = list(dict.fromkeys(id(event.sig) for event in block.events))
    extra = {}
    cycle = block.last_cycle + marker.interval
    while active and (config.max_cycles is None or cycle <= config.max_cycles):
        if not sampler._get_bool(block_prob):
            break
        active = [sig_id for sig_id in active if sampler._get_bool(component_prob)]
        if active:
            extra[cycle] = active.copy()
        cycle += marker.interval
    return extra


def _sample_cycles(
    anchored: list[AnchoredBlock],
    explicit: list[TimedEvent],
    *,
    sampler: BaseRandomSampler,
    config: ScheduleConfig,
) -> tuple[list[AnchoredBlock], set[tuple[int, int]]]:
    """Extend block cycle numbers, retaining the original anchor relationships."""
    resolved = {
        id(event.schedule_event.sig)
        for event in explicit
        if event.calendar_date is not None
    }
    replacements = {}
    allowed = set()
    optional_prob = _probability(config.optional_cycle_probability, OPTIONAL_CYCLE_PROB)
    for anchor in anchored:
        block = anchor.block
        extra = {}
        if any(id(event.sig) in resolved for event in block.events):
            extra = _continuing_cycles(block, sampler=sampler, config=config)
        for cycle in sorted(block.cycle_numbers):
            if cycle not in block.optional_cycles or sampler._get_bool(optional_prob):
                allowed.update((id(event.sig), cycle) for event in block.events)
        for cycle, sig_ids in extra.items():
            allowed.update((sig_id, cycle) for sig_id in sig_ids)
        # Empty intervening cycles still consume time for (+2), (+3), etc.
        numbers = block.cycle_numbers
        if extra:
            numbers = frozenset(range(block.first_cycle, max(extra) + 1))
        replacements[id(block)] = replace(block, cycle_numbers=numbers)
    sampled = [
        replace(
            anchor,
            block=replacements[id(anchor.block)],
            anchor_block=replacements.get(id(anchor.anchor_block)),
            overlap_candidates=tuple(
                replacements[id(block)] for block in anchor.overlap_candidates
            ),
        )
        for anchor in anchored
    ]
    return sampled, allowed


def _unresolved(event: TimedEvent, reason: str) -> TimedEvent:
    return replace(
        event,
        day=None,
        elapsed_day=None,
        calendar_date=None,
        timing_status=f"unresolved: {reason}",
    )


def _day_continuation(
    event: TimedEvent,
    *,
    sampler: BaseRandomSampler,
    config: ScheduleConfig,
    end: date | None,
) -> list[TimedEvent]:
    """Extend known numeric day cadence, bounded by the current full cycle."""
    source = event.schedule_event
    marker = source.indefinite
    if source.choices:
        return [
            _unresolved(event, "day continuation depends on an unresolved day choice")
        ]
    if marker.interval is None:
        return [_unresolved(event, "day continuation has no numeric repeat interval")]
    if event.calendar_date is None or event.day is None:
        return []  # The existing unresolved event already carries the source failure.
    start = event.calendar_date - timedelta(
        days=event.day - 1 if event.day > 0 else event.day
    )
    if end is None:
        return [_unresolved(event, "day continuation has no resolved cycle boundary")]
    probability = _probability(
        config.within_component_continue_probability, WITHIN_COMPONENT_CONTINUE_PROB
    )
    output = []
    day = event.day + marker.interval
    delivery = start + timedelta(days=day - 1 if day > 0 else day)
    while delivery < end:
        if not sampler._get_bool(probability):
            break
        output.append(replace(event, day=day, calendar_date=delivery, optional=False))
        day += marker.interval
        delivery = start + timedelta(days=day - 1 if day > 0 else day)
    return output


def _missing_events(
    anchored: list[AnchoredBlock],
    explicit: list[TimedEvent],
) -> list[TimedEvent]:
    """Do not lose signatures whose source contains no rollable day at all."""
    present = {id(event.schedule_event) for event in explicit}
    missing = []
    for anchor in anchored:
        for source in anchor.block.events:
            if id(source) in present:
                continue
            missing.append(
                TimedEvent(
                    schedule_event=source,
                    phase=source.phase,
                    phase_step=source.phase_step,
                    cycle_number=None,
                    day=None,
                    elapsed_day=None,
                    calendar_date=None,
                    intensity=1.0,
                    optional=False,
                    cycle_indefinite=anchor.block.timing_indefinite,
                    timing_status="unresolved: no explicit dosing days",
                )
            )
    return missing


def sample_phase(
    anchored: list[AnchoredBlock],
    *,
    phase_start: date | int,
    seed: int,
    config: ScheduleConfig,
) -> list[TimedEvent]:
    """Keep fixed dates; sample repeat counts and ask HemOnc to date extra cycles."""
    sampler = BaseRandomSampler(seed=seed)
    explicit = roll_out_phase(anchored, phase_start, decay_days=0)
    sampled, allowed = _sample_cycles(
        anchored, explicit, sampler=sampler, config=config
    )
    rolled = roll_out_phase(sampled, phase_start, decay_days=0)
    output = [
        event
        for event in rolled
        if (
            event.cycle_number is None
            or (id(event.schedule_event.sig), event.cycle_number) in allowed
        )
        and (
            config.max_cycles is None
            or event.cycle_number is None
            or event.cycle_number <= config.max_cycles
        )
    ]
    output.extend(_missing_events(anchored, explicit))
    boundaries = _cycle_boundaries(sampled, phase_start)
    output.extend(
        _continuation_events(
            output, sampler=sampler, config=config, boundaries=boundaries
        )
    )
    return output


def _continuation_events(
    output: list[TimedEvent],
    *,
    sampler: BaseRandomSampler,
    config: ScheduleConfig,
    boundaries: dict[tuple[int, int], date],
) -> list[TimedEvent]:
    additions = []
    # One continuation diagnostic/extension per sig and cycle, not per dose day.
    tails = {}
    for event in output:
        key = (id(event.schedule_event.sig), event.cycle_number)
        if event.day is not None:
            previous = tails.get(key)
            if previous is None or event.day > previous.day:
                tails[key] = event
    for event in tails.values():
        if event.schedule_event.indefinite is not None:
            additions.extend(
                _day_continuation(
                    event,
                    sampler=sampler,
                    config=config,
                    end=boundaries.get(
                        (id(event.schedule_event.sig), event.cycle_number)
                    ),
                )
            )
    unknown_cycles = {}
    for event in output:
        if (
            event.cycle_indefinite is not None
            and event.cycle_indefinite.interval is None
        ):
            unknown_cycles[id(event.schedule_event.sig)] = event
    additions.extend(
        _unresolved(event, "cycle continuation has no numeric repeat interval")
        for event in unknown_cycles.values()
    )
    return additions


def _cycle_boundaries(
    anchored: list[AnchoredBlock],
    phase_start: date | int,
) -> dict[tuple[int, int], date]:
    """Ask HemOnc for the next cycle's day 1 without moving any real block end."""
    boundaries = {}
    for anchor in anchored:
        events = tuple(
            replace(event, days=(Day(1),), choices=())
            for event in anchor.block.events
            if event.indefinite is not None
        )
        if not events:
            continue
        probe = replace(
            anchor,
            block=replace(
                anchor.block,
                events=events,
                cycle_numbers=frozenset(
                    cycle + 1 for cycle in anchor.block.cycle_numbers
                ),
            ),
        )
        probe_events = {id(event) for event in events}
        for event in roll_out_phase([*anchored, probe], phase_start, decay_days=0):
            if (
                id(event.schedule_event) in probe_events
                and event.calendar_date is not None
            ):
                boundaries[id(event.schedule_event.sig), event.cycle_number - 1] = (
                    event.calendar_date
                )
    return boundaries
