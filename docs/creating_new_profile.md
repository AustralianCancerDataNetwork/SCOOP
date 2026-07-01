# Creating A New Profile

This guide describes how to build a new SCOOP disease profile from a different batch of clinical practice guidelines.

## What A Profile Is

A disease profile requires a number of related artifacts:

1. One or more transformation graphs to model clinical state and treatment decision endpoints.
2. A regimen registry that defines the canonical treatment categories for that disease.
3. A map to link those categories to HemOnc-backed regimen candidates.
4. Source guideline documents that justify the decision points and category structure.

## Before You Start

Before writing YAML, decide what kind of profile you are building.

Questions to answer first:

- Are you modeling one disease only, or a disease family?
- Are the guidelines naturally divided into stages, lines of therapy, molecular subsets, or resectability groups?
- Do you want one combined graph, or several stage-specific graphs?
- Do you expect treatment endpoints to map cleanly to existing HemOnc variants, or will some endpoints require combinations of sig bundles?

For most new domains, the easiest path is:

1. Start with stage- or subgroup-specific graphs.
2. Create a registry early.
3. Treat regimen selection as a separate layer from graph logic.

That separation is important. The graph should answer "what kind of treatment decision endpoint did this patient land in?" The regimen layer should answer "which regimen candidates belong to that endpoint?"

## Recommended Workflow

### 1. Collect Source Guidelines

First, extract the clinical decision structure. These decision points may include:

- disease subgroups
- staging buckets
- biomarker branches
- eligibility rules
- treatment endpoints

What you want to produce from guideline reading is a simple table like:

| Clinical context | Decision factors | Endpoint |
|---|---|---|
| early disease, operable | margin, biomarker | surgery, surgery plus adjuvant targeted therapy, post-op RT |
| metastatic, no driver | morphology, PD-L1, PS, ICI eligibility | ICI monotherapy, ICI plus chemo, platinum doublet, supportive care |

That table becomes the basis for the graph and the registry.

### 2. Decide The Canonical Treatment Endpoint Vocabulary

This is one of the highest-leverage design decisions.

Create canonical entry IDs that sit at the guideline-endpoint level. 

A good canonical ID should answer:

- Would a clinician recognise this as a meaningful guideline endpoint?
- Could multiple HemOnc regimen candidates live under this one label?
- Is this label likely to stay stable even if one regimen option changes?

If the answer is yes, it is probably a good registry entry.

### 3. Create The Profile Folder Structure

For a new disease area, create a peer directory under [`scoop/configuration/profiles/`](../scoop/configuration/profiles).

For example:

```text
scoop/configuration/profiles/<disease>/
  profile.yaml
  <disease>_stg1.yaml
  <disease>_advanced.yaml
  <disease>_special_subset.yaml
  <disease>_regimen_registry.yaml
```

Each profile folder should include a small `profile.yaml` manifest that declares:

- the profile id
- the default graph
- the available graph names in that folder
- the regimen registry file
- mapper paths if they differ from the shared defaults

This keeps profile discovery data-driven and avoids adding new Python factory functions for every disease area.

If your disease does not divide naturally by stage, divide by the same clinical slices that the guidelines use. The important thing is that each graph remains understandable.

### 4. Build The Transformation Graphs

Graph files are loaded through the transformation graph system in [`transformation_graph.py`](../scoop/configuration/transformation_graph.py).

Current node types are:

- `numeric`
- `categorical`
- `numeric_range`
- `branch`

Current graph patterns used in NSCLC:

- Demographic sampling at the `person.*` level
- Disease descriptors at the `condition_occurrence.*` level
- Derived treatment eligibility or subgroup routing at the `condition_episode.*` level
- Treatment decision endpoints at the `treatment_episode.*` level

When building a new graph:

1. Add only the upstream variables that truly influence a guideline decision.
2. Keep derived routing logic explicit with `branch` nodes.
3. Reserve `treatment_episode.*` nodes for actual treatment endpoints, not incidental state.


### 5. Split Complex Guidelines Into Multiple Graphs When Helpful

Do not force one giant graph if the guideline is naturally modular.

The current NSCLC work benefits from splitting into:

- stage I
- resectable stage II-III
- unresectable stage III
- stage IV driver-positive
- stage IV no drivers

This approach helps because:

- each graph is easier to read
- each graph has a tighter decision surface
- the treatment endpoint set becomes more coherent
- review conversations stay clinically grounded

As a rule of thumb, split a graph when one of these is true:

- different guidelines drive fundamentally different pathways
- different biomarker frameworks apply in different settings
- eligibility logic changes substantially across subgroups
- the endpoint set becomes too large to review comfortably

### 6. Create The Regimen Registry Early

Once you have a draft endpoint vocabulary, create the registry file.

The registry is the stable contract between graph endpoints and runtime regimen selection

### 7. Decide Which Entries Are Public And Which Are Internal

Not every registry entry should be a public runtime category.

Public entries are the categories the graph can land on directly.

Internal entries are helper categories used to build public composites. For example, NSCLC uses internal entries like:

- `ptdb_squamous`
- `single_chemo_squamous`
- `atezolizumab`

These support public composites such as:

- `chemotherapy_or_atezolizumab_squamous`

Use an internal entry when:

- it exists mainly to support composite resolution
- it is not a stable guideline endpoint on its own
- it is useful for query composition but not for public reporting

### 8. Choose The Right Resolver Type

The current registry supports three resolver modes:

- `none`
- `component_query`
- `composite`

Use `none` when:

- the graph endpoint is valid
- the guideline support is real
- but you have not yet built a regimen query definition

Use `component_query` when:

- you can describe the endpoint using HemOnc component requirements
- you want direct retrieval of candidate variants or sig bundles

Use `composite` when:

- a public endpoint is really a union of several helper scenarios
- the guideline describes a menu rather than one tightly bounded regimen family

Current examples:

- `met_intervention` is composite
- `ici_monotherapy` is component-query
- unresectable stage III endpoints are currently `none`

### 10. Build Resolver YAMLs

For categories that should resolve into HemOnc-backed regimen candidates...
