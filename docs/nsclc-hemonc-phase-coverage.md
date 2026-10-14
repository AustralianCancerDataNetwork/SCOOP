# NSCLC HemOnc phase coverage

This is a snapshot of the 36 source phases in the NSCLC regimen registry,
measured against the live HemOnc database on 9 October 2026 with
`hemonc-alchemy` 0.2.0. Counts are `RegimenCandidate` groups returned by
`SqlHemOncGateway`, not signature or patient counts.

## Intent policy

Intent comes from HemOnc's closed vocabularies. The study-level
`studies_intent` rows take precedence; when a study has no study-level intent,
matching `study_results.intent` values are used. Curative and non-curative
phases match only their corresponding enum member. Preventive, mixed,
and not-applicable values do not match either intent. An unspecified study
intent is treated as absent, so result-level intent can be used as a fallback.
Unspecified result intent is also treated as absent. Context text is not
parsed.

The unknown policy applies only when neither source has an informative intent.
A study with an explicit but non-matching intent is excluded and does not fall
back to result-level intent.

Every source phase declares `unknown_context_policy`:

- `exclude` omits studies with unknown context.
- `phase_type` admits unknown context only when the study has a HemOnc
  signature whose phase matches the registry phase type.
- `include` admits unknown context after the phase's condition, regimen, and
  component requirements have matched. This is a manual clinical judgement
  and needs review before treating the resulting candidates as curative or
  non-curative evidence.

Driver-directed phases use the most specific NSCLC condition CUIs available in
HemOnc alongside the general lung and NSCLC conditions. HemOnc has no
lung-specific NTRK condition, so that phase keeps the NSCLC conditions and its
drug requirements; the broader NTRK solid-neoplasm condition is not used.

## Candidate counts

The Before column is the original intent-policy comparison. After now reflects
the current registry, including CUI-bound drug entries and corrected spellings.
The spelling-only change is isolated in the registry vocabulary check below.

| Phase | Intent | Before | After | Unknown context | Remaining gap |
| --- | --- | ---: | ---: | --- | --- |
| `surgery_only` | curative | 0 | 0 | phase type | Surgery is not a HemOnc drug or radiotherapy signature. |
| `port` | curative | 0 | 4 | include† | |
| `adj_osimertinib` | curative | 0 | 6 | include† | |
| `neoadjuvant_chemo_ici` | curative | 0 | 13 | include† | |
| `adjuvant_ici` | curative | 4 | 5 | include† | |
| `adjuvant_pembrolizumab` | curative | 4 | 1 | include† | |
| `adjuvant_atezolizumab_pembrolizumab` | curative | 4 | 4 | include† | |
| `adjuvant_osimertinib` | curative | 0 | 4 | include† | |
| `adjuvant_cisplatin_based` | curative | 17 | 19 | phase type | |
| `adjuvant_optchemo_alectinib` | curative | 0 | 3 | include† | |
| `conc_crt` | curative | 2 | 4 | phase type | |
| `seq_crt_chemo` | curative | 0 | 2 | include† | |
| `definitive_rt` | curative | 0 | 4 | include† | |
| `consolidation_durvalumab` | curative | 0 | 1 | include† | |
| `consolidation_osimertinib` | curative | 0 | 4 | include† | |
| `egfr_intervention` | non-curative† | 2 | 18 | exclude | |
| `alk_intervention` | non-curative† | 3 | 6 | exclude | |
| `ros1_intervention` | non-curative† | 0 | 3 | include† | |
| `braf_intervention` | non-curative† | 0 | 0 | include† | No NSCLC-associated signatures match the dabrafenib/trametinib combination. |
| `ret_intervention` | non-curative† | 0 | 1 | include† | |
| `erbb2_intervention` | non-curative† | 0 | 2 | include† | |
| `ntrk_intervention` | non-curative† | 0 | 1 | include† | |
| `met_tki` | non-curative† | 0 | 2 | include† | |
| `met_ptdb` | non-curative† | 14 | 67 | exclude | |
| `met_ptdb_w_ici` | non-curative† | 0 | 28 | include† | |
| `best_supportive_care` | non-curative† | 0 | 10 | exclude | |
| `platinum_doublet_3rd_gen_agent` | non-curative† | 12 | 44 | exclude | |
| `platinum_doublet_nonsquamous` | non-curative† | 1 | 28 | exclude | |
| `ici_w_chemo_nonsquamous` | non-curative† | 0 | 53 | include† | |
| `ici_w_chemo_squamous` | non-curative† | 0 | 38 | include† | |
| `ici_monotherapy` | non-curative† | 4 | 17 | exclude | |
| `ptdb_squamous` | non-curative† | 1 | 16 | exclude | |
| `single_chemo_squamous` | non-curative† | 0 | 3 | include† | |
| `ptdb_nonsquamous` | non-curative† | 1 | 28 | exclude | |
| `single_chemo_nonsquamous` | non-curative† | 0 | 6 | include† | |
| `atezolizumab` | non-curative† | 0 | 1 | include† | |

† This is a clinical judgement, either for phase intent or unknown-intent
inclusion. For `include`, the source lacks a classified context; candidate
signatures match the configured conditions and component rules. Review these
phases before interpreting their intent. Their after-counts are sensitive to
the declared component rules. A `phase_type` policy requires matching HemOnc
phase data for unknown-context studies; other matching contexts still follow
the declared intent. Candidate counts are sensitive to the configured
component rules.

The registry also marks the following untyped intervention groups as
non-curative: gene-directed interventions, metastatic platinum doublets,
supportive care, and the general platinum, immunotherapy, and single-agent
chemotherapy groups. That interpretation is based on the phase names and
regimen rules and should be reviewed with the unknown-context allowances.

The earlier text-prefix measurement was replaced with the study-intent-first
enum rule. Changes such as `adjuvant_cisplatin_based` (1 to 19) and `met_ptdb`
(13 to 67) reflect the corrected intent source, not broader unknown inclusion.
The broader unknown-intent policy was not applied to every phase:
including unknown context for `adjuvant_cisplatin_based` would produce 187
candidates under the old text-based query. Under the enum-backed query,
including studies with neither intent source produces 150 candidates; the
phase uses `phase_type` to avoid that broader unknown-intent inclusion.

`surgery_only` and `braf_intervention` are the only empty phases in this
snapshot. The former is outside HemOnc's drug/radiotherapy content. The latter
has BRAF drug records in HemOnc, but no matching NSCLC study association for
the required combination. A later database refresh may change either result.

## Registry vocabulary check

The registry now binds 31 drug references to HemOnc CUIs. For every named
reference, its text match and CUI match return the same signature set. The
corrections from capamatinib to capmatinib and reptrectinib to repotrectinib
each add one candidate group: met_tki changes from 1 to 2, and
ros1_intervention changes from 2 to 3. Case-only duplicates were removed,
and the dead geftinib entry was removed.

All three configured class terms resolve to HemOnc Component Class concepts.
The recursive Subsumes check agrees with text/CANMED membership for
Platinum agent and Taxane. It reports a data mismatch for
Immunostimulatory monoclonal antibody: five text-only CUIs and four
hierarchy-only CUIs. The lint prints each divergent CUI with its HemOnc name
and fails until each difference is reviewed and recorded in
`scoop/protocols/hemonc/class_membership_allowlist.yaml`. That file starts
empty. Every acknowledgement needs a one-line reason and review date; entries
fail as stale if the CUI is no longer divergent or no longer exists in the
HemOnc database. This records reviewed differences without choosing which
membership method is authoritative.
