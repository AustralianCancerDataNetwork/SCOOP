# Per-phase treatment scheduling

SCOOP schedules each selected phase's own candidate signatures with
`hemonc-alchemy`: `schedule_events` → `group_into_blocks` → `anchor_blocks` →
`roll_out_phase`. A protocol may select different variants for different phases.
`roll_out_variant` is therefore not the composition boundary.

`rollout.py` owns the existing recipe loop: ordered phases start one day after
the preceding phase's last administration, and concurrent phases use the existing
shared anchor. Within a phase, HemOnc's block transitions include full cycle
lengths. This change does not switch SCOOP's cross-phase rule to full-cycle ends.
An unresolved ordered phase now propagates an unknown start to its successors.

`roll_out_phase` runs with `decay_days=0`: intensity tails represent treatment
activity, not additional doses. Administration dates, actual cycle numbers and
elapsed offsets come from `TimedEvent`; route groups and dose bounds come from
`ScheduleEvent`. The actual signature route stays in `route` for vocabulary
mapping; `route_group` separately carries the broad clinic/home category. HemOnc's
"IV" group includes intrathecal and intramuscular routes and must not replace
the actual route in an OMOP fact. Radiation classification uses `sig_class_value` and
`RAD_SIG_CLASS_VALUE`. Duration values are not used as drug quantities. Missing
or nonnumeric dose bounds produce a null dose.

## Patient sampling

`scoop/schedule/sampling.py` owns continuation decisions. It reuses
`BaseRandomSampler` and the existing probability defaults without modifying
`systemic.py`. Each phase has the existing `seed + phase_index` seed. Dose and
continuation sampling have separate streams; the same signatures, ordering,
configuration and seed reproduce the same result, including freshly loaded
signature objects. Exact legacy random draws are not preserved.

| Configuration | Meaning |
| --- | --- |
| `max_cycles` | Highest source cycle number included, for fixed and continuing cycles. |
| `within_cycleset_continue_probability` | Probability that a whole continuing block survives each next repeat opportunity (default 0.97). |
| `within_component_continue_probability` | Probability that each still-active component survives each next opportunity within a surviving block (default 0.8). Also controls numeric day continuation within a cycle. |
| `optional_cycle_probability` | One decision per optional block cycle, shared by all its components and dosing days (default 0.7). |

Zero and one are honored exactly. Required explicit cycles are not subjected to
continuation probabilities. Once a component or block stops it does not restart.
Unbounded cycle continuation with both survival probabilities equal to one
requires `max_cycles`; otherwise it could never terminate. Optional dosing days
retain their explicit source dates; `optional_cycle_probability` only samples
cycles. Skipped optional cycles still occupy their original calendar interval.

Only numeric `(+k)` supplies a repeat interval: cycles in `timing_sequence`, days
in `alldays`. The sampler extends cycle numbers and asks HemOnc to date them using
the original anchor relationships. Empty intervening cycles in `(+2)` consume
elapsed time without administrations. Numeric day continuation stops at the
next full cycle boundary, also calculated by HemOnc (including calendar months).
The original signature objects and source anchor failures are preserved.

Letter markers such as `(+c)` or `(+n21)` do not supply that cadence; their
explicit doses remain and an unresolved continuation record explains why no
extra dates were generated. In particular, `max_days` on a letter marker is not
reinterpreted as an interval. An unresolved day choice also prevents guessing
where subsequent day continuation starts.

## Unresolved records

`Administration.date` and `SystemicAdministration.cycle_number` can be null.
Keyword-only `timing_status` and `elapsed_day` fields preserve timing provenance
without changing existing positional constructors. Undated records sort last.

Choice-only instructions, the uncertain part of mixed fixed/Choice instructions,
and instructions with no explicit dosing days remain visible. SCOOP does not
sample Choice alternatives in this delivery. Unresolved block timing retains its
reason instead of receiving a fallback date. `phase_start_dates` can contain
null for a phase whose preceding ordered phase has unresolved timing.

`Treatment_Episode.attach_schedule()` preserves timing status and offsets in its
event payloads. Both schedule and episode OMOP export paths explicitly reject
unresolved administrations; they must not be exported as dated clinical facts.

## Validation and compatibility

Existing scheduling date/count assertions are unchanged. The stub fixture now
supplies `phase` and `phase_step`, and radiation fixtures explicitly carry
`class_field="rad sig"`, replacing the old name-based classification assumption.
New tests check negative-day offsets, full preceding cycles, overlaps, source
gaps, actual cycle numbers, stochastic bounds and reproducibility, calendar-month
continuation, unresolved provenance, and export rejection.

`tests/fixtures/nsclc_pembrolizumab_schedule.json` is a real signature snapshot
(Sigs 20450, variant 133537) from the local HemOnc source-data audit database,
loaded through the HemOnc ORM. Its provenance is recorded in the fixture. The
bridge test loads the NSCLC registry and selects this candidate through the
in-memory gateway, asserting three 200 mg administrations 21 days apart. It runs
without a configured live database. Existing live database tests remain intact.

No new hemonc-alchemy exports or changes were needed. The existing local editable
source in `pyproject.toml` and `uv.lock` remains necessary until the rollout fixes
are released. Current code also uses the numeric-marker `Indefinite.interval`
field supplied by that local checkout.

Delivery verification (2026-09-30): `uv run pytest -q` reports **236 passed,
17 skipped, 20 subtests passed**. The skips include the existing live HemOnc
checks because this environment has no configured `hemonc_db`. Ruff passes for
all changed Python files. Whole-repository Ruff still reports 308 pre-existing
findings in unchanged files (321 at baseline); broad lint cleanup is outside this
change, including the explicitly protected `systemic.py`. `git diff --check`
passes, and ripwire's quality comparison has no gating regressions.
