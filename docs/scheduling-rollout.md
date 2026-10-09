# Understanding treatment schedules

SCOOP turns selected HemOnc instructions into dated administration events for
simulation. A schedule is not a clinical order, a dose recommendation, or evidence that
a patient received treatment.

## What appears in a schedule

Each event represents one source-based administration point for one treatment
component. A multi-drug cycle can therefore produce several events on the same date.
Events retain source component and dose details where those details are available.

The schedule keeps several ideas separate:

- **Administration events** are the source-supported treatment points.
- **Cycle numbers** identify source cycles when they can be determined.
- **Elapsed days and dates** place events on a timeline when the source timing can be anchored.
- **Activity intensity** describes treatment activity around an event; it does not add administrations or doses.

## How SCOOP handles timing choices

When HemOnc gives alternative days, SCOOP picks one option using the schedule seed before rolling out the events. For example, `1,8|9` becomes day 1 plus either day 8 or day 9. The same seed and source instructions produce the same choice, including when the signatures are freshly loaded. Choice resolution has its own random stream, so it does not consume the random draws used for dose and continuation sampling.

This seeded choice is a simulation policy; it does not mean the source prefers that day. The selected alternative is retained as a dated event, rather than representing all alternatives as administrations.

## Repeated and optional treatment

Numeric repeat markers use the interval recorded in the source. Letter markers such as `(+c)` or `(+n21)` in `timing_sequence` have no numeric cycle interval, so SCOOP repeats them at one-cycle intervals; for `(+n21)`, the 21-day marker does not cap this cycle-level repeat. The usual cycle-block and component survival probabilities sample a geometric continuation tail. `max_cycles` caps the highest cycle included. When both continuation probabilities are `1`, `max_cycles` is required.

Letter markers in `alldays` remain unresolved because their day interval is ambiguous. SCOOP does not infer a day-level cadence from the marker.

The main settings are:

| Setting | Meaning |
| --- | --- |
| `max_cycles` | Highest source cycle number included, including continuing cycles. |
| `within_cycleset_continue_probability` | Chance that a continuing cycle block repeats at the next opportunity. Default: `0.97`. |
| `within_component_continue_probability` | Chance that an active component continues at the next opportunity. Also controls numeric day continuation within a cycle. Default: `0.8`. |
| `optional_cycle_probability` | Chance that an optional cycle is included. The decision is shared by the components and dosing days in that cycle. Default: `0.7`. |

Explicitly required cycles are retained. Once a block or component stops, it does not restart later. A skipped optional cycle keeps its place in the cycle sequence. Numeric day continuation stops at the next full cycle boundary. Continuous infusions retain their source duration and frequency; they are not expanded into daily doses.

## When schedule generation fails

`schedule()` returns only dated administrations. If timing remains unresolved, it raises `UnresolvedScheduleError` with the HemOnc signature id (`sig_id`), selected phase id (`phase_id`), and a short `reason`. Examples include:

- non-contiguous source cycle numbers;
- a day-level letter repeat marker;
- an instruction with no explicit dosing days; and
- a later phase whose start depends on an unresolved earlier phase.

The caller must handle or report this error. SCOOP does not return a partial schedule containing undated administrations. OMOP export retains its own unresolved-timing check as a safeguard for schedules constructed outside `schedule()`.

Dates are illustrative. If no start date is supplied, elapsed timing can be represented without calendar dates. Use the same seed, selected instructions, ordering, and settings to reproduce a schedule; exact random draws from older versions are not preserved.
