# Understanding treatment schedules

SCOOP turns the selected HemOnc treatment instructions into dated or relatively timed administration events. A schedule is an interpretation of the source instructions for simulation. It is not a clinical order, a dose recommendation, or a claim that a patient received treatment.

## What appears in a schedule

Each event represents a source-based administration point for one treatment component. For example, a three-week protocol with two drugs on day 1 and one daily oral drug can produce several events per cycle. Events retain the source component and dose details where those details are available.

The schedule keeps several ideas separate:

- **Administration events** are the source-supported treatment points.
- **Cycle numbers** identify the source cycle when it can be determined.
- **Elapsed days and dates** provide a timeline when the source timing can be anchored.
- **Activity intensity** describes treatment activity around an event; it does not add administrations or doses.

Missing or ambiguous source information stays visible as unresolved timing. SCOOP does not fill gaps with guessed dates or administrations. An unresolved event may have no date or cycle number, and its timing status explains why.

## Reading timing

| Source instruction | How SCOOP treats it |
| --- | --- |
| Explicit numeric cycle and day | Retains the stated cycle/day and derives a relative date when an anchor is available. |
| Negative day, such as day −1 | Places the event before the cycle anchor by that many days. |
| Numeric repeat marker, such as `(+2)` | Uses the source interval to identify later cycle or day opportunities. Empty cycles still count toward elapsed time. |
| Letter repeat marker, such as `(+c)` | Keeps explicit administrations but does not infer a repeat interval. The continuation remains unresolved. |
| Choice between possible days | Keeps the uncertain timing unresolved. SCOOP does not choose one alternative or combine alternatives into extra doses. |
| No explicit dosing day | Retains an unresolved timing record rather than inventing an administration date. |
| Continuous infusion | Preserves the source duration/frequency information; the infusion is not expanded into daily doses. |

Dates are illustrative. If no start date is supplied, elapsed timing can still be represented without calendar dates. A skipped optional cycle retains its place in the cycle sequence, so later treatment is not pulled earlier.

## Repeated and optional treatment

The schedule sampler applies continuation settings to repeat opportunities. By default, it can stop a continuing cycle block, stop an individual component, or omit an optional cycle. Explicitly required cycles are retained. Once a block or component stops, it does not restart later in the sampled schedule.

The main settings are:

| Setting | Meaning |
| --- | --- |
| `max_cycles` | Highest source cycle number included, including continuing cycles. |
| `within_cycleset_continue_probability` | Chance that a continuing cycle block repeats at the next opportunity. Default: `0.97`. |
| `within_component_continue_probability` | Chance that an active component continues at the next opportunity. Also controls numeric day continuation within a cycle. Default: `0.8`. |
| `optional_cycle_probability` | Chance that an optional cycle is included. The decision is shared by the components and dosing days in that optional cycle. Default: `0.7`. |

Use a fixed random seed with the same selected source instructions, ordering, and settings to reproduce a sampled schedule. The exact sequence of random draws from older versions is not preserved.

## Limits and export

SCOOP currently leaves HemOnc `Choice` timing alternatives unresolved; it does not sample among them. If an ordered treatment phase has unresolved timing, later phase start dates may also be unknown. These records should be reviewed before use as dated facts.

OMOP export rejects unresolved administrations. Resolve or exclude those records before exporting; SCOOP does not convert unknown timing into a dated clinical event.
