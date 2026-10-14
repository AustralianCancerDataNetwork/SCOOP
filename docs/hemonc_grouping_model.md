# How SCOOP matches treatment decisions to HemOnc

SCOOP separates a patient's treatment decision from the source regimens that could represent it. A graph may produce an endpoint such as an adjuvant treatment decision; the profile then describes which treatment phases or protocols fit that endpoint. SCOOP uses those descriptions to find candidate HemOnc instructions.

This is a simulation mapping. It does not judge whether a regimen is clinically appropriate, rank treatments by evidence, or replace guideline review.

## The matching path

1. **The patient pathway** samples patient and disease fields and reaches a treatment endpoint in a transformation graph.
2. **The intervention catalog** connects that endpoint to a treatment phase, a multi-phase protocol, a choice between interventions, a recipe, or a non-HemOnc outcome such as surgery.
3. **The protocol registry** describes each HemOnc-backed phase: its condition context, component requirements, broad treatment categories, and optional study restrictions.
4. **The HemOnc source** is searched for candidate variants and instructions that meet those requirements. The configured vocabulary and HemOnc access must be available for this step.

The patient context can also be required before an intervention can be resolved. For example, an endpoint may require a biomarker field. A missing required field is a missing input, not evidence that a treatment does or does not apply.

## How phase requirements work

A phase can restrict candidates by condition, component, and broad category. Component lists express required alternatives: terms within one list are alternatives, while separate lists are separate requirements. Category restrictions constrain the number of components classified in categories such as chemotherapy, immunotherapy, surgery, or radiotherapy.

For example, this NSCLC phase asks for at least one chemotherapy component and one immunotherapy component matching any of the named drugs:

```yaml
neoadjuvant_chemo_ici:
  component_requirements:
    component_lists:
      - components:
          - nivolumab
          - pembrolizumab
          - durvalumab
          - tislelizumab
    category_restrictions:
      - category: chemotherapy
        type: min
        amount: 1
      - category: immunotherapy
        type: exact
        amount: 1
```

This is a source-candidate filter, not a complete clinical rule. A matching result does not establish that the regimen is appropriate for a particular patient.

## One phase or several

Use one phase when the endpoint corresponds to one treatment period. A protocol groups phases that belong to one broader course. A `Choice` represents alternatives; SCOOP retains the alternatives as alternatives rather than treating them as components to administer together. A recipe describes a sequence or concurrent arrangement that cannot be represented as a single HemOnc phase. Non-HemOnc outcomes represent decisions outside the HemOnc regimen model.

An endpoint with no declared or resolvable treatment mapping should remain visibly unresolved. Do not add a broad candidate query merely to make every endpoint return a regimen.

## What a match does and does not say

- A candidate means the declared source criteria matched HemOnc data. It is not a recommendation or a confirmed treatment history.
- Condition names are resolved through the configured vocabulary gateway; matching depends on the available vocabulary and source data.
- Drug and category rules depend on SCOOP's component classification. Review the resulting candidates when changing classification or query criteria.
- HemOnc timing can be incomplete or ambiguous. See [Understanding treatment schedules](scheduling-rollout.md) for how timing uncertainty is retained.

## Where to edit

For the current NSCLC profile, treatment phases and protocols are in [`nsclc_protocol_registry.yaml`](../scoop/profiles/nsclc/nsclc_protocol_registry.yaml).
Decision-to-treatment mappings are in [`nsclc_interventions.yaml`](../scoop/profiles/nsclc/nsclc_interventions.yaml).
The profile manifest connects those files to the graphs. The [profile guide](creating_new_profile.md) describes how to review and validate the whole set.
